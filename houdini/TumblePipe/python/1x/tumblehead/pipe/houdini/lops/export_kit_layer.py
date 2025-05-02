from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import get_user_name, path_str, default_client
from tumblehead.config import FrameRange
from tumblehead.util.io import store_json
from tumblehead.pipe.houdini.lops import import_kit_layer
from tumblehead.pipe.houdini import util
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    next_kit_export_file_path,
    get_workfile_context,
    KitContext
)

api = default_client()

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

    def get_category_name(self):
        category_names = self.list_category_names()
        if len(category_names) == 0: return None
        category_name = self.parm('category').eval()
        if len(category_name) == 0: return category_names[0]
        if category_name not in category_names: return None
        return category_name

    def get_kit_name(self):
        kit_names = self.list_kit_names()
        if len(kit_names) == 0: return None
        kit_name = self.parm('kit').eval()
        if len(kit_name) == 0: return kit_names[0]
        if kit_name not in kit_names: return None
        return kit_name
    
    def get_department_name(self):
        department_names = self.list_department_names()
        if len(department_names) == 0: return None
        department_name = self.parm('department').eval()
        if len(department_name) == 0: return department_names[0]
        if department_name not in department_names: return None
        return department_name

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

    def execute(self):

        # Nodes
        context = self.native()
        export_node = context.node('export')

        # Parameters
        category_name = self.get_category_name()
        kit_name = self.get_kit_name()
        department_name = self.get_department_name()
        frame_range, step = self.get_frame_range()
        render_range = frame_range.full_range()
        timestamp = dt.datetime.now().isoformat()

        # Prepare kit department export
        file_path = next_kit_export_file_path(category_name, kit_name, department_name)
        version_path = file_path.parent
        version_name = version_path.name
        export_node.parm('lopoutput').set(path_str(file_path))
        export_node.parm('f1').set(render_range.first_frame)
        export_node.parm('f2').set(render_range.last_frame)
        export_node.parm('f3').set(step)

        # Export kit department layer
        version_path.mkdir(parents = True, exist_ok = True)
        export_node.parm('execute').pressButton()

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
                timestamp = timestamp,
                user = get_user_name(),
                parameters = {}
            )]
        )
        store_json(context_path, context)

        # Clear import cache
        import_kit_layer.clear_cache()

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

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('export_kit_layer', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ExportKitLayer(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return
    
    # Set the default values
    match context:
        case KitContext(
            department_name,
            category_name,
            kit_name,
            version_name
            ):
            node.set_category_name(category_name)
            node.set_kit_name(kit_name)
            node.set_department_name(department_name)

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ExportKitLayer(raw_node)
    node.execute()