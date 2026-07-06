"""Drop handling for the Pipeline catalog.

Routes asset / shot / Root drops onto Houdini network panes:

- LOP context: creates ``th::import_asset`` / ``th::import_shot`` HDAs,
  or appends to existing ``th::import_assets`` nodes, or upgrades a
  singular ``th::import_asset`` to the plural form when a second asset
  is dropped on it. Root cards drop as a stock ``sublayer`` pointing at
  the Root's ``entity:/scenes/...`` URI.
- SOP context: creates a ``th::import_model`` HDA and drives the
  internal ``lopnet/import_layer`` directly.
- Multi-drop: bundles selected assets into one ``th::import_assets``
  (or appends to an existing one); shots are skipped (multi-shot drops
  are not supported yet).

The router owns no mutable state — every call routes off the catalog
services it was constructed with.
"""

from __future__ import annotations

import logging
from typing import Callable

from tumbletrove.asset_browser.core.projects import ProjectConfig

import _pipeline_containers as containers
from _pipeline_containers import SceneContainer
import _pipeline_uris as uris

log = logging.getLogger(__name__)


def entity_uri_for(asset_or_detail) -> str | None:
    """Build an entity URI string from an Asset/AssetDetail's id and tags.

    Asset IDs are 3-segment ``"PROJECT/CATEGORY/Name"`` (or
    ``"PROJECT/SEQ/Shot"``); the project segment is dropped from the
    URI since URIs themselves don't carry project info.
    """
    parts = asset_or_detail.id.split("/")
    if len(parts) != 3:
        # Exactly PROJECT/SECOND/THIRD. The old `< 3` silently accepted a
        # 4+-segment id and took parts[1]/parts[2], building a plausible-but-
        # wrong URI; reject it instead (mirrors parse_entity_ref).
        return None
    _project, second, third = parts
    if "type:asset" in asset_or_detail.tags:
        return str(uris.entity_asset(second, third))
    if "type:shot" in asset_or_detail.tags:
        return str(uris.entity_shot(second, third))
    return None


def is_import_assets_node(node) -> bool:
    """Detect the *plural* import_assets HDA (multi-asset wrapper)."""
    if node is None:
        return False
    try:
        return node.type().name().startswith("th::import_assets::")
    except Exception:
        return False


def is_import_asset_node(node) -> bool:
    """Detect the *singular* import_asset HDA (one asset per node)."""
    if node is None:
        return False
    try:
        return node.type().name().startswith("th::import_asset::")
    except Exception:
        return False


