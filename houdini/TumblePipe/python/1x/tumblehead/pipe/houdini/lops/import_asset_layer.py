from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    list_version_paths,
    get_export_file_path,
    get_workfile_context
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/import_asset_layer')

def _valid_version_path(path):
    context_path = path / 'context.json'
    return context_path.exists()

def _set_metadata_script(
    asset_uri: Uri,
    department_name: str,
    version_name: str,
    metadata_prim_path: str
    ):

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
        f"prim = root.GetPrimAtPath('{metadata_prim_path}')",
        "if not prim.IsValid(): return",
        "metadata = util.get_metadata(prim)",
        ""
    ]

    # Add metadata if not already present
    content += [
        "if metadata is None:",
        "    metadata = {",
        f"        'uri': '{str(asset_uri)}',",
        f"        'instance': '{asset_uri.segments[-1]}',",
        f"        'inputs': []",
        "    }",
        ""
    ]

    # Update metadata inputs
    content += [
        "util.add_metadata_input(metadata, {",
        f"    'uri': '{str(asset_uri)}',",
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

    # Done
    script = header
    script += _indent(content)
    script += footer
    return script

class ImportAssetLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        return list(asset_entities)

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

    def list_version_names(self):
        asset_uri = self.get_asset_uri()
        if asset_uri is None: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        export_uri = (
            Uri.parse_unsafe('export:/') /
            asset_uri.segments /
            department_name
        )
        asset_path = api.storage.resolve(export_uri)
        version_paths = list(filter(
            _valid_version_path,
            list_version_paths(asset_path)
        ))
        version_names = [version_path.name for version_path in version_paths]
        return version_names

    def get_asset_uri(self) -> Uri | None:
        asset_uris = self.list_asset_uris()
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm('asset').eval()
        if len(asset_uri_raw) == 0: return asset_uris[0]
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in asset_uris: return None
        return asset_uri

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
        if len(version_name) == 0: return version_names[-1]
        if version_name == 'latest': return version_names[-1]
        if version_name not in version_names: return None
        return version_name
    
    def get_include_layerbreak(self):
        return bool(self.parm('include_layerbreak').eval())

    def set_asset_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('asset').set(str(asset_uri))

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

        # Parameters
        asset_uri = self.get_asset_uri()
        department_name = self.get_department_name()
        version_name = self.get_version_name()

        # Check parameters
        if asset_uri is None: return
        if department_name is None: return
        if version_name is None: return

        # Set metadata script
        metadata_prim_path = '/'.join(['', 'METADATA'] + asset_uri.segments)
        self.parm('metaprim_primpath').set(metadata_prim_path)
        script = _set_metadata_script(asset_uri, department_name, version_name, metadata_prim_path)
        self.parm('metadata_python').set('\n'.join(script))

        # Load asset
        file_path = get_export_file_path(asset_uri, department_name, version_name)
        self.parm('import_filepath1').set(path_str(file_path))
        self.parm('bypass_input').set(1 if file_path.exists() else 0)

        # Update the version label on the node UI
        self.parm('version_label').set(version_name)

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

    # Set the default values from context
    node.set_asset_uri(context.entity_uri)

def execute():
    raw_node = hou.pwd()
    node = ImportAssetLayer(raw_node)
    node.execute()