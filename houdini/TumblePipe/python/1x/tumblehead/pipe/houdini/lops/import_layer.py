from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.uri import Uri
from tumblehead.util.io import load_json
from tumblehead.config.department import list_departments
from tumblehead.config.variants import get_entity_type
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.util import uri_to_metadata_prim_path
import tumblehead.pipe.context as ctx
from tumblehead.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    latest_export_path,
    get_export_uri,
    get_layer_file_name
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/import_layer')

def _valid_version_path(path: Path) -> bool:
    context_path = path / 'context.json'
    return context_path.exists()

def _metadata_update_script(
    entity_uri: Uri,
    department_name: str,
    version_name: str,
    assets: list[dict]
) -> str:
    """
    Generate Python script to update asset metadata inputs.

    For each asset in the imported layer, adds the current department/version
    to the asset's inputs array (if not already present).
    """
    def _indent(lines):
        return [f"    {line}" for line in lines]

    header = [
        'import hou',
        '',
        'from tumblehead.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
        'def update(root):',
    ]

    content = []
    for asset_info in assets:
        asset_uri_str = asset_info['asset']
        asset_uri = Uri.parse_unsafe(asset_uri_str)
        prim_path = uri_to_metadata_prim_path(asset_uri)

        content.extend([
            f"# Asset: {asset_uri_str}",
            f"prim = root.GetPrimAtPath('{prim_path}')",
            "if prim.IsValid():",
            "    metadata = util.get_metadata(prim)",
            "    if metadata is not None:",
            "        util.add_metadata_input(metadata, {",
            f"            'uri': '{str(entity_uri)}',",
            f"            'department': '{department_name}',",
            f"            'version': '{version_name}',",
            "        })",
            "        util.set_metadata(prim, metadata)",
            "",
        ])

    footer = [
        'update(root)',
        '',
    ]

    script = header + _indent(content) + footer
    return '\n'.join(script)

