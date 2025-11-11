from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import (
    fix_path,
    path_str,
    get_user_name,
    default_client
)
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.config import FrameRange
from tumblehead.apps.deadline import Deadline
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.context as ctx
from tumblehead.pipe.paths import (
    latest_render_layer_export_path,
    ShotEntity
)

api = default_client()

def _entity_from_context_json():

    # Path to current workfile
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

def _get_output(node, index):
    connections = node.outputConnectors()
    if len(connections) == 0: return []
    if len(connections) <= index: return []
    return [
        connection.outputNode()
        for connection in connections[index]
    ]

def _get_preview_node(node):
    preview_nodes = _get_output(node, 1)
    if len(preview_nodes) == 0: return None
    return preview_nodes[0]

def _ensure_preview_node(node):
    preview_node = _get_preview_node(node)
    if preview_node is not None: return preview_node
    preview_node = node.parent().createNode('null', f'{node.name()}_preview')
    preview_node.setInput(0, node, 1)
    node.parent().layoutChildren(items = [node, preview_node])
    return preview_node

class SubmitRender(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_sequence_names(self):
        return api.config.list_sequence_names()
    
    def list_shot_names(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return []
        return api.config.list_shot_names(sequence_name)
    
    def list_shot_department_names(self):
        shot_department_names = api.config.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/submit_render')
        return [
            department_name
            for department_name in default_values['departments']['shot']
            if department_name in shot_department_names
        ]

    def list_render_layer_names(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return []
        shot_name = self.get_shot_name()
        if shot_name is None: return []
        render_layer_names = api.config.list_render_layer_names(sequence_name, shot_name)
        return render_layer_names
    
    def list_render_department_names(self):
        render_department_names = api.config.list_render_department_names()
        if len(render_department_names) == 0: return
        default_values = api.config.resolve(
            'defaults:/houdini/lops/submit_render'
        )
        return [
            department_name
            for department_name in default_values['departments']['render']
            if department_name in render_department_names
        ]

    def list_pool_names(self):
        try: deadline = Deadline()
        except: return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0: return []
        default_values = api.config.resolve(
            'defaults:/houdini/lops/submit_render'
        )
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
        ]
    
    def list_aov_names(self, render_layer_name):
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_shot_department_name()
        export_path = latest_render_layer_export_path(
            sequence_name,
            shot_name,
            department_name,
            render_layer_name
        )
        if export_path is None: return []
        context_path = export_path / 'context.json'
        context_data = load_json(context_path)
        if context_data is None: return []
        layer_info = ctx.find_output(context_data,
            context = 'render_layer',
            render_layer = render_layer_name
        )
        if layer_info is None: return []
        return layer_info['parameters']['aov_names']
    
    def get_entity_source(self):
        return self.parm('entity_source').eval()

    def get_sequence_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                if entity_data is not None:
                    return entity_data.sequence_name
            case 'from_settings':
                pass
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

        # Fall back to settings
        sequence_names = self.list_sequence_names()
        if len(sequence_names) == 0: return None
        sequence_name = self.parm('sequence').eval()
        if len(sequence_name) == 0: return sequence_names[0]
        if sequence_name not in sequence_names: return None
        return sequence_name

    def get_shot_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                if entity_data is not None:
                    return entity_data.shot_name
            case 'from_settings':
                pass
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

        # Fall back to settings
        shot_names = self.list_shot_names()
        if len(shot_names) == 0: return None
        shot_name = self.parm('shot').eval()
        if len(shot_name) == 0: return shot_names[0]
        if shot_name not in shot_names: return None
        return shot_name

    def get_shot_department_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                if entity_data is not None:
                    return entity_data.department_name
            case 'from_settings':
                pass
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

        # Fall back to settings
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return None
        shot_department_name = self.parm('shot_department').eval()
        if len(shot_department_name) == 0: return shot_department_names[0]
        if shot_department_name not in shot_department_names: return None
        return shot_department_name

    def get_render_layer_name(self):
        render_layer_names = self.list_render_layer_names()
        if len(render_layer_names) == 0: return None
        render_layer_name = self.parm('render_layer').eval()
        if len(render_layer_name) == 0: return render_layer_names[0]
        if render_layer_name == 'all': return 'all'
        if render_layer_name not in render_layer_names: return None
        return render_layer_name

    def get_render_layer_names(self):
        render_layer_name = self.get_render_layer_name()
        if render_layer_name is None: return []
        if render_layer_name != 'all': return [render_layer_name]
        return self.list_render_layer_names()

    def get_render_department_name(self):
        render_department_names = self.list_render_department_names()
        if len(render_department_names) == 0: return None
        render_department_name = self.parm('render_department').eval()
        if len(render_department_name) == 0: return render_department_names[0]
        if render_department_name not in render_department_names: return None
        return render_department_name

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
    
    def get_priority_full(self):
        return self.parm('full_priority').eval()

    def get_priority_partial(self):
        return self.parm('partial_priority').eval()

    def get_step_size(self):
        return self.parm('stepsize').eval()
    
    def get_batch_size(self):
        return self.parm('batchsize').eval()
    
    def get_samples(self):
        render_settings = self.parm('render_settings').eval()
        match render_settings:
            case 'from_export':
                stage = self.native().node('preview').stage()
                root = stage.GetPseudoRoot()
                render_settings_prim = root.GetPrimAtPath(
                    '/Render/rendersettings'
                )
                if not render_settings_prim.IsValid(): return None
                samples_attribute = render_settings_prim.GetAttribute(
                    'karma:global:pathtracedsamples'
                )
                if not samples_attribute.IsValid(): return None
                return samples_attribute.Get()
            case 'from_settings':
                return self.parm('samples').eval()
            case _: assert False, f'Unknown render settings token: {render_settings}'
        
    def get_tile_count(self):
        return int(self.parm('tile_count').eval())
    
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
    
    def get_partial_denoise(self):
        return bool(self.parm('partial_denoise_task').eval())

    def get_full_denoise(self):
        return bool(self.parm('full_denoise_task').eval())

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

    def set_shot_department_name(self, shot_department_name):
        shot_department_names = self.list_shot_department_names()
        if shot_department_name not in shot_department_names: return
        self.parm('shot_department').set(shot_department_name)
    
    def set_render_layer_name(self, render_layer_name):
        render_layer_names = self.list_render_layer_names()
        if render_layer_name not in render_layer_names: return
        self.parm('render_layer').set(render_layer_name)
    
    def set_render_department_name(self, render_department_name):
        render_department_names = self.list_render_department_names()
        if render_department_name not in render_department_names: return
        self.parm('render_department').set(render_department_name)
    
    def set_frame_range_source(self, frame_range_source):
        valid_sources = ['from_config', 'from_settings']
        if frame_range_source not in valid_sources: return
        self.parm('frame_range_source').set(frame_range_source)
    
    def set_frame_range(self, frame_range):
        self.parm('frame_settingsx').set(frame_range.start_frame)
        self.parm('frame_settingsy').set(frame_range.end_frame)
        self.parm('roll_settingsx').set(frame_range.start_roll)
        self.parm('roll_settingsy').set(frame_range.end_roll)
    
    def set_pool_name(self, pool_name):
        pool_names = self.list_pool_names()
        if pool_name not in pool_names: return
        self.parm('pool').set(pool_name)
    
    def set_priority_full(self, priority):
        assert priority >= 0 and priority <= 100, 'Invalid priority'
        self.parm('full_priority').set(priority)

    def set_priority_partial(self, priority):
        assert priority >= 0 and priority <= 100, 'Invalid priority'
        self.parm('partial_priority').set(priority)

    def set_batch_size(self, batch_size):
        assert batch_size >= 1, 'Invalid batch size'
        self.parm('batchsize').set(batch_size)
    
    def set_submit_partial(self, submit_partial):
        self.parm('submit_partial').set(submit_partial)
    
    def set_submit_full(self, submit_full):
        self.parm('submit_full').set(submit_full)
    
    def build_preview(self):

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        render_layer_name = self.get_render_layer_name()
        if render_layer_name == 'all':
            render_layer_names = self.list_render_layer_names()
            render_layer_name = render_layer_names[0]

        # Set the preview parameters
        self.parm('preview_sequence').set(sequence_name)
        self.parm('preview_shot').set(shot_name)
        self.parm('preview_render_layer').set(render_layer_name)

        # Create the preview null node
        preview_node = _ensure_preview_node(self.native())
        preview_node.setSelected(True, clear_all_selected=True)
        preview_node.setDisplayFlag(True)

        # Execute the preview
        self.native().node('preview').parm('build').pressButton()

    def submit_farm(self):

        # Import the job module
        from tumblehead.farm.jobs.houdini.stage_render import (
            job as stage_render_job
        )

        # Submit the job
        return self._submit(stage_render_job)
    
    def submit_cloud(self):

        # Import the job module
        from tumblehead.farm.jobs.houdini.cloud_stage_render import (
            job as cloud_stage_render_job
        )

        # Submit the job
        return self._submit(cloud_stage_render_job)

    def _submit(self, stage_render_job):

        # Get the entity
        entity = ShotEntity(
            self.get_sequence_name(),
            self.get_shot_name(),
            self.get_shot_department_name()
        )

        # Get render layer names to process
        render_layer_names = self.get_render_layer_names()
        if len(render_layer_names) == 0:
            raise ValueError("No render layers to submit")

        # Prepare common settings
        user_name = get_user_name()
        pool_name = self.get_pool_name()
        full_priority = self.get_priority_full()
        partial_priority = self.get_priority_partial()
        render_department_name = self.get_render_department_name()
        samples = self.get_samples()
        tile_count = self.get_tile_count()
        frame_range = self.get_frame_range()
        render_range = frame_range.full_range()
        step_size = self.get_step_size()
        batch_size = self.get_batch_size()
        timestamp = dt.datetime.now()

        # Collect AOVs for all render layers
        all_aov_names = []
        for render_layer_name in render_layer_names:
            layer_aov_names = self.list_aov_names(render_layer_name)
            all_aov_names.extend(layer_aov_names)

        # Remove duplicates while preserving order
        seen = set()
        aov_names = []
        for aov in all_aov_names:
            if aov in seen: continue
            seen.add(aov)
            aov_names.append(aov)

        # Prepare tasks
        tasks = dict()

        # Add the stage task
        tasks['stage'] = dict(
            priority = full_priority,
            channel_name = 'exports'
        )

        # Maybe add partial render task
        if self.get_submit_partial():
            denoise = self.get_partial_denoise()
            tasks['partial_render'] = dict(
                priority = partial_priority,
                denoise = denoise,
                channel_name = 'previews'
            )

        # Maybe add full render task
        if self.get_submit_full():
            denoise = self.get_full_denoise()
            tasks['full_render'] = dict(
                priority = full_priority,
                denoise = denoise,
                channel_name = 'renders'
            )

        # Open temporary directory
        root_temp_path = fix_path(api.storage.resolve('temp:/'))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Save the render settings
            render_settings_path = temp_path / 'render_settings.json'
            relative_render_settings_path = (
                render_settings_path
                .relative_to(temp_path)
            )
            store_json(render_settings_path, dict(
                layer_names = render_layer_names,
                aov_names = aov_names,
                overrides = {
                    'karma:global:pathtracedsamples': samples
                }
            ))

            # Submit the job
            stage_render_job.submit(dict(
                entity = entity.to_json(),
                settings = dict(
                    user_name = user_name,
                    purpose = 'render',
                    pool_name = pool_name,
                    render_layer_names = render_layer_names,
                    render_department_name = render_department_name,
                    render_settings_path = path_str(
                        relative_render_settings_path
                    ),
                    tile_count = tile_count,
                    first_frame = render_range.first_frame,
                    last_frame = render_range.last_frame,
                    step_size = step_size,
                    batch_size = batch_size,
                ),
                tasks = tasks
            ), {
                render_settings_path: relative_render_settings_path
            })

        # Update node comment
        native = self.native()
        layers_text = ', '.join(render_layer_names)
        new_comment = (
            f'{user_name} submitted:\n'
            f'{layers_text}\n'
            f'{samples} samples\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M")}\n'
        )
        native.setComment(new_comment)
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)
    
def create(scene, name):
    node_type = ns.find_node_type('submit_render', 'Lop')
    assert node_type is not None, 'Could not find submit_render node type'
    native = scene.node(name)
    if native is not None: return SubmitRender(native)
    return SubmitRender(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Change entity source to settings if we have no context
    entity = _entity_from_context_json()
    if entity is not None: return
    node = SubmitRender(raw_node)
    node.set_entity_source('from_settings')

def build_preview():
    raw_node = hou.pwd()
    node = SubmitRender(raw_node)
    node.build_preview()

def submit_farm():
    raw_node = hou.pwd()
    node = SubmitRender(raw_node)
    node.submit_farm()