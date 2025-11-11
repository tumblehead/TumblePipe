from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import get_user_name, path_str, default_client
from tumblehead.util.io import store_json, load_json
from tumblehead.apps.deadline import Deadline
from tumblehead.config import FrameRange
from tumblehead.pipe.houdini.lops import import_kit_layer
from tumblehead.pipe.houdini import util
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    next_kit_export_file_path,
    latest_kit_export_path,
    KitEntity
)

api = default_client()

def _entity_from_context_json():

    # Path to current workfile
    file_path = Path(hou.hipFile.path())
    if not file_path.exists(): return None

    # Look for context.json in the workfile directory
    context_json_path = file_path.parent / "context.json"
    if not context_json_path.exists(): return None
    context_data = load_json(context_json_path)
    if context_data is None: return None

    # Parse the loaded context
    if context_data['entity'] != 'kit': return None
    return KitEntity(
        category_name = context_data['category'],
        kit_name = context_data['kit'],
        department_name = context_data['department']
    )

class ExportKitLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def list_category_names(self):
        return api.config.list_kit_category_names()

    def list_kit_names(self):
        category_name = self.get_category_name()
        if category_name is None: return []
        return api.config.list_kit_names(category_name)

    def list_department_names(self):
        kit_department_names = api.config.list_kit_department_names()
        if len(kit_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/export_kit_layer')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in kit_department_names
        ]

    def list_downstream_department_names(self):
        kit_department_names = api.config.list_kit_department_names()
        if len(kit_department_names) == 0: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        if department_name not in kit_department_names: return []
        department_index = kit_department_names.index(department_name)
        return kit_department_names[department_index + 1:]

    def list_pool_names(self):
        try: deadline = Deadline()
        except: return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/submit_render')
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
        ]

    def get_entity_source(self):
        return self.parm('entity_source').eval()

    def get_category_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.category_name
            case 'from_settings':
                category_names = self.list_category_names()
                if len(category_names) == 0: return None
                category_name = self.parm('category').eval()
                if len(category_name) == 0: return category_names[0]
                if category_name not in category_names: return None
                return category_name
            case _:
                raise AssertionError(f'Unknown entity source "{entity_source}"')

    def get_kit_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.kit_name
            case 'from_settings':
                kit_names = self.list_kit_names()
                if len(kit_names) == 0: return None
                kit_name = self.parm('kit').eval()
                if len(kit_name) == 0: return kit_names[0]
                if kit_name not in kit_names: return None
                return kit_name

    def get_department_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.department_name
            case 'from_settings':
                department_names = self.list_department_names()
                if len(department_names) == 0: return None
                department_name = self.parm('department').eval()
                if len(department_name) == 0: return department_names[0]
                if department_name not in department_names: return None
                return department_name
            case _:
                raise AssertionError(f'Unknown entity source "{entity_source}"')

    def get_downstream_department_names(self):
        department_names = self.list_downstream_department_names()
        if len(department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/export_kit_layer')
        selected_department_names = list(filter(len, self.parm('export_departments').eval().split(' ')))
        if len(selected_department_names) == 0: return default_values.get('downstream_departments', [])
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

    def set_category_name(self, category_name):
        category_names = self.list_category_names()
        if category_name not in category_names: return
        self.parm('category').set(category_name)

    def set_kit_name(self, kit_name):
        kit_names = self.list_kit_names()
        if kit_name not in kit_names: return
        self.parm('kit').set(kit_name)
    
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
    
    def _export_local(self):

        # Nodes
        native = self.native()
        export_node = native.node('export')

        # Parameters
        category_name = self.get_category_name()
        kit_name = self.get_kit_name()
        department_name = self.get_department_name()
        frame_range, frame_step = self.get_frame_range()
        render_range = frame_range.full_range()
        timestamp = dt.datetime.now()
        user_name = get_user_name()
        fps = api.config.get_fps()

        # Set fps
        self.parm('set_metadata_fps').set(fps)

        # Prepare kit department export
        file_path = next_kit_export_file_path(category_name, kit_name, department_name)
        version_path = file_path.parent
        version_name = version_path.name
        self.parm('export_lopoutput').set(path_str(file_path))
        self.parm('export_f1').set(render_range.first_frame)
        self.parm('export_f2').set(render_range.last_frame)
        self.parm('export_f3').set(frame_step)

        # Export kit department layer
        version_path.mkdir(parents = True, exist_ok = True)
        self.parm('export_execute').pressButton()

        # Write context
        context_path = version_path / 'context.json'
        context = dict(
            inputs = [],
            outputs = [dict(
                context = 'kit',
                category = category_name,
                kit = kit_name,
                layer = department_name,
                version = version_name,
                timestamp = timestamp.isoformat(),
                user = user_name,
                parameters = {}
            )]
        )
        store_json(context_path, context)

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
        category_name = self.get_category_name()
        kit_name = self.get_kit_name()
        department_name = self.get_department_name()
        
        if not all([category_name, kit_name, department_name]):
            hou.ui.displayMessage(
                "Missing required parameters (category, kit, or department)",
                severity=hou.severityType.Error
            )
            return
        
        frame_range, _step = self.get_frame_range()
        
        # Get farm parameters
        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()
        
        # Build config
        config = {
            'entity': {
                'tag': 'kit',
                'category_name': category_name,
                'kit_name': kit_name,
                'department_name': department_name
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
        category_name = self.get_category_name()
        kit_name = self.get_kit_name()
        department_name = self.get_department_name()
        export_path = latest_kit_export_path(
            category_name,
            kit_name,
            department_name
        )
        if export_path is None: return
        if not export_path.exists(): return
        hou.ui.showInFileBrowser(f'{path_str(export_path)}/')

def create(scene, name):
    node_type = ns.find_node_type('export_kit_layer', 'Lop')
    assert node_type is not None, 'Could not find export_kit_layer node type'
    native = scene.node(name)
    if native is not None: return ExportKitLayer(native)
    return ExportKitLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Change entity source to settings if we have no context
    entity = _entity_from_context_json()
    if entity is not None: return
    node = ExportKitLayer(raw_node)
    node.set_entity_source('from_settings')

def execute():
    raw_node = hou.pwd()
    node = ExportKitLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ExportKitLayer(raw_node)
    node.open_location()