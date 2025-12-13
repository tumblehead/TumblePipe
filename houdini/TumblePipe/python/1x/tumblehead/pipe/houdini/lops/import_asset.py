from pathlib import Path

import hou

from tumblehead.api import default_client, path_str
from tumblehead.util.uri import Uri
from tumblehead.util.io import load_json
from tumblehead.config.department import list_departments
from tumblehead.config.variants import list_variants
from tumblehead.util import result
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.util import uri_to_metadata_prim_path
from tumblehead.pipe.paths import (
    get_workfile_context,
    get_latest_staged_file_path,
    get_staged_file_path,
    current_staged_file_path,
    list_version_paths
)

api = default_client()


def _metadata_script(
    asset_uri: Uri,
    shot_uri: Uri | None = None,
    shot_department: str | None = None
) -> str:
    """
    Generate Python script for creating asset metadata prim.

    If shot_uri and shot_department are provided, adds a shot department
    entry to the inputs array to track which department introduced this asset.
    """
    metadata_prim_path = uri_to_metadata_prim_path(asset_uri)
    entity_name = asset_uri.segments[-1]

    # Build initial inputs list
    inputs_str = '[]'
    if shot_uri is not None and shot_department is not None:
        # Add shot department entry to track source
        inputs_str = f"[{{'uri': '{str(shot_uri)}', 'department': '{shot_department}', 'version': 'initial'}}]"

    script = f'''import hou

from tumblehead.pipe.houdini import util

node = hou.pwd()
stage = node.editableStage()

# Create metadata prim
metadata_path = "{metadata_prim_path}"
prim = stage.DefinePrim(metadata_path, "Scope")

# Set metadata
metadata = {{
    'uri': '{str(asset_uri)}',
    'instance': '{entity_name}',
    'inputs': {inputs_str}
}}
util.set_metadata(prim, metadata)
'''
    return script

class ImportAsset(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        return [entity.uri for entity in asset_entities]

    def list_department_names(self):
        return [d.name for d in list_departments('assets') if d.publishable]

    def list_version_names(self) -> list[str]:
        """List available staged versions including 'latest' and 'current'."""
        asset_uri = self.get_asset_uri()
        if asset_uri is None:
            return ['latest', 'current']

        # Get staged directory
        staged_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / '_staged'
        staged_path = api.storage.resolve(staged_uri)

        # Get versioned directories (v0001, v0002, etc.)
        version_paths = list_version_paths(staged_path)
        version_names = [vp.name for vp in version_paths]

        # Add special options at the beginning
        return ['latest', 'current'] + version_names

    def list_variant_names(self) -> list[str]:
        """List available variant names for current entity."""
        asset_uri = self.get_asset_uri()
        if asset_uri is None:
            return ['default']
        variants = list_variants(asset_uri)
        if not variants:
            return ['default']
        # Ensure 'default' is always first
        if 'default' in variants:
            variants.remove('default')
            variants.insert(0, 'default')
        return variants

    def get_variant_name(self) -> str:
        """Get selected variant name, defaults to 'default'."""
        variant_names = self.list_variant_names()
        variant_name = self.parm('variant').eval()
        if not variant_name or variant_name not in variant_names:
            return 'default'
        return variant_name

    def set_variant_name(self, variant_name: str):
        """Set variant name."""
        self.parm('variant').set(variant_name)

    def get_version_name(self) -> str:
        """Get selected version name. Default is 'latest'."""
        version_name = self.parm('version').eval()
        if len(version_name) == 0:
            return 'latest'  # Default to latest
        return version_name

    def get_asset_uri(self) -> Uri | None:
        asset_uris = self.list_asset_uris()
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm('entity').eval()
        if len(asset_uri_raw) == 0: return asset_uris[0]
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in asset_uris: return None
        return asset_uri
    
    def get_exclude_department_names(self):
        return list(filter(len, self.parm('departments').eval().split()))

    def get_department_names(self):
        department_names = self.list_department_names()
        if len(department_names) == 0: return []
        exclude_department_names = self.get_exclude_department_names()
        return [
            department_name
            for department_name in department_names
            if department_name not in exclude_department_names
        ]
    
    def get_include_layerbreak(self):
        return bool(self.parm('include_layerbreak').eval())

    def set_asset_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('entity').set(str(asset_uri))
    
    def set_exclude_department_names(self, exclude_department_names):
        department_names = self.list_department_names()
        self.parm('departments').set(' '.join([
            department_name
            for department_name in exclude_department_names
            if department_name in department_names
        ]))

    def set_include_layerbreak(self, include_layerbreak):
        self.parm('include_layerbreak').set(int(include_layerbreak))

    def _update_labels(self):
        """Update label parameters to show current entity selection."""
        entity_uri = self.get_asset_uri()
        if entity_uri:
            self.parm('entity_label').set(str(entity_uri))
        else:
            self.parm('entity_label').set('none')

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name()
        if version_name == 'latest':
            version_names = self.list_version_names()
            actual_versions = [v for v in version_names if v not in ('latest', 'current')]
            if actual_versions:
                version_name = actual_versions[-1]
        self.parm('version_label').set(version_name)

    def execute(self):
        self._update_labels()
        asset_uri = self.get_asset_uri()
        if asset_uri is None:
            return result.Value(None)

        # Get variant and staged file path based on version selection
        variant_name = self.get_variant_name()
        version_name = self.get_version_name()
        if version_name == 'latest':
            staged_file_path = get_latest_staged_file_path(asset_uri, variant_name)
        elif version_name == 'current':
            staged_file_path = current_staged_file_path(asset_uri, variant_name)
        else:
            staged_file_path = get_staged_file_path(asset_uri, version_name, variant_name)

        if staged_file_path is None:
            raise FileNotFoundError(f"No staged build found for {asset_uri}")
        if not staged_file_path.exists():
            raise FileNotFoundError(f"Staged file not found: {staged_file_path}")

        # Set import node filepath
        self.parm('import_filepath1').set(path_str(staged_file_path))

        # Update version label with resolved folder name
        resolved_version = staged_file_path.parent.name
        self.parm('version_label').set(resolved_version)

        # Get shot context if we're in a shot workfile
        shot_uri = None
        shot_department = None
        file_path = Path(hou.hipFile.path())
        workfile_context = get_workfile_context(file_path)
        if workfile_context is not None:
            if str(workfile_context.entity_uri).startswith('entity:/shots/'):
                shot_uri = workfile_context.entity_uri
                shot_department = workfile_context.department_name

        # Generate and set metadata script
        script = _metadata_script(asset_uri, shot_uri, shot_department)
        self.parm('metadata_python').set(script)

        return result.Value(None)

def create(scene, name):
    node_type = ns.find_node_type('import_asset', 'Lop')
    assert node_type is not None, 'Could not find import_asset node type'
    native = scene.node(name)
    if native is not None: return ImportAsset(native)
    return ImportAsset(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):
    set_style(raw_node)
    node = ImportAsset(raw_node)
    # Set entity to first available
    asset_uris = node.list_asset_uris()
    if asset_uris:
        node.set_asset_uri(asset_uris[0])
    node._update_labels()

def execute():
    raw_node = hou.pwd()
    node = ImportAsset(raw_node)
    node.execute()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblehead.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportAsset(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=False,
        current_selection=node.parm('entity').eval(),
        title="Select Asset",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node.execute()