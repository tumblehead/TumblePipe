import logging
from pathlib import Path
import datetime as dt
import shutil
import json

import hou

logger = logging.getLogger(__name__)

from tumblepipe.api import get_user_name, path_str, api, project_temp_dir
from tumblepipe.util.errors import TaskSkipped
from tumblepipe.util.progress import report_progress
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import CHANNEL_KEY, list_channels
from tumblepipe.config.timeline import require_fps
from tumblepipe.pipe import export_targets
from tumblepipe.pipe.houdini import prim_metadata, prim_queries
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.farm_node import FarmNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode
from tumblepipe.pipe.houdini.lops.layer_cook import LayerWriter
from tumblepipe.pipe.houdini.time_offset import TimeOffsetNode
from tumblepipe.pipe.usd.retime import shift_directory
from tumblepipe.pipe.usd.stitch import rename_layer_file
from tumblepipe.pipe.houdini.export_checks import (
    ExportLayerError,
    check_no_dropped_assets,
    check_no_root_asset_imports,
    cook_errors,
    list_expected_asset_uris,
    make_portable,
)
from tumblepipe.pipe.paths import (
    SHARED_CHANNEL,
    latest_export_path,
    next_export_path,
    get_layer_file_name,
    latest_complete_hip_file_path,
)
from tumblepipe.pipe.usd.sublayer_refs import add_sublayer
from tumblepipe.pipe.context import (
    announce_change, save_layer_context, sourced_from_root,
)
from tumblepipe.util import events
from tumblepipe.util.io import copy_version_contents
from tumblepipe.pipe.provenance import scene_producer
from tumblepipe.pipe.build import sourced_from_shot, arrived_from_downstream
from tumblepipe.config.department import list_departments
from tumblepipe.pipe.houdini.nodes import button


def _downstream_shot_departments(department_name: str) -> list[str]:
    """Shot departments after ``department_name`` in the pool."""
    names = [d.name for d in list_departments('shots')]
    if department_name not in names:
        return []
    return names[names.index(department_name) + 1:]


def _scrape_assets(root, entity_uri, department_name, version_name, expected_asset_uris):
    """The assets the export tracks, grouped by URI, and their inputs.

    Returns ``({asset uri: [instance info]}, {json-encoded input})``. An
    asset workfile does not track the assets of a shot layer it imported for
    context; a shot export does not track an asset a downstream department
    layer brought in, unless this workfile imports it too. A shot export
    records itself in each tracked asset's inputs. Neither tracks an asset
    of a shot's Root: those are the Root's.
    """
    is_shot_export = entity_uri.segments[0] == 'shots'
    downstream_departments = (
        _downstream_shot_departments(department_name) if is_shot_export else []
    )
    assets_by_uri = dict()
    asset_inputs = set()
    for asset_info in prim_metadata.list_assets(root):
        prim_path = asset_info['prim_path']
        asset_metadata = asset_info['metadata']
        asset_uri_str = asset_metadata['uri']

        if sourced_from_root(asset_metadata.get('inputs')):
            logger.info(
                "Not tracking %s at %s: it is the shot's Root's",
                asset_uri_str, prim_path,
            )
            continue
        if not is_shot_export and sourced_from_shot(asset_metadata.get('inputs')):
            logger.info(
                "Not tracking %s at %s: it came in through a shot layer",
                asset_uri_str, prim_path,
            )
            continue
        if (
            is_shot_export
            and asset_uri_str not in expected_asset_uris
            and arrived_from_downstream(
                asset_metadata.get('inputs'), downstream_departments
            )
        ):
            logger.info(
                "Not tracking %s at %s: it came in through a downstream "
                "department layer",
                asset_uri_str, prim_path,
            )
            continue

        if is_shot_export:
            shot_dept_entry = {
                'uri': str(entity_uri),
                'department': department_name,
                'version': version_name
            }
            existing_inputs = asset_metadata.get('inputs', [])
            if shot_dept_entry not in existing_inputs:
                asset_metadata['inputs'] = existing_inputs + [shot_dept_entry]

        assets_by_uri.setdefault(asset_uri_str, []).append({
            'prim_path': prim_path,
            'instance': asset_metadata['instance'],
            CHANNEL_KEY: asset_metadata[CHANNEL_KEY],
            'inputs': asset_metadata.get('inputs', [])
        })
        asset_inputs.update(set(map(json.dumps, asset_metadata['inputs'])))
    return assets_by_uri, asset_inputs


