from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import get_user_name, path_str, default_client
from tumblehead.util.uri import Uri
from tumblehead.util.io import store_json
from tumblehead.config.department import list_departments
from tumblehead.apps.deadline import Deadline
from tumblehead.config.timeline import FrameRange, get_fps
from tumblehead.pipe.houdini.lops import submit_render
from tumblehead.pipe.paths import (
    next_export_file_path,
    latest_export_path,
    get_workfile_context
)
from tumblehead.pipe.houdini.lops import set_kinds
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini import util

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/export_asset_layer')


class ExportAssetLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        return [entity.uri for entity in asset_entities]

    def list_department_names(self):
        asset_departments = list_departments('assets')
        if len(asset_departments) == 0: return []
        asset_department_names = [dept.name for dept in asset_departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in asset_department_names
        ]

    def list_downstream_department_names(self):
        asset_departments = list_departments('assets')
        if len(asset_departments) == 0: return []
        asset_department_names = [dept.name for dept in asset_departments]
        department_name = self.get_department_name()
        if department_name is None: return []
        if department_name not in asset_department_names: return []
        department_index = asset_department_names.index(department_name)
        return asset_department_names[department_index + 1:]

    def list_pool_names(self):
        try: deadline = Deadline()
        except: return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0: return []
        default_values = api.config.get_properties(submit_render.DEFAULTS_URI)
        if default_values is None: return []
        if 'pools' not in default_values: return []
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
        ]

    def get_entity_source(self):
        return self.parm('entity_source').eval()

    def get_asset_uri(self) -> Uri | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
                if context is None: return None
                return context.entity_uri
            case 'from_settings':
                asset_uris = self.list_asset_uris()
                if len(asset_uris) == 0: return None
                asset_uri_raw = self.parm('asset').eval()
                if len(asset_uri_raw) == 0: return asset_uris[0]
                asset_uri = Uri.parse_unsafe(asset_uri_raw)
                if asset_uri not in asset_uris: return None
                return asset_uri
            case _:
                raise AssertionError(f'Unknown entity source setting "{entity_source}"')

    def get_department_name(self) -> str | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
                if context is None: return None
                return context.department_name
            case 'from_settings':
                department_names = self.list_department_names()
                if len(department_names) == 0: return None
                department_name = self.parm('department').eval()
                if len(department_name) == 0: return department_names[0]
                if department_name not in department_names: return None
                return department_name
            case _:
                raise AssertionError(f'Unknown entity source setting "{entity_source}"')

    def get_downstream_department_names(self):
        department_names = self.list_downstream_department_names()
        if len(department_names) == 0: return []
        default_values = api.config.get_properties(DEFAULTS_URI)
        selected_department_names = list(filter(len, self.parm('export_departments').eval().split(' ')))
        if len(selected_department_names) == 0:
            if default_values is None: return []
            return default_values.get('downstream_departments', [])
        selected_department_names.sort(key = department_names.index)
        return selected_department_names
    
    def get_pool_name(self):
        pool_names = self.list_pool_names()
        if len(pool_names) == 0: return None
        pool_name = self.parm('export_pool').eval()
        if pool_name == '': return pool_names[0]
        return pool_name

    def get_priority(self):
        return self.parm('export_priority').eval()
    
    def get_frame_range_source(self):
        return self.parm('frame_range').eval()
    
    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'single_frame':
                return FrameRange(1001, 1001, 0, 0), 1
            case 'playback_range':
                frame_range = util.get_frame_range()
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range setting "{frame_range_source}"'
    
    def set_entity_source(self, entity_source):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources: return
        self.parm('entity_source').set(entity_source)

    def set_asset_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('asset').set(str(asset_uri))

    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)

    def get_export_type(self):
        return self.parm('export_type').eval()

    def execute(self, force_local=False):
        if force_local:
            return self._export_local()
        export_type = self.get_export_type()
        match export_type:
            case 'local': return self._export_local()
            case 'farm': return self._export_farm()
            case _: assert False, f'Unknown export type: {export_type}'
    
    def _do_export_to_asset(self, kinds_node, asset_uri: Uri, department_name: str,
                             frame_range: FrameRange, frame_step: int):
        """Core export logic for a single asset

        Args:
            kinds_node: Kinds helper node
            asset_uri: Asset URI
            department_name: Department name
            frame_range: Frame range
            frame_step: Frame step size

        Returns:
            Tuple of (version_name, timestamp, user_name)
        """
        # Additional parameters
        render_range = frame_range.full_range()
        timestamp = dt.datetime.now()
        user_name = get_user_name()
        fps = get_fps()

        # Isolate the asset
        from tumblehead.pipe.houdini.util import uri_to_prim_path, uri_to_parent_prim_path
        asset_prim_path = uri_to_prim_path(asset_uri)
        parent_prim_path = uri_to_parent_prim_path(asset_uri)
        self.parm('isolated_asset_srcprimpath1').set(asset_prim_path)
        self.parm('isolated_asset_dstprimpath1').set(parent_prim_path)

        # Set kinds
        kinds_node.set_prim_path(asset_prim_path)
        kinds_node.execute()

        # Set fps
        self.parm('set_metadata_fps').set(fps)

        # Prepare asset department export
        file_path = next_export_file_path(asset_uri, department_name)
        version_path = file_path.parent
        version_name = version_path.name
        self.parm('export_lopoutput').set(path_str(file_path))
        self.parm('export_f1').set(render_range.first_frame)
        self.parm('export_f2').set(render_range.last_frame)
        self.parm('export_f3').set(frame_step)

        # Export asset department layer
        version_path.mkdir(parents = True, exist_ok = True)
        self.parm('export_execute').pressButton()

        # Write context
        context_path = version_path / 'context.json'
        context_data = dict(
            inputs = [],
            outputs = [dict(
                entity = str(asset_uri),
                department = department_name,
                version = version_name,
                timestamp = timestamp.isoformat(),
                user = user_name,
                parameters = {}
            )]
        )
        store_json(context_path, context_data)

        return version_name, timestamp, user_name

    def _export_local(self):

        # Nodes
        native = self.native()
        kinds_node = set_kinds.SetKinds(native.node('kinds'))

        # Parameters
        asset_uri = self.get_asset_uri()
        department_name = self.get_department_name()
        frame_range_result = self.get_frame_range()

        # Check parameters
        if asset_uri is None: return
        if department_name is None: return
        if frame_range_result is None: return

        frame_range, frame_step = frame_range_result

        # Export single asset
        version_name, timestamp, user_name = self._do_export_to_asset(
            kinds_node,
            asset_uri,
            department_name,
            frame_range,
            frame_step
        )

        # Update node comment
        native.setComment(
            'last export: '
            f'{version_name} \n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")} \n'
            f'by {user_name}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

    def _export_farm(self):
        """Submit export job to farm"""
        # Import job module
        try:
            from tumblehead.farm.jobs.houdini.publish import job as publish_job
        except ImportError as e:
            hou.ui.displayMessage(
                f"Failed to import publish job module: {str(e)}",
                severity=hou.severityType.Error
            )
            return

        # Get parameters
        asset_uri = self.get_asset_uri()
        department_name = self.get_department_name()

        if not all([asset_uri, department_name]):
            hou.ui.displayMessage(
                "Missing required parameters (asset or department)",
                severity=hou.severityType.Error
            )
            return

        frame_range_result = self.get_frame_range()
        if frame_range_result is None: return
        frame_range, _step = frame_range_result

        # Get farm parameters
        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()

        # Build config
        config = {
            'settings': {
                'priority': priority,
                'pool_name': pool_name,
                'entity_uri': str(asset_uri),
                'department_name': department_name,
                'first_frame': frame_range.full_range().first_frame,
                'last_frame': frame_range.full_range().last_frame
            },
            'tasks': {
                'publish': {
                    'downstream_departments': downstream_deps
                }
            }
        }

        # Submit to farm
        try:
            publish_job.submit(config, {})

            # Update node comment
            native = self.native()
            timestamp = dt.datetime.now()
            user_name = get_user_name()
            native.setComment(
                f'farm export submitted: \n'
                f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")} \n'
                f'by {user_name}\n'
                f'downstream: {", ".join(downstream_deps) if downstream_deps else "None"}'
            )
            native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

            # Show success message
            downstream_msg = f"\nDownstream: {', '.join(downstream_deps)}" if downstream_deps else ""
            hou.ui.displayMessage(
                f"Export job submitted to farm\n"
                f"Department: {department_name}"
                f"{downstream_msg}",
                title="Farm Export Submitted"
            )

        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to submit farm job: {str(e)}",
                severity=hou.severityType.Error
            )
            return
        
    def open_location(self):
        asset_uri = self.get_asset_uri()
        if asset_uri is None: return
        department_name = self.get_department_name()
        if department_name is None: return
        export_path = latest_export_path(asset_uri, department_name)
        if export_path is None: return
        if not export_path.exists(): return
        hou.ui.showInFileBrowser(f'{path_str(export_path)}')

def create(scene, name):
    node_type = ns.find_node_type('export_asset_layer', 'Lop')
    assert node_type is not None, 'Could not find export_asset_layer node type'
    native = scene.node(name)
    if native is not None: return ExportAssetLayer(native)
    return ExportAssetLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Check if workfile context exists
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is not None: return  # Context exists → keep 'from_context'

    # No context → change entity source to settings
    node = ExportAssetLayer(raw_node)
    node.set_entity_source('from_settings')

def execute():
    raw_node = hou.pwd()
    node = ExportAssetLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ExportAssetLayer(raw_node)
    node.open_location()