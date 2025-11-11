import hou

from tumblehead.api import default_client
from tumblehead.util import result
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.lops import import_asset

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

def _update_script(instances):

    # Prepare script
    script = [
        'import hou',
        '',
        'from tumblehead.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        ''
    ]

    # Update metadata instance names
    for prim_path, instance_name in instances:
        prim_var = f'prim_{instance_name}'
        metadata_var = f'metadata_{instance_name}'
        script += [
            f'{prim_var} = root.GetPrimAtPath("{prim_path}")',
            f'{metadata_var} = util.get_metadata({prim_var})',
            f'{metadata_var}["instance"] = "{instance_name}"',
            f'util.set_metadata({prim_var}, {metadata_var})',
            ''
        ]
    
    # Done
    return script

class ImportAssets(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def list_category_names(self):
        return [
            category_name
            for category_name in api.config.list_category_names()
            if len(api.config.list_asset_names(category_name)) > 0
        ]

    def list_asset_names(self, index):
        category_name = self.get_category_name(index)
        if category_name is None: return []
        count = self.parm('asset_imports').eval()
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
    
    def list_department_names(self):
        asset_department_names = api.config.list_asset_department_names()
        if len(asset_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/import_assets')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in asset_department_names
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

    def get_asset_imports(self):
        asset_imports = {}
        count = self.parm('asset_imports').eval()
        for index in range(1, count + 1):
            category_name = self.get_category_name(index)
            if category_name is None: continue
            asset_name = self.get_asset_name(index)
            if asset_name is None: continue
            instances = self.get_instances(index)
            _insert(asset_imports, [category_name, asset_name], instances)
        return asset_imports
    
    def get_include_layerbreak(self):
        return bool(self.parm('include_layerbreak').eval())
    
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
    
    def set_exclude_department_names(self, exclude_department_names):
        department_names = self.list_department_names()
        self.parm('departments').set(' '.join([
            department_name
            for department_name in exclude_department_names
            if department_name in department_names
        ]))
    
    def set_include_layerbreak(self, include_layerbreak):
        self.parm('include_layerbreak').set(int(include_layerbreak))
    
    def execute(self):

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        switch_node = context.node('switch')
        output_node = dive_node.node('output')
        _clear_scene(dive_node, output_node)

        # Parameters
        asset_imports = self.get_asset_imports()
        exclude_department_names = self.get_exclude_department_names()
        include_layerbreak = self.get_include_layerbreak()

        # Build the merge node
        merge_node = dive_node.createNode('merge', 'merge')
        merge_node.parm('mergestyle').set('separate')

        # Build asset nodes
        script_args = []
        for category_name, asset_instances in asset_imports.items():
            for asset_name, instances in asset_instances.items():
                if instances == 0: continue

                # Import the asset
                asset_node = import_asset.create(
                    dive_node,
                    f'{category_name}_{asset_name}_import'
                )
                asset_node.set_category_name(category_name)
                asset_node.set_asset_name(asset_name)
                asset_node.set_exclude_department_names(
                    exclude_department_names
                )
                asset_node.set_include_layerbreak(include_layerbreak)
                asset_node.execute()

                # In the case of one instance
                if instances == 1:
                    _connect(asset_node.native(), merge_node)
                    continue

                # Duplicate the asset
                duplicate_node = dive_node.createNode(
                    'duplicate',
                    f'{category_name}_{asset_name}_duplicate'
                )
                duplicate_node.parm('sourceprims').set(
                    f'/{category_name}'
                    f'/{asset_name}'
                )
                duplicate_node.parm('ncy').set(instances)
                duplicate_node.parm('duplicatename').set(
                    '`@srcname``@copy`'
                )
                _connect(asset_node.native(), duplicate_node)

                # Duplicate the metadata
                duplicate_metadata_node = dive_node.createNode(
                    'duplicate',
                    f'{category_name}_{asset_name}_metadata_duplicate'
                )
                duplicate_metadata_node.parm('sourceprims').set(
                    '/METADATA'
                    '/asset'
                    f'/{category_name}'
                    f'/{asset_name}'
                )
                duplicate_metadata_node.parm('ncy').set(instances)
                duplicate_metadata_node.parm('duplicatename').set(
                    '`@srcname``@copy`'
                )
                duplicate_metadata_node.parm('parentprimtype').set('')
                _connect(duplicate_node, duplicate_metadata_node)
                _connect(duplicate_metadata_node, merge_node)

                # Update the script arguments
                for index in range(instances):
                    instance_name = f'{asset_name}{index}'
                    script_args.append((
                        f'/METADATA/asset/{category_name}/{instance_name}',
                        instance_name
                    ))

        # Update the instances names in the metadata
        python_node = dive_node.createNode(
            'pythonscript',
            'metadata_update'
        )
        python_node.parm('python').set(
            '\n'.join(_update_script(script_args))
        )
        python_node.setInput(0, merge_node)
        output_node.setInput(0, python_node)

        # Enable or disable layerbreak
        switch_node.parm('input').set(1 if include_layerbreak else 0)

        # Layout the nodes
        dive_node.layoutChildren()

        # Done
        return result.Value(None)

def create(scene, name):
    node_type = ns.find_node_type('import_assets', 'Lop')
    assert node_type is not None, 'Could not find import_assets node type'
    native = scene.node(name)
    if native is not None: return ImportAssets(native)
    return ImportAssets(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ImportAssets(raw_node)
    node.execute()