class DropRouter:
    """Dispatches asset/shot/Root drops to the right Houdini handler.

    The catalog instantiates one and forwards ``on_drop`` /
    ``on_deck_drop`` / ``on_multi_drop`` / ``attach_network_thumbnail``
    through it. Catalog-side concerns (project activation, asset
    resolution, sidecar thumbnail lookup, detail fetch) are injected
    via constructor so the router has no implicit dependency on the
    full catalog surface.
    """

    def __init__(
        self,
        *,
        activate_project: Callable[[ProjectConfig], None],
        project_for_asset_id: Callable[[str], ProjectConfig | None],
        thumbnail_path: Callable[[str], object],
        get_detail: Callable[[str], object],
        get_registry,
    ) -> None:
        self._activate_project = activate_project
        self._project_for_asset_id = project_for_asset_id
        self._thumbnail_path = thumbnail_path
        self._get_detail = get_detail
        self._registry = get_registry

    # ── Single-asset drop ────────────────────────────────

    def on_drop(self, detail, drop) -> bool:
        """Import a single pipeline asset / shot / Root into the scene."""
        import hou

        # Roots (scenes) drop as a sublayer node — they aren't asset/
        # shot entities, so they bypass the import_* HDA path.
        if detail.kind == "scene":
            return self._drop_root_as_sublayer(detail, drop)

        if not drop.network or drop.context not in ("lop", "sop"):
            hou.ui.setStatusMessage(
                "Pipeline assets can only be imported into LOP or SOP networks",
                severity=hou.severityType.Warning,
            )
            return True

        entity_uri = entity_uri_for(detail)
        if entity_uri is None:
            hou.ui.setStatusMessage(
                f"Cannot derive entity URI for {detail.name}",
                severity=hou.severityType.Warning,
            )
            return True  # Always handled — never fall through to fallback menu

        # Activate the asset's project so import_asset / import_shot
        # resolve against the right pipeline config.
        proj = self._project_for_asset_id(detail.id)
        if proj is not None:
            self._activate_project(proj)

        # SOP context: drop a th::import_model HDA. Shots and group
        # containers don't have a SOP-side equivalent — reject them.
        if drop.context == "sop":
            if "type:shot" in detail.tags:
                hou.ui.setStatusMessage(
                    "Shots can only be imported into LOP networks",
                    severity=hou.severityType.Warning,
                )
                return True
            return self._drop_asset_as_import_model(detail, drop, entity_uri)

        # Shots cannot be appended to an import_assets node — fall
        # through to the normal single-shot path below.
        target = getattr(drop, "target_node", None)
        if (
            "type:shot" not in detail.tags
            and is_import_assets_node(target)
        ):
            try:
                from tumblepipe.pipe.houdini.lops import import_assets
                wrapper = import_assets.ImportAssets(target)
                wrapper.add_asset_entry(uris.parse(entity_uri))
                wrapper.execute()
            except Exception:
                log.exception(
                    "Failed to append %s to %s",
                    detail.id, target.path(),
                )
                hou.ui.setStatusMessage(
                    f"Failed to add {detail.name} (see console)",
                    severity=hou.severityType.Error,
                )
                return True
            hou.ui.setStatusMessage(
                f"Added {detail.name} to {target.name()}",
                severity=hou.severityType.Message,
            )
            return True

        # Drop onto a singular import_asset → upgrade it to a plural
        # import_assets node carrying both the original asset and the
        # newly dropped one, preserving wiring and position.
        if (
            "type:shot" not in detail.tags
            and is_import_asset_node(target)
        ):
            try:
                raw = self._upgrade_singular_to_plural(
                    target, uris.parse(entity_uri),
                )
            except Exception:
                log.exception(
                    "Failed to upgrade %s to plural import_assets",
                    target.path(),
                )
                raw = None
            if raw is not None:
                raw.setSelected(True, clear_all_selected=True)
                self.attach_network_thumbnail(detail.id, raw, drop)
                hou.ui.setStatusMessage(
                    f"Combined {detail.name} into {raw.name()}",
                    severity=hou.severityType.Message,
                )
                return True
            # Fall through to the normal single-asset path on failure.

        network = drop.network
        try:
            if "type:shot" in detail.tags:
                from tumblepipe.pipe.houdini.lops import import_shot
                # Prefix with sequence so the node name matches the
                # displayed Asset.name and avoids leading-digit shot
                # names (e.g. "010") that Houdini rejects.
                seq = detail.metadata.get("sequence", "")
                node_name = f"{seq}_{detail.name}" if seq else detail.name
                node = import_shot.create(network, node_name.replace(" ", "_"))
                node.set_shot_uri(uris.parse(entity_uri))
                node.execute()
            else:
                from tumblepipe.pipe.houdini.lops import import_asset
                node = import_asset.create(network, detail.name.replace(" ", "_"))
                node.set_entity_uri(uris.parse(entity_uri))
                node.execute()
            raw = node.native()
            if drop.position is not None:
                raw.setPosition(drop.position - hou.Vector2(0.5, 0.0))
            else:
                raw.moveToGoodPosition()
            raw.setSelected(True, clear_all_selected=True)
            raw.setDisplayFlag(True)
            raw.setRenderFlag(True)
            self.attach_network_thumbnail(detail.id, raw, drop)
        except Exception:
            log.exception("Failed to drop %s", detail.id)
            hou.ui.setStatusMessage(
                f"Failed to import {detail.name} (see console)",
                severity=hou.severityType.Error,
            )
            return True  # Always handled — error logged + status message

        hou.ui.setStatusMessage(
            f"Imported {detail.name}", severity=hou.severityType.Message,
        )
        return True

    # ── Deck item drop ────────────────────────────────────

    def on_deck_drop(self, asset, deck_keys, drop) -> bool:
        """Asset/shot dept deck items fall through to the browser's
        default ``import_layer`` LOP path."""
        return False

    # ── Multi-asset drop ─────────────────────────────────

    def on_multi_drop(self, assets, drop) -> bool:
        """Import multiple pipeline assets into the scene.

        Multiple assets → one ``th::import_assets::2.0`` with multiparm
        entries populated. Shots are skipped (multi-shot drops are not
        supported yet).
        """
        import hou

        if drop.context != "lop" or not drop.network:
            hou.ui.setStatusMessage(
                "Pipeline assets can only be imported into LOP networks",
                severity=hou.severityType.Warning,
            )
            return True

        # Filter to assets only — multi-shot drops are not supported.
        asset_items = [a for a in assets if "type:asset" in a.tags]
        if not asset_items:
            return False

        # A single asset should fall back to the single-drop path so
        # we get an `import_asset` node instead of an `import_assets`.
        if len(asset_items) == 1:
            try:
                detail = self._get_detail(asset_items[0].id)
            except Exception:
                log.exception("Failed to fetch detail for single drop")
                return False
            return self.on_drop(detail, drop)

        # If the drop lands on an existing import_assets node,
        # append each dragged asset as a new entry.
        target = getattr(drop, "target_node", None)
        if is_import_assets_node(target):
            try:
                from tumblepipe.pipe.houdini.lops import import_assets
                wrapper = import_assets.ImportAssets(target)
                added = 0
                for a in asset_items:
                    uri_str = entity_uri_for(a)
                    if uri_str is None:
                        continue
                    wrapper.add_asset_entry(uris.parse(uri_str))
                    added += 1
                wrapper.execute()
            except Exception:
                log.exception(
                    "Failed to append %d assets to %s",
                    len(asset_items), target.path(),
                )
                return False
            hou.ui.setStatusMessage(
                f"Added {added} assets to {target.name()}",
                severity=hou.severityType.Message,
            )
            return True

        network = drop.network
        try:
            from tumblepipe.pipe.houdini.lops import import_assets

            node = import_assets.create(network, "import_assets")
            multiparm = node.parm("asset_imports")
            multiparm.set(len(asset_items))

            for i, a in enumerate(asset_items, start=1):
                entity_uri = entity_uri_for(a)
                if entity_uri is None:
                    continue
                node.parm(f"entity{i}").set(entity_uri)

            node.execute()
            raw = node.native()
            if drop.position is not None:
                raw.setPosition(drop.position - hou.Vector2(0.5, 0.0))
            else:
                raw.moveToGoodPosition()
            raw.setSelected(True, clear_all_selected=True)
            raw.setDisplayFlag(True)
            raw.setRenderFlag(True)
        except Exception:
            log.exception("Failed multi-drop of %d assets", len(asset_items))
            return False

        hou.ui.setStatusMessage(
            f"Imported {len(asset_items)} assets",
            severity=hou.severityType.Message,
        )
        return True

    # ── Thumbnail attach (public — used by browser host too) ──

    def attach_network_thumbnail(self, asset_id, raw_node, drop) -> None:
        """Attach the asset/shot's ``thumbnail.png`` sidecar as a
        ``hou.NetworkImage`` next to the given import node.

        Public so the asset browser host can call it for non-catalog-
        owned drops too (e.g. ``import_layer`` nodes created by the
        deck item drop path). No-op when the sidecar isn't on disk or
        the drop didn't land on a network editor.
        """
        import hou
        try:
            thumb = self._thumbnail_path(asset_id)
            if thumb is None:
                log.debug(
                    "network thumbnail: no path resolvable for %s",
                    asset_id,
                )
                return
            if not thumb.exists():
                log.debug(
                    "network thumbnail: sidecar missing on disk for %s "
                    "(expected at %s)",
                    asset_id, thumb,
                )
                return
            from tumblepipe.pipe.houdini import network_thumbnail
            editor = (
                drop.pane
                if isinstance(drop.pane, hou.NetworkEditor)
                else None
            )
            log.debug(
                "network thumbnail: attaching %s to %s (editor=%s)",
                thumb, raw_node.path(), editor,
            )
            network_thumbnail.attach(raw_node, thumb, editor=editor)
        except Exception:
            log.exception(
                "Failed to attach network thumbnail for %s", asset_id,
            )

    # ── Per-context handlers ─────────────────────────────

    def _drop_asset_as_import_model(self, detail, drop, entity_uri: str) -> bool:
        """Drop an asset into a SOP network as a ``th::import_model`` HDA.

        Sets the HDA's ``entity`` parm to the asset URI, defaults the
        ``department`` parm based on tags (``blendshape`` if the asset
        is tagged as such; ``model`` otherwise), then triggers the
        HDA's ``execute()`` so the internal LOP chain populates.
        Returns ``True`` always — failures emit a status message.
        """
        import hou

        network = drop.network
        node_name = detail.name.replace(" ", "_")
        try:
            raw = network.createNode("th::import_model::1.0", node_name)
            raw.parm("entity").set(entity_uri)
            dept_parm = raw.parm("department")
            if dept_parm is not None:
                dept = "blendshape" if "type:blendshape" in detail.tags else "model"
                dept_parm.set(dept)
            # Drive the internal lopnet/import_layer directly rather than
            # going through the HDA's PythonModule.execute(). The HDA's
            # execute() relies on hou.pwd() which isn't set from a drop-
            # handler context, AND its definition may be cached in the
            # live session (so a freshly-rebuilt .hda's new signatures
            # wouldn't be live yet). Talking straight to the inner IL
            # node sidesteps both issues; entity/department/version on
            # the IL are channel-referenced to the SOP HDA's parms, so
            # we just set the SOP parms above and let IL.execute() read
            # them through the references.
            try:
                il_node = raw.node("lopnet/import_layer")
                if il_node is not None:
                    from tumblepipe.pipe.houdini.lops import import_layer
                    import_layer.ImportLayer(il_node).execute()
                    # Copy the IL's resolved labels onto the SOP HDA so
                    # entity_label / version_label show the live values
                    # instead of "from_context: none".
                    for parm_name in ("entity_label", "version_label"):
                        src = il_node.parm(parm_name)
                        dst = raw.parm(parm_name)
                        if src is not None and dst is not None:
                            dst.set(src.eval())
                    # Mirror IL's bypass onto the outer SOP HDA so the
                    # node is visibly disabled when the asset isn't
                    # staged on disk. IL already wrote a "Bypassed: ..."
                    # comment with the reason — surface that on the
                    # outer node instead of leaving it silent.
                    if il_node.isBypassed():
                        comment = il_node.comment() or "Bypassed: No staged asset"
                        raw.setComment(comment)
                        raw.setGenericFlag(
                            hou.nodeFlag.DisplayComment, True,
                        )
                        raw.bypass(True)
            except Exception:
                log.exception(
                    "import_model: driving inner import_layer failed for %s",
                    detail.id,
                )
            if drop.position is not None:
                raw.setPosition(drop.position - hou.Vector2(0.5, 0.0))
            else:
                raw.moveToGoodPosition()
            raw.setSelected(True, clear_all_selected=True)
            raw.setDisplayFlag(True)
            raw.setRenderFlag(True)
            self.attach_network_thumbnail(detail.id, raw, drop)
        except Exception:
            log.exception("Failed to drop %s as import_model", detail.id)
            hou.ui.setStatusMessage(
                f"Failed to import {detail.name} (see console)",
                severity=hou.severityType.Error,
            )
            return True

        hou.ui.setStatusMessage(
            f"Imported {detail.name}", severity=hou.severityType.Message,
        )
        return True

    def _drop_root_as_sublayer(self, detail, drop) -> bool:
        """Drop a Root card into a LOP network as a stock ``sublayer``
        node pointing at the Root's ``entity:/scenes/...`` URI.

        The asset resolver maps that URI to the latest exported scene
        USD at evaluation time, so the sublayer stays in sync with
        re-exports automatically. Returns ``True`` always (drop is
        considered handled even on failure — failures emit a status
        message rather than fall through to a fallback menu).
        """
        import hou

        if drop.context != "lop" or not drop.network:
            hou.ui.setStatusMessage(
                "Roots can only be sublayered into LOP networks",
                severity=hou.severityType.Warning,
            )
            return True

        ref = containers.parse(detail.id)
        if not isinstance(ref, SceneContainer):
            return True

        proj = self._registry.get(ref.project_name)
        if proj is not None:
            try:
                self._activate_project(proj)
            except Exception:
                log.exception(
                    "activate_project failed for Root drop %s", detail.id,
                )

        try:
            from tumblepipe.pipe.usd import generate_scene_sublayer_uri
            layer_uri = generate_scene_sublayer_uri(ref.uri)
        except Exception:
            log.exception(
                "Failed to build sublayer URI for Root %s", detail.id,
            )
            hou.ui.setStatusMessage(
                f"Failed to sublayer Root {detail.name}",
                severity=hou.severityType.Warning,
            )
            return True

        network = drop.network
        name = (detail.name or "root").replace(" ", "_")
        try:
            node = network.createNode("sublayer", name)
            # ``num_files`` controls the multiparm count for layer
            # paths; defaults to 1 on a fresh node but set explicitly
            # in case of future schema changes.
            try:
                node.parm("num_files").set(1)
            except Exception:
                pass
            node.parm("filepath1").set(layer_uri)
            if drop.position is not None:
                node.setPosition(drop.position - hou.Vector2(0.5, 0.0))
            else:
                node.moveToGoodPosition()
            node.setSelected(True, clear_all_selected=True)
            node.setDisplayFlag(True)
        except Exception:
            log.exception(
                "Failed to create sublayer for Root %s", detail.id,
            )
            hou.ui.setStatusMessage(
                f"Failed to import Root {detail.name}",
                severity=hou.severityType.Warning,
            )
            return True

        hou.ui.setStatusMessage(
            f"Sublayered Root: {detail.name}",
            severity=hou.severityType.Message,
        )
        return True

    # ── Node upgrade ─────────────────────────────────────

    def _upgrade_singular_to_plural(self, target, new_uri):
        """Replace a singular ``th::import_asset`` node with a new
        ``th::import_assets`` node containing both the existing asset
        and ``new_uri``.

        Preserves the target's position, name, wiring, exclude-departments,
        include-layerbreak flag, and import mode. Returns the new raw node,
        or ``None``
        if the existing asset URI couldn't be read (the caller should
        fall back to the regular drop path in that case).
        """
        from tumblepipe.pipe.houdini.lops import import_asset, import_assets

        # Read state from the singular node before we destroy it.
        try:
            old = import_asset.ImportAsset(target)
            old_uri = old.get_entity_uri()
            old_variant = old.get_variant_name()
            old_version = old.get_version_name()
            old_excl = old.get_exclude_department_names()
            old_layerbreak = old.get_include_layerbreak()
            old_import_mode = old.get_import_mode()
        except Exception:
            log.exception(
                "Failed to read state from singular import_asset %s",
                target.path(),
            )
            return None
        if old_uri is None:
            return None

        old_position = target.position()
        old_color = target.color()
        old_name = target.name()
        input_conns = list(target.inputConnections())
        output_conns = list(target.outputConnections())
        network = target.parent()

        # Free the name so the plural node can take it cleanly. Suffix
        # the old node so unique_name=False would still work; we destroy
        # it shortly anyway.
        try:
            target.setName(f"{old_name}__obsolete", unique_name=True)
        except Exception:
            log.debug(
                "Could not rename %s before upgrade", target.path(),
                exc_info=True,
            )

        plural = import_assets.create(network, old_name)
        raw = plural.native()

        # Reset the default entry count so the two add_asset_entry calls
        # below produce indices 1 and 2 instead of stacking on top of an
        # empty pre-allocated entry from the HDA's default state.
        try:
            plural.parm('asset_imports').set(0)
        except Exception:
            log.debug("Could not reset asset_imports count", exc_info=True)

        try:
            plural.set_exclude_department_names(old_excl)
            plural.set_include_layerbreak(old_layerbreak)
            plural.set_import_mode(old_import_mode)
        except Exception:
            log.debug(
                "Could not transfer node-level params to plural",
                exc_info=True,
            )

        plural.add_asset_entry(
            old_uri, variant=old_variant, version=old_version, instances=1,
        )
        plural.add_asset_entry(new_uri)

        # Re-wire: inputs going into target now go into the new raw,
        # outputs consuming target now consume the new raw.
        for ic in input_conns:
            try:
                src_node = ic.inputNode()
                src_idx = ic.outputIndex()
                dst_idx = ic.inputIndex()
                raw.setInput(dst_idx, src_node, src_idx)
            except Exception:
                log.debug("Failed to rewire input", exc_info=True)
        for oc in output_conns:
            try:
                consumer = oc.inputNode()
                src_idx = oc.outputIndex()
                dst_idx = oc.inputIndex()
                consumer.setInput(dst_idx, raw, src_idx)
            except Exception:
                log.debug("Failed to rewire output", exc_info=True)

        # Destroy the old singular AFTER wiring so we don't disconnect
        # anything by accident.
        try:
            target.destroy()
        except Exception:
            log.exception("Failed to destroy old singular %s", old_name)

        # Position + color/style the new node where the old one was.
        try:
            raw.setPosition(old_position)
            if old_color is not None:
                raw.setColor(old_color)
        except Exception:
            log.debug("Position transfer failed", exc_info=True)

        try:
            plural.execute()
        except Exception:
            log.exception(
                "execute() failed on upgraded plural import_assets %s",
                old_name,
            )
        return raw
