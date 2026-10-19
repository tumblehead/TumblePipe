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
- Deck-item drop: one ``th::import_layer`` per dragged department deck
  item of an asset or shot, LOP only.

The router owns no mutable state — every call routes off the catalog
services it was constructed with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .projects import ProjectConfig

from . import containers as containers
from .containers import SceneContainer
from . import uris as uris



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
        # wrong URI; reject it instead (mirrors AssetResolver.split).
        return None
    _project, second, third = parts
    if "type:asset" in asset_or_detail.tags:
        return str(uris.entity_asset(second, third))
    if "type:shot" in asset_or_detail.tags:
        return str(uris.entity_shot(second, third))
    return None


def is_import_assets_node(node) -> bool:
    """Detect the *plural* import_assets HDA (multi-asset wrapper)."""
    return node is not None and node.type().name().startswith(
        "th::import_assets::",
    )


def is_import_asset_node(node) -> bool:
    """Detect the *singular* import_asset HDA (one asset per node)."""
    return node is not None and node.type().name().startswith(
        "th::import_asset::",
    )


def show_dropped_node(raw) -> None:
    """Make a freshly dropped node the one the viewport shows.

    Only SOP (and OBJ) nodes carry a render flag. ``hou.LopNode`` has no
    ``setRenderFlag`` at all, so calling it on an import_asset raises
    AttributeError. Until v1.4.5 an ``except AttributeError: pass`` hid
    that; a cleanup then removed it as dead code. After that, every LOP
    drop failed at this line, after the node had been built. The
    single-asset thumbnail was never attached, and a multi-drop reported
    failure, so the browser dropped each asset again as its own
    import_asset.
    """
    raw.setDisplayFlag(True)
    set_render_flag = getattr(raw, "setRenderFlag", None)
    if set_render_flag is not None:
        set_render_flag(True)