def _parameter_assets(assets_by_uri) -> list[dict]:
    """The context.json 'assets' entries: one per asset, with its count.

    'instance' names the asset's BASE prim, so it comes from the URI. The
    first scraped instance is whichever copy the scrape walked first — and
    since the prototype is deactivated, list_assets skips it and the first
    LIVE copy wins, having tagged itself with its own name (Haybale9, not
    Haybale). Consumers read this as the base name and regenerate
    <base>0..<base>N-1 from it, so an arbitrary copy's name spawned a
    phantom set at the origin.
    """
    return [
        dict(
            asset=asset_uri_str,
            instance=Uri.parse(asset_uri_str).segments[-1],
            instances=len(instances),
            channel=instances[0][CHANNEL_KEY],
            inputs=instances[0]['inputs'],
        )
        for asset_uri_str, instances in assets_by_uri.items()
    ]


def _add_shared_sublayer(layer_path: Path, entity_uri, department_name: str):
    """A multi-channel entity's channel layer composes the department's
    channel-independent part (layer_split's shared export) when there is
    one. Added before the copy, so the version is never published without
    it."""
    if len(list_channels(entity_uri)) <= 1:
        return
    if latest_export_path(entity_uri, SHARED_CHANNEL, department_name) is None:
        return
    shared_uri = f"{entity_uri}?dept={department_name}&channel={SHARED_CHANNEL}"
    add_sublayer(layer_path, shared_uri)
    logger.info(f"Added shared layer sublayer: {shared_uri}")


def _retype_asset_structure(layer_path: Path, entity_uri):
    """Give the asset prim and its geo/blshp/mtl child the types the
    structure validator expects, where it only warned (no transform)."""
    from pxr import Sdf
    from tumblepipe.pipe.houdini.validators.asset_structure import (
        retype_asset_structure,
    )
    layer = Sdf.Layer.FindOrOpen(path_str(layer_path))
    if layer is None:
        raise ExportLayerError(f"Could not open the exported layer {layer_path}")
    changes = retype_asset_structure(
        layer, prim_queries.uri_to_prim_path(entity_uri)
    )
    if not changes:
        return
    if not layer.Save():
        raise ExportLayerError(f"Could not save the exported layer {layer_path}")
    logger.info("Retyped in the published layer:\n" + "\n".join(changes))


def _stamp_entity(layer_path: Path, entity_uri, department_name, version_name):
    """Stamp the asset's root prim, in the published layer, with the entity
    and the department and version exporting it (``entity_stamp``), so a
    stage can say which asset a prim belongs to. A shot export owns no prim
    and is left as it is."""
    from tumblepipe.pipe.usd.entity_stamp import stamp_exported_layer
    try:
        stamped = stamp_exported_layer(
            layer_path, entity_uri, department_name, version_name
        )
    except RuntimeError as error:
        raise ExportLayerError(str(error)) from error
    if stamped:
        logger.info(
            "Stamped %s as %s (%s %s)",
            prim_queries.uri_to_prim_path(entity_uri), entity_uri,
            department_name, version_name,
        )


