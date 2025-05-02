from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import get_user_name, path_str, default_client
from tumblehead.config import FrameRange
from tumblehead.util.io import store_json
from tumblehead.pipe.paths import (
    next_asset_export_file_path,
    get_workfile_context,
    AssetContext
)
from tumblehead.pipe.houdini.lops import import_asset_layer, set_kinds
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini import util

api = default_client()

class ExportAssetLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def list_category_names(self):
        return api.config.list_category_names()

    def list_asset_names(self):
        category_name = self.get_category_name()
        if category_name is None: return []
        return api.config.list_asset_names(category_name)

    def list_department_names(self):
        asset_department_names = api.config.list_asset_department_names()
        if len(asset_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/export_asset_layer')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in asset_department_names
        ]

    def get_category_name(self):
        category_names = self.list_category_names()
        if len(category_names) == 0: return None
        category_name = self.parm('category').eval()
        if len(category_name) == 0: return category_names[0]
        if category_name not in category_names: return None
        return category_name

    def get_asset_name(self):
        asset_names = self.list_asset_names()
        if len(asset_names) == 0: return None
        asset_name = self.parm('asset').eval()
        if len(asset_name) == 0: return asset_names[0]
        if asset_name not in asset_names: return None
        return asset_name
    
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

    def set_asset_name(self, asset_name):
        asset_names = self.list_asset_names()
        if asset_name not in asset_names: return
        self.parm('asset').set(asset_name)
    
    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)

    def execute(self):

        # Nodes
        context = self.native()
        kinds_node = set_kinds.SetKinds(context.node('kinds'))

        # Parameters
        category_name = self.get_category_name()
        asset_name = self.get_asset_name()
        department_name = self.get_department_name()
        frame_range, frame_step = self.get_frame_range()
        render_range = frame_range.full_range()
        timestamp = dt.datetime.now().isoformat()

        # Isolate the asset
        self.parm('isolated_asset_srcprimpath1').set(f'/{category_name}/{asset_name}')
        self.parm('isolated_asset_dstprimpath1').set(f'/{category_name}')

        # Set kinds
        kinds_node.set_category_name(category_name)
        kinds_node.set_item_name(asset_name)
        kinds_node.execute()

        # Prepare asset department export
        file_path = next_asset_export_file_path(category_name, asset_name, department_name)
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
                context = 'asset',
                category = category_name,
                asset = asset_name,
                layer = department_name,
                version = version_name,
                timestamp = timestamp,
                user = get_user_name(),
                parameters = {}
            )]
        )
        store_json(context_path, context_data)

        # Clear import cache
        import_asset_layer.clear_cache()

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

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('export_asset_layer', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ExportAssetLayer(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return
    
    # Set the default values
    match context:
        case AssetContext(
            department_name,
            category_name,
            asset_name,
            version_name
            ):
            node.set_category_name(category_name)
            node.set_asset_name(asset_name)
            node.set_department_name(department_name)

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ExportAssetLayer(raw_node)
    node.execute()