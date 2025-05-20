from tempfile import TemporaryDirectory
from pathlib import Path
import shutil

import nodesearch
import hou

from tumblehead.api import (
    path_str,
    fix_path,
    get_user_name,
    to_windows_path,
    default_client
)
from tumblehead.config import FrameRange
from tumblehead.util.io import store_json
from tumblehead.apps.deadline import Deadline
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.houdini.util as util
from tumblehead.pipe.paths import (
    get_render,
    get_render_context,
    get_workfile_context,
    get_next_frame_path,
    entity_from_context,
    ShotContext,
    ShotEntity
)

api = default_client()

def _set(data, value, *path_steps):
    data = data.setdefault(path_steps[0], dict())
    for step in path_steps[1:-1]:
        data = data.setdefault(step, dict())
    data[path_steps[-1]] = value

def _get(data, *path_steps):
    data = data.get(path_steps[0])
    for step in path_steps[1:]:
        if data is None: return None
        data = data.get(step)
    return data

def _contains(data, *path_steps):
    data = data.get(path_steps[0])
    for step in path_steps[1:]:
        if data is None: return False
        data = data.get(step)
    return data is not None

def _ensure_node(context, node_type, name):
    node = context.node(name)
    if node is not None: return node
    return context.createNode(node_type, name)

def _connect(output_node, output_index, input_node, input_index):
    input_node.setInput(input_index, output_node, output_index)

def _aov_included(aov_name):
    name = aov_name.lower()
    if name.startswith('beauty_'): return True
    if name.startswith('objid_'): return True
    if name == 'beauty': return True
    if name == 'alpha': return True
    if name == 'albedo': return True
    if name == 'normal': return True
    if name == 'depth': return True
    if name == 'uv': return True
    if name == 'position': return True
    return False

def _get_frame_path(framestack_path, frame_index):
    frame_name = str(frame_index).zfill(4)
    return framestack_path.with_name(framestack_path.name.replace('$F4', frame_name))

def _get_connected_output(node, index):
    connections = node.outputConnections()
    if len(connections) <= index: return None
    return connections[index].outputNode()

def _fix_connections(node, node_data):

    def _is_connection(data):
        if 'from' not in data: return False
        if 'from_index' not in data: return False
        if 'to_index' not in data: return False
        return True
    
    def _resolve_input(node, name):
        def _input_index(node, name):
            for index, input in enumerate(node.inputNames()):
                if input == name: return index
            assert False, f'Could not find input index for {name}'
        try: return int(name)
        except: return _input_index(node, name)
    
    def _resolve_output(node, name):
        def _output_index(node, name):
            for index, output in enumerate(node.outputNames()):
                if output == name: return index
            assert False, f'Could not find output index for {name}'
        try: return int(name)
        except: return _output_index(node, name)

    for to_node_name, to_node_data in node_data.items():
        to_node = node.node(to_node_name)
        if to_node is None: continue

        # Fix input connections
        if 'inputs' in to_node_data:
            if not isinstance(to_node_data['inputs'], list): continue
            for connection in to_node_data['inputs']:
                if not _is_connection(connection): continue
                from_node = node.node(connection['from'])
                if from_node is None: continue
                from_index = _resolve_output(from_node, connection['from_index'])
                to_index = _resolve_input(to_node, connection['to_index'])
                to_node.setInput(to_index, from_node, from_index)
        
        # Fix subsubnodes
        if 'children' in to_node_data:
            _fix_connections(to_node, to_node_data['children'])

class Source:
    Render = 'render'
    Proxy = 'proxy'

class Resolution:
    Full = 'full'
    Half = 'half'
    Quarter = 'quarter'

def _scale(resolution):
    match resolution:
        case Resolution.Full: return 1.0
        case Resolution.Half: return 2.0
        case Resolution.Quarter: return 4.0
        case _: assert False, f'Unknown resolution {resolution}'

class AOVType:
    Mask = 'mask'
    LPE = 'lpe'
    Util = 'util'
    Mono = 'mono'

class _SourceContext:
    def __init__(self, node, source):
        self.node = node
        self.current_source = source
        self.previous_source = None

    def __enter__(self):
        self.previous_source = self.node.get_source_name()
        self.node.set_source_name(self.current_source)
        self.node.update()

    def __exit__(self, *args):
        self.node.set_source_name(self.previous_source)
        self.node.update()

