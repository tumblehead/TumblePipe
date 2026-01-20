from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.uri import Uri
from tumblehead.util.io import load_json
from tumblehead.config.department import list_departments
from tumblehead.config.variants import get_entity_type, list_variants
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.util import uri_to_metadata_prim_path
import tumblehead.pipe.context as ctx
from tumblehead.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    latest_export_path,
    get_export_uri,
    get_layer_file_name,
    latest_shared_export_file_path
)

api = default_client()


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

        instance_name = asset_uri.segments[-1] if asset_uri.segments else ''
        content.extend([
            f"# Asset: {asset_uri_str}",
            f"prim = root.GetPrimAtPath('{prim_path}')",
            "if not prim.IsValid():",
            f"    prim = stage.DefinePrim('{prim_path}', 'Scope')",
            f"    util.set_metadata(prim, {{'uri': '{asset_uri_str}', 'instance': '{instance_name}', 'inputs': []}})",
            "metadata = util.get_metadata(prim)",
            "if metadata is not None:",
            "    util.add_metadata_input(metadata, {",
            f"        'uri': '{str(entity_uri)}',",
            f"        'department': '{department_name}',",
            f"        'version': '{version_name}',",
            "    })",
            "    util.set_metadata(prim, metadata)",
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
        names = [d.name for d in list_departments(context_name) if d.publishable]
        return ['from_context'] + names

    def list_variant_names(self) -> list[str]:
        """List available variant names for current entity."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['default']
        return list_variants(entity_uri)

    def list_version_names(self) -> list[str]:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['current']
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        if department_name is None:
            return ['current']

        # Get export path with variant
        export_uri = get_export_uri(entity_uri, variant_name, department_name)
        export_path = api.storage.resolve(export_uri)
        version_paths = list(filter(
            _valid_version_path,
            list_version_paths(export_path)
        ))
        version_names = [vp.name for vp in version_paths]

        # Add 'current' option at the beginning (resolves to highest numbered version)
        return ['current'] + version_names

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
        department_name_raw = self.parm('department').eval()
        # Handle 'from_context' special value
        if department_name_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            return context.department_name
        # From settings
        department_names = self.list_department_names()
        if len(department_names) == 0:
            return None
        if len(department_name_raw) == 0:
            return department_names[0]
        if department_name_raw not in department_names:
            return None
        return department_name_raw

    def get_variant_name(self) -> str:
        """Get selected variant name, defaults to 'default'."""
        variant_names = self.list_variant_names()
        variant_name = self.parm('variant').eval()
        if not variant_name or variant_name not in variant_names:
            return 'default'
        return variant_name

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

    def set_version_name(self, version_name: str):
        version_names = self.list_version_names()
        if version_name not in version_names:
            return
        self.parm('version').set(version_name)

    def set_include_layerbreak(self, include_layerbreak: bool):
        self.parm('include_layerbreak').set(int(include_layerbreak))

    def _update_labels(self):
        """Update label parameters to show current entity selection."""
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

    def _initialize(self):
        """Initialize node with defaults from workfile context and update labels."""
        file_path = Path(hou.hipFile.path())
        context = get_workfile_context(file_path)

        # If no context, set first available entity
        if context is None:
            entity_uris = self.list_entity_uris()
            if len(entity_uris) > 1:  # Skip 'from_context'
                self.set_entity_uri(Uri.parse_unsafe(entity_uris[1]))

        # Update labels to show resolved values
        self._update_labels()

    def execute(self):
        self._update_labels()
        return self._import_layer()

    def _get_layer_file_name(self) -> str | None:
        """Determine the layer file name."""
        version_name = self.get_version_name()
        if version_name is None:
            return None

        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return None

        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        if department_name is None:
            return None

        # Import layer file (assets + cameras, lights, volumes, etc. all in one)
        return get_layer_file_name(entity_uri, variant_name, department_name, version_name)

    def _import_layer(self):
        """Unified import method for both assets and shots."""
        native = self.native()
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        version_name = self.get_version_name()

        if entity_uri is None:
            ns.set_node_comment(native, "Bypassed: No entity selected")
            native.bypass(True)
            return
        if department_name is None:
            ns.set_node_comment(native, "Bypassed: No department selected")
            native.bypass(True)
            return
        if version_name is None:
            ns.set_node_comment(native, "Bypassed: No version selected")
            native.bypass(True)
            return

        # Get layer file name
        layer_file_name = self._get_layer_file_name()
        if layer_file_name is None:
            self.parm('import_enable1').set(0)
            self.parm('import_enable2').set(0)
            self.parm('bypass_input').set(0)
            return

        # Import shared layer (index 1) - only if entity has multiple variants
        shared_exists = False
        if len(list_variants(entity_uri)) > 1:
            shared_file_path = latest_shared_export_file_path(entity_uri, department_name)
            shared_exists = shared_file_path is not None and shared_file_path.exists()
            if shared_exists:
                self.parm('import_filepath1').set(path_str(shared_file_path))

        self.parm('import_enable1').set(1 if shared_exists else 0)

        # Get version path for variant layer
        export_uri = get_export_uri(entity_uri, variant_name, department_name) / version_name
        version_path = api.storage.resolve(export_uri)
        variant_file_path = version_path / layer_file_name

        # Import variant layer (index 2)
        variant_exists = variant_file_path.exists()
        self.parm('import_enable2').set(1 if variant_exists else 0)
        self.parm('import_filepath2').set(path_str(variant_file_path))

        # Enable bypass if either layer exists
        self.parm('bypass_input').set(1 if (shared_exists or variant_exists) else 0)

        # Update version label
        self.parm('version_label').set(version_name)

        # Generate metadata update script from context.json
        context_path = version_path / 'context.json'
        context_data = None
        layer_info = None
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

        # Set success comment with import metadata
        if layer_info is not None:
            timestamp = layer_info.get('timestamp', '')
            user = layer_info.get('user', '')
            if timestamp and user:
                ns.set_node_comment(native, f"Imported: {version_name}\n{timestamp}\nby {user}")
            else:
                ns.set_node_comment(native, f"Imported: {version_name}")
        else:
            ns.set_node_comment(native, f"Imported: {version_name}")

    def open_location(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        if department_name is None:
            return

        export_path = latest_export_path(entity_uri, variant_name, department_name)
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
    # Set node style
    set_style(raw_node)

    # Validate node type
    raw_node_type = raw_node.type()
    if raw_node_type is None:
        return
    node_type = ns.find_node_type('import_layer', 'Lop')
    if node_type is None:
        return
    if raw_node_type != node_type:
        return

    node = ImportLayer(raw_node)
    node._initialize()

def execute():
    raw_node = hou.pwd()
    node = ImportLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ImportLayer(raw_node)
    node.open_location()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblehead.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportLayer(raw_node)

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
            node.execute()
