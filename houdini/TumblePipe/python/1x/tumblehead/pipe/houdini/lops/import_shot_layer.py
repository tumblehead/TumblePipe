from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.cache import Cache
from tumblehead.util.io import load_json
import tumblehead.pipe.context as ctx
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    ShotContext
)

api = default_client()

CACHE_VERSION_NAMES = Cache()
CACHE_INSTANCE_NAMES = Cache()

class ImportShotLayer(ns.Node):
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
        default_values = api.config.resolve('defaults:/houdini/lops/import_shot_layer')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in shot_department_names
        ]
    
    def list_version_names(self):
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        layer_key = (sequence_name, shot_name, department_name)
        if CACHE_VERSION_NAMES.contains(layer_key):
            return CACHE_VERSION_NAMES.lookup(layer_key).copy()
        layer_path = api.storage.resolve(f'export:/shots/{sequence_name}/{shot_name}/{department_name}')
        version_paths = list_version_paths(layer_path)
        version_names = [path.name for path in version_paths]
        CACHE_VERSION_NAMES.insert(layer_key, version_names)
        return version_names
    
    def _update_instance_names(self):
        
        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        version_name = self.get_version_name()

        # Check cache
        asset_key = (sequence_name, shot_name, department_name, version_name)
        if CACHE_INSTANCE_NAMES.contains(asset_key): return
        
        # Load context
        layer_path = api.storage.resolve(
            f'export:/shots/{sequence_name}/{shot_name}'
            f'/{department_name}/{version_name}'
        )
        context_file_path = layer_path / 'context.json'
        if not context_file_path.exists(): return
        context = load_json(context_file_path)
        layer_info = ctx.find_output(context,
            context = 'shot',
            sequence = sequence_name,
            shot = shot_name,
            layer = department_name
        )
        assert layer_info is not None, f'Could not find layer info for {sequence_name} {shot_name} {department_name}'
        parameters = layer_info['parameters']

        # Find assets instances
        asset_instances = dict()
        for asset_datum in parameters['assets']:
            category_name = asset_datum['category']
            asset_name = asset_datum['asset']
            instance_name = asset_datum['instance']
            if category_name not in asset_instances:
                asset_instances[category_name] = dict()
            if asset_name not in asset_instances[category_name]:
                asset_instances[category_name][asset_name] = list()
            asset_instances[category_name][asset_name].append(instance_name)
        
        # Store in cache
        CACHE_INSTANCE_NAMES.insert(asset_key, asset_instances)
    
    def list_category_names(self):
        self._update_instance_names()
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        version_name = self.get_version_name()
        asset_key = (sequence_name, shot_name, department_name, version_name)
        if not CACHE_INSTANCE_NAMES.contains(asset_key): return list()
        asset_instances = CACHE_INSTANCE_NAMES.lookup(asset_key)
        match self.get_stage_type():
            case 'asset': return list(asset_instances.keys())
            case _: return list()
    
    def list_item_names(self):
        self._update_instance_names()
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        version_name = self.get_version_name()
        asset_key = (sequence_name, shot_name, department_name, version_name)
        if not CACHE_INSTANCE_NAMES.contains(asset_key): return list()
        asset_instances = CACHE_INSTANCE_NAMES.lookup(asset_key)
        match self.get_stage_type():
            case 'asset':
                category_name = self.get_category_name()
                if category_name not in asset_instances: return list()
                return list(asset_instances[category_name].keys())
            case _:
                return list()
    
    def list_instance_names(self):
        self._update_instance_names()
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        version_name = self.get_version_name()
        asset_key = (sequence_name, shot_name, department_name, version_name)
        if not CACHE_INSTANCE_NAMES.contains(asset_key): return list()
        asset_instances = CACHE_INSTANCE_NAMES.lookup(asset_key)
        match self.get_stage_type():
            case 'asset':
                category_name = self.get_category_name()
                asset_name = self.get_item_name()
                if category_name not in asset_instances: return list()
                if asset_name not in asset_instances[category_name]: return list()
                return asset_instances[category_name][asset_name]
            case _:
                return list()
    
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

    def get_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0: return version_names[0]
        if version_name not in version_names: return None
        return version_name
    
    def get_stage_type(self):
        return self.parm('stage_type').eval()
    
    def get_category_name(self):
        category_names = self.list_category_names()
        if len(category_names) == 0: return None
        category_name = self.parm('category').eval()
        if len(category_name) == 0: return category_names[0]
        if category_name not in category_names: return None
        return category_name
    
    def get_item_name(self):
        asset_names = self.list_item_names()
        if len(asset_names) == 0: return None
        asset_name = self.parm('item').eval()
        if len(asset_name) == 0: return asset_names[0]
        if asset_name not in asset_names: return None
        return asset_name

    def get_instance_name(self):
        instance_names = self.list_instance_names()
        if len(instance_names) == 0: return None
        instance_name = self.parm('instance').eval()
        if len(instance_name) == 0: return instance_names[0]
        if instance_name not in instance_names: return None
        return instance_name

    def get_include_layerbreak(self):
        return bool(self.parm('include_layerbreak').eval())
    
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
    
    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)
    
    def set_stage_type(self, stage_type):
        match stage_type:
            case 'asset': self.parm('stage_type').set('asset')
            case 'cameras': self.parm('stage_type').set('cameras')
            case 'lights': self.parm('stage_type').set('lights')
            case 'collections': self.parm('stage_type').set('collections')
            case 'render': self.parm('stage_type').set('render')
            case 'scene': self.parm('stage_type').set('scene')
            case _: assert False, f'Invalid stage type {stage_type}'
    
    def set_category_name(self, category_name):
        category_names = self.list_category_names()
        if category_name not in category_names: return
        self.parm('category').set(category_name)
    
    def set_item_name(self, asset_name):
        asset_names = self.list_item_names()
        if asset_name not in asset_names: return
        self.parm('item').set(asset_name)
    
    def set_instance_name(self, instance_name):
        instance_names = self.list_instance_names()
        if instance_name not in instance_names: return
        self.parm('instance').set(instance_name)
    
    def set_include_layerbreak(self, include_layerbreak):
        self.parm('include_layerbreak').set(int(include_layerbreak))
    
    def latest(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return
        self.set_version_name(version_names[-1])

    def execute(self):

        # Get context
        context = self.native()
        import_node = context.node('import')
        switch_node = context.node('switch')
        bypass_node = context.node('bypass')

        # General parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        version_name = self.get_version_name()
        include_layerbreak = self.get_include_layerbreak()
        input_version_path = api.storage.resolve(
            f'export:/shots/{sequence_name}/{shot_name}'
            f'/{department_name}/{version_name}'
        )

        # Enable or disable layerbreak
        switch_node.parm('input').set(1 if include_layerbreak else 0)
        
        # Get layer file path
        def _get_layer_file_name():
            stage_type = self.get_stage_type()
            match stage_type:
                case 'asset':
                    category_name = self.get_category_name()
                    asset_name = self.get_item_name()
                    instance_name = self.get_instance_name()
                    return f'asset_{category_name}_{asset_name}_{instance_name}_{department_name}_{version_name}.usd'
                case 'cameras':
                    return f'cameras_{version_name}.usd'
                case 'lights':
                    return f'lights_{version_name}.usd'
                case 'collections':
                    return f'collections_{version_name}.usd'
                case 'render':
                    return f'render_{version_name}.usd'
                case 'scene':
                    return f'scene_{version_name}.usd'
                case _:
                    assert False, f'Invalid stage type {stage_type}'
        
        input_file_path = input_version_path / _get_layer_file_name()
        
        # Import layer file
        if input_file_path.exists():
            import_node.parm('filepath1').set(path_str(input_file_path))
            bypass_node.parm('input').set(1)
        else:
            bypass_node.parm('input').set(0)

def clear_cache():
    CACHE_VERSION_NAMES.clear()
    CACHE_INSTANCE_NAMES.clear()

def create(scene, name):
    node_type = ns.find_node_type('import_shot_layer', 'Lop')
    assert node_type is not None, 'Could not find import_shot_layer node type'
    native = scene.node(name)
    if native is not None: return ImportShotLayer(native)
    return ImportShotLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('import_shot_layer', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ImportShotLayer(raw_node)

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

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def latest():
    raw_node = hou.pwd()
    node = ImportShotLayer(raw_node)
    node.latest()

def execute():
    raw_node = hou.pwd()
    node = ImportShotLayer(raw_node)
    node.execute()