class ImportLayer(ns.Node):

    def __init__(self, native):
        super().__init__(native)

    def get_entity_type(self) -> str | None:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return None
        return get_entity_type(entity_uri)

    def list_entity_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        shot_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )
        return [e.uri for e in asset_entities] + [e.uri for e in shot_entities]

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
            return []

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        departments = list_departments(context_name)
        if len(departments) == 0:
            return []

        department_names = [dept.name for dept in departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        if default_values is None:
            return department_names

        configured_depts = default_values.get('departments', [])
        if configured_depts:
            return [
                name for name in configured_depts
                if name in department_names
            ]
        return department_names

    def list_version_names(self) -> list[str]:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['current']
        department_name = self.get_department_name()
        if department_name is None:
            return ['current']

        # Get export path (default/non-variant path)
        export_uri = get_export_uri(entity_uri, department_name)
        export_path = api.storage.resolve(export_uri)
        version_paths = list(filter(
            _valid_version_path,
            list_version_paths(export_path)
        ))
        version_names = [vp.name for vp in version_paths]

        # Add 'current' option at the beginning (resolves to highest numbered version)
        return ['current'] + version_names

    def get_entity_source(self) -> str:
        return self.parm('entity_source').eval()

    def get_entity_uri(self) -> Uri | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
                if context is None:
                    return None
                return context.entity_uri
            case 'from_settings':
                entity_uris = self.list_entity_uris()
                if len(entity_uris) == 0:
                    return None
                entity_uri_raw = self.parm('entity').eval()
                if len(entity_uri_raw) == 0:
                    return entity_uris[0]
                entity_uri = Uri.parse_unsafe(entity_uri_raw)
                if entity_uri not in entity_uris:
                    return None
                return entity_uri
            case _:
                raise AssertionError(f'Unknown entity source: {entity_source}')

    def get_department_name(self) -> str | None:
        department_names = self.list_department_names()
        if len(department_names) == 0:
            return None
        department_name = self.parm('department').eval()
        if len(department_name) == 0:
            return department_names[0]
        if department_name not in department_names:
            return None
        return department_name

    def get_version_name(self) -> str | None:
        version_names = self.list_version_names()
        if len(version_names) == 0:
            return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0:
            return version_names[-1]
        if version_name == 'current':
            return version_names[-1]
        if version_name not in version_names:
            return None
        return version_name

    def get_include_layerbreak(self) -> bool:
        return bool(self.parm('include_layerbreak').eval())

    def set_entity_source(self, entity_source: str):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources:
            return
        self.parm('entity_source').set(entity_source)

    def set_entity_uri(self, entity_uri: Uri):
        entity_uris = self.list_entity_uris()
        if entity_uri not in entity_uris:
            return
        self.parm('entity').set(str(entity_uri))

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names:
            return
        self.parm('department').set(department_name)

    def set_version_name(self, version_name: str):
        version_names = self.list_version_names()
        if version_name not in version_names:
            return
        self.parm('version').set(version_name)

    def set_include_layerbreak(self, include_layerbreak: bool):
        self.parm('include_layerbreak').set(int(include_layerbreak))

    def execute(self):
        return self._import_layer()

    def _get_layer_file_name(self) -> str | None:
        """Determine the layer file name."""
        version_name = self.get_version_name()
        if version_name is None:
            return None

        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return None

        department_name = self.get_department_name()
        if department_name is None:
            return None

        # Import layer file (assets + cameras, lights, volumes, etc. all in one)
        return get_layer_file_name(entity_uri, department_name, version_name)

    def _import_layer(self):
        """Unified import method for both assets and shots."""
        entity_uri = self.get_entity_uri()
        department_name = self.get_department_name()
        version_name = self.get_version_name()

        if entity_uri is None:
            return
        if department_name is None:
            return
        if version_name is None:
            return

        # Get version path
        export_uri = get_export_uri(entity_uri, department_name) / version_name
        version_path = api.storage.resolve(export_uri)

        # Get layer file name
        layer_file_name = self._get_layer_file_name()
        if layer_file_name is None:
            self.parm('import_enable1').set(0)
            return

        file_path = version_path / layer_file_name

        # Import layer file
        file_exists = file_path.exists()
        self.parm('import_enable1').set(1 if file_exists else 0)
        self.parm('import_filepath1').set(path_str(file_path))
        self.parm('bypass_input').set(1 if file_exists else 0)

        # Update version label
        self.parm('version_label').set(version_name)

        # Generate metadata update script from context.json
        context_path = version_path / 'context.json'
        if context_path.exists():
            context_data = load_json(context_path)
            layer_info = ctx.find_output(
                context_data,
                uri=str(entity_uri),
                department=department_name
            )
            if layer_info is not None:
                assets = layer_info.get('parameters', {}).get('assets', [])
                if assets:
                    script = _metadata_update_script(
                        entity_uri, department_name, version_name, assets
                    )
                    self.parm('metadata_python').set(script)

    def open_location(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return
        department_name = self.get_department_name()
        if department_name is None:
            return

        export_path = latest_export_path(entity_uri, department_name)
        if export_path is None:
            return
        if not export_path.exists():
            return
        hou.ui.showInFileBrowser(path_str(export_path))

def create(scene, name):
    node_type = ns.find_node_type('import_layer', 'Lop')
    assert node_type is not None, 'Could not find import_layer node type'
    native = scene.node(name)
    if native is not None:
        return ImportLayer(native)
    return ImportLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):
    set_style(raw_node)

    # Check context
    raw_node_type = raw_node.type()
    if raw_node_type is None:
        return
    node_type = ns.find_node_type('import_layer', 'Lop')
    if node_type is None:
        return
    if raw_node_type != node_type:
        return
    node = ImportLayer(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None:
        node.set_entity_source('from_settings')

    # Always set default entity
    entity_uris = node.list_entity_uris()
    if entity_uris:
        node.set_entity_uri(entity_uris[0])

def execute():
    raw_node = hou.pwd()
    node = ImportLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ImportLayer(raw_node)
    node.open_location()
