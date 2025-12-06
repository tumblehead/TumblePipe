from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.variants import list_variants
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    get_variant_export_file_path,
    latest_variant_export_path
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/import_variant')

class ImportVariant(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_entity_uris(self) -> list[Uri]:
        """List all shot URIs (import_variant is shot-only)."""
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return [entity.uri for entity in shot_entities]

    def list_department_names(self):
        shot_departments = list_departments('shots')
        if len(shot_departments) == 0: return []
        shot_department_names = [dept.name for dept in shot_departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        if default_values is None:
            # No defaults configured, return all shot departments
            return shot_department_names
        configured_depts = default_values.get('departments')
        if configured_depts is None:
            return shot_department_names
        return [
            department_name
            for department_name in configured_depts
            if department_name in shot_department_names
        ]

    def list_variant_names(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None: return []
        return list_variants(entity_uri)

    def list_version_names(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        variant_name = self.get_variant_name()
        if variant_name is None: return []
        export_uri = (
            Uri.parse_unsafe('export:/') /
            entity_uri.segments /
            'variants' /
            variant_name /
            department_name
        )
        layer_path = api.storage.resolve(export_uri)
        if not layer_path.exists(): return []
        version_paths = list_version_paths(layer_path)
        version_names = [path.name for path in version_paths]
        return version_names

    def get_entity_uri(self) -> Uri | None:
        entity_uris = self.list_entity_uris()
        if len(entity_uris) == 0: return None
        entity_uri_raw = self.parm('entity').eval()
        if len(entity_uri_raw) == 0: return entity_uris[0]
        entity_uri = Uri.parse_unsafe(entity_uri_raw)
        if entity_uri not in entity_uris: return None
        return entity_uri

    def get_department_name(self):
        department_names = self.list_department_names()
        if len(department_names) == 0: return None
        department_name = self.parm('department').eval()
        if len(department_name) == 0: return department_names[0]
        if department_name not in department_names: return None
        return department_name

    def get_variant_name(self):
        variant_names = self.list_variant_names()
        if len(variant_names) == 0: return None
        variant_name = self.parm('variant').eval()
        if len(variant_name) == 0: return variant_names[0]
        if variant_name not in variant_names: return None
        return variant_name

    def get_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0: return version_names[-1]
        if version_name == 'latest': return version_names[-1]
        if version_name not in version_names: return None
        return version_name

    def set_entity_uri(self, entity_uri: Uri):
        entity_uris = self.list_entity_uris()
        if entity_uri not in entity_uris: return
        self.parm('entity').set(str(entity_uri))

    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)

    def set_variant_name(self, variant_name):
        variant_names = self.list_variant_names()
        if variant_name not in variant_names: return
        self.parm('variant').set(variant_name)

    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)

    def execute(self):

        # Parameters
        entity_uri = self.get_entity_uri()
        department_name = self.get_department_name()
        variant_name = self.get_variant_name()
        version_name = self.get_version_name()

        # Check parameters
        if entity_uri is None: return
        if department_name is None: return
        if variant_name is None: return
        if version_name is None: return

        # Get input file path
        input_file_path = get_variant_export_file_path(
            entity_uri,
            variant_name,
            department_name,
            version_name
        )

        # Import layer file
        self.parm('import_enable1').set(1 if input_file_path.exists() else 0)
        self.parm('import_filepath1').set(path_str(input_file_path))

        # Update the version label on the node UI
        self.parm('version_label').set(version_name)

    def open_location(self):
        """Open import location in file browser."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None: return
        department_name = self.get_department_name()
        if department_name is None: return
        variant_name = self.get_variant_name()
        if variant_name is None: return

        export_path = latest_variant_export_path(
            entity_uri,
            variant_name,
            department_name
        )
        if export_path is None: return
        if not export_path.exists(): return
        hou.ui.showInFileBrowser(path_str(export_path))

def create(scene, name):
    node_type = ns.find_node_type('import_variant', 'Lop')
    assert node_type is not None, 'Could not find import_variant node type'
    native = scene.node(name)
    if native is not None: return ImportVariant(native)
    return ImportVariant(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('import_variant', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ImportVariant(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None:
        # No context available - still set defaults
        pass

    # Always set default entity (ensures department menu works correctly)
    entity_uris = node.list_entity_uris()
    if entity_uris:
        node.set_entity_uri(entity_uris[0])

    # Set from context if available
    if context is not None:
        node.set_entity_uri(context.entity_uri)

def execute():
    raw_node = hou.pwd()
    node = ImportVariant(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ImportVariant(raw_node)
    node.open_location()