class BuildComp(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_sequence_names(self):
        return api.config.list_sequence_names()
    
    def list_shot_names(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return []
        return api.config.list_shot_names(sequence_name)
    
    def list_render_department_names(self):
        render_department_names = api.config.list_render_department_names()
        if not render_department_names: return []
        default_values = api.config.resolve('defaults:/houdini/cops/build_comp')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in render_department_names
        ]
    
    def list_pool_names(self):
        try: deadline = Deadline()
        except: return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/submit_render')
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
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
    
    def get_render_department_name(self):
        department_names = self.list_render_department_names()
        if len(department_names) == 0: return None
        department_name = self.parm('render_department').eval()
        if len(department_name) == 0: return department_names[0]
        if department_name not in department_names: return None
        return department_name
    
    def get_source_name(self):
        return self.parm('source').eval()

    def get_proxy_resolution(self):
        source_name = self.get_source_name()
        if source_name == Source.Render: return Resolution.Full
        return self.parm('proxy_resolution').eval()
    
    def get_frame_range_source(self):
        return self.parm('frame_range').eval()
    
    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_config':
                sequence_name = self.get_sequence_name()
                shot_name = self.get_shot_name()
                frame_range = api.config.get_frame_range(
                    sequence_name,
                    shot_name
                )
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
    
    def get_pool_name(self):
        pool_names = self.list_pool_names()
        if len(pool_names) == 0: return None
        pool_name = self.parm('pool').eval()
        if pool_name not in pool_names: return pool_names[0]
        return pool_name
    
    def get_priority(self):
        return self.parm('priority').eval()
    
    def get_step_size(self):
        return self.parm('stepsize').eval()
    
    def get_batch_size(self):
        return self.parm('batchsize').eval()
    
    def get_submit_partial(self):
        return self.parm('submit_partial').eval()
    
    def get_submit_full(self):
        return self.parm('submit_full').eval()
    
    def get_partial_frames(self):
        return (
            self.parm('specific_framesx').eval(),
            self.parm('specific_framesy').eval(),
            self.parm('specific_framesz').eval()
        )
    
    def set_sequence_name(self, sequence_name):
        sequence_names = self.list_sequence_names()
        if sequence_name not in sequence_names: return
        self.parm('sequence').set(sequence_name)
    
    def set_shot_name(self, shot_name):
        shot_names = self.list_shot_names()
        if shot_name not in shot_names: return
        self.parm('shot').set(shot_name)
    
    def set_render_department_name(self, render_department_name):
        department_names = self.list_render_department_names()
        if render_department_name not in department_names: return
        self.parm('render_department').set(render_department_name)
    
    def set_source_name(self, source_name):
        source_values = self.parm('source').menuContents()
        if source_name not in source_values: return
        self.parm('source').set(source_name)
    
    def set_proxy_resolution(self, resolution_name):
        resolution_values = self.parm('proxy_resolution').menuContents()
        if resolution_name not in resolution_values: return
        self.parm('proxy_resolution').set(resolution_name)
    
    def set_pool_name(self, pool_name):
        pool_names = self.list_pool_names()
        if pool_name not in pool_names: return
        self.parm('pool').set(pool_name)
    
    def set_priority(self, priority):
        self.parm('priority').set(priority)
    
    def update(self):
        
        # Nodes
        context = self.native()
        proxy_node = context.node('proxy')
        tops_node = proxy_node.node('tops')

        # Parameters
        source_name = self.get_source_name()

        # Build if not already built
        if not self.parm('built').eval(): self._build()

        # Check if source is render
        if source_name == Source.Render: return self._update()
        
        # Set tops target, in order to display the progress
        context.parm('targettopnetwork').set('proxy/tops/merge')

        # Generate proxy frames
        tops_node.parm('dirtybutton').pressButton()
        tops_node.parm('cookbutton').pressButton()
    
    def _resolve_aovs(
        self,
        source_name,
        sequence_name,
        shot_name,
        resolution_name,
        render_department_names
        ):

        def _resolve(aov_context):
            aovs = dict()
            for render_department_name in reversed(render_department_names):
                if render_department_name not in aov_context: continue
                for render_layer_name, render_layer_aovs in aov_context[render_department_name].items():
                    for aov in render_layer_aovs.values():
                        if _contains(aovs, render_layer_name, aov.label): continue
                        _set(aovs, aov, render_layer_name, aov.label)
            return aovs
        
        def _types(aovs):

            # Get all aov names
            aov_names = set()
            for render_layer_aovs in aovs.values():
                for aov in render_layer_aovs.values():
                    aov_names.add(aov.label)

            # Find LPE names
            lpe_names = list()
            if 'beauty' in aov_names: lpe_names.append('beauty')
            for aov_name in aov_names:
                if not aov_name.startswith('beauty_'): continue
                lpe_names.append(aov_name)
            
            # Find mask names
            mask_names = list()
            for aov_name in aov_names:
                if not aov_name.startswith('objid_'): continue
                mask_names.append(aov_name)
            
            # Find mono names
            mono_names = list()
            if 'alpha' in aov_names: mono_names.append('alpha')

            # The rest is util names
            util_names = list(
                set(aov_names) -
                set(lpe_names) -
                set(mask_names) -
                set(mono_names)
            )

            # Get aov type
            def _aov_type(aov_name):
                if aov_name in lpe_names: return AOVType.LPE
                if aov_name in mask_names: return AOVType.Mask
                if aov_name in util_names: return AOVType.Util
                if aov_name in mono_names: return AOVType.Mono
                assert False, f'Unknown aov type for {aov_name}'

            # Resolve types
            types = dict()
            for render_layer_name, render_layer_aovs in aovs.items():
                for aov in render_layer_aovs.values():
                    if _contains(types, render_layer_name, aov.label): continue
                    _set(types, _aov_type(aov.label), render_layer_name, aov.label)
            return types

        match source_name:
            case Source.Render:
                render_context = get_render_context(
                    sequence_name,
                    shot_name,
                    purpose = Source.Render
                )
                if render_context is None: return None
                aovs = _resolve(render_context.list_latest_complete_aovs())
                types = _types(aovs)
                return aovs, types
            case Source.Proxy:
                render = get_render(
                    sequence_name,
                    shot_name,
                    resolution_name,
                    purpose = Source.Proxy
                )
                if render is None: return None
                aovs = _resolve(dict(
                    resolution_name = render.list_latest_complete_aovs()
                ))
                types = _types(aovs)
                return aovs, types
    
    def _build_lpe_aov(self, parent_node, render_layer_name, aov):

        # Parameters
        render_range = aov.get_frame_range()
            
        # Create aov subnet
        aov_subnet = _ensure_node(parent_node, 'subnet', aov.label)
        aov_subnet.parm('inputs').set(0)
        aov_subnet.parm('outputs').set(1)
        aov_subnet.parm('outputlabel1').set(aov.label)
        aov_subnet.parm('outputtype1').set(3)
        aov_subnet_inputs = aov_subnet.node('inputs')
        aov_subnet_outputs = aov_subnet.node('outputs')
        aov_subnet.setColor(hou.Color((1, 1, 0)))
        aov_subnet_inputs.setColor(hou.Color((1, 1, 1)))
        aov_subnet_outputs.setColor(hou.Color((0, 0, 0)))

        # Get aov frame path
        aov_frame_path = aov.get_aov_frame_path('$F4')
        assert aov_frame_path is not None, f'Could not find aov frame path for {aov.label}'

        # Create import node
        aov_import_name = f'{render_layer_name}_{aov.label}'
        aov_import_node = _ensure_node(aov_subnet, 'file', aov_import_name)
        aov_import_node.parm('filename').set(path_str(aov_frame_path))
        aov_import_node.parm('videoframestart').deleteAllKeyframes()
        aov_import_node.parm('videoframestart').set(render_range.first_frame)
        hou.setFrame(render_range.first_frame)
        aov_import_node.parm('missingdata').set(1)
        aov_import_node.parm('missingcolora').set(0)
        aov_import_node.parm('addaovs').pressButton()

        # Create the resample node
        aov_resample_node = _ensure_node(aov_subnet, 'resample', 'resample')
        aov_resample_node.parm('scale').set(1)
        aov_resample_node.parm('filter').set('point')
        _connect(aov_import_node, 0, aov_resample_node, 0)

        # Create aov subnet output node
        _connect(aov_resample_node, 0, aov_subnet_outputs, 0)

        # Layout aov subnet nodes
        aov_subnet.layoutChildren()

        # Return aov subnet
        return aov_subnet

    def _build_mask_aov(self, parent_node, render_layer_name, aov):

        # Parameters
        render_range = aov.get_frame_range()
        
        # Create aov subnet
        aov_subnet = _ensure_node(parent_node, 'subnet', aov.label)
        aov_subnet.parm('inputs').set(0)
        aov_subnet.parm('outputs').set(3)
        aov_subnet.parm('outputlabel1').set('R')
        aov_subnet.parm('outputtype1').set(1)
        aov_subnet.parm('outputlabel2').set('G')
        aov_subnet.parm('outputtype2').set(1)
        aov_subnet.parm('outputlabel3').set('B')
        aov_subnet.parm('outputtype3').set(1)
        aov_subnet_inputs = aov_subnet.node('inputs')
        aov_subnet_outputs = aov_subnet.node('outputs')
        aov_subnet.setColor(hou.Color((0, 1, 0)))
        aov_subnet_inputs.setColor(hou.Color((1, 1, 1)))
        aov_subnet_outputs.setColor(hou.Color((0, 0, 0)))

        # Get aov frame path
        aov_frame_path = aov.get_aov_frame_path('$F4')
        assert aov_frame_path is not None, f'Could not find aov frame path for {aov.label}'

        # Create import node
        aov_import_name = f'{render_layer_name}_{aov.label}'
        aov_import_node = _ensure_node(aov_subnet, 'file', aov_import_name)
        aov_import_node.parm('filename').set(path_str(aov_frame_path))
        aov_import_node.parm('videoframestart').deleteAllKeyframes()
        aov_import_node.parm('videoframestart').set(render_range.first_frame)
        hou.setFrame(render_range.first_frame)
        aov_import_node.parm('missingdata').set(1)
        aov_import_node.parm('missingcolora').set(0)
        aov_import_node.parm('addaovs').pressButton()

        # Create the resample node
        aov_resample_node = _ensure_node(aov_subnet, 'resample', 'resample')
        aov_resample_node.parm('scale').set(1)
        aov_resample_node.parm('filter').set('point')
        _connect(aov_import_node, 0, aov_resample_node, 0)

        # Create a channel split node
        aov_split_node = _ensure_node(aov_subnet, 'channelsplit', 'split')
        _connect(aov_resample_node, 0, aov_split_node, 0)

        # Create aov subnet output node
        _connect(aov_split_node, 0, aov_subnet_outputs, 0)
        _connect(aov_split_node, 1, aov_subnet_outputs, 1)
        _connect(aov_split_node, 2, aov_subnet_outputs, 2)

        # Layout aov subnet nodes
        aov_subnet.layoutChildren()

        # Return aov subnet
        return aov_subnet

    def _build_util_aov(self, parent_node, render_layer_name, aov):

        # Parameters
        render_range = aov.get_frame_range()
        
        # Create aov subnet
        aov_subnet = _ensure_node(parent_node, 'subnet', aov.label)
        aov_subnet.parm('inputs').set(0)
        aov_subnet.parm('outputs').set(1)
        aov_subnet.parm('outputlabel1').set(aov.label)
        aov_subnet.parm('outputtype1').set(4)
        aov_subnet_inputs = aov_subnet.node('inputs')
        aov_subnet_outputs = aov_subnet.node('outputs')
        aov_subnet.setColor(hou.Color((0, 0, 1)))
        aov_subnet_inputs.setColor(hou.Color((1, 1, 1)))
        aov_subnet_outputs.setColor(hou.Color((0, 0, 0)))

        # Get aov frame path
        aov_frame_path = aov.get_aov_frame_path('$F4')
        assert aov_frame_path is not None, f'Could not find aov frame path for {aov.label}'

        # Create import node
        aov_import_name = f'{render_layer_name}_{aov.label}'
        aov_import_node = _ensure_node(aov_subnet, 'file', aov_import_name)
        aov_import_node.parm('filename').set(path_str(aov_frame_path))
        aov_import_node.parm('videoframestart').deleteAllKeyframes()
        aov_import_node.parm('videoframestart').set(render_range.first_frame)
        hou.setFrame(render_range.first_frame)
        aov_import_node.parm('missingdata').set(1)
        aov_import_node.parm('missingcolora').set(0)
        aov_import_node.parm('addaovs').pressButton()

        # Create the resample node
        aov_resample_node = _ensure_node(aov_subnet, 'resample', 'resample')
        aov_resample_node.parm('scale').set(1)
        aov_resample_node.parm('filter').set('point')
        _connect(aov_import_node, 0, aov_resample_node, 0)

        # Create aov subnet output node
        _connect(aov_resample_node, 0, aov_subnet_outputs, 0)

        # Layout aov subnet nodes
        aov_subnet.layoutChildren()

        # Return aov subnet
        return aov_subnet
    
    def _build_mono_aov(self, parent_node, render_layer_name, aov):

        # Parameters
        render_range = aov.get_frame_range()
        
        # Create aov subnet
        aov_subnet = _ensure_node(parent_node, 'subnet', aov.label)
        aov_subnet.parm('inputs').set(0)
        aov_subnet.parm('outputs').set(1)
        aov_subnet.parm('outputlabel1').set(aov.label)
        aov_subnet.parm('outputtype1').set(1)
        aov_subnet_inputs = aov_subnet.node('inputs')
        aov_subnet_outputs = aov_subnet.node('outputs')
        aov_subnet.setColor(hou.Color((0.5, 0.5, 0.5)))
        aov_subnet_inputs.setColor(hou.Color((1, 1, 1)))
        aov_subnet_outputs.setColor(hou.Color((0, 0, 0)))

        # Get aov frame path
        aov_frame_path = aov.get_aov_frame_path('$F4')
        assert aov_frame_path is not None, f'Could not find aov frame path for {aov.label}'

        # Create import node
        aov_import_name = f'{render_layer_name}_{aov.label}'
        aov_import_node = _ensure_node(aov_subnet, 'file', aov_import_name)
        aov_import_node.parm('filename').set(path_str(aov_frame_path))
        aov_import_node.parm('videoframestart').deleteAllKeyframes()
        aov_import_node.parm('videoframestart').set(render_range.first_frame)
        hou.setFrame(render_range.first_frame)
        aov_import_node.parm('missingdata').set(1)
        aov_import_node.parm('missingcolora').set(0)
        aov_import_node.parm('addaovs').pressButton()

        # Create the resample node
        aov_resample_node = _ensure_node(aov_subnet, 'resample', 'resample')
        aov_resample_node.parm('scale').set(1)
        aov_resample_node.parm('filter').set('point')
        _connect(aov_import_node, 0, aov_resample_node, 0)

        # Create aov subnet output node
        _connect(aov_resample_node, 0, aov_subnet_outputs, 0)

        # Layout aov subnet nodes
        aov_subnet.layoutChildren()

        # Return aov subnet
        return aov_subnet
    
    def _update(self):

        # Nodes
        context = self.native()
        dive_node = context.node('dive')

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        source_name = self.get_source_name()
        resolution_name = self.get_proxy_resolution()
        scale = _scale(resolution_name)

        # Find the ordered list of render department names
        render_department_names = self.list_render_department_names()
        render_department_name = self.get_render_department_name()
        if render_department_name not in render_department_names: return
        render_department_index = render_department_names.index(render_department_name)
        render_department_names = render_department_names[:render_department_index + 1]

        # Get render data
        aov_context, aov_types = self._resolve_aovs(
            source_name,
            sequence_name,
            shot_name,
            resolution_name,
            render_department_names
        )
        if aov_context is None: return

        # Find all file nodes
        aov_import_nodes = dict()
        file_matcher = nodesearch.NodeType('file', 'Cop')
        for aov_import_node in file_matcher.nodes(dive_node, recursive = True):

            # Get aov context
            aov_import_node_name = aov_import_node.name()
            render_layer_name, aov_name = aov_import_node_name.split('_', 1)

            # Get the resample node
            aov_resample_node = _get_connected_output(aov_import_node, 0)
            if aov_resample_node is None: continue
            if aov_resample_node.type().name() != 'resample': continue

            # Store aov import node
            _set(aov_import_nodes, (aov_import_node, aov_resample_node), render_layer_name, aov_name)
        
        # Update file node paths and resample scales
        for render_layer_name, aov_nodes in aov_import_nodes.items():
            for aov_name, (aov_import_node, aov_resample_node) in aov_nodes.items():
                aov = _get(aov_context, render_layer_name, aov_name)
                if aov is None: continue
                aov_frame_path = aov.get_aov_frame_path('$F4')
                if aov_frame_path is None: continue
                aov_import_node.parm('filename').set(path_str(aov_frame_path))
                aov_resample_node.parm('scale').set(scale)

        # Find new aovs that do not have a file node
        for render_layer_name, aovs in aov_context.items():
            for aov in aovs.values():
                if _contains(aov_import_nodes, render_layer_name, aov.label): continue

                # Check if aov is included in comp
                if not _aov_included(aov.label): continue

                # Create aov import node
                render_layer_subnet = dive_node.node(render_layer_name)
                if render_layer_subnet is None: continue
                match _get(aov_types, render_layer_name, aov.label):
                    case AOVType.LPE:
                        aov_subnet = self._build_lpe_aov(
                            render_layer_subnet,
                            render_layer_name,
                            aov
                        )
                    case AOVType.Mask:
                        aov_subnet = self._build_mask_aov(
                            render_layer_subnet,
                            render_layer_name,
                            aov
                        )
                    case AOVType.Util:
                        aov_subnet = self._build_util_aov(
                            render_layer_subnet,
                            render_layer_name,
                            aov
                        )
                    case AOVType.Mono:
                        aov_subnet = self._build_mono_aov(
                            render_layer_subnet,
                            render_layer_name,
                            aov
                        )
                    case _:
                        assert False, f'Unknown aov type for {aov.label}'
                
                # Store import node
                if render_layer_name not in aov_import_nodes: aov_import_nodes[render_layer_name] = dict()
                aov_import_nodes[render_layer_name][aov.label] = (aov_subnet.node('outputs'), aov_subnet.node('inputs'))

    def _build(self):

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        render_layer_names = api.config.list_render_layer_names(sequence_name, shot_name)
        frame_range = api.config.get_frame_range(sequence_name, shot_name)

        # Find the ordered list of render department names
        render_department_names = self.list_render_department_names()
        render_department_name = self.get_render_department_name()
        if render_department_name not in render_department_names: return
        render_department_index = render_department_names.index(render_department_name)
        render_department_names = render_department_names[:render_department_index + 1]

        # Nodes
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')

        # Update dive node outputs
        dive_node.parm('outputs').set(len(render_layer_names) + 1)
        for index, render_layer_name in enumerate(render_layer_names):
            dive_node.parm(f'outputlabel{index + 2}').set(render_layer_name)
            dive_node.parm(f'outputtype{index + 2}').set(4)

        # Get render data
        aov_context, aov_types = self._resolve_aovs(
            Source.Render,
            sequence_name,
            shot_name,
            Resolution.Full,
            render_department_names
        )
        if aov_context is None: return

        # Set frame range and playback
        util.set_frame_range(frame_range)

        # Build render layer comps
        layer_nodes = dict()
        for render_layer_name in render_layer_names:

            # Get render layer aovs
            aovs = _get(aov_context, render_layer_name)
            if aovs is None: continue

            # All names
            aov_names = list(aovs.keys())
            assert 'beauty' in aov_names, (
                'Missing beauty aov in '
                f'{render_layer_name}'
            )

            # Prepare render layer subnet
            render_layer_subnet = _ensure_node(dive_node, 'subnet', render_layer_name)
            render_layer_subnet.parm('inputs').set(0)
            render_layer_subnet.parm('outputs').set(1)
            render_layer_subnet.parm('outputlabel1').set('rgba')
            render_layer_subnet.parm('outputtype1').set(4)
            render_layer_subnet_inputs = render_layer_subnet.node('inputs')
            render_layer_subnet_outputs = render_layer_subnet.node('outputs')
            render_layer_subnet.setColor(hou.Color((1, 0, 1)))
            render_layer_subnet_inputs.setColor(hou.Color((1, 1, 1)))
            render_layer_subnet_outputs.setColor(hou.Color((0, 0, 0)))

            # Import layer aovs
            aov_nodes = {
                AOVType.LPE: dict(),
                AOVType.Mask: dict(),
                AOVType.Util: dict(),
                AOVType.Mono: dict()
            }
            for aov in aovs.values():

                # Check if aov is included in comp
                if not _aov_included(aov.label): continue

                # Build aov import node
                aov_type = _get(aov_types, render_layer_name, aov.label)
                match aov_type:
                    case AOVType.LPE:
                        aov_subnet = self._build_lpe_aov(
                            render_layer_subnet,
                            render_layer_name,
                            aov
                        )
                    case AOVType.Mask:
                        aov_subnet = self._build_mask_aov(
                            render_layer_subnet,
                            render_layer_name,
                            aov
                        )
                    case AOVType.Util:
                        aov_subnet = self._build_util_aov(
                            render_layer_subnet,
                            render_layer_name,
                            aov
                        )
                    case AOVType.Mono:
                        aov_subnet = self._build_mono_aov(
                            render_layer_subnet,
                            render_layer_name,
                            aov
                        )
                    case _:
                        assert False, f'Unknown aov type for {aov.label}'

                # Store import node
                aov_nodes[aov_type][aov.label] = aov_subnet

            # Prepare grade subnet
            assert 'beauty' in aov_nodes[AOVType.LPE], (
                'Missing beauty aov in '
                f'{render_layer_name}'
            )
            num_lpes = len(aov_nodes[AOVType.LPE]) - 1
            lpe_names = [
                lpe_name for lpe_name in aov_nodes[AOVType.LPE].keys()
                if lpe_name != 'beauty'
            ]
            grade_subnet = _ensure_node(render_layer_subnet, 'subnet', 'grade')
            grade_subnet.parm('inputs').set(num_lpes + 1)
            for lpe_index, lpe_name in enumerate(lpe_names):
                grade_subnet.parm(f'inputlabel{lpe_index + 1}').set(lpe_name)
                grade_subnet.parm(f'inputtype{lpe_index + 1}').set(3)
            grade_subnet.parm(f'inputlabel{num_lpes + 1}').set('alpha')
            grade_subnet.parm(f'inputtype{num_lpes + 1}').set(1)
            grade_subnet.parm('outputs').set(1)
            grade_subnet.parm('outputlabel1').set('rgba')
            grade_subnet.parm('outputtype1').set(4)
            lpe_subnet_inputs = grade_subnet.node('inputs')
            lpe_subnet_outputs = grade_subnet.node('outputs')
            grade_subnet.setColor(hou.Color((1, 0, 1)))
            lpe_subnet_inputs.setColor(hou.Color((1, 1, 1)))
            lpe_subnet_outputs.setColor(hou.Color((0, 0, 0)))

            # Prepare spare parameters
            parm_group = hou.ParmTemplateGroup()
            spare_folder_old = hou.FolderParmTemplate(
                'old_parms', 'Old Parameters'
            )
            for old_parm in grade_subnet.parmTemplateGroup().entries():
                spare_folder_old.addParmTemplate(old_parm)
            spare_folder_old.hide(True)
            parm_group.append(spare_folder_old)

            # Compose LPEs per render layer
            grade_nodes = dict()
            layer_output_node = None
            for lpe_index, lpe_name in enumerate(lpe_names):

                # Get LPE import node
                lpe_import_node = aov_nodes[AOVType.LPE][lpe_name]
                _connect(lpe_import_node, 0, grade_subnet, lpe_index)

                # Create color correct node
                lpe_grade_node = _ensure_node(
                    grade_subnet, 'bright',
                    f'{render_layer_name}_{lpe_name}_grade'
                )
                grade_nodes[lpe_name] = lpe_grade_node
                _connect(lpe_subnet_inputs, lpe_index, lpe_grade_node, 0)

                # Add spare brightness parameter
                spare_brightness_parm = (
                    lpe_grade_node
                    .parmTuple('bright')
                    .parmTemplate()
                )
                spare_brightness_parm.setName(f'{lpe_name}_brightness')
                spare_brightness_parm.setLabel(f'{lpe_name} brightness')
                parm_group.append(spare_brightness_parm)

                # Add spare color parameter
                spare_color_parm = (
                    lpe_grade_node
                    .parmTuple('brighttint')
                    .parmTemplate()
                )
                spare_color_parm.setName(f'{lpe_name}_tint')
                spare_color_parm.setLabel(f'{lpe_name} tint')
                parm_group.append(spare_color_parm)

                # Merge LPEs
                if layer_output_node is None:
                    layer_output_node = (lpe_name, lpe_grade_node)
                else:
                    prev_lpe_name, prev_output_node = layer_output_node
                    add_node = _ensure_node(
                        grade_subnet, 'blend',
                        f'{render_layer_name}_{prev_lpe_name}_{lpe_name}_add'
                    )
                    add_node.parm('mode').set('add')
                    _connect(prev_output_node, 0, add_node, 0)
                    _connect(lpe_grade_node, 0, add_node, 1)
                    layer_output_node = (lpe_name, add_node)
            
            # Set the spare parameters on the grade subnet
            grade_subnet.setParmTemplateGroup(parm_group)

            # Hook up the new spare parameters
            for lpe_name in lpe_names:
                grade_node = grade_nodes[lpe_name]

                # Brightness
                grade_node.parm('bright').setExpression(
                    grade_node
                    .parm('bright')
                    .referenceExpression(
                        grade_subnet.parm(f'{lpe_name}_brightness')
                    )
                )

                # Tint
                grade_node.parm('brighttintr').setExpression(
                    grade_node
                    .parm('brighttintr')
                    .referenceExpression(
                        grade_subnet.parm(f'{lpe_name}_tintr')
                    )
                )
                grade_node.parm('brighttintg').setExpression(
                    grade_node
                    .parm('brighttintg')
                    .referenceExpression(
                        grade_subnet.parm(f'{lpe_name}_tintg')
                    )
                )
                grade_node.parm('brighttintb').setExpression(
                    grade_node
                    .parm('brighttintb')
                    .referenceExpression(
                        grade_subnet.parm(f'{lpe_name}_tintb')
                    )
                )
            
            # Convert grade output to rgba
            grade_rgba_node = _ensure_node(grade_subnet, 'rgbtorgba', 'rgba')
            final_output_node = None if layer_output_node is None else layer_output_node[1]
            if final_output_node is None:
                _connect(lpe_subnet_inputs, num_lpes, grade_rgba_node, 0)
            else:
                _connect(final_output_node, 0, grade_rgba_node, 0)
            _connect(lpe_subnet_inputs, num_lpes, grade_rgba_node, 1)
            _connect(grade_rgba_node, 0, lpe_subnet_outputs, 0)
            
            # Create the render layer subnet output node
            render_layer_alpha_node = aov_nodes[AOVType.Mono]['alpha']
            _connect(render_layer_alpha_node, 0, grade_subnet, num_lpes)
            _connect(grade_subnet, 0, render_layer_subnet_outputs, 0)

            # Layout grade subnet nodes
            grade_subnet.layoutChildren()
            
            # Layout render layer subnet nodes
            render_layer_subnet.layoutChildren()
            
            # Store layer output node
            layer_nodes[render_layer_name] = (render_layer_subnet, aov_nodes)
        
        # Merge render layer comps
        comp_output_node = None
        for index, render_layer_name in enumerate(render_layer_names):

            # Get render layer nodes
            if render_layer_name not in layer_nodes: continue
            layer_subnet, _ = layer_nodes[render_layer_name]

            # Connect render layer to output
            _connect(layer_subnet, 0, output_node, index + 1)

            # Merge render layers
            if comp_output_node is None:
                comp_output_node = (render_layer_name, layer_subnet)
            else:

                # Merge render layers
                prev_render_layer_name, prev_output_node = comp_output_node
                over_node = _ensure_node(
                    dive_node,
                    'blend',
                    f'{prev_render_layer_name}_{render_layer_name}_over'
                )
                over_node.parm('mode').set('over')
                _connect(prev_output_node, 0, over_node, 0)
                _connect(layer_subnet, 0, over_node, 1)
                comp_output_node = (render_layer_name, over_node)
        
        # Set output node
        if comp_output_node is not None:
            _, final_output_node = comp_output_node
            comp_rgb_node = _ensure_node(dive_node, 'rgbatorgb', 'rgb')
            _connect(final_output_node, 0, comp_rgb_node, 0)
            _connect(comp_rgb_node, 0, output_node, 0)
        
        # Layout nodes
        dive_node.layoutChildren()

        # Set built flag
        self.parm('built').set(1)
    
    def stop(self):

        # Nodes
        context = self.native()

        # Get task node to cancel
        task_name = context.parm('targettopnetwork').eval().split('/', 1)[0]
        task_node = context.node(task_name)
        tops_node = task_node.node('tops')

        # Generate proxy frames
        tops_node.parm('cancelbutton').pressButton()
        tops_node.parm('dirtybutton').pressButton()
    
    def preview(self):
        
        # Nodes
        context = self.native()
        preview_node = context.node('preview')

        # Trigger the preview
        preview_node.parm('renderpreview').pressButton()
    
    def _temporary_source(self, source):
        return _SourceContext(self, source)
    
    def submit(self):

        # Import the job module
        from importlib import reload
        from tumblehead.farm.jobs.houdini.composite import (
            job as composite_job
        )
        reload(composite_job)

        # Get the entity
        workfile_path = Path(hou.hipFile.path())
        context = get_workfile_context(workfile_path)
        assert context is not None, 'Invalid workfile path'
        entity = entity_from_context(context)

        # Prepare settings
        user_name = get_user_name()
        pool_name = self.get_pool_name()
        priority = self.get_priority()
        workfile_path = Path(hou.hipFile.path())
        node_path = self.native().node('render').path()
        frame_range = self.get_frame_range()
        render_range = frame_range.full_range()
        step_size = self.get_step_size()
        batch_size = self.get_batch_size()

        # Prepare tasks
        tasks = dict()

        # Add the stage task
        tasks['stage'] = dict(
            channel_name = 'exports'
        )

        # Maybe add partial render task
        if self.get_submit_partial():
            tasks['partial_composite'] = dict(
                channel_name = 'previews'
            )
        
        # Maybe add full render task
        if self.get_submit_full():
            tasks['full_composite'] = dict(
                channel_name = 'comp'
            )
        
        # Open temporary directory
        root_temp_path = fix_path(api.storage.resolve('temp:/'))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Set the update mode to manual
            with util.update_mode(hou.updateMode.Manual):

                # Set the source to render
                with self._temporary_source(Source.Render):

                    # Save and copy workfile to temporary directory
                    input_path = temp_path / 'workfile.hip'
                    relative_input_path = (
                        input_path
                        .relative_to(temp_path)
                    )
                    hou.hipFile.save()
                    shutil.copyfile(workfile_path, input_path)

                    # Submit the job
                    composite_job.submit(dict(
                        entity = entity.to_json(),
                        settings = dict(
                            user_name = user_name,
                            purpose = 'render',
                            priority = priority,
                            pool_name = pool_name,
                            input_path = path_str(relative_input_path),
                            node_path = node_path,
                            first_frame = render_range.first_frame,
                            last_frame = render_range.last_frame,
                            step_size = step_size,
                            batch_size = batch_size
                        ),
                        tasks = tasks
                    ), {
                        input_path: relative_input_path
                    })

    def render(self):

        # Nodes
        context = self.native()
        render_node = context.node('render')

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        render_layer_names = api.config.list_render_layer_names(
            sequence_name,
            shot_name
        )
        frame_range = api.config.get_frame_range(sequence_name, shot_name)
        render_range = frame_range.full_range()
        
        # Render the composite
        root_temp_path = to_windows_path(api.storage.resolve('temp:/'))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            dir = path_str(root_temp_path),
            ignore_cleanup_errors=True
            ) as temp_dir:
            temp_path = Path(temp_dir)

            # Render each render layer
            for render_layer_index, render_layer_name in enumerate(render_layer_names):

                # Paths
                temp_frames_path = (
                    temp_path /
                    render_layer_name /
                    f'{render_layer_name}.$F4.exr'
                )
                output_frames_path = get_next_frame_path(
                    ShotEntity(
                        sequence_name,
                        shot_name
                    ),
                    'composite',
                    render_layer_name,
                    '$F4'
                )

                # Render the frames
                temp_frames_path.parent.mkdir(parents = True, exist_ok = True)
                render_node.parm('copoutput').set(path_str(temp_frames_path))
                render_node.parm('port1').set(render_layer_index + 1)
                render_node.parm('f1').set(render_range.first_frame)
                render_node.parm('f2').set(render_range.last_frame)
                render_node.parm('f3').set(render_range.step_size)
                render_node.parm('execute').pressButton()

                # Copy the frames to the output
                output_frames_path.parent.mkdir(parents = True, exist_ok = True)
                for frame in render_range:
                    frame_name = str(frame).zfill(4)
                    temp_frame_path = _get_frame_path(temp_frames_path, frame_name)
                    output_frame_path = _get_frame_path(output_frames_path, frame_name)
                    shutil.copyfile(temp_frame_path, output_frame_path)
                
                # Write context data to the output
                output_context_path = output_frames_path.parent / 'context.json'
                store_json(output_context_path, dict(
                    sequence_name = sequence_name,
                    shot_name = shot_name,
                    render_layer_name = render_layer_name,
                    first_frame = render_range.first_frame,
                    last_frame = render_range.last_frame,
                    step_size = render_range.step_size
                ))

def create(scene, name):
    node_type = ns.find_node_type('build_comp', 'Cop')
    assert node_type is not None, 'Could not find build_comp node type'
    native = scene.node(name)
    if native is not None: return BuildComp(native)
    return BuildComp(scene.createNode(node_type.name(), name))

def on_created(raw_node):

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('build_comp', 'Cop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = BuildComp(raw_node)

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

def update():
    raw_node = hou.pwd()
    node = BuildComp(raw_node)
    node.update()

def stop():
    raw_node = hou.pwd()
    node = BuildComp(raw_node)
    node.stop()

def preview():
    raw_node = hou.pwd()
    node = BuildComp(raw_node)
    node.preview()

def render():
    raw_node = hou.pwd()
    node = BuildComp(raw_node)
    node.render()

def submit():
    raw_node = hou.pwd()
    node = BuildComp(raw_node)
    node.submit()