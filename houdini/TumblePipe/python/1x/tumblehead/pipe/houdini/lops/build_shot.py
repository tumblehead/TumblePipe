from pathlib import Path

import hou

from tumblehead.api import default_client
from tumblehead.config import FrameRange
from tumblehead.util.cache import Cache
from tumblehead.util.io import load_json
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.houdini.util as util
import tumblehead.pipe.context as ctx
from tumblehead.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    ShotContext
)
from tumblehead.pipe.houdini.lops import (
    import_asset_layer,
    import_kit_layer,
    import_shot_layer
)

class Mode:
    Latest = 'Latest'
    Strict = 'Strict'

api = default_client()

def _ensure_node(stage, kind, name):
    node = stage.node(name)
    if node is not None: return node
    return stage.createNode(kind, name)

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _clear_scene(dive_node, output_node):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than inputs and outputs
    for node in dive_node.children():
        if node.name() == output_node.name(): continue
        node.destroy()

def _context_from_workfile():
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return None
    if not isinstance(context, ShotContext): return None
    return context

def _valid_export(path):
    context_path = path / 'context.json'
    return context_path.exists()

def _resolve_versions_latest(
    sequence_name,
    shot_name,
    shot_departments,
    asset_departments,
    kit_departments
    ):

    # Helpers
    def _latest_shot_layer_paths(shot_departments):
        layer_data = dict()
        shot_assets = dict()
        shot_kits = dict()
        for department_name in shot_departments:

            # Find latest layer version
            layer_path = api.storage.resolve(f'export:/shots/{sequence_name}/{shot_name}/{department_name}')
            if not layer_path.exists(): continue
            version_paths = list(filter(
                _valid_export,
                list_version_paths(layer_path)
            ))
            if len(version_paths) == 0: continue
            latest_version_path = version_paths[-1]

            # Find shot info
            context_file_path = latest_version_path / 'context.json'
            context_data = load_json(context_file_path)
            layer_info = ctx.find_output(context_data,
                context = 'shot',
                sequence = sequence_name,
                shot = shot_name,
                layer = department_name
            )
            assert layer_info is not None, f'Could not find shot info for {sequence_name} {shot_name} {department_name}'
            
            # Find kits in layer
            for kit_datum in layer_info['parameters']['kits']:
                category_name = kit_datum['category']
                kit_name = kit_datum['kit']
                if category_name not in shot_kits: shot_kits[category_name] = set()
                shot_kits[category_name].add(kit_name)

            # Find assets in layer
            layer_assets = dict()
            for asset_datum in layer_info['parameters']['assets']:
                category_name = asset_datum['category']
                asset_name = asset_datum['asset']
                instance_name = asset_datum['instance']
                asset_key = (category_name, asset_name)
                if asset_key not in shot_assets: shot_assets[asset_key] = set()
                if asset_key not in layer_assets: layer_assets[asset_key] = set()
                shot_assets[asset_key].add(instance_name)
                layer_assets[asset_key].add(instance_name)
            
            # Store layer data
            layer_data[department_name] = (latest_version_path, layer_assets)

        # Done
        return layer_data, shot_kits, shot_assets
    
    def _latest_kit_layer_paths(kits, kit_departments):
        layer_data = dict()
        for category_name, kit_names in kits.items():
            for kit_name in kit_names:
                for department_name in kit_departments:

                    # Find latest layer version
                    layer_path = api.storage.resolve(f'export:/kits/{category_name}/{kit_name}/{department_name}')
                    if not layer_path.exists(): continue
                    version_paths = list(filter(
                        _valid_export,
                        list_version_paths(layer_path)
                    ))
                    if len(version_paths) == 0: continue
                    latest_version_path = version_paths[-1]
                    
                    # Store layer data
                    if department_name not in layer_data: layer_data[department_name] = dict()
                    layer_data[department_name][(category_name, kit_name)] = latest_version_path
        
        # Done
        return layer_data

    def _latest_asset_layer_paths(assets, asset_departments):
        layer_data = dict()
        for (category_name, asset_name) in assets.keys():
            for department_name in asset_departments:

                # Find latest layer version
                layer_path = api.storage.resolve(f'export:/assets/{category_name}/{asset_name}/{department_name}')
                if not layer_path.exists(): continue
                version_paths = list(filter(
                    _valid_export,
                    list_version_paths(layer_path)
                ))
                if len(version_paths) == 0: continue
                latest_version_path = version_paths[-1]
                
                # Store layer data
                if department_name not in layer_data: layer_data[department_name] = dict()
                layer_data[department_name][(category_name, asset_name)] = latest_version_path
        
        # Done
        return layer_data

    # Find latest paths
    shot_layer_paths, kits, assets = _latest_shot_layer_paths(shot_departments)
    kit_layer_paths = _latest_kit_layer_paths(kits, kit_departments)
    asset_layer_paths = _latest_asset_layer_paths(assets, asset_departments)
    
    # Done
    return dict(
        kits = kits,
        assets = assets,
        shot_layers = shot_layer_paths,
        kit_layers = kit_layer_paths,
        asset_layers = asset_layer_paths
    )

