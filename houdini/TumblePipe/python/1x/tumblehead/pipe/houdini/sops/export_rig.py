from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import (
    get_user_name,
    path_str,
    default_client
)
from tumblehead.util.io import store_json
from tumblehead.pipe.houdini.sops import import_rig
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    get_next_version_path,
    get_workfile_context,
    AssetContext
)

api = default_client()

class ExportRig(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def list_category_names(self):
        return api.config.list_category_names()

    def list_asset_names(self):
        category_name = self.get_category_name()
        if category_name is None: return []
        return api.config.list_asset_names(category_name)

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

    def set_category_name(self, category_name):
        category_names = self.list_category_names()
        if category_name not in category_names: return
        self.parm('category').set(category_name)

    def set_asset_name(self, asset_name):
        asset_names = self.list_asset_names()
        if asset_name not in asset_names: return
        self.parm('asset').set(asset_name)

    def execute(self):

        # Nodes
        context = self.native()
        export_node = context.node('export')

        # Parameters
        category_name = self.get_category_name()
        asset_name = self.get_asset_name()
        export_path = api.storage.resolve(f'export:/assets/{category_name}/{asset_name}/rig')
        version_path = get_next_version_path(export_path)
        version_name = version_path.name
        timestamp = dt.datetime.now().isoformat()

        # Prepare rig export
        output_file_path = version_path / f'{category_name}_{asset_name}_rig_{version_name}.bgeo.sc'
        export_node.parm('file').set(path_str(output_file_path))

        # Export rig
        version_path.mkdir(parents=True, exist_ok=True)
        export_node.parm('execute').pressButton()

        # Write context
        context_path = version_path / 'context.json'
        context = dict(
            inputs = [],
            outputs = [dict(
                context = 'asset',
                category = category_name,
                asset = asset_name,
                layer = 'rig',
                version = version_name,
                timestamp = timestamp,
                user = get_user_name(),
                parameters = {}
            )]
        )
        store_json(context_path, context)

        # Clear import cache
        import_rig.clear_cache()

def create(scene, name):
    node_type = ns.find_node_type('export_rig', 'Sop')
    assert node_type is not None, 'Could not find export_rig node type'
    native = scene.node(name)
    if native is not None: return ExportRig(native)
    return ExportRig(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('export_rig', 'Sop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ExportRig(raw_node)

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

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ExportRig(raw_node)
    node.execute()