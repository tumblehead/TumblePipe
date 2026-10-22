import logging
from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import shutil
import json

import hou

logger = logging.getLogger(__name__)

from tumblepipe.api import get_user_name, path_str, api
from tumblepipe.util.errors import TaskSkipped
from tumblepipe.util.progress import report_progress
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import CHANNEL_KEY, list_channels
from tumblepipe.config.timeline import get_fps
from tumblepipe.pipe.houdini import prim_metadata, prim_queries
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.farm_node import FarmNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode
from tumblepipe.pipe.houdini.export_checks import (
    ExportLayerError,
    check_no_dropped_assets,
    cook_errors,
    list_expected_asset_uris,
    make_portable,
    validate_export_files,
)
from tumblepipe.pipe.paths import (
    SHARED_CHANNEL,
    latest_export_path,
    next_export_path,
    get_layer_file_name,
    latest_hip_file_path
)
from tumblepipe.pipe.usd.sublayer_refs import add_sublayer
from tumblepipe.pipe.context import save_layer_context
from tumblepipe.util.io import copy_version_contents
from tumblepipe.pipe.provenance import scene_producer
from tumblepipe.pipe.build import sourced_from_shot, arrived_from_downstream
from tumblepipe.config.department import list_departments
from tumblepipe.pipe.usd.stitch import (
    stitch_usd_directories,
    calculate_chunks,
    flatten_sidecar_directories,
)


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
    records itself in each tracked asset's inputs.
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


def _copy_to_version(temp_path: Path, version_path: Path):
    """Copy the exported folder's contents into the version folder.

    ``context.json`` goes last (``copy_version_contents``): it is what marks
    the version finished.
    """
    report_progress(f"copying {version_path.name} to server")
    copy_version_contents(temp_path, version_path)