def _resolve_versions_strict(sequence_name, shot_name):
    assert False, 'Not implemented'

def _get(context, *keys):
    current = context
    for key in keys:
        if not isinstance(current, dict): return None
        if key not in current: return None
        current = current[key]
    return current

def _update_script(instances):

    # Prepare script
    script = [
        'import json',
        'import pxr',
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

CACHE_VERSION_NAMES = Cache()

class BuildShot(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def _load_scene_context(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return None
        shot_name = self.get_shot_name()
        if shot_name is None: return None
        shot_key = (sequence_name, shot_name)
        if CACHE_VERSION_NAMES.contains(shot_key):
            return CACHE_VERSION_NAMES.lookup(shot_key).copy()
        match self.get_mode():
            case Mode.Latest:
                shot_departments = self.get_shot_department_names()
                asset_departments = self.get_asset_department_names()
                kit_departments = self.get_kit_department_names()
                version_names = _resolve_versions_latest(
                    sequence_name,
                    shot_name,
                    shot_departments,
                    asset_departments,
                    kit_departments
                )
            case Mode.Strict:
                version_names = _resolve_versions_strict(sequence_name, shot_name)
        CACHE_VERSION_NAMES.insert(shot_key, version_names)
        return version_names
    
    def list_sequence_names(self):
        return api.config.list_sequence_names()

    def list_shot_names(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return []
        return api.config.list_shot_names(sequence_name)

    def list_asset_department_names(self):
        asset_department_names = api.config.list_asset_department_names()
        if len(asset_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/build_shot')
        return [
            asset_department_name
            for asset_department_name in default_values['departments']['asset']
            if asset_department_name in asset_department_names
        ]
    
    def list_kit_department_names(self):
        kit_department_names = api.config.list_kit_department_names()
        default_values = api.config.resolve('defaults:/houdini/lops/build_shot')
        return [
            kit_department_name
            for kit_department_name in default_values['departments']['kit']
            if kit_department_name in kit_department_names
        ]

    def list_shot_department_names(self):
        shot_department_names = api.config.list_shot_department_names()
        default_values = api.config.resolve('defaults:/houdini/lops/build_shot')
        return [
            shot_department_name
            for shot_department_name in default_values['departments']['shot']
            if shot_department_name in shot_department_names
        ]
    
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
    
    def get_mode(self):
        match self.parm('mode').eval():
            case Mode.Latest: return Mode.Latest
            case Mode.Strict: return Mode.Strict
            case _: assert False, 'Invalid mode'
        
    def get_exclude_asset_department_names(self):
        return list(filter(len, self.parm('asset_departments').eval().split(' ')))
    
    def get_exclude_kit_department_names(self):
        return list(filter(len, self.parm('kit_departments').eval().split(' ')))

    def get_exclude_shot_department_names(self):
        return list(filter(len, self.parm('shot_departments').eval().split(' ')))
    
    def get_asset_department_names(self):
        asset_department_names = self.list_asset_department_names()
        if len(asset_department_names) == 0: return []
        exclude_asset_department_names = self.get_exclude_asset_department_names()
        return [
            asset_department_name
            for asset_department_name in asset_department_names
            if asset_department_name not in exclude_asset_department_names
        ]
    
    def get_kit_department_names(self):
        kit_department_names = self.list_kit_department_names()
        if len(kit_department_names) == 0: return []
        exclude_kit_department_names = self.get_exclude_kit_department_names()
        return [
            kit_department_name
            for kit_department_name in kit_department_names
            if kit_department_name not in exclude_kit_department_names
        ]
    
    def get_shot_department_names(self):
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        exclude_shot_department_names = self.get_exclude_shot_department_names()
        return [
            shot_department_name
            for shot_department_name in shot_department_names
            if shot_department_name not in exclude_shot_department_names
        ]
    
    def get_downstream_shot_department_names(self):
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        context = _context_from_workfile()
        if context is None: return []
        shot_department_index = shot_department_names.index(context.department_name)
        return shot_department_names[shot_department_index + 1:]

    def get_frame_range_source(self):
        return self.parm('frame_range').eval()
    
    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_config':
                sequence_name = self.get_sequence_name()
                if sequence_name is None: return None
                shot_name = self.get_shot_name()
                if shot_name is None: return None
                frame_range = api.config.get_frame_range(sequence_name, shot_name)
                return frame_range
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                )
            case _:
                assert False, f'Unknown frame range token: {frame_range_source}'
    
    def get_include_downstream_departments(self):
        return bool(self.parm('include_downstream_departments').eval())
            
    def get_include_procedurals(self):
        return bool(self.parm('include_procedurals').eval())
    
    def set_sequence_name(self, sequence_name):
        sequence_names = self.list_sequence_names()
        if sequence_name not in sequence_names: return
        self.parm('sequence').set(sequence_name)
    
    def set_shot_name(self, shot_name):
        shot_names = self.list_shot_names()
        if shot_name not in shot_names: return
        self.parm('shot').set(shot_name)

    def set_mode(self, mode):
        match mode:
            case Mode.Latest: self.parm('mode').set(Mode.Latest)
            case Mode.Strict: self.parm('mode').set(Mode.Strict)
            case _: assert False, f'Invalid mode {mode}'
        self.state.mode = mode
    
    def set_exclude_asset_department_names(self, exclude_asset_department_names):
        asset_department_names = self.list_asset_department_names()
        self.parm('asset_departments').set(' '.join([
            department_name
            for department_name in exclude_asset_department_names
            if department_name in asset_department_names 
        ]))
    
    def set_exclude_kit_department_names(self, exclude_kit_department_names):
        kit_department_names = self.list_kit_department_names()
        self.parm('kit_departments').set(' '.join([
            department_name
            for department_name in exclude_kit_department_names
            if department_name in kit_department_names
        ]))
    
    def set_exclude_shot_department_names(self, exclude_shot_department_names):
        shot_department_names = self.list_shot_department_names()
        self.parm('shot_departments').set(' '.join([
            department_name
            for department_name in exclude_shot_department_names
            if department_name in shot_department_names
        ]))
    
    def set_frame_range_source(self, frame_range_source):
        match frame_range_source:
            case 'from_config': self.parm('frame_range').set('from_config')
            case 'from_settings': self.parm('frame_range').set('from_settings')
            case _: assert False, f'Invalid frame range source {frame_range_source}'
        self.state.frame_range_source = frame_range_source
    
    def set_frame_range(self, frame_range):
        self.parm('frame_settingsx').set(frame_range.start_frame)
        self.parm('frame_settingsy').set(frame_range.end_frame)
        self.parm('roll_settingsx').set(frame_range.start_roll)
        self.parm('roll_settingsy').set(frame_range.end_roll)
    
    def set_include_downstream_departments(self, include_downstream_departments):
        self.parm('include_downstream_departments').set(int(include_downstream_departments))
    
    def set_include_procedurals(self, include_procedurals):
        self.parm('include_procedurals').set(int(include_procedurals))
    
    def execute(self):

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        _clear_scene(dive_node, output_node)

        # Parameters
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return
        shot_name = self.get_shot_name()
        if shot_name is None: return
        frame_range = self.get_frame_range()
        include_asset_departments = self.get_asset_department_names()
        include_kit_departments = self.get_kit_department_names()
        include_shot_departments = self.get_shot_department_names()
        downstream_shot_departments = self.get_downstream_shot_department_names()

        # Check if we have any departments to load
        if len(include_asset_departments) == 0: return
        if len(include_kit_departments) == 0: return
        if len(include_shot_departments) == 0: return

        # Update scene without updating the viewport
        with util.update_mode(hou.updateMode.Manual):

            # Get the resolved paths
            clear_cache()
            scene_context = self._load_scene_context()
            if scene_context is None: return

            # Collection nodes
            merge_node = _ensure_node(dive_node, 'merge', 'merge')
            merge_node.parm('mergestyle').set('separate')

            # Set frame range
            util.set_frame_range(frame_range)

            # Load assets
            for (category_name, asset_name), instance_names in scene_context['assets'].items():

                # Prepare
                prev_node = None

                # Load asset department layers
                for asset_department in api.config.list_asset_department_names():
                    if asset_department not in include_asset_departments: continue

                    # Check if asset department layer is available
                    resolved_layer_path = _get(
                        scene_context,
                        'asset_layers',
                        asset_department,
                        (category_name, asset_name)
                    )
                    if resolved_layer_path is None: continue

                    # Load asset department
                    version_name = resolved_layer_path.name
                    asset_layer_node = import_asset_layer.create(
                        dive_node, (
                            'asset'
                            f'_{category_name}'
                            f'_{asset_name}'
                            f'_{asset_department}'
                            '_import'
                        )
                    )
                    asset_layer_node.set_category_name(category_name)
                    asset_layer_node.set_asset_name(asset_name)
                    asset_layer_node.set_department_name(asset_department)
                    asset_layer_node.set_version_name(version_name)
                    asset_layer_node.set_include_layerbreak(False)
                    asset_layer_node.execute()

                    # Connect to previous node
                    if prev_node is not None:
                        _connect(prev_node, asset_layer_node.native())
                    prev_node = asset_layer_node.native()
                
                # Check if we need to duplicate asset
                instances = len(instance_names)
                if instances > 1:

                    # Create duplicate subnet
                    duplicate_subnet = dive_node.createNode('subnet', f'{category_name}_{asset_name}_duplicate')
                    duplicate_subnet.node('output0').destroy()
                    duplicate_subnet_input = duplicate_subnet.indirectInputs()[0]
                    duplicate_subnet_output = duplicate_subnet.createNode('output', 'output')
                    _connect(prev_node, duplicate_subnet)
                    prev_node = duplicate_subnet_input

                    # Duplicate asset
                    duplicate_node = duplicate_subnet.createNode('duplicate', 'asset_duplicate')
                    duplicate_node.parm('sourceprims').set(f'/{category_name}/{asset_name}')
                    duplicate_node.parm('ncy').set(instances)
                    duplicate_node.parm('duplicatename').set('`@srcname``@copy`')
                    _connect(prev_node, duplicate_node)
                    prev_node = duplicate_node

                    # Duplicate metadata
                    duplicate_metadata_node = duplicate_subnet.createNode('duplicate', 'metadata_duplicate')
                    duplicate_metadata_node.parm('sourceprims').set(f'/METADATA/asset/{category_name}/{asset_name}')
                    duplicate_metadata_node.parm('ncy').set(instances)
                    duplicate_metadata_node.parm('duplicatename').set('`@srcname``@copy`')
                    duplicate_metadata_node.parm('parentprimtype').set('')
                    _connect(prev_node, duplicate_metadata_node)
                    prev_node = duplicate_metadata_node

                    # Update metadata instance names
                    python_node = duplicate_subnet.createNode('pythonscript', 'metadata_update')
                    python_node.parm('python').set('\n'.join(_update_script([
                        (f'/METADATA/asset/{category_name}/{instance_name}', instance_name)
                        for instance_name in instance_names
                    ])))
                    _connect(prev_node, python_node)
                    prev_node = python_node

                    # Connect last node to output
                    _connect(prev_node, duplicate_subnet_output)
                    prev_node = duplicate_subnet

                    # Layout duplicate subnet
                    duplicate_subnet.layoutChildren()
                
                # Load shot department layer for each instance
                anchor_node = prev_node
                for instance_name in instance_names:
                    prev_node = anchor_node

                    # Prepare
                    upstream_department_nodes = []
                    downstream_department_nodes = []
                
                    # Load shot department layers
                    for shot_department in api.config.list_shot_department_names():
                        if shot_department not in include_shot_departments: continue

                        # Check if the shot department layer is available
                        layer_lookup = _get(
                            scene_context,
                            'shot_layers',
                            shot_department
                        )
                        if layer_lookup is None: continue
                        resolved_layer_path, layer_assets = layer_lookup

                        # Get shot department data
                        if (category_name, asset_name) not in layer_assets: continue
                        if instance_name not in layer_assets[(category_name, asset_name)]: continue
                        version_name = resolved_layer_path.name
                        
                        # Load shot layer
                        asset_shot_layer_node = import_shot_layer.create(
                            dive_node, (
                                'asset'
                                f'_{category_name}'
                                f'_{asset_name}'
                                f'_{instance_name}'
                                f'_{shot_department}'
                            )
                        )
                        asset_shot_layer_node.set_sequence_name(sequence_name)
                        asset_shot_layer_node.set_shot_name(shot_name)
                        asset_shot_layer_node.set_department_name(shot_department)
                        asset_shot_layer_node.set_version_name(version_name)
                        asset_shot_layer_node.set_stage_type('asset')
                        asset_shot_layer_node.set_category_name(category_name)
                        asset_shot_layer_node.set_item_name(asset_name)
                        asset_shot_layer_node.set_instance_name(instance_name)
                        asset_shot_layer_node.set_include_layerbreak(False)
                        asset_shot_layer_node.execute()

                        # Store department nodes
                        if shot_department in downstream_shot_departments:
                            downstream_department_nodes.append(asset_shot_layer_node.native())
                        else:
                            upstream_department_nodes.append(asset_shot_layer_node.native())
                    
                    # Connect upstream department nodes
                    for upstream_department_node in upstream_department_nodes:
                        _connect(prev_node, upstream_department_node)
                        prev_node = upstream_department_node
                    
                    # Connect downstream department nodes
                    if len(downstream_department_nodes) > 0:
                        
                        # Create departments switch
                        downstream_switch_node = dive_node.createNode('switch', (
                            f'{category_name}'
                            f'_{instance_name}'
                            '_downstream'
                        ))
                        downstream_switch_node.parm('input').setExpression(f'ch("{self.parm("include_downstream_departments").path()}")')
                        _connect(prev_node, downstream_switch_node)

                        # Connect downstream department nodes
                        for downstream_department_node in downstream_department_nodes:
                            _connect(prev_node, downstream_department_node)
                            prev_node = downstream_department_node
                        
                        # Connect last node to switch
                        _connect(prev_node, downstream_switch_node)
                        prev_node = downstream_switch_node

                    # Create a procedurals subnet
                    include_procedual_parm = self.parm('include_procedurals')
                    asset_procedurals_subnet = dive_node.createNode('subnet', f'{category_name}_{instance_name}_procedurals')
                    asset_procedurals_subnet.node('output0').destroy()
                    asset_procedurals_subnet_input = asset_procedurals_subnet.indirectInputs()[0]
                    asset_procedurals_subnet_output = asset_procedurals_subnet.createNode('output', 'output')
                    _connect(prev_node, asset_procedurals_subnet)
                    prev_node = asset_procedurals_subnet_input

                    # Load asset procedural nodes
                    asset_procedural_names = api.config.list_shot_asset_procedural_names(
                        sequence_name, shot_name, category_name, asset_name
                    )
                    for asset_procedural_name in asset_procedural_names:
                        node_type = ns.find_node_type(asset_procedural_name, 'Lop')
                        assert node_type is not None, f'Could not find {asset_procedural_name} node type'
                        asset_procedural_node = asset_procedurals_subnet.createNode(node_type.name(), asset_procedural_name)
                        procedual_switch_node = asset_procedurals_subnet.createNode('switch', f'{asset_procedural_name}_switch')
                        procedual_switch_node.parm('input').setExpression(f'ch("{include_procedual_parm.path()}")')
                        _connect(prev_node, asset_procedural_node)
                        procedual_switch_node.setInput(0, asset_procedural_node, 1)
                        procedual_switch_node.setInput(1, asset_procedural_node, 0)
                        prev_node = procedual_switch_node

                    # Connect last node to output
                    _connect(prev_node, asset_procedurals_subnet_output)
                    prev_node = asset_procedurals_subnet

                    # Layout procedurals subnet
                    asset_procedurals_subnet.layoutChildren()

                    # Connect last node to output
                    _connect(prev_node, merge_node)
            
            # Load kits
            for category_name, kit_names in scene_context['kits'].items():
                for kit_name in kit_names:

                    # Prepare
                    prev_node = None

                    # Load kit department layers
                    for kit_department in api.config.list_kit_department_names():
                        if kit_department not in include_kit_departments: continue

                        # Check if kit department layer is available
                        resolved_layer_path = _get(
                            scene_context,
                            'kit_layers',
                            kit_department,
                            (category_name, kit_name)
                        )
                        if resolved_layer_path is None: continue

                        # Load kit department
                        version_name = resolved_layer_path.name
                        kit_layer_node = import_kit_layer.create(
                            dive_node, (
                                'kit'
                                f'_{category_name}'
                                f'_{kit_name}'
                                f'_{kit_department}'
                                '_import'
                            )
                        )
                        kit_layer_node.set_category_name(category_name)
                        kit_layer_node.set_kit_name(kit_name)
                        kit_layer_node.set_department_name(kit_department)
                        kit_layer_node.set_version_name(version_name)
                        kit_layer_node.set_include_layerbreak(False)
                        kit_layer_node.execute()

                        # Connect to previous node
                        if prev_node is not None:
                            _connect(prev_node, kit_layer_node.native())
                        prev_node = kit_layer_node.native()

                    # Connect last node to output
                    _connect(prev_node, merge_node)

            # Import shot scene layers 
            upstream_department_nodes = []
            downstream_department_nodes = []
            for shot_department in api.config.list_shot_department_names():
                if shot_department not in include_shot_departments: continue

                # Check if the shot department layer is available
                layer_lookup = _get(
                    scene_context,
                    'shot_layers',
                    shot_department
                )
                if layer_lookup is None: continue
                resolved_layer_path, _ = layer_lookup

                # Create a department subnet
                version_name = resolved_layer_path.name
                department_subnet = dive_node.createNode('subnet', f'{shot_department}_import')
                department_subnet.node('output0').destroy()
                department_subnet_input = department_subnet.indirectInputs()[0]
                department_subnet_output = department_subnet.createNode('output', 'output')
                prev_node = department_subnet_input

                # Load cameras layer
                cameras_shot_layer_node = import_shot_layer.create(department_subnet, 'cameras_import')
                cameras_shot_layer_node.set_sequence_name(sequence_name)
                cameras_shot_layer_node.set_shot_name(shot_name)
                cameras_shot_layer_node.set_department_name(shot_department)
                cameras_shot_layer_node.set_version_name(version_name)
                cameras_shot_layer_node.set_stage_type('cameras')
                cameras_shot_layer_node.set_include_layerbreak(False)
                cameras_shot_layer_node.execute()

                # Connect cameras layer node
                _connect(prev_node, cameras_shot_layer_node.native())
                prev_node = cameras_shot_layer_node.native()

                # Load lights layer
                lights_shot_layer_node = import_shot_layer.create(department_subnet, 'lights_import')
                lights_shot_layer_node.set_sequence_name(sequence_name)
                lights_shot_layer_node.set_shot_name(shot_name)
                lights_shot_layer_node.set_department_name(shot_department)
                lights_shot_layer_node.set_version_name(version_name)
                lights_shot_layer_node.set_stage_type('lights')
                lights_shot_layer_node.set_include_layerbreak(False)
                lights_shot_layer_node.execute()

                # Connect lights layer node
                _connect(prev_node, lights_shot_layer_node.native())
                prev_node = lights_shot_layer_node.native()

                # Load collections layer
                collections_shot_layer_node = import_shot_layer.create(department_subnet, 'collections_import')
                collections_shot_layer_node.set_sequence_name(sequence_name)
                collections_shot_layer_node.set_shot_name(shot_name)
                collections_shot_layer_node.set_department_name(shot_department)
                collections_shot_layer_node.set_version_name(version_name)
                collections_shot_layer_node.set_stage_type('collections')
                collections_shot_layer_node.set_include_layerbreak(False)
                collections_shot_layer_node.execute()

                # Connect collections layer node
                _connect(prev_node, collections_shot_layer_node.native())
                prev_node = collections_shot_layer_node.native()

                # Load render layer
                render_shot_layer_node = import_shot_layer.create(department_subnet, 'render_import')
                render_shot_layer_node.set_sequence_name(sequence_name)
                render_shot_layer_node.set_shot_name(shot_name)
                render_shot_layer_node.set_department_name(shot_department)
                render_shot_layer_node.set_version_name(version_name)
                render_shot_layer_node.set_stage_type('render')
                render_shot_layer_node.set_include_layerbreak(False)
                render_shot_layer_node.execute()

                # Connect render layer node
                _connect(prev_node, render_shot_layer_node.native())
                prev_node = render_shot_layer_node.native()

                # Load scene layer
                scene_shot_layer_node = import_shot_layer.create(department_subnet, 'scene_import')
                scene_shot_layer_node.set_sequence_name(sequence_name)
                scene_shot_layer_node.set_shot_name(shot_name)
                scene_shot_layer_node.set_department_name(shot_department)
                scene_shot_layer_node.set_version_name(version_name)
                scene_shot_layer_node.set_stage_type('scene')
                scene_shot_layer_node.set_include_layerbreak(False)
                scene_shot_layer_node.execute()

                # Connect scene layer node
                _connect(prev_node, scene_shot_layer_node.native())
                prev_node = scene_shot_layer_node.native()

                # Connect last node to output
                _connect(prev_node, department_subnet_output)
                prev_node = department_subnet

                # Layout department subnet
                department_subnet.layoutChildren()

                # Store department nodes
                if shot_department in downstream_shot_departments:
                    downstream_department_nodes.append(department_subnet)
                else:
                    upstream_department_nodes.append(department_subnet)
            
            # Connect upstream department subnets
            prev_node = merge_node
            for upstream_department_node in upstream_department_nodes:
                _connect(prev_node, upstream_department_node)
                prev_node = upstream_department_node

            # Connect downstream department subnets
            if len(downstream_department_nodes) > 0:
                
                # Create departments switch
                downstream_switch_node = dive_node.createNode('switch', 'downstream_switch')
                downstream_switch_node.parm('input').setExpression(f'ch("{self.parm("include_downstream_departments").path()}")')
                _connect(prev_node, downstream_switch_node)

                # Connect downstream department subnets
                for downstream_department_node in downstream_department_nodes:
                    _connect(prev_node, downstream_department_node)
                    prev_node = downstream_department_node
                
                # Connect last node to switch
                _connect(prev_node, downstream_switch_node)
                prev_node = downstream_switch_node
            
            # Connect last node to output
            _connect(prev_node, output_node)

            # Layout scene
            dive_node.layoutChildren()

def clear_cache():
    CACHE_VERSION_NAMES.clear()
    import_asset_layer.clear_cache()
    import_kit_layer.clear_cache()
    import_shot_layer.clear_cache()

def create(scene, name):
    node_type = ns.find_node_type('build_shot', 'Lop')
    assert node_type is not None, 'Could not find build_shot node type'
    native = scene.node(name)
    if native is not None: return BuildShot(native)
    return BuildShot(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('build_shot', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = BuildShot(raw_node)

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
            node.set_exclude_shot_department_names([department_name])

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = BuildShot(raw_node)
    node.execute()