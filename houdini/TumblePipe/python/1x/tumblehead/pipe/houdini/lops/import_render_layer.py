from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.shots import list_render_layers
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    get_render_layer_export_file_path
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/import_render_layer')

class ImportRenderLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_shot_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return list(shot_entities)

    def list_department_names(self):
        shot_departments = list_departments('shots')
        if len(shot_departments) == 0: return []
        shot_department_names = [dept.name for dept in shot_departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in shot_department_names
        ]

    def list_render_layer_names(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return []
        return list_render_layers(shot_uri)
    
    def list_version_names(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        render_layer_name = self.get_render_layer_name()
        if render_layer_name is None: return []
        export_uri = (
            Uri.parse_unsafe('export:/') /
            shot_uri.segments /
            'render_layers' /
            department_name /
            render_layer_name
        )
        layer_path = api.storage.resolve(export_uri)
        if not layer_path.exists(): return []
        version_paths = list_version_paths(layer_path)
        version_names = [path.name for path in version_paths]
        return version_names

    def get_shot_uri(self) -> Uri | None:
        shot_uris = self.list_shot_uris()
        if len(shot_uris) == 0: return None
        shot_uri_raw = self.parm('shot').eval()
        if len(shot_uri_raw) == 0: return shot_uris[0]
        shot_uri = Uri.parse_unsafe(shot_uri_raw)
        if shot_uri not in shot_uris: return None
        return shot_uri
    
    def get_department_name(self):
        department_names = self.list_department_names()
        if len(department_names) == 0: return None
        department_name = self.parm('department').eval()
        if len(department_name) == 0: return department_names[0]
        if department_name not in department_names: return None
        return department_name
    
    def get_render_layer_name(self):
        render_layer_names = self.list_render_layer_names()
        if len(render_layer_names) == 0: return None
        render_layer_name = self.parm('layer').eval()
        if len(render_layer_name) == 0: return render_layer_names[0]
        if render_layer_name not in render_layer_names: return None
        return render_layer_name

    def get_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0: return version_names[-1]
        if version_name == 'latest': return version_names[-1]
        if version_name not in version_names: return None
        return version_name

    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if shot_uri not in shot_uris: return
        self.parm('shot').set(str(shot_uri))
    
    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)
    
    def set_render_layer_name(self, render_layer_name):
        render_layer_names = self.list_render_layer_names()
        if render_layer_name not in render_layer_names: return
        self.parm('layer').set(render_layer_name)
    
    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)
    
    def latest(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return
        self.set_version_name(version_names[-1])

    def execute(self):

        # Parameters
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        render_layer_name = self.get_render_layer_name()
        version_name = self.get_version_name()

        # Check parameters
        if shot_uri is None: return
        if department_name is None: return
        if render_layer_name is None: return
        if version_name is None: return

        # Get input file path
        input_file_path = get_render_layer_export_file_path(
            shot_uri,
            department_name,
            render_layer_name,
            version_name
        )

        # Import layer file
        self.parm('import_enable1').set(1 if input_file_path.exists() else 0)
        self.parm('import_filepath1').set(path_str(input_file_path))

        # Update the version label on the node UI
        self.parm('version_label').set(version_name)

def create(scene, name):
    node_type = ns.find_node_type('import_render_layer', 'Lop')
    assert node_type is not None, 'Could not find import_render_layer node type'
    native = scene.node(name)
    if native is not None: return ImportRenderLayer(native)
    return ImportRenderLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('import_render_layer', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ImportRenderLayer(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return

    # Set the default values from context
    node.set_shot_uri(context.entity_uri)

def execute():
    raw_node = hou.pwd()
    node = ImportRenderLayer(raw_node)
    node.execute()