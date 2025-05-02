from tempfile import TemporaryDirectory
from pathlib import Path
import shutil

import hou

from tumblehead.api import (
    path_str,
    to_windows_path,
    default_client
)
from tumblehead.pipe.paths import (
    ShotEntity,
    get_render,
    get_next_frame_path,
    get_workfile_context,
    ShotContext
)
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.houdini.util as util
from tumblehead.apps.deadline import log_progress

api = default_client()

def _ensure_node(context, node_type, name):
    node = context.node(name)
    if node is not None: return node
    return context.createNode(node_type, name)

def _connect(output_node, input_index, input_node, output_index = 0):
    if input_node.input(input_index) is not None: return
    input_node.setInput(input_index, output_node, output_index)

def _aov_included(aov_name):
    name = aov_name.lower()
    if name.startswith('beauty_'): return True
    if name == 'beauty': return True
    return False

def _get_frame_path(framestack_path, frame_index):
    frame_name = str(frame_index).zfill(4)
    return framestack_path.with_name(
        framestack_path.name.replace('$F4', frame_name)
    )

class Slapcomp(ns.Node):
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
        if len(render_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/cops/slapcomp')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in render_department_names
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
        if len(shot_names) == 0: return
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

    def export(self):

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        render_department_name = self.get_render_department_name()
        render_layer_names = api.config.list_render_layer_names(
            sequence_name,
            shot_name
        )
        frame_range = api.config.get_frame_range(sequence_name, shot_name)
        render_range = frame_range.full_range()

        # Get render data
        render = get_render(
            sequence_name,
            shot_name,
            render_department_name
        )
        if render is None: return

        # Find the latest render layer versions
        render_layer_aovs = dict()
        for render_layer_name in render_layer_names:

            # Get latest complete render layer version
            render_layer = render.get_latest_complete_layer(render_layer_name)
            if render_layer is None: continue

            # Store the aov paths
            render_layer_aovs[render_layer_name] = {
                aov_name: aov.get_aov_frame_path('$F4')
                for aov_name, aov in render_layer.aovs.items()
            }
        
        # Get the output path
        output_path = get_next_frame_path(
            ShotEntity(
                sequence_name,
                shot_name
            ),
            render_department_name,
            'slapcomp',
            '$F4',
            'jpg'
        )

        # Export slapcomp
        self._export(
            render_range,
            render_layer_names,
            render_layer_aovs,
            output_path
        )

    def _export(
        self,
        render_range,
        render_layer_names,
        render_layer_aovs,
        output_path
        ):

        # Nodes
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        export_node = context.node('export')

        # Set frame range and playback
        util.set_block_range(render_range)

        # Build render layer comps
        def _create_aov_import_node(render_layer_name, aov):

            # Get aov frame path
            aov_name = aov.label
            aov_frame_path = aov.get_aov_frame_path('$F4')
            aov_render_range = aov.get_frame_range()
            assert aov_frame_path is not None, (
                'Could not find aov frame path for '
                f'{aov_name}'
            )

            # Create import node
            aov_import_node_name = (
                f'{render_layer_name}_'
                f'{aov_name}_'
                'import'
            )
            aov_import_node = _ensure_node(
                dive_node, 'file', aov_import_node_name
            )
            aov_import_node.parm('filename').set(path_str(aov_frame_path))
            aov_import_node.parm('videoframestart').deleteAllKeyframes()
            aov_import_node.parm('videoframestart').set(
                aov_render_range.first_frame
            )
            hou.setFrame(aov_render_range.first_frame)
            aov_import_node.parm('missingdata').set(1)
            aov_import_node.parm('missingcolora').set(0)
            aov_import_node.parm('addaovs').pressButton()

            # Return the import node
            return aov_import_node
        
        layer_nodes = dict()
        for render_layer_name in render_layer_names:
            
            # Get latest complete render layer version
            render_layer = render_layer_aovs.get(render_layer_name)
            if render_layer is None: continue

            # Get aov names
            aov_names = list(render_layer.keys())

            # Find LPE names
            lpe_names = list()
            for aov_name in aov_names:
                name = aov_name.lower()
                if not name.startswith('beauty_'): continue
                lpe_names.append(aov_name)

            # Import layer aovs
            aov_nodes = dict()
            for aov_name in aov_names:

                # Check if aov is included in comp
                if not _aov_included(aov_name): continue

                # Store the import node
                aov = render_layer.get(aov_name)
                assert aov is not None, f'Could not find aov {aov_name}'
                aov_nodes[aov_name] = _create_aov_import_node(
                    render_layer_name, aov
                )
            
            # Make sure we have a color aov
            assert 'beauty' in aov_nodes, (
                'Could not find color aov for '
                f'{render_layer_name}'
            )
        
            # Compose LPEs per render layer
            layer_output_node = None
            for lpe_name in lpe_names:

                # Convert LPE to RGB
                lpe_import_node = aov_nodes[lpe_name]
                rgb_convert_node = _ensure_node(
                    dive_node,
                    'rgbatorgb',
                    f'{render_layer_name}_{lpe_name}_rgb'
                )
                _connect(lpe_import_node, 0, rgb_convert_node)

                # Merge LPEs
                if layer_output_node is None:
                    layer_output_node = (lpe_name, rgb_convert_node)
                else:
                    prev_lpe_name, prev_output_node = layer_output_node
                    add_node = _ensure_node(
                        dive_node,
                        'blend',
                        f'{render_layer_name}_{prev_lpe_name}_{lpe_name}_add'
                    )
                    add_node.parm('mode').set('add')
                    _connect(prev_output_node, 0, add_node)
                    _connect(rgb_convert_node, 1, add_node)
                    layer_output_node = (lpe_name, add_node)
            
            # Store layer output node
            final_layer_output_node = (
                aov_nodes['beauty']
                if layer_output_node is None else
                layer_output_node[1]
            )
            layer_nodes[render_layer_name] = (
                aov_nodes, final_layer_output_node
            )
        
        # Merge render layer comps
        comp_output_node = None
        for render_layer_name in render_layer_names:

            # Get render layer nodes
            if render_layer_name not in layer_nodes: continue
            layer_aov_nodes, layer_output_node = layer_nodes[render_layer_name]

            # Merge render layers
            if comp_output_node is None:
                comp_output_node = (render_layer_name, layer_output_node)
            else:

                # Get render layer alpha
                layer_color_node = layer_aov_nodes['beauty']
                layer_split_node = _ensure_node(
                    dive_node,
                    'channelsplit',
                    f'{render_layer_name}_beauty_split'
                )
                _connect(layer_color_node, 0, layer_split_node)

                # Merge render layers
                prev_render_layer_name, prev_output_node = comp_output_node
                over_node = _ensure_node(
                    dive_node,
                    'blend',
                    f'{prev_render_layer_name}_{render_layer_name}_over'
                )
                over_node.parm('mode').set('over')
                _connect(prev_output_node, 0, over_node)
                _connect(layer_output_node, 1, over_node)
                _connect(
                    layer_split_node, 2,
                    over_node, layer_split_node.outputIndex('alpha')
                )
                comp_output_node = (render_layer_name, over_node)
        
        # Set output node
        if comp_output_node is not None:
            _, final_output_node = comp_output_node
            _connect(final_output_node, 0, output_node)
        
        # Layout nodes
        dive_node.layoutChildren()

        # Export slapcomp frames
        root_temp_path = to_windows_path(api.storage.resolve('temp:/'))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            temp_frame_path = temp_path / 'slapcomp.$F4.jpg'
            export_node.parm('copoutput').set(path_str(temp_frame_path))

            # Export frames
            for frame_index in log_progress(render_range):

                # Skip if already exported
                current_frame_path = _get_frame_path(output_path, frame_index)
                if current_frame_path.exists(): continue

                # Export the frame
                export_node.parm('f1').set(frame_index)
                export_node.parm('f2').set(frame_index)
                export_node.parm('execute').pressButton()
            
            # Copy slapcomp frames to network
            for frame_index in render_range:
                current_output_frame_path = _get_frame_path(
                    output_path, frame_index
                )
                current_temp_frame_path = _get_frame_path(
                    temp_frame_path, frame_index
                )
                if not current_temp_frame_path.exists(): continue
                current_output_frame_path.parent.mkdir(
                    parents=True, exist_ok=True
                )
                shutil.copyfile(
                    current_temp_frame_path,
                    current_output_frame_path
                )

def create(scene, name):
    node_type = ns.find_node_type('slapcomp', 'Cop')
    assert node_type is not None, 'Could not find slapcomp node type'
    native = scene.node(name)
    if native is not None: return Slapcomp(native)
    return Slapcomp(scene.createNode(node_type.name(), name))

def on_created(raw_node):

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('slapcomp', 'Cop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = Slapcomp(raw_node)

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

def export():
    raw_node = hou.pwd()
    node = Slapcomp(raw_node)
    node.export()