from pathlib import Path

import hou

from tumblehead.api import default_client
from tumblehead.util.io import load_json
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    ShotEntity
)

api = default_client()

def _entity_from_context_json():

    # Path to current workfilw
    file_path = Path(hou.hipFile.path())
    if not file_path.exists(): return None

    # Look for context.json in the workfile directory
    context_json_path = file_path.parent / "context.json"
    if not context_json_path.exists(): return None
    context_data = load_json(context_json_path)
    if context_data is None: return None

    # Parse the loaded context
    if context_data.get('entity') != 'shot': return None
    return ShotEntity(
        sequence_name = context_data['sequence'],
        shot_name = context_data['shot'],
        department_name = context_data['department']
    )

class Animate(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_sequence_names(self):
        return api.config.list_sequence_names()

    def list_shot_names(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return []
        return api.config.list_shot_names(sequence_name)
    
    def get_entity_source(self):
        return self.parm('entity_source').eval()
    
    def get_sequence_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.sequence_name
            case 'from_settings':
                sequence_names = self.list_sequence_names()
                if len(sequence_names) == 0: return None
                sequence_name = self.parm('sequence').eval()
                if len(sequence_name) == 0: return sequence_names[0]
                if sequence_name not in sequence_names: return None
                return sequence_name
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

    def get_shot_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.shot_name
            case 'from_settings':
                shot_names = self.list_shot_names()
                if len(shot_names) == 0: return None
                shot_name = self.parm('shot').eval()
                if len(shot_name) == 0: return shot_names[0]
                if shot_name not in shot_names: return None
                return shot_name
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')
    
    def set_entity_source(self, entity_source):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources: return
        self.parm('entity_source').set(entity_source)
    
    def set_sequence_name(self, sequence_name):
        sequence_names = self.list_sequence_names()
        if sequence_name not in sequence_names: return
        self.parm('sequence').set(sequence_name)
    
    def set_shot_name(self, shot_name):
        shot_names = self.list_shot_names()
        if shot_name not in shot_names: return
        self.parm('shot').set(shot_name)

def create(scene, name):
    animate_type = ns.find_node_type('animate', 'Lop')
    assert animate_type is not None, 'Could not find animate node type'
    native = scene.node(name)
    if native is not None: return Animate(native)
    return Animate(scene.createNode(animate_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DIVE)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Change entity source to settings if we have no context
    entity = _entity_from_context_json()
    if entity is not None: return
    node = Animate(raw_node)
    node.set_entity_source('from_settings')