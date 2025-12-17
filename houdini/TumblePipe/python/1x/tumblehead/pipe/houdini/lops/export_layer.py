from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import shutil
import json
import os

import hou

from tumblehead.api import get_user_name, path_str, fix_path, default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.variants import get_entity_type, list_variants
from tumblehead.config.timeline import FrameRange, get_frame_range, get_fps
from tumblehead.config.farm import list_pools
from tumblehead.pipe.houdini import util
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    latest_export_path,
    next_export_path,
    get_workfile_context,
    get_layer_file_name,
    latest_hip_file_path
)
from tumblehead.pipe.usd import add_sublayer
from tumblehead.pipe.context import save_layer_context

api = default_client()


class ExportLayerError(Exception):
    """Raised when export_layer encounters a validation or execution error."""
    pass


class ExportLayer(ns.Node):

    def __init__(self, native):
        super().__init__(native)

    def get_entity_type(self) -> str | None:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return None
        return get_entity_type(entity_uri)

    def list_entity_uris(self) -> list[str]:
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        shot_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )
        uris = [e.uri for e in asset_entities] + [e.uri for e in shot_entities]
        return ['from_context'] + [str(uri) for uri in uris]

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        return [entity.uri for entity in asset_entities]

    def list_shot_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )
        return [entity.uri for entity in shot_entities]

    def list_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return ['from_context']

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        # Exclude generated departments and filter to publishable only
        names = [
            d.name for d in list_departments(context_name, include_generated=False)
            if d.publishable
        ]
        return ['from_context'] + names

    def list_downstream_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return []

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        departments = list_departments(context_name)
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

    def list_variant_names(self) -> list[str]:
        """List available variant names for current entity.

        Returns variants from entity config, always includes 'default'.
        """
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['default']
        return list_variants(entity_uri)

    def list_pool_names(self) -> list[str]:
        return [pool.name for pool in list_pools()]

    def get_entity_uri(self) -> Uri | None:
        entity_uri_raw = self.parm('entity').eval()
        if entity_uri_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            # Only accept entity URIs, not group URIs
            if context.entity_uri.purpose != 'entity':
                return None
            return context.entity_uri
        # From settings
        entity_uris = self.list_entity_uris()
        if len(entity_uris) <= 1:  # Only 'from_context' means no real URIs
            return None
        if len(entity_uri_raw) == 0:
            return Uri.parse_unsafe(entity_uris[1])  # Skip 'from_context'
        if entity_uri_raw not in entity_uris:  # Compare strings
            return None
        return Uri.parse_unsafe(entity_uri_raw)

    def get_department_name(self) -> str | None:
        department_name = self.parm('department').eval()
        if department_name == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            return context.department_name
        # From settings
        department_names = self.list_department_names()
        if len(department_names) <= 1:  # Only 'from_context' means no real names
            return None
        if len(department_name) == 0:
            return department_names[1]  # Skip 'from_context'
        if department_name not in department_names:
            return None
        return department_name

    def get_variant_name(self) -> str:
        """Get selected variant name, defaults to 'default'."""
        variant_names = self.list_variant_names()
        variant_name = self.parm('variant').eval()
        if not variant_name or variant_name not in variant_names:
            return 'default'
        return variant_name

    def get_downstream_department_names(self) -> list[str]:
        department_names = self.list_downstream_department_names()
        if len(department_names) == 0:
            return []
        selected = list(filter(len, self.parm('export_departments').eval().split(' ')))
        if len(selected) == 0:
            return []
        selected.sort(key=department_names.index)
        return selected

    def get_pool_name(self) -> str | None:
        pool_names = self.list_pool_names()
        if len(pool_names) == 0:
            return None
        pool_name = self.parm('export_pool').eval()
        if pool_name == '':
            return pool_names[0]
        return pool_name

    def get_priority(self) -> int:
        return self.parm('export_priority').eval()

    def get_frame_range_source(self) -> str:
        return self.parm('frame_range').eval()

    def get_frame_range(self) -> tuple[FrameRange, int] | None:
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'single_frame':
                return FrameRange(1001, 1001, 0, 0), 1
            case 'playback_range':
                frame_range = util.get_frame_range()
                return frame_range, 1
            case 'from_context':
                entity_uri = self.get_entity_uri()
                if entity_uri is None:
                    return None
                frame_range = get_frame_range(entity_uri)
                if frame_range is None:
                    return None
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range source: {frame_range_source}'

    def get_export_type(self) -> str:
        """Get export type ('local' or 'farm')."""
        return self.parm('export_type').eval()

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        entity_raw = self.parm('entity').eval()
        if entity_raw == 'from_context':
            entity_uri = self.get_entity_uri()
            if entity_uri:
                self.parm('entity_label').set(f'from_context: {entity_uri}')
            else:
                self.parm('entity_label').set('from_context: none')
        else:
            # Specific entity URI selected
            self.parm('entity_label').set(entity_raw)

        department_raw = self.parm('department').eval()
        if department_raw == 'from_context':
            department_name = self.get_department_name()
            if department_name:
                self.parm('department_label').set(f'from_context: {department_name}')
            else:
                self.parm('department_label').set('from_context: none')
        else:
            # Specific department selected
            self.parm('department_label').set(department_raw)

    def _initialize(self):
        """Initialize node and update labels to show resolved values."""
        self._update_labels()

    def set_entity_uri(self, entity_uri: Uri):
        entity_uris = self.list_entity_uris()
        if str(entity_uri) not in entity_uris:  # Compare strings
            return
        self.parm('entity').set(str(entity_uri))

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names:
            return
        self.parm('department').set(department_name)

    def set_variant_name(self, variant_name: str):
        """Set variant name."""
        self.parm('variant').set(variant_name)

    def execute(self, force_local: bool = False):
        """
        Execute export.

        If force_local=True, executes directly (used by ProcessDialog callbacks).
        Otherwise, opens the ProcessDialog for task selection and execution.
        """
        if force_local:
            return self._execute()
        # Open ProcessDialog
        from tumblehead.pipe.houdini.ui.project_browser.utils.process_executor import (
            open_process_dialog_for_node
        )
        open_process_dialog_for_node(self, dialog_title="Export Layer")

    def _execute(self):
        """Internal execution - called by ProcessDialog callbacks."""
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
        stage_node = native.node('IN_stage')

        # Get parameters
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        frame_range_result = self.get_frame_range()

        if entity_uri is None:
            raise ExportLayerError("Entity URI is not set. Check 'Entity Source' setting or workfile context.")
        if department_name is None:
            raise ExportLayerError(f"Department name is not set for entity: {entity_uri}")
        if frame_range_result is None:
            if str(entity_uri).startswith('entity:/assets/'):
                raise ExportLayerError(
                    f"Frame range could not be determined for asset: {entity_uri}. "
                    "Assets don't have frame ranges - use 'Single Frame' or 'Playback Range' instead."
                )
            raise ExportLayerError(f"Frame range could not be determined for entity: {entity_uri}")

        frame_range, step = frame_range_result
        render_range = frame_range.full_range()
        user_name = get_user_name()
        timestamp = dt.datetime.now()
        fps = get_fps()

        # Determine version path
        version_path = next_export_path(entity_uri, variant_name, department_name)
        version_name = version_path.name

        # Prepare for stage scrape
        root = stage_node.stage().GetPseudoRoot()

        # Check if we're exporting a shot (to add shot dept entry to inputs)
        is_shot_export = str(entity_uri).startswith('entity:/shots/')

        # Scrape stage for assets
        assets = dict()
        asset_inputs = set()
        for asset_metadata in util.list_assets(root):
            asset_uri = Uri.parse_unsafe(asset_metadata['uri'])
            asset_path = util.uri_to_prim_path(asset_uri)
            instance_name = asset_metadata['instance']
            asset_instance_path = f'{asset_path.rsplit("/", 1)[0]}/{instance_name}'

            # Add current shot department entry to inputs if exporting a shot
            if is_shot_export:
                shot_dept_entry = {
                    'uri': str(entity_uri),
                    'department': department_name,
                    'version': version_name
                }
                # Add to inputs if not already present
                existing_inputs = asset_metadata.get('inputs', [])
                if shot_dept_entry not in existing_inputs:
                    asset_metadata['inputs'] = existing_inputs + [shot_dept_entry]

            assets[asset_instance_path] = asset_metadata
            asset_inputs.update(set(map(json.dumps, asset_metadata['inputs'])))

        # Set fps
        self.parm('set_metadata_fps').set(fps)

        # Export the stage
        root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Collect asset parameters
            parameter_assets = []
            for asset_metadata in assets.values():
                parameter_assets.append(dict(
                    asset=asset_metadata['uri'],
                    instance=asset_metadata['instance'],
                    inputs=asset_metadata.get('inputs', [])
                ))

            # Export the stage
            layer_file_name = get_layer_file_name(entity_uri, variant_name, department_name, version_name)
            self.parm('export_f1').deleteAllKeyframes()
            self.parm('export_f2').deleteAllKeyframes()
            self.parm('export_f1').set(render_range.first_frame)
            self.parm('export_f2').set(render_range.last_frame)
            self.parm('export_f3').set(step)
            self.parm('export_lopoutput').set(path_str(temp_path / layer_file_name))
            self.parm('export_execute').pressButton()

            # Re-fetch root prim after export (stage may have been modified)
            root = stage_node.stage().GetPseudoRoot()

            # Extract AOV names from the stage
            aov_names = [
                aov_path.rsplit('/', 1)[-1].lower()
                for aov_path in util.list_render_vars(root)
            ]

            # Write layer context
            save_layer_context(
                target_path=temp_path,
                entity_uri=entity_uri,
                department_name=department_name,
                version_name=version_name,
                timestamp=timestamp.isoformat(),
                user_name=user_name,
                variant_name=variant_name,
                parameters=dict(assets=parameter_assets, aov_names=aov_names),
                inputs=list(map(json.loads, asset_inputs))
            )

            # Copy all files to output path
            version_path.mkdir(parents=True, exist_ok=True)
            for temp_item_path in temp_path.iterdir():
                output_item_path = version_path / temp_item_path.name
                if temp_item_path.is_file():
                    shutil.copy(temp_item_path, output_item_path)
                if temp_item_path.is_dir():
                    shutil.copytree(temp_item_path, output_item_path)

        # Add shared layer as sublayer if any shared export exists
        # Use entity URI - the resolver handles dynamic version lookup at runtime
        from tumblehead.pipe.paths import latest_shared_export_path
        shared_version_path = latest_shared_export_path(entity_uri, department_name)
        if shared_version_path is not None:
            exported_layer_path = version_path / layer_file_name
            shared_uri = f"{entity_uri}?dept={department_name}&variant=_shared"
            add_sublayer(exported_layer_path, shared_uri)

        # Update node comment
        native.setComment(
            f'last export: {version_name}\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

    def _export_farm(self):
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        frame_range_result = self.get_frame_range()

        if entity_uri is None:
            raise ExportLayerError("Entity URI is not set. Check 'Entity Source' setting or workfile context.")
        if department_name is None:
            raise ExportLayerError(f"Department name is not set for entity: {entity_uri}")
        if frame_range_result is None:
            if str(entity_uri).startswith('entity:/assets/'):
                raise ExportLayerError(
                    f"Frame range could not be determined for asset: {entity_uri}. "
                    "Assets don't have frame ranges - use 'Single Frame' or 'Playback Range' instead."
                )
            raise ExportLayerError(f"Frame range could not be determined for entity: {entity_uri}")

        frame_range, _step = frame_range_result

        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()

        if pool_name is None:
            raise ExportLayerError("No render pool available. Check Deadline configuration.")
        if priority is None:
            raise ExportLayerError("Priority is not set for farm export.")

        # Get workfile path for bundling
        workfile_path = latest_hip_file_path(entity_uri, department_name)
        if not workfile_path.exists():
            raise ExportLayerError(f"No workfile found for {entity_uri} {department_name}")
        workfile_dest = Path('workfiles') / workfile_path.name
        paths = {workfile_path: workfile_dest}

        config = {
            'entity': {
                'uri': str(entity_uri),
                'department': department_name
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

        from tumblehead.farm.jobs.houdini.publish import job as publish_job
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
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        if department_name is None:
            hou.ui.displayMessage("No department selected.", severity=hou.severityType.Warning)
            return

        export_path = latest_export_path(entity_uri, variant_name, department_name)
        if export_path is None:
            hou.ui.displayMessage(f"No exports found for {department_name}.", severity=hou.severityType.Warning)
            return
        if not export_path.exists():
            hou.ui.displayMessage(f"Export path does not exist: {export_path}", severity=hou.severityType.Warning)
            return
        hou.ui.showInFileBrowser(path_str(export_path))

def create(scene, name):
    node_type = ns.find_node_type('export_layer', 'Lop')
    assert node_type is not None, 'Could not find export_layer node type'
    native = scene.node(name)
    if native is not None:
        return ExportLayer(native)
    return ExportLayer(scene.createNode(node_type.name(), name))


def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):
    # Set node style
    set_style(raw_node)

    node = ExportLayer(raw_node)
    node._initialize()

def execute():
    raw_node = hou.pwd()
    node = ExportLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ExportLayer(raw_node)
    node.open_location()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblehead.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ExportLayer(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='both',
        include_from_context=True,
        current_selection=node.parm('entity').eval(),
        title="Select Entity",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node._update_labels()