def _embed_apex_files(layer_path: Path):
    """Copy the APEX characters' scene files (rig, skeleton, animation) into
    the version, or raise. The USD ROP saves them to each APEX LOP's save
    path, ``$HIP/usd`` by default: the work area, which the next export
    overwrites (``apex_files``). This is what lets a rig workfile publish a
    LOP rig, and an APEX Animate shot its animation, as a layer."""
    from tumblepipe.pipe.usd.apex_files import APEX_DIR, embed_apex_files

    report_progress("copying APEX scene files")
    result = embed_apex_files(layer_path)
    problems = []
    if result.unsaved:
        problems.append(
            "APEX scene geometry the export did not write (still op: "
            "paths):\n  - " + "\n  - ".join(result.unsaved)
            + "\nTurn on the APEX LOP's Set Scene/Rig/Animation Save Path, "
            "or turn it off so the export writes the files beside the layer."
        )
    if result.missing:
        problems.append(
            "APEX scene files the layer points at that do not exist:\n  - "
            + "\n  - ".join(result.missing)
        )
    if problems:
        raise ExportLayerError(
            "Export aborted: the APEX characters would publish without "
            "their rigs.\n" + "\n".join(problems)
        )
    if result.rewritten:
        logger.info(
            "Copied %d APEX scene file(s) into %s/",
            len(set(result.rewritten.values())), APEX_DIR,
        )


def _copy_to_version(temp_path: Path, version_path: Path):
    """Copy the exported folder's contents into the version folder.

    ``context.json`` goes last (``copy_version_contents``): it is what marks
    the version finished. The copy reports its progress ("copying v0011 to
    server: 1.2 / 3.0 GB, 45 MB/s") itself.
    """
    copy_version_contents(
        temp_path, version_path, label=f"copying {version_path.name} to server",
    )


