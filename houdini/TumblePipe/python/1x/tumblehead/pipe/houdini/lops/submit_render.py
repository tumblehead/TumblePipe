from tempfile import TemporaryDirectory
from pathlib import Path
import shutil

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
    get_workfile_context,
    ShotContext,
    ShotEntity
)

api = default_client()

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
        return api.config.list_render_layer_names(sequence_name, shot_name)
    
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
    
    def list_aov_names(self):
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        render_layer_name = self.get_render_layer_name()
        export_path = latest_render_layer_export_path(
            sequence_name,
            shot_name,
            'light',
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
    
    def get_shot_department_name(self):
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
        if render_layer_name not in render_layer_names: return None
        return render_layer_name

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
    
    def get_priority(self):
        return self.parm('priority').eval()
    
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
        
    def get_aov_names(self):
        all_aov_names = self.list_aov_names()
        selected_aov_names = self.parm('aovs').eval().split(' ')
        match self.parm('aovs_mode').eval():
            case 'all': return all_aov_names
            case 'include':
                return [
                    aov_name
                    for aov_name in selected_aov_names
                    if aov_name in all_aov_names
                ]
            case 'exclude':
                return [
                    aov_name
                    for aov_name in all_aov_names
                    if aov_name not in selected_aov_names
                ]
            case aovs_mode:
                assert False, f'Unknown aovs mode token: {aovs_mode}'
    
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
    
    def get_slapcomp_path(self):
        mode = self.parm('slapcomp_mode').eval()
        match mode:
            case 'none':
                return None
            case 'cops':
                # TODO: Not implemented yet
                return None
            case 'file':
                slapcomp_path = self.parm('slapcomp_file').eval()
                if len(slapcomp_path) == 0: return None
                slapcomp_path = Path(slapcomp_path)
                if not slapcomp_path.exists(): return None
                return slapcomp_path
            case _:
                assert False, f'Unknown slapcomp mode token: {mode}'
    
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
        match frame_range_source:
            case 'from_config': self.parm('frame_range').set('from_config')
            case 'from_settings': self.parm('frame_range').set('from_settings')
            case _: assert False, f'Unknown frame range source: {frame_range_source}'
    
    def set_frame_range(self, frame_range):
        self.parm('frame_settingsx').set(frame_range.start_frame)
        self.parm('frame_settingsy').set(frame_range.end_frame)
        self.parm('roll_settingsx').set(frame_range.start_roll)
        self.parm('roll_settingsy').set(frame_range.end_roll)
    
    def set_pool_name(self, pool_name):
        pool_names = self.list_pool_names()
        if pool_name not in pool_names: return
        self.parm('pool').set(pool_name)
    
    def set_priority(self, priority):
        assert priority >= 0 and priority <= 100, 'Invalid priority'
        self.parm('priority').set(priority)
    
    def set_batch_size(self, batch_size):
        assert batch_size >= 1, 'Invalid batch size'
        self.parm('batchsize').set(batch_size)
    
    def set_submit_partial(self, submit_partial):
        self.parm('submit_partial').set(submit_partial)
    
    def set_submit_full(self, submit_full):
        self.parm('submit_full').set(submit_full)
    
    def preview(self):

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        render_layer_name = self.get_render_layer_name()

        # Set the preview parameters
        self.parm('preview_sequence').set(sequence_name)
        self.parm('preview_shot').set(shot_name)
        self.parm('preview_render_layer').set(render_layer_name)

        # Execute the preview
        self.native().node('preview').parm('build').pressButton()

    def submit(self):

        # Import the job module
        from importlib import reload
        from tumblehead.farm.jobs.houdini.stage_render import (
            job as stage_render_job
        )
        reload(stage_render_job)

        # Check if scene has been previewed
        stage = self.native().stage()
        root = stage.GetPseudoRoot()
        render_prim = root.GetPrimAtPath('/Render')
        if not render_prim.IsValid(): self.preview()

        # Get the entity
        entity = ShotEntity(
            self.get_sequence_name(),
            self.get_shot_name(),
            self.get_shot_department_name()
        )

        # Prepare settings
        user_name = get_user_name()
        pool_name = self.get_pool_name()
        priority = self.get_priority()
        render_layer_name = self.get_render_layer_name()
        render_department_name = self.get_render_department_name()
        samples = self.get_samples()
        aov_names = self.get_aov_names()
        slapcomp_path = self.get_slapcomp_path()
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
            denoise = self.get_partial_denoise()
            tasks['partial_render'] = dict(
                denoise = denoise,
                channel_name = 'previews'
            )
        
        # Maybe add full render task
        if self.get_submit_full():
            denoise = self.get_full_denoise()
            tasks['full_render'] = dict(
                denoise = denoise,
                channel_name = 'renders'
            )

        # Open temporary directory
        root_temp_path = fix_path(api.storage.resolve('temp:/'))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Copy the slapcomp file
            temp_slapcomp_path = temp_path / 'slapcomp.bgeo.sc'
            relative_slapcomp_path = (
                None if slapcomp_path is None else
                temp_slapcomp_path .relative_to(temp_path)
            )
            if slapcomp_path is not None:
                shutil.copyfile(slapcomp_path, temp_slapcomp_path)

            # Save the render settings
            render_settings_path = temp_path / 'render_settings.json'
            relative_render_settings_path = (
                render_settings_path
                .relative_to(temp_path)
            )
            store_json(render_settings_path, dict(
                layer_names = [render_layer_name],
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
                    priority = priority,
                    pool_name = pool_name,
                    render_layer_name = render_layer_name,
                    render_department_name = render_department_name,
                    render_settings_path = path_str(
                        relative_render_settings_path
                    ),
                    slapcomp_path = (
                        None if relative_slapcomp_path is None else
                        path_str(relative_slapcomp_path)
                    ),
                    first_frame = render_range.first_frame,
                    last_frame = render_range.last_frame,
                    step_size = step_size,
                    batch_size = batch_size,
                ),
                tasks = tasks
            ), {
                render_settings_path: relative_render_settings_path
            } | (
                {} if slapcomp_path is None else
                { slapcomp_path: relative_slapcomp_path }
            ))

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

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('submit_render', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = SubmitRender(raw_node)

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
            node.set_shot_department_name(department_name)

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def preview():
    raw_node = hou.pwd()
    node = SubmitRender(raw_node)
    node.preview()

def submit():
    raw_node = hou.pwd()
    node = SubmitRender(raw_node)
    node.submit()