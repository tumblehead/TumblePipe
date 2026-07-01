import hou

from tumblepipe.api import api
from tumblepipe.util.io import load_json, store_json
from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns

class RenderSettings(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_preset_paths(self):
        preset_path = api.storage.resolve(Uri.parse_unsafe('preset:/houdini/lops/render_settings'))
        return {
            preset_path.stem: preset_path
            for preset_path in preset_path.glob('*.json')
        }
    
    def list_preset_names(self):
        preset_names = list(self.list_preset_paths().keys())
        if 'default' not in preset_names:
            preset_names.insert(0, 'default')
        return preset_names
    
    def get_preset_name(self):
        return self.parm('preset').eval()
    
    def set_save_lock(self, state):
        self.parm('unlock_save').set(not state)
    
    def load(self):
        preset_name = self.get_preset_name()
        preset_path = api.storage.resolve(f'preset:/houdini/lops/render_settings/{preset_name}.json')
        if not preset_path.exists(): return
        self.native().setParmsFromData(load_json(preset_path))
        self.set_save_lock(True)

    def save(self):
        preset_name = self.get_preset_name()
        preset_path = api.storage.resolve(f'preset:/houdini/lops/render_settings/{preset_name}.json')
        preset_path.parent.mkdir(parents=True, exist_ok=True)
        store_json(preset_path, self.native().parmsAsData())
        self.set_save_lock(True)

def create(scene, name):
    return ns.create_node(scene, name, RenderSettings, 'render_settings')

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def load():
    raw_node = hou.pwd()
    node = RenderSettings(raw_node)
    node.load()

def save():
    raw_node = hou.pwd()
    node = RenderSettings(raw_node)
    node.save()