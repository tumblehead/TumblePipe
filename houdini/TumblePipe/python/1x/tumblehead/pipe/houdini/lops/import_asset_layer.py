from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.cache import Cache
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    list_version_paths,
    get_asset_export_file_path,
    get_workfile_context,
    AssetContext
)

api = default_client()

def _valid_version_path(path):
    context_path = path / 'context.json'
    return context_path.exists()

def _set_metadata_script(category_name, asset_name, department_name, version_name):

    def _indent(lines):
        return [f"    {line}" for line in lines]

    # Prepare script
    header = [
        'import hou',
        '',
        'from tumblehead.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
        'def update(root):'
    ]

    # Get the prim
    content = [
        f"prim = root.GetPrimAtPath('/METADATA/asset/{category_name}/{asset_name}')",
        "if not prim.IsValid(): return",
        "metadata = util.get_metadata(prim)",
        ""
    ]

    # Add metadata if not already present
    content += [
        "if metadata is None:",
        "    metadata = {",
        "        'context': 'asset',",
        f"        'category': '{category_name}',",
        f"        'asset': '{asset_name}',",
        f"        'instance': '{asset_name}',",
        f"        'inputs': []",
        "    }",
        ""
    ]

    # Update metadata inputs
    content += [
        "util.add_metadata_input(metadata, {",
        "    'context': 'asset',",
        f"    'category': '{category_name}',",
        f"    'asset': '{asset_name}',",
        f"    'department': '{department_name}',",
        f"    'version': '{version_name}',",
        "})",
        ""
    ]

    # Set metadata
    content += [
        "util.set_metadata(prim, metadata)",
        ""
    ]

    # Footer
    footer = [
        "update(root)",
        ""
    ]


    # Done´
    script = header
    script += _indent(content)
    script += footer
    return script

CACHE_VERSION_NAMES = Cache()

class ImportAssetLayer(ns.Node):
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
        default_values = api.config.resolve('defaults:/houdini/lops/import_asset_layer')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in asset_department_names
        ]

    def list_version_names(self):
        category_name = self.get_category_name()
        asset_name = self.get_asset_name()
        department_name = self.get_department_name()
        asset_key = (category_name, asset_name, department_name)
        if CACHE_VERSION_NAMES.contains(asset_key):
            return CACHE_VERSION_NAMES.lookup(asset_key).copy()
        asset_path = api.storage.resolve(f'export:/assets/{category_name}/{asset_name}/{department_name}')
        version_paths = list(filter(
            _valid_version_path,
            list_version_paths(asset_path)
        ))
        version_names = [version_path.name for version_path in version_paths]
        CACHE_VERSION_NAMES.insert(asset_key, version_names)
        return version_names

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

    def get_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0: return version_names[0]
        if version_name not in version_names: return None
        return version_name
    
    def get_include_layerbreak(self):
        return bool(self.parm('include_layerbreak').eval())

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

    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)
    
    def set_include_layerbreak(self, include_layerbreak):
        self.parm('include_layerbreak').set(int(include_layerbreak))

    def latest(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return
        self.set_version_name(version_names[-1])
    
    def execute(self):

        # Clear scene
        context = self.native()
        import_node = context.node('import')
        metaprim_node = context.node('metaprim')
        metadata_node = context.node('metadata')
        switch_node = context.node('switch')
        bypass_node = context.node('bypass')

        # Parameters
        category_name = self.get_category_name()
        asset_name = self.get_asset_name()
        department_name = self.get_department_name()
        version_name = self.get_version_name()
        include_layerbreak = self.get_include_layerbreak()

        # Set metadata script
        metaprim_node.parm('primpath').set(f'/METADATA/asset/{category_name}/{asset_name}')
        script = _set_metadata_script(category_name, asset_name, department_name, version_name)
        metadata_node.parm('python').set('\n'.join(script))

        # Enable or disable layerbreak
        switch_node.parm('input').set(1 if include_layerbreak else 0)

        # Load asset
        file_path = get_asset_export_file_path(category_name, asset_name, department_name, version_name)
        if file_path.exists():
            import_node.parm('filepath1').set(path_str(file_path))
            bypass_node.parm('input').set(1)
        else:
            bypass_node.parm('input').set(0)

def clear_cache():
    CACHE_VERSION_NAMES.clear()

def create(scene, name):
    node_type = ns.find_node_type('import_asset_layer', 'Lop')
    assert node_type is not None, 'Could not find import_asset_layer node type'
    native = scene.node(name)
    if native is not None: return ImportAssetLayer(native)
    return ImportAssetLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)
    
    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('import_asset_layer', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ImportAssetLayer(raw_node)

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

def latest():
    raw_node = hou.pwd()
    node = ImportAssetLayer(raw_node)
    node.latest()

def execute():
    raw_node = hou.pwd()
    node = ImportAssetLayer(raw_node)
    node.execute()