class DropRouter:
    """Dispatches asset/shot/Root drops to the right Houdini handler.

    The catalog instantiates one and forwards ``on_drop`` /
    ``on_deck_drop`` / ``on_multi_drop`` through it. Catalog-side concerns
    (project activation, asset resolution, sidecar thumbnail lookup,
    detail fetch) are injected via constructor so the router has no
    implicit dependency on the full catalog surface.

    A drop raises when it fails: the browser is the failure boundary
    (TumbleTrove >= 0.48) — it reports the error and does not retry the
    drop its own way.
    """

    def __init__(
        self,
        *,
        activate_project: Callable[[ProjectConfig], None],
        project_for_asset_id: Callable[[str], ProjectConfig | None],
        project_for_path: Callable[[Path], ProjectConfig | None],
        thumbnail_path: Callable[[str], object],
        get_detail: Callable[[str], object],
        get_registry,
    ) -> None:
        self._activate_project = activate_project
        self._project_for_asset_id = project_for_asset_id
        self._project_for_path = project_for_path
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
            raise ValueError(f"{detail.id} does not name an asset or a shot")

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
            from tumblepipe.pipe.houdini.lops import import_assets
            wrapper = import_assets.ImportAssets(target)
            wrapper.add_asset_entry(uris.parse(entity_uri))
            wrapper.execute()
            hou.ui.setStatusMessage(
                f"Added {detail.name} to {target.name()}",
                severity=hou.severityType.Message,
            )
            return True

        # Drop onto a singular import_asset → upgrade it to a plural
        # import_assets node carrying both the original asset and the
        # newly dropped one, preserving wiring and position. One with no
        # asset set yet has nothing to combine with: the drop then makes
        # its own node beside it.
        if (
            "type:shot" not in detail.tags
            and is_import_asset_node(target)
        ):
            raw = self._upgrade_singular_to_plural(
                target, uris.parse(entity_uri),
            )
            if raw is not None:
                raw.setSelected(True, clear_all_selected=True)
                self.attach_network_thumbnail(detail.id, raw, drop)
                hou.ui.setStatusMessage(
                    f"Combined {detail.name} into {raw.name()}",
                    severity=hou.severityType.Message,
                )
                return True

        network = drop.network
        if "type:shot" in detail.tags:
            from tumblepipe.pipe.houdini.lops import import_shot
            # Prefix with sequence so the node name matches the
            # displayed Asset.name and avoids leading-digit shot
            # names (e.g. "010") that Houdini rejects.
            seq = detail.metadata.get("sequence", "")
            node_name = f"{seq}_{detail.name}" if seq else detail.name
            node = import_shot.create(network, node_name.replace(" ", "_"))
            node.set_entity_uri(uris.parse(entity_uri))
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
        show_dropped_node(raw)
        self.attach_network_thumbnail(detail.id, raw, drop)

        hou.ui.setStatusMessage(
            f"Imported {detail.name}", severity=hou.severityType.Message,
        )
        return True

    # ── Deck item drop ────────────────────────────────────

    def on_deck_drop(self, asset, deck_keys, drop) -> bool:
        """Import one department layer per dragged department deck item.

        Each key is a department of the asset or shot; each becomes a
        ``th::import_layer`` on its 'current' version (the newest export)
        in the LOP network under the cursor, stacked below the drop point.
        Returns False for a card with no entity (a Multi or Root), whose
        deck items are not layers. Refuses an asset of another project
        than the open scene's: the USD resolver serves one project's
        exports at a time, so the layers would not resolve.
        """
        import hou

        entity_uri = entity_uri_for(asset)
        if entity_uri is None:
            return False
        if drop.context != "lop" or not drop.network:
            hou.ui.setStatusMessage(
                "Department layers can only be imported into LOP networks",
                severity=hou.severityType.Warning,
            )
            return True

        asset_proj = self._project_for_asset_id(asset.id)
        scene_proj = self._project_for_path(Path(hou.hipFile.path()))
        if (
            asset_proj is not None and scene_proj is not None
            and asset_proj.name != scene_proj.name
        ):
            hou.ui.setStatusMessage(
                f"Cannot import {asset_proj.name} layers into a "
                f"{scene_proj.name} scene — the USD resolver serves one "
                "project at a time.",
                severity=hou.severityType.Warning,
            )
            return True
        if asset_proj is not None:
            self._activate_project(asset_proj)

        from tumblepipe.pipe.houdini.lops import import_layer

        uri = uris.parse(entity_uri)
        for index, dept in enumerate(deck_keys):
            node = import_layer.create(
                drop.network, f"{asset.name}_{dept}".replace(" ", "_"),
            )
            node.set_entity_uri(uri)
            node.set_department_name(dept)
            node.execute()
            raw = node.native()
            if drop.position is not None:
                raw.setPosition(
                    drop.position + hou.Vector2(-0.5, -index * 1.2),
                )
            else:
                raw.moveToGoodPosition()
            first = index == 0
            raw.setSelected(first, clear_all_selected=first)
            show_dropped_node(raw)
            self.attach_network_thumbnail(asset.id, raw, drop)

        hou.ui.setStatusMessage(
            f"Imported {asset.name} / {', '.join(deck_keys)}",
            severity=hou.severityType.Message,
        )
        return True

    # ── Multi-asset drop ─────────────────────────────────

    def on_multi_drop(self, assets, drop) -> bool:
        """Import multiple pipeline assets into the scene.

        Multiple assets → one ``th::import_assets::2.0`` with multiparm
        entries populated. Shots are skipped (multi-shot drops are not
        supported yet).

        Returns False only when there is no asset to bundle. The browser
        answers False by dropping each asset on its own, which would put
        an import_asset beside the import_assets this built.
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
            return self.on_drop(self._get_detail(asset_items[0].id), drop)

        entity_uris = [entity_uri_for(a) for a in asset_items]

        # If the drop lands on an existing import_assets node,
        # append each dragged asset as a new entry.
        target = getattr(drop, "target_node", None)
        if is_import_assets_node(target):
            from tumblepipe.pipe.houdini.lops import import_assets
            wrapper = import_assets.ImportAssets(target)
            for entity_uri in entity_uris:
                wrapper.add_asset_entry(uris.parse(entity_uri))
            wrapper.execute()
            hou.ui.setStatusMessage(
                f"Added {len(entity_uris)} assets to {target.name()}",
                severity=hou.severityType.Message,
            )
            return True

        from tumblepipe.pipe.houdini.lops import import_assets

        node = import_assets.create(drop.network, "import_assets")
        node.parm("asset_imports").set(len(entity_uris))
        for i, entity_uri in enumerate(entity_uris, start=1):
            node.parm(f"entity{i}").set(entity_uri)

        node.execute()
        raw = node.native()
        if drop.position is not None:
            raw.setPosition(drop.position - hou.Vector2(0.5, 0.0))
        else:
            raw.moveToGoodPosition()
        raw.setSelected(True, clear_all_selected=True)
        show_dropped_node(raw)

        hou.ui.setStatusMessage(
            f"Imported {len(entity_uris)} assets",
            severity=hou.severityType.Message,
        )
        return True

    # ── Thumbnail attach ──────────────────────────────────

    def attach_network_thumbnail(self, asset_id, raw_node, drop) -> None:
        """Attach the asset/shot's ``thumbnail.png`` sidecar as a
        ``hou.NetworkImage`` next to the given import node. No-op when
        the sidecar isn't on disk.
        """
        import hou
        thumb = self._thumbnail_path(asset_id)
        if thumb is None or not thumb.exists():
            return
        from tumblepipe.pipe.houdini import network_thumbnail
        editor = (
            drop.pane
            if isinstance(drop.pane, hou.NetworkEditor)
            else None
        )
        network_thumbnail.attach(raw_node, thumb, editor=editor)

    # ── Per-context handlers ─────────────────────────────

    def _drop_asset_as_import_model(self, detail, drop, entity_uri: str) -> bool:
        """Drop an asset into a SOP network as a ``th::import_model`` HDA.

        Sets the HDA's ``entity`` parm to the asset URI, defaults the
        ``department`` parm based on tags (``blendshape`` if the asset
        is tagged as such; ``model`` otherwise), then runs its Import.
        """
        import hou
        from tumblepipe.pipe.houdini.sops.import_model import ImportModel

        raw = drop.network.createNode(
            "th::import_model::1.0", detail.name.replace(" ", "_"),
        )
        raw.parm("entity").set(entity_uri)
        raw.parm("department").set(
            "blendshape" if "type:blendshape" in detail.tags else "model",
        )
        # The same Import the node's button runs: it mirrors the inner
        # import's labels and bypass reason onto the SOP.
        ImportModel(raw).execute()
        if drop.position is not None:
            raw.setPosition(drop.position - hou.Vector2(0.5, 0.0))
        else:
            raw.moveToGoodPosition()
        raw.setSelected(True, clear_all_selected=True)
        show_dropped_node(raw)
        self.attach_network_thumbnail(detail.id, raw, drop)

        hou.ui.setStatusMessage(
            f"Imported {detail.name}", severity=hou.severityType.Message,
        )
        return True

    def _drop_root_as_sublayer(self, detail, drop) -> bool:
        """Drop a Root card into a LOP network as a stock ``sublayer``
        node pointing at the Root's ``entity:/scenes/...`` URI.

        The asset resolver maps that URI to the latest exported scene
        USD at evaluation time, so the sublayer stays in sync with
        re-exports automatically.
        """
        import hou
        from tumblepipe.pipe.usd.sublayer_refs import generate_scene_sublayer_uri

        if drop.context != "lop" or not drop.network:
            hou.ui.setStatusMessage(
                "Roots can only be sublayered into LOP networks",
                severity=hou.severityType.Warning,
            )
            return True

        ref = containers.parse(detail.id)
        if not isinstance(ref, SceneContainer):
            raise ValueError(f"{detail.id} is not a Root")

        proj = self._registry.get(ref.project_name)
        if proj is not None:
            self._activate_project(proj)

        layer_uri = generate_scene_sublayer_uri(ref.uri)
        name = (detail.name or "root").replace(" ", "_")
        node = drop.network.createNode("sublayer", name)
        # ``num_files`` controls the multiparm count for layer paths;
        # defaults to 1 on a fresh node but set explicitly in case of
        # future schema changes.
        node.parm("num_files").set(1)
        node.parm("filepath1").set(layer_uri)
        if drop.position is not None:
            node.setPosition(drop.position - hou.Vector2(0.5, 0.0))
        else:
            node.moveToGoodPosition()
        node.setSelected(True, clear_all_selected=True)
        node.setDisplayFlag(True)

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
        or ``None`` when the singular node has no asset set yet (there is
        nothing to combine with).
        """
        from tumblepipe.pipe.houdini.lops import import_asset, import_assets

        # Read state from the singular node before we destroy it.
        old = import_asset.ImportAsset(target)
        old_uri = old.get_entity_uri()
        if old_uri is None:
            return None
        old_channel = old.get_channel_name()
        old_version = old.get_version_name()
        old_excl = old.get_exclude_department_names()
        old_layerbreak = old.get_include_layerbreak()
        old_import_mode = old.get_import_mode()

        old_position = target.position()
        old_color = target.color()
        old_name = target.name()
        input_conns = list(target.inputConnections())
        output_conns = list(target.outputConnections())
        network = target.parent()

        # Free the name so the plural node can take it cleanly; the old
        # node is destroyed below.
        target.setName(f"{old_name}__obsolete", unique_name=True)

        plural = import_assets.create(network, old_name)
        raw = plural.native()

        # Reset the default entry count so the two add_asset_entry calls
        # below produce indices 1 and 2 instead of stacking on top of an
        # empty pre-allocated entry from the HDA's default state.
        plural.parm('asset_imports').set(0)
        plural.set_exclude_department_names(old_excl)
        plural.set_include_layerbreak(old_layerbreak)
        plural.set_import_mode(old_import_mode)

        plural.add_asset_entry(
            old_uri, channel=old_channel, version=old_version, instances=1,
        )
        plural.add_asset_entry(new_uri)

        # Re-wire: inputs going into target now go into the new raw,
        # outputs consuming target now consume the new raw.
        for ic in input_conns:
            raw.setInput(ic.inputIndex(), ic.inputNode(), ic.outputIndex())
        for oc in output_conns:
            # outputNode() is the consumer. (This read inputNode(), the
            # old node itself, and a swallow hid that no output was ever
            # rewired.)
            oc.outputNode().setInput(oc.inputIndex(), raw, oc.outputIndex())

        # Destroy the old singular AFTER wiring so we don't disconnect
        # anything by accident.
        target.destroy()

        # Position + color/style the new node where the old one was.
        raw.setPosition(old_position)
        raw.setColor(old_color)

        plural.execute()
        return raw
