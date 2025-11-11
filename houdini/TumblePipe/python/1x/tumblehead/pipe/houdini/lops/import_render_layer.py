from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    ShotContext
)

api = default_client()

class ImportRenderLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_sequence_names(self):
        return api.config.list_sequence_names()
    
    def list_shot_names(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return []
        return api.config.list_shot_names(sequence_name)
    
    def list_department_names(self):
        shot_department_names = api.config.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/import_render_layer')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in shot_department_names
        ]
    
    def list_render_layer_names(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return []
        shot_name = self.get_shot_name()
        if shot_name is None: return []
        return api.config.list_render_layer_names(sequence_name, shot_name)
    
    def list_version_names(self):
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        render_layer_name = self.get_render_layer_name()
        layer_path = api.storage.resolve(
            f'export:/shots/{sequence_name}/{shot_name}/render_layers'
            f'/{department_name}/{render_layer_name}'
        )
        if not layer_path.exists(): return []
        version_paths = list_version_paths(layer_path)
        version_names = [path.name for path in version_paths]
        return version_names
    
    def get_sequence_name(self):
        sequence_names = self.list_sequence_names()
        if len(sequence_names) == 0: return None
        sequence_name = self.parm('sequence').eval()
        if len(sequence_name) == 0: return sequence_names[0]
        if sequence_name not in sequence_names: return None
        return sequence_name

    def get_shot_name(self):
        shot_names = self.list_shot_names()
        if len(shot_names) == 0: return None
        shot_name = self.parm('shot').eval()
        if len(shot_name) == 0: return shot_names[0]
        if shot_name not in shot_names: return None
        return shot_name
    
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
    
    def set_sequence_name(self, sequence_name):
        sequence_names = self.list_sequence_names()
        if sequence_name not in sequence_names: return
        self.parm('sequence').set(sequence_name)
    
    def set_shot_name(self, shot_name):
        shot_names = self.list_shot_names()
        if shot_name not in shot_names: return
        self.parm('shot').set(shot_name)
    
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
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        render_layer_name = self.get_render_layer_name()
        version_name = self.get_version_name()

        # Paths
        version_path = api.storage.resolve(
            f'export:/shots/{sequence_name}/{shot_name}/render_layers'
            f'/{department_name}/{render_layer_name}/{version_name}'
        )
        input_file_name = f'{sequence_name}_{shot_name}_{department_name}_{render_layer_name}_{version_name}.usd'
        input_file_path = version_path / input_file_name

        # Import layer file
        self.parm('import_enable1').set(1 if input_file_path.exists() else 0)
        self.parm('import_filepath1').set(path_str(input_file_path))

        # Update the version label on the node UI
        self.parm('version_label').set(f'v{version_name}')

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
    
    # Set the default values
    match context:
        case ShotContext(
            department_name,
            sequence_name,
            shot_name,
            version_name
            ):
            node.set_sequence_name(sequence_name)
            node.set_shot_name(shot_name)

def execute():
    raw_node = hou.pwd()
    node = ImportRenderLayer(raw_node)
    node.execute()