class ExportLayer(FrameRangeNode, TimeOffsetNode, FarmNode, EntityNode, LayerWriter):

    POOL_PARM = 'export_pool'
    PRIORITY_PARM = 'export_priority'
    BATCH_SIZE_PARM = 'batch_size'
    EXPORTS_ENTITY_RANGE = True

    def list_department_names(self) -> list[str]:
        return self.department_choices(include_generated=False, publishable=True)

    def list_downstream_department_names(self) -> list[str]:
        context = self.department_context()
        if context is None:
            return []
        departments = self.scoped_departments(context)
        if len(departments) == 0:
            return []

        department_names = [dept.name for dept in departments]
        department_name = self.get_department_name()
        if department_name is None:
            return []
        if department_name not in department_names:
            return []

        department_index = department_names.index(department_name)
        return department_names[department_index + 1:]

    def get_downstream_department_names(self) -> list[str]:
        department_names = self.list_downstream_department_names()
        if len(department_names) == 0:
            return []
        selected = list(filter(len, self.parm('export_departments').eval().split(' ')))
        # A stored selection can name a department that is no longer
        # downstream — the pool was reordered, the department retired, or the
        # entity scoped away from it. Drop those instead of raising out of
        # the sort key.
        selected = [name for name in selected if name in department_names]
        if len(selected) == 0:
            return []
        selected.sort(key=department_names.index)
        return selected

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        self.parm('entity_label').set(self._entity_label())
        self.parm('department_label').set(self._department_label())

    def _initialize(self):
        """Initialize node and update labels to show resolved values."""
        self._update_labels()

    def execute(self, force_local: bool = False, render_range=None):
        """
        Execute export.

        If force_local=True, exports here and now: the publish dialog's
        *In this session*, and the farm's publish worker. Otherwise, opens
        the ProcessDialog for task selection and execution.

        ``render_range`` (a ``BlockRange``, rolls included) exports those
        frames instead of the node's Frame Range: the publish window's
        First/Last changed for one submit (#76). The node's parms are not
        changed. None reads them, as always.
        """
        if force_local:
            return self._execute(render_range)
        # Open ProcessDialog
        from tumblepipe.pipe.houdini.ui.dialog_launcher import (
            open_process_dialog_for_node
        )
        open_process_dialog_for_node(self, dialog_title="Export Layer")

    def _execute(self, render_range=None):
        """The export itself, in this process.

        Always local, whatever the node's **Type** parm says. Where an
        export runs is chosen by whoever runs it: the publish dialog's mode
        (a farm mode calls ``_export_farm`` itself) or being the farm's
        publish worker. Following the parm, a workfile saved with Type =
        Farm (the setting that enables Downstream Exports) made *In this
        session* submit a farm job instead, and the farm's publish worker
        submit yet another publish instead of exporting.

        The config cache is refreshed once up front in
        open_process_dialog_for_node (the single entry point for every
        interactive export/publish flow), so entity-derived values resolved
        here are read from fresh on-disk config.
        """
        return self._export_local(render_range)

    # ── Targets (#91) ─────────────────────────────────────

    TARGETS_MODE_PARM = 'targets_mode'
    TARGETS_PARM = 'targets'

    def get_targets_mode(self) -> str:
        """The Targets menu; a node saved before it existed has the parm
        missing, which is its own entity alone."""
        parm = self.parm(self.TARGETS_MODE_PARM)
        return parm.eval() if parm is not None else export_targets.ENTITY

    def get_chosen_targets(self) -> list[Uri]:
        parm = self.parm(self.TARGETS_PARM)
        return export_targets.parse_chosen(parm.eval() if parm is not None else '')

    def set_chosen_targets(self, uris) -> None:
        self.parm(self.TARGETS_PARM).set(export_targets.format_chosen(uris))

    def get_target_uris(self) -> list[Uri]:
        """The entities the export writes to: the node's own entity unless
        the Targets menu says otherwise. Raises when the entity does not
        resolve."""
        from tumblepipe.config.groups import children_of
        return export_targets.resolve(
            self.get_targets_mode(),
            self.get_chosen_targets(),
            self.require_entity_uri(),
            lambda context: children_of(Uri.parse(f'entity:/{context}')),
        )

    def _export_local(self, render_range=None):
        native = self.native()

        # Get parameters (each raises when it does not resolve)
        entity_uri = self.require_entity_uri()
        targets = self.get_target_uris()
        if targets != [entity_uri]:
            return self._export_to_targets(targets, render_range)
        department_name = self.require_department_name()
        channel_name = self.get_channel_name()
        # A range set for this submit (#76) stands in for the node's own
        if render_range is None:
            render_range = self.get_frame_range().full_range()

        logger.info(
            f"Starting local export: uri={entity_uri}, dept={department_name}, "
            f"channel={channel_name}, frames={render_range.first_frame}-"
            f"{render_range.last_frame}"
        )

        time_offset = self.get_time_offset()
        user_name = get_user_name()
        timestamp = dt.datetime.now()
        version_path = next_export_path(entity_uri, channel_name, department_name)
        version_name = version_path.name

        _expected, assets_by_uri, asset_inputs = self._checked_stage(
            entity_uri, department_name, channel_name, version_name,
        )

        with project_temp_dir() as temp_path:
            layer_file_name = get_layer_file_name(
                entity_uri, channel_name, department_name, version_name
            )
            self.cook_layer(
                temp_path, layer_file_name, render_range,
                time_offset, self.get_batch_size(),
            )
            layout_keys = self._write_layout_keys(
                temp_path, layer_file_name, time_offset
            )
            self._finish_version(
                temp_path, layer_file_name, entity_uri, department_name,
                channel_name, version_path,
                timestamp=timestamp, user_name=user_name,
                asset_inputs=asset_inputs,
                parameters=dict(
                    assets=_parameter_assets(assets_by_uri),
                    aov_names=self._aov_names(channel_name),
                    # Provenance only: the layer is on the shot's frames.
                    **({'time_offset': time_offset} if time_offset else {}),
                    **({'layout_keys': layout_keys} if layout_keys else {}),
                ),
            )
        announce_change(events.EXPORT, entity_uri, department_name)

        ns.set_node_comment(
            native,
            f'last export: {version_name}\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}'
        )
        logger.info(
            f"Export completed: uri={entity_uri}, dept={department_name}, "
            f"version={version_name}, output={version_path}"
        )
        return version_name

    def _export_to_targets(self, targets: list[Uri], render_range=None):
        """Export the layer once and write a version of it to each of
        ``targets`` (#91): the Targets menu set to Chosen, All shots or
        All assets.

        The stage is cooked once, in this scene, over this node's frames
        (the test shot's). Each target then gets a copy of it, shifted so
        the node's first frame lands on the target's first frame, and goes
        through the same per-entity steps as a single export: its own layer
        name and version, the entity stamp, the shared sublayer, its own
        context.json. Overrides on prims a target lacks are written as they
        are; USD ignores them there. Layout keys are not written: they
        belong to one shot's APEX Animate.
        """
        from tumblepipe.config.timeline import get_fps, get_export_frame_range

        native = self.native()
        source_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        channel_name = self.get_channel_name()
        refused = export_targets.problems(
            source_uri, targets,
            exists=lambda uri: api.config.get_properties(uri) is not None,
            fps_of=get_fps,
        )
        if refused:
            raise ExportLayerError(
                f'{self.path()}: cannot export to these targets.\n'
                + '\n'.join(refused)
            )
        node_range = self.get_frame_range()
        if render_range is None:
            render_range = node_range.full_range()
        logger.info(
            f"Starting export to {len(targets)} targets: source={source_uri}, "
            f"dept={department_name}, channel={channel_name}, "
            f"frames={render_range.first_frame}-{render_range.last_frame}"
        )
        time_offset = self.get_time_offset()
        user_name = get_user_name()
        timestamp = dt.datetime.now()

        # The stage is the source's: check it once, as a single export does.
        expected_asset_uris, _scraped, _inputs = self._checked_stage(
            source_uri, department_name, channel_name, '',
        )

        written = []
        with project_temp_dir() as cook_path:
            cook_name = 'layer.usd'
            self.cook_layer(
                cook_path, cook_name, render_range,
                time_offset, self.get_batch_size(),
            )
            # The stage may have changed while exporting
            root = self._input_stage(channel_name).GetPseudoRoot()
            aov_names = self._aov_names(channel_name)
            for index, target_uri in enumerate(targets, 1):
                report_progress(
                    f"writing {target_uri} ({index} of {len(targets)})"
                )
                version_path = next_export_path(
                    target_uri, channel_name, department_name
                )
                version_name = version_path.name
                # Each target records its own export in the assets' inputs.
                assets_by_uri, asset_inputs = _scrape_assets(
                    root, target_uri, department_name, version_name,
                    expected_asset_uris,
                )
                target_range = get_export_frame_range(target_uri)
                shift = export_targets.frame_shift(
                    node_range.start_frame,
                    None if target_range is None else target_range.start_frame,
                )
                with project_temp_dir() as temp_path:
                    shutil.copytree(cook_path, temp_path, dirs_exist_ok=True)
                    layer_file_name = get_layer_file_name(
                        target_uri, channel_name, department_name, version_name
                    )
                    # A clipped layer's clips and clip set take its name
                    rename_layer_file(temp_path, cook_name, layer_file_name)
                    if shift != 0:
                        shift_directory(temp_path, shift)
                    self._finish_version(
                        temp_path, layer_file_name, target_uri,
                        department_name, channel_name, version_path,
                        timestamp=timestamp, user_name=user_name,
                        asset_inputs=asset_inputs,
                        parameters=dict(
                            assets=_parameter_assets(assets_by_uri),
                            aov_names=aov_names,
                            # Provenance: where the layer was authored, and
                            # how far its frames moved to land on this one.
                            exported_from=str(source_uri),
                            **({'frame_shift': shift} if shift else {}),
                            **({'time_offset': time_offset} if time_offset else {}),
                        ),
                    )
                announce_change(events.EXPORT, target_uri, department_name)
                written.append(f'{target_uri} {version_name}')
                logger.info(
                    f"Exported {source_uri} to {target_uri}: dept="
                    f"{department_name}, version={version_name}, "
                    f"shift={shift}, output={version_path}"
                )

        ns.set_node_comment(
            native,
            f'last export: {len(written)} targets\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}'
        )
        return ', '.join(written)

    def _checked_stage(self, entity_uri: Uri, department_name: str, channel_name: str, version_name: str):
        """Check the input stage for an export of ``entity_uri`` and scrape
        its assets; set the export's fps. Returns ``(expected asset uris,
        assets by uri, asset inputs)``: the scrape records ``version_name``
        (blank when it only checks).

        The expected assets are what the upstream import nodes placed on the
        stage, read before the scrape: they decide which downstream-sourced
        assets stay, and the drop check cross-checks the scrape against
        them. A self-import (import_layer pulling the exporting entity's own
        layer) is deliberately untagged: never expected in the scrape."""
        root = self._input_stage(channel_name).GetPseudoRoot()
        expected_asset_uris = {
            str(uri) for uri in list_expected_asset_uris(self.native())
            if uri != entity_uri
        }
        check_no_root_asset_imports(entity_uri, expected_asset_uris)
        assets_by_uri, asset_inputs = _scrape_assets(
            root, entity_uri, department_name, version_name, expected_asset_uris
        )
        check_no_dropped_assets(
            root, entity_uri, expected_asset_uris, set(assets_by_uri)
        )
        self.parm('set_metadata_fps').set(require_fps(entity_uri))
        return expected_asset_uris, assets_by_uri, asset_inputs

    def _aov_names(self, channel_name) -> list[str]:
        """The render vars on the input stage, read again after the cook
        (the stage may have changed while exporting): context.json's
        ``aov_names``."""
        root = self._input_stage(channel_name).GetPseudoRoot()
        return [
            aov_path.rsplit('/', 1)[-1].lower()
            for aov_path in prim_queries.list_render_vars(root)
        ]

    def _finish_version(
        self, temp_path: Path, layer_file_name: str, entity_uri: Uri,
        department_name: str, channel_name: str, version_path: Path, *,
        timestamp, user_name: str, asset_inputs, parameters: dict,
    ):
        """Make the exported folder ``temp_path`` a finished version of
        ``entity_uri`` at ``version_path``: the steps every export runs on
        its layer, a single export's and each target's alike.

        Embeds the APEX scene files, gives an asset's structure its types,
        stamps the entity, makes the paths portable, adds the shared
        sublayer, writes context.json with ``parameters``, and copies it
        all to the version (context.json last, which marks it finished).
        """
        layer_path = temp_path / layer_file_name
        version_name = version_path.name
        _embed_apex_files(layer_path)
        if entity_uri.segments[0] == 'assets':
            _retype_asset_structure(layer_path, entity_uri)
        _stamp_entity(layer_path, entity_uri, department_name, version_name)
        make_portable(layer_path)
        _add_shared_sublayer(layer_path, entity_uri, department_name)
        save_layer_context(
            target_path=temp_path,
            entity_uri=entity_uri,
            department_name=department_name,
            version_name=version_name,
            timestamp=timestamp.isoformat(),
            user_name=user_name,
            channel_name=channel_name,
            parameters=parameters,
            inputs=list(map(json.loads, asset_inputs)),
            producer=scene_producer(self.native().path())
        )
        _copy_to_version(temp_path, version_path)

    def _write_layout_keys(self,temp_path, layer_file_name, time_offset):
        """With Layout in Animate on and an APEX Animate LOP feeding this
        node, write the layout keys (an Animation Catalog, one clip per
        character and the camera) beside the layer; the context.json entry
        for them, or None (setting off, no APEX Animate, no keys)."""
        from tumblepipe.config import layout_animate
        from tumblepipe.pipe.houdini import layout_keys

        if not layout_animate.enabled():
            return None
        animate = layout_keys.find_animate_lop(self.native())
        if animate is None:
            return None
        report_progress("writing the layout keys")
        try:
            return layout_keys.write_layout_keys(
                animate, temp_path, layer_file_name, time_offset
            )
        except Exception as error:
            raise ExportLayerError(
                f"Could not write the layout keys of {animate.path()}: {error}"
            ) from error

    def _input_stage(self, channel_name):
        """The stage fed into the node; a disconnected node skips, a
        connected one whose input fails to cook raises."""
        native = self.native()
        stage_node = native.node('IN_stage')
        stage = stage_node.stage()
        if stage is not None:
            return stage
        if not any(native.inputs()):
            # A node left disconnected in the network. There is nothing to
            # publish, so skip this one and let the other exports in the
            # group run.
            raise TaskSkipped(
                f"Nothing exported for channel '{channel_name}': the node "
                f"{self.path()} has no stage input connected."
            )
        # Connected, but what feeds it failed to cook. That is a failure, not
        # a skip, and saying "no stage input connected" sent people looking
        # at the wiring (a farm job whose rig could not compile).
        raise ExportLayerError(
            f"Nothing exported for channel '{channel_name}': the input of "
            f"{self.path()} failed to cook.\n"
            + cook_errors(stage_node)
        )

    def _own_workfile(self) -> tuple[Path, str]:
        """The workfile this node lives in, as last saved, and its department:
        what a farm export bundles.

        An export node publishes the workfile it lives in, whichever
        department it exports into: a lookdev export built in the model
        workfile publishes the model workfile's lookdev. This used to bundle
        the *target* department's workfile, so the farm republished
        lookdev's own (stale, or missing) workfile instead, and crashed on
        ``None.exists()`` when there was none. The newest finished save: one
        still being written is passed over (the publish dialog saves the
        scene before it submits).
        """
        context = self._workfile_context()
        if context is None:
            raise ExportLayerError(
                f'{self.path()}: the open scene ({hou.hipFile.path()}) is not '
                'a pipeline workfile, so there is no saved workfile to send '
                'to the farm. Save it as a workfile first.'
            )
        workfile_path = latest_complete_hip_file_path(
            context.entity_uri, context.department_name
        )
        if workfile_path is None or not workfile_path.exists():
            raise ExportLayerError(
                f'No saved workfile found for {context.entity_uri} '
                f'{context.department_name} to send to the farm.'
            )
        return workfile_path, context.department_name

    def _export_farm(self, build: dict | None = None, render_range=None):
        """Submit the publish of this node's department to the farm.

        ``build`` is the publish dialog's Build USD task riding along
        (``specs.publish`` tasks.build): its builds chain after the publish
        job in the same batch, and the worker queues none of its own.

        ``render_range`` is a range set in the publish window for this
        submit only (#76): the job carries it (tasks.publish.frame_range)
        and the worker exports those frames; the node is not changed.
        """
        # Each getter raises when its parm does not resolve
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        channel_name = self.get_channel_name()
        job_range = (
            render_range if render_range is not None
            else self.get_frame_range().full_range()
        )
        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()

        workfile_path, workfile_department = self._own_workfile()
        from tumblepipe.farm.pipelines.publish import bundle_workfile
        paths = {}
        workfile_dest = bundle_workfile(workfile_path, workfile_department, paths)

        config = {
            'entity': {
                'uri': str(entity_uri),
                'department': department_name,
                'channel': channel_name
            },
            'settings': {
                'priority': priority,
                'pool_name': pool_name,
                'first_frame': job_range.first_frame,
                'last_frame': job_range.last_frame
            },
            'tasks': {
                'publish': {
                    'downstream_departments': downstream_deps
                }
            },
            'workfile_path': path_str(workfile_dest)
        }
        if render_range is not None:
            config['tasks']['publish']['frame_range'] = {
                'first_frame': render_range.first_frame,
                'last_frame': render_range.last_frame,
            }
        if build is not None:
            config['tasks']['build'] = build

        # A failed submit raises, so the publish dialog marks the task failed
        # (with the error in its report). It used to show the error and
        # return, and the task read as completed with nothing submitted.
        from tumblepipe.farm.pipelines import publish as publish_job
        try:
            publish_job.submit(config, paths)
        except Exception as e:
            raise ExportLayerError(f'Failed to submit farm job: {e}') from e

        # Where it went: the publish dialog submits under its Run on choice
        # (farm.backend.using).
        from tumblepipe.farm import backend as farm_backend
        backend = farm_backend.selected()
        where = 'this machine' if backend == farm_backend.LOCAL else 'farm'
        native = self.native()
        timestamp = dt.datetime.now()
        user_name = get_user_name()
        native.setComment(
            f'{where} export submitted:\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}\n'
            f'downstream: {", ".join(downstream_deps) if downstream_deps else "None"}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)
        # No dialog per export: the publish dialog that runs this says where
        # the run's jobs went and opens the Farm Manager. One modal per
        # channel used to stop the run until each was dismissed.

    def open_location(self):
        """Open the latest export's folder in the OS file browser."""
        entity_uri = self.require_entity_uri()
        channel_name = self.get_channel_name()
        department_name = self.require_department_name()
        export_path = latest_export_path(entity_uri, channel_name, department_name)
        if export_path is None or not export_path.exists():
            raise self._error(
                f"{entity_uri} has no '{department_name}' export of channel "
                f"'{channel_name}' yet"
            )
        hou.ui.showInFileBrowser(path_str(export_path))


def create(scene, name):
    return ns.create_node(scene, name, ExportLayer, 'export_layer')


def on_created(raw_node):
    # Set node style
    ns.set_node_style(raw_node, ns.SHAPE_NODE_EXPORT)

    node = ExportLayer(raw_node)
    node._initialize()


@button('Export Layer')
def execute(kwargs):
    raw_node = kwargs['node']
    node = ExportLayer(raw_node)
    node.execute()


@button('Export Layer')
def open_location(kwargs):
    raw_node = kwargs['node']
    node = ExportLayer(raw_node)
    node.open_location()


@button('Export Layer')
def select(kwargs):
    """Entity button."""
    ExportLayer(kwargs['node']).pick_entity()


@button('Export Layer')
def select_targets(kwargs):
    """The Targets Browse... button: pick the shots or assets to export to
    (Targets: Chosen), several at once. The picker opens on the node's own
    kind of entity and starts from the current list."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    node = ExportLayer(kwargs['node'])
    source = node.get_entity_uri()
    kind = source.segments[0] if source is not None and source.segments else 'both'
    dialog = EntitySelectorDialog(
        api=api,
        entity_filter=kind if kind in ('shots', 'assets') else 'both',
        include_from_context=False,
        title="Export To",
        parent=hou.qt.mainWindow(),
        multi_select=True,
    )
    if not dialog.exec_():
        return
    picked = [Uri.parse(raw) for raw in dialog.get_selected_uris()]
    with hou.undos.group(f'Pick targets of {node.path()}'):
        node.set_chosen_targets(picked)
        node.parm(node.TARGETS_MODE_PARM).set(export_targets.CHOSEN)


@button('Validate')
def validate(kwargs):
    """The Validate button."""
    from tumblepipe.pipe.houdini.validators import show_validation
    show_validation(ExportLayer(kwargs['node']), kwargs['node'])


def output_modified_prims(raw_node) -> str:
    """Return the prim path this HDA wrote, for the output's modifiedprims.

    Resolve through the wrapper: the parm's default is the 'from_context'
    sentinel, which is not a URI, so parsing it raw reported no modified
    prims for a node in its default state.
    """
    entity_uri = ExportLayer(raw_node).get_entity_uri()
    if entity_uri is None:
        return ''
    try:
        return prim_queries.uri_to_prim_path(entity_uri)
    except ValueError:
        return ''
