from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import get_user_name, path_str, default_client
from tumblehead.pipe.houdini.lops import import_render_layer
from tumblehead.util.io import store_json, load_json
from tumblehead.config import FrameRange
from tumblehead.pipe.houdini import util
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    get_next_version_path,
    latest_render_layer_export_path,
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

def _sort_aov_names(aov_names):
    beauty = None
    lpes = []
    mattes = []
    mses = []
    other = []
    for aov_name in aov_names:
        name = aov_name.lower()
        if name == 'beauty': beauty = aov_name; continue
        if name.endswith('_mse'): mses.append(aov_name); continue
        if name.startswith('beauty_'): lpes.append(aov_name); continue
        if name.startswith('objid_'): mattes.append(aov_name); continue
        if name.startswith('holdout_'): mattes.append(aov_name); continue
        other.append(aov_name)
    other.sort()
    result = [] if beauty is None else [beauty]
    result += lpes
    result += mattes
    result += mses
    result += other
    return result

class ExportRenderLayer(ns.Node):
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
        default_values = api.config.resolve(
            'defaults:/houdini/lops/export_render_layer'
        )
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

    def get_department_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.department_name
            case 'from_settings':
                department_names = self.list_department_names()
                if len(department_names) == 0: return None
                department_name = self.parm('department').eval()
                if len(department_name) == 0: return department_names[0]
                if department_name not in department_names: return None
                return department_name
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')
    
    def get_render_layer_name(self):
        render_layer_names = self.list_render_layer_names()
        if len(render_layer_names) == 0: return None
        render_layer_name = self.parm('layer').eval()
        if len(render_layer_name) == 0: return render_layer_names[0]
        if render_layer_name not in render_layer_names: return None
        return render_layer_name
    
    def get_frame_range_source(self):
        return self.parm('frame_range').eval()
    
    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_context':
                sequence_name = self.get_sequence_name()
                shot_name = self.get_shot_name()
                frame_range = api.config.get_frame_range(sequence_name, shot_name)
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range token: {frame_range_source}'
    
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
    
    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)
    
    def set_layer_name(self, layer_name):
        layer_names = self.list_render_layer_names()
        if layer_name not in layer_names: return
        self.parm('layer').set(layer_name)

    def execute(self):

        # Nodes
        native = self.native()
        stage_node = self.native().node('IN_stage')

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        render_layer_name = self.get_render_layer_name()
        frame_range, frame_step = self.get_frame_range()
        render_range = frame_range.full_range()
        timestamp = dt.datetime.now()
        user_name = get_user_name()

        # Scrape stage for aovs
        aov_names = list()
        root = stage_node.stage().GetPseudoRoot()
        for aov_path in util.list_render_vars(root):
            aov_names.append(aov_path.rsplit('/', 1)[-1].lower())

        # Paths
        export_path = api.storage.resolve(
            f'export:/shots/{sequence_name}/{shot_name}/render_layers'
            f'/{department_name}/{render_layer_name}'
        )
        version_path = get_next_version_path(export_path)
        version_name = version_path.name
        file_name = (
            f'{sequence_name}_'
            f'{shot_name}_'
            f'{department_name}_'
            f'{render_layer_name}_'
            f'{version_name}.usd'
        )
        file_path = version_path / file_name

        # Export layer
        self.parm('export_lopoutput').set(path_str(file_path))
        self.parm('export_f1').set(render_range.first_frame)
        self.parm('export_f2').set(render_range.last_frame)
        self.parm('export_f3').set(frame_step)
        self.parm('export_execute').pressButton()

        # Store context
        context_path = version_path / 'context.json'
        context = dict(
            inputs = [],
            outputs = [dict(
                context = 'render_layer',
                render_layer = render_layer_name,
                version = version_name,
                timestamp = timestamp.isoformat(),
                user = user_name,
                parameters = {
                    'aov_names': _sort_aov_names(aov_names)
                }
            )]
        )
        store_json(context_path, context)

        # Update node comment
        native.setComment(
            'last export: '
            f'{version_name} \n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")} \n'
            f'by {user_name}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

    def open_location(self):
        sequence_name = self.get_sequence_name() 
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        render_layer_name = self.get_render_layer_name()
        export_path = latest_render_layer_export_path(
            sequence_name,
            shot_name,
            department_name,
            render_layer_name
        )
        if export_path is None: return
        if not export_path.exists(): return
        hou.ui.showInFileBrowser(f'{path_str(export_path)}/')

def create(scene, name):
    node_type = ns.find_node_type('export_render_layer', 'Lop')
    assert node_type is not None, 'Could not find export_render_layer node type'
    native = scene.node(name)
    if native is not None: return ExportRenderLayer(native)
    return ExportRenderLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Change entity source to settings if we have no context
    entity = _entity_from_context_json()
    if entity is not None: return
    node = ExportRenderLayer(raw_node)
    node.set_entity_source('from_settings')

def execute():
    raw_node = hou.pwd()
    node = ExportRenderLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ExportRenderLayer(raw_node)
    node.open_location()