class ExportLayer(FrameRangeNode, FarmNode, EntityNode):

    POOL_PARM = 'export_pool'
    PRIORITY_PARM = 'export_priority'
    BATCH_SIZE_PARM = 'batch_size'

    def list_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return ['from_context']

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        # Exclude generated departments and filter to publishable only.
        # scoped_departments narrows the pool to the departments the target
        # entity actually has.
        names = [
            d.name for d in
            self.scoped_departments(context_name, include_generated=False)
            if d.publishable
        ]
        return ['from_context'] + names

    def list_downstream_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return []

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        departments = self.scoped_departments(context_name)
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

    def get_export_type(self) -> str:
        """Get export type ('local' or 'farm')."""
        return self.parm('export_type').eval()

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        self.parm('entity_label').set(self._entity_label())
        self.parm('department_label').set(self._department_label())

    def _initialize(self):
        """Initialize node and update labels to show resolved values."""
        self._update_labels()

    def execute(self, force_local: bool = False):
        """
        Execute export.

        If force_local=True, executes directly (used by ProcessDialog callbacks).
        Otherwise, opens the ProcessDialog for task selection and execution.
        """
        if force_local:
            return self._execute()
        # Open ProcessDialog
        from tumblepipe.pipe.houdini.ui.dialog_launcher import (
            open_process_dialog_for_node
        )
        open_process_dialog_for_node(self, dialog_title="Export Layer")

    def _execute(self):
        """Internal execution - called by ProcessDialog callbacks.

        The config cache is refreshed once up front in
        open_process_dialog_for_node (the single entry point for every
        interactive export/publish flow), so entity-derived values resolved
        here are read from fresh on-disk config.
        """
        export_type = self.get_export_type()
        match export_type:
            case 'local':
                return self._export_local()
            case 'farm':
                return self._export_farm()
            case _:
                assert False, f'Unknown export type: {export_type}'

    def _export_local(self):
        native = self.native()

        # Get parameters (each raises when it does not resolve)
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        channel_name = self.get_channel_name()
        frame_range = self.get_frame_range()

        logger.info(
            f"Starting local export: uri={entity_uri}, dept={department_name}, "
            f"channel={channel_name}"
        )

        # The render step is deliberately dropped: a published stage cache
        # must carry every frame so downstream renders can sample any frame
        # (including a later switch to 1s). Sub-sampling the export on the
        # render step also broke batched export - integer chunk boundaries
        # do not align to a step>1 grid, so the stitched samples came out
        # off-grid whenever batch_size was not a multiple of step. Exporting
        # on 1s (export_f3) matches the farm exporter, which has always
        # discarded step (see the farm submit path and export_houdini).
        render_range = frame_range.full_range()
        user_name = get_user_name()
        timestamp = dt.datetime.now()
        version_path = next_export_path(entity_uri, channel_name, department_name)
        version_name = version_path.name

        root = self._input_stage(channel_name).GetPseudoRoot()

        # What the upstream import nodes placed on the stage. Read before the
        # scrape: it decides which downstream-sourced assets stay, and the
        # drop check cross-checks the scrape against it. A self-import
        # (import_layer pulling the exporting entity's own layer) is
        # deliberately untagged — never expect it in the scrape.
        expected_asset_uris = {
            str(uri) for uri in list_expected_asset_uris(native)
            if uri != entity_uri
        }
        assets_by_uri, asset_inputs = _scrape_assets(
            root, entity_uri, department_name, version_name, expected_asset_uris
        )
        check_no_dropped_assets(
            root, entity_uri, expected_asset_uris, set(assets_by_uri)
        )

        self.parm('set_metadata_fps').set(get_fps())

        root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)
            layer_file_name = get_layer_file_name(
                entity_uri, channel_name, department_name, version_name
            )
            self._write_layer(
                temp_path, layer_file_name, render_range, self.get_batch_size()
            )
            layer_path = temp_path / layer_file_name
            make_portable(layer_path)
            _add_shared_sublayer(layer_path, entity_uri, department_name)

            # The stage may have changed while exporting
            root = self._input_stage(channel_name).GetPseudoRoot()
            aov_names = [
                aov_path.rsplit('/', 1)[-1].lower()
                for aov_path in prim_queries.list_render_vars(root)
            ]
            save_layer_context(
                target_path=temp_path,
                entity_uri=entity_uri,
                department_name=department_name,
                version_name=version_name,
                timestamp=timestamp.isoformat(),
                user_name=user_name,
                channel_name=channel_name,
                parameters=dict(
                    assets=_parameter_assets(assets_by_uri),
                    aov_names=aov_names,
                ),
                inputs=list(map(json.loads, asset_inputs)),
                producer=scene_producer(native.path())
            )
            _copy_to_version(temp_path, version_path)

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

    def _write_layer(self, temp_path, layer_file_name, render_range, batch_size):
        """Export the stage into ``temp_path/layer_file_name``: in one go, or
        (batch_size > 0) in chunks of frames stitched together."""
        self.parm('export_f3').set(1)
        if batch_size == 0:
            report_progress(
                f"cooking frames {render_range.first_frame}-{render_range.last_frame}"
            )
            self._export_frames(
                temp_path, layer_file_name,
                render_range.first_frame, render_range.last_frame,
                f"standard export (frames {render_range.first_frame}-"
                f"{render_range.last_frame})",
            )
            return

        chunks_dir = temp_path / 'chunks'
        chunks_dir.mkdir()
        chunk_dirs = []
        for chunk_start, chunk_end in calculate_chunks(
            render_range.first_frame, render_range.last_frame, batch_size
        ):
            chunk_name = f"{chunk_start:04d}-{chunk_end:04d}"
            report_progress(
                f"cooking frames {chunk_start}-{chunk_end} of {render_range.last_frame}"
            )
            chunk_dir = chunks_dir / chunk_name
            chunk_dir.mkdir()
            self._export_frames(
                chunk_dir, layer_file_name, chunk_start, chunk_end,
                f"batch export chunk {chunk_name}",
            )
            chunk_dirs.append(chunk_dir)

        report_progress(f"stitching {len(chunk_dirs)} chunks")
        try:
            stitch_usd_directories(chunk_dirs, layer_file_name, temp_path)
        except Exception as error:
            raise ExportLayerError(
                f"Failed to stitch {len(chunk_dirs)} USD chunks into "
                f"{temp_path / layer_file_name}: {error}"
            ) from error
        shutil.rmtree(chunks_dir)

    def _export_frames(self, out_dir, layer_file_name, first_frame, last_frame, description):
        """Run the export ROP for ``first_frame``..``last_frame`` into
        ``out_dir/layer_file_name``, flattening its sidecar folders."""
        # 'stage.usd' as the ROP's file name makes its sidecar folder 'stage/'
        temp_export_name = 'stage.usd'
        self.parm('export_f1').deleteAllKeyframes()
        self.parm('export_f2').deleteAllKeyframes()
        self.parm('export_f1').set(first_frame)
        self.parm('export_f2').set(last_frame)
        self.parm('export_lopoutput').set(path_str(out_dir / temp_export_name))
        self.parm('export_execute').pressButton()
        validate_export_files(out_dir, temp_export_name, description)
        (out_dir / temp_export_name).rename(out_dir / layer_file_name)
        flatten_sidecar_directories(out_dir)

    def _export_farm(self):
        # Each getter raises when its parm does not resolve
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        channel_name = self.get_channel_name()
        frame_range = self.get_frame_range()
        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()

        # Get workfile path for bundling
        workfile_path = latest_hip_file_path(entity_uri, department_name)
        if not workfile_path.exists():
            raise ExportLayerError(f"No workfile found for {entity_uri} {department_name}")
        from tumblepipe.farm.pipelines.publish import bundle_workfile
        paths = {}
        workfile_dest = bundle_workfile(workfile_path, department_name, paths)

        config = {
            'entity': {
                'uri': str(entity_uri),
                'department': department_name,
                'channel': channel_name
            },
            'settings': {
                'priority': priority,
                'pool_name': pool_name,
                'first_frame': frame_range.full_range().first_frame,
                'last_frame': frame_range.full_range().last_frame
            },
            'tasks': {
                'publish': {
                    'downstream_departments': downstream_deps
                }
            },
            'workfile_path': path_str(workfile_dest)
        }

        from tumblepipe.farm.pipelines import publish as publish_job
        try:
            publish_job.submit(config, paths)
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to submit farm job: {str(e)}",
                severity=hou.severityType.Error
            )
            return

        native = self.native()
        timestamp = dt.datetime.now()
        user_name = get_user_name()
        native.setComment(
            f'farm export submitted:\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}\n'
            f'downstream: {", ".join(downstream_deps) if downstream_deps else "None"}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

        downstream_msg = f"\nDownstream: {', '.join(downstream_deps)}" if downstream_deps else ""
        hou.ui.displayMessage(
            f"Export job submitted to farm\n"
            f"Department: {department_name}"
            f"{downstream_msg}",
            title="Farm Export Submitted"
        )
    
    def open_location(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            hou.ui.displayMessage("No entity selected.", severity=hou.severityType.Warning)
            return
        channel_name = self.get_channel_name()
        department_name = self.get_department_name()
        if department_name is None:
            hou.ui.displayMessage("No department selected.", severity=hou.severityType.Warning)
            return

        export_path = latest_export_path(entity_uri, channel_name, department_name)
        if export_path is None:
            hou.ui.displayMessage(f"No exports found for {department_name}.", severity=hou.severityType.Warning)
            return
        if not export_path.exists():
            hou.ui.displayMessage(f"Export path does not exist: {export_path}", severity=hou.severityType.Warning)
            return
        hou.ui.showInFileBrowser(path_str(export_path))


def create(scene, name):
    return ns.create_node(scene, name, ExportLayer, 'export_layer')


def on_created(raw_node):
    # Set node style
    ns.set_node_style(raw_node, ns.SHAPE_NODE_EXPORT)

    node = ExportLayer(raw_node)
    node._initialize()


def execute(kwargs):
    raw_node = kwargs['node']
    node = ExportLayer(raw_node)
    node.execute()


def open_location(kwargs):
    raw_node = kwargs['node']
    node = ExportLayer(raw_node)
    node.open_location()


def select(kwargs):
    """Entity button."""
    ExportLayer(kwargs['node']).pick_entity()


def validate(kwargs):
    """HDA button callback to run stage validation."""
    from tumblepipe.pipe.houdini.validators import validate_stage_for_department

    raw_node = kwargs['node']
    stage_node = raw_node.node('IN_stage')

    if stage_node is None:
        hou.ui.displayMessage(
            "No stage input connected.",
            severity=hou.severityType.Warning
        )
        return

    stage = stage_node.stage()
    if stage is None:
        hou.ui.displayMessage(
            "No stage available for validation.",
            severity=hou.severityType.Warning
        )
        return

    node = ExportLayer(raw_node)
    entity_uri = node.get_entity_uri()
    department = node.get_department_name()

    if entity_uri is None or department is None:
        hou.ui.displayMessage(
            "Entity or department not set on this export node - cannot pick validators.",
            severity=hou.severityType.Warning
        )
        return

    uri_str = str(entity_uri)
    entity_context = 'shots' if uri_str.startswith('entity:/shots/') else 'assets'

    root = stage.GetPseudoRoot()
    result = validate_stage_for_department(
        root, entity_context, department, {'entity_uri': uri_str}
    )

    if result.passed:
        # A warning does not fail validation, but saying "no issues found"
        # while holding a non-empty issue list is a lie — and the most common
        # warning is "No stage available for validation", i.e. validation could
        # not run at all.
        if result.warnings:
            hou.ui.displayMessage(
                result.format_message(),
                severity=hou.severityType.Warning,
                title="Validation Passed With Warnings"
            )
        else:
            hou.ui.displayMessage(
                "Validation passed - no issues found.",
                severity=hou.severityType.Message,
                title="Validation Passed"
            )
        return

    from tumblepipe.pipe.houdini.ui.validation_dialog import (
        ValidationConfirmDialog,
    )
    entity_name = uri_str.rsplit('/', 1)[-1] if uri_str else 'unknown'
    dialog = ValidationConfirmDialog(
        validation_result=result,
        department=department,
        entity_name=entity_name,
        parent=hou.qt.mainWindow(),
        read_only=True,
    )
    dialog.exec_()


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
