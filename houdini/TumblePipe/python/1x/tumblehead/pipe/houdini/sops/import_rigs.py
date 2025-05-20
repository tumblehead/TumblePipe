import hou

from tumblehead.api import default_client
from tumblehead.util import result
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.sops import import_rig

api = default_client()

def _clear_scene(dive_node, output_node):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than inputs and outputs
    for node in dive_node.children():
        if node.name() == output_node.name(): continue
        node.destroy()

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _insert(data, path, value):
    for key in path[:-1]:
        data = data.setdefault(key, {})
    data[path[-1]] = value

class ImportRigs(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def list_category_names(self):
        return api.config.list_category_names()
    
    def list_asset_names(self, index):
        category_name = self.get_category_name(index)
        if category_name is None: return []
        count = self.parm('rig_imports').eval()
        available_asset_names = api.config.list_asset_names(category_name)
        other_asset_names = set()
        for other_index in range(1, count + 1):
            if other_index == index: continue
            other_category_name = self.get_category_name(other_index)
            if other_category_name is None: continue
            if other_category_name != category_name: continue
            other_asset_name = self.parm(f'asset{other_index}').eval()
            if len(other_asset_name) == 0: continue
            if other_asset_name not in available_asset_names: continue
            other_asset_names.add(other_asset_name)
        return [
            asset_name
            for asset_name in available_asset_names
            if asset_name not in other_asset_names
        ]
    
    def get_category_name(self, index):
        category_names = self.list_category_names()
        if len(category_names) == 0: return None
        category_name = self.parm(f'category{index}').eval()
        if len(category_name) == 0 or category_name not in category_names:
            category_name = category_names[0]
            self.parm(f'category{index}').set(category_name)
        return category_name

    def get_asset_name(self, index):
        asset_names = self.list_asset_names(index)
        if len(asset_names) == 0: return None
        asset_name = self.parm(f'asset{index}').eval()
        if len(asset_name) == 0 or asset_name not in asset_names:
            asset_name = asset_names[0]
            self.parm(f'asset{index}').set(asset_name)
        return asset_name

    def get_instances(self, index):
        return self.parm(f'instances{index}').eval()

    def get_rig_imports(self):
        rig_imports = {}
        count = self.parm('rig_imports').eval()
        for index in range(1, count + 1):
            category_name = self.get_category_name(index)
            if category_name is None: continue
            asset_name = self.get_asset_name(index)
            if asset_name is None: continue
            instances = self.get_instances(index)
            _insert(rig_imports, [category_name, asset_name], instances)
        return rig_imports

    def set_category_name(self, index, category_name):
        category_names = self.list_category_names()
        if category_name not in category_names: return
        self.parm(f'category{index}').set(category_name)
    
    def set_asset_name(self, index, asset_name):
        asset_names = self.list_asset_names(index)
        if asset_name not in asset_names: return
        self.parm(f'asset{index}').set(asset_name)
    
    def set_instances(self, index, instances):
        self.parm(f'instances{index}').set(instances)
    
    def set_rig_imports(self, rig_imports):
        self.parm('rig_imports').set(0)
        for category_name, asset_instances in rig_imports.items():
            for asset_name, instances in asset_instances.items():
                if instances == 0: continue
                index = self.parm('rig_imports').eval() + 1
                self.parm('rig_imports').set(index)
                self.set_category_name(index, category_name)
                self.set_asset_name(index, asset_name)
                self.set_instances(index, instances)
    
    def execute(self):

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        _clear_scene(dive_node, output_node)

        # Parameters
        rig_imports = self.get_rig_imports()

        # Build asset nodes
        prev_node = None
        for category_name, rig_instances in rig_imports.items():
            for asset_name, instances in rig_instances.items():
                if instances == 0: continue

                # Import the rig
                rig_node = import_rig.create(dive_node, f'{category_name}_{asset_name}_import')
                rig_node.set_category_name(category_name)
                rig_node.set_asset_name(asset_name)
                rig_node.set_instances(instances)
                rig_node.latest()
                rig_node.execute()

                # Connect the rig
                if prev_node is not None:
                    _connect(prev_node, rig_node.native())
                prev_node = rig_node.native()
        
        # Connect to output
        if prev_node is not None:
            _connect(prev_node, output_node)

        # Layout the nodes
        dive_node.layoutChildren()

        # Done
        return result.Value(None)

def clear_cache():
    import_rig.clear_cache()

def create(scene, name):
    node_type = ns.find_node_type('import_rigs', 'Sop')
    assert node_type is not None, 'Could not find import_rigs node type'
    native = scene.node(name)
    if native is not None: return ImportRigs(native)
    return ImportRigs(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def on_loaded(raw_node):
    
    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ImportRigs(raw_node)
    node.execute()