from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import (
    path_str,
    default_client,
    get_user_name
)
from tumblehead.util.io import store_json
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    next_asset_export_path,
    get_workfile_context,
    AssetContext
)

api = default_client()

def _find_texture_nodes(node):
    return {
        child.name().split('_', 1)[1]: child
        for child in node.children()
        if child.name().startswith('OUT_')
    }

class TextureAsset(ns.Node):
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
    
    def get_material_name(self):
        material_name = self.parm('material_name').eval()
        assert len(material_name) > 0, 'Material name is empty'
        return material_name
    
    def set_category_name(self, category_name):
        category_names = self.list_category_names()
        if category_name not in category_names: return
        self.parm('category').set(category_name)
    
    def set_asset_name(self, asset_name):
        asset_names = self.list_asset_names()
        if asset_name not in asset_names: return
        self.parm('asset').set(asset_name)

    def set_material_name(self, material_name):
        assert len(material_name) > 0, 'Material name is empty'
        self.parm('material_name').set(material_name)

    def export(self):

        def _colorspace(texture_name):
            if texture_name == 'basecolor': return 'sRGB - Texture'
            return 'Automatic'
        
        # Nodes
        context = self.native()
        copnet_node = context.node('copnet1')
        texture_nodes = _find_texture_nodes(copnet_node)

        # Check if there are any texture nodes
        if len(texture_nodes) == 0: return

        # Parameters
        category_name = self.get_category_name()
        asset_name = self.get_asset_name()
        material_name = self.get_material_name()
        timestamp = dt.datetime.now().isoformat()

        # Paths
        version_path = next_asset_export_path(category_name, asset_name, 'texture')
        version_name = version_path.name
        export_path = version_path / material_name

        # Create temporary render node
        render_node = copnet_node.createNode('rop_image', 'TEMP_RENDER')

        # Export textures
        export_path.mkdir(parents = True, exist_ok = True)
        for texture_name, texture_node in texture_nodes.items():
            texture_path = export_path / f'{material_name}_{texture_name}.exr'
            render_node.parm('coppath').set(render_node.relativePathTo(texture_node))
            render_node.parm('copoutput').set(path_str(texture_path))
            render_node.parm('ociocolorspace').set(_colorspace(texture_name))
            render_node.parm('execute').pressButton()

        # Delete temporary render node
        render_node.destroy()
        
        # Write context
        context_path = version_path / 'context.json'
        context_data = dict(
            inputs = [],
            outputs = [dict(
                context = 'asset',
                category = category_name,
                asset = asset_name,
                layer = 'texture',
                version = version_name,
                timestamp = timestamp,
                user = get_user_name(),
                parameters = {}
            )]
        )
        store_json(context_path, context_data)

def create(scene, name):
    animate_type = ns.find_node_type('texture_asset', 'Lop')
    assert animate_type is not None, 'Could not find texture_asset node type'
    native = scene.node(name)
    if native is not None: return TextureAsset(native)
    return TextureAsset(scene.createNode(animate_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DIVE)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('texture_asset', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = TextureAsset(raw_node)

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

def export():
    raw_node = hou.pwd()
    node = TextureAsset(raw_node)
    node.export()