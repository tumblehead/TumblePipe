from tempfile import TemporaryDirectory
from pathlib import Path
import shutil

import hou

from tumblepipe.api import (
    local_path,
    path_str,
    get_user_name,
    api
)
from tumblepipe.config.timeline import FrameRange
from tumblepipe.config.farm import list_pools
from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns
import tumblepipe.pipe.houdini.util as util
from tumblepipe.pipe.paths import load_entity_context

def _entity_from_context_json():
    # Path to current workfile
    file_path = Path(hou.hipFile.path())
    if not file_path.exists(): return None

    # Look for context.json in the workfile directory
    context_json_path = file_path.parent / "context.json"
    if not context_json_path.exists(): return None

    # Load context using shared helper
    context = load_entity_context(context_json_path)
    if context is None: return None

    return context

class LookdevStudio(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_pool_names(self):
        return [pool.name for pool in list_pools()]
    
    def get_frame_range(self):
        first_frame = self.parm('farm_frame_settingsx').eval()
        last_frame = self.parm('farm_frame_settingsy').eval()
        return FrameRange(
            first_frame,
            last_frame,
            0, 0
        )
    
    def get_priority(self):
        return self.parm('farm_priority').eval()
    
    def get_pool_name(self):
        pool_names = self.list_pool_names()
        if len(pool_names) == 0: return None
        pool_name = self.parm('farm_pool').eval()
        if pool_name not in pool_names: return pool_names[0]
        return pool_name
    
    def get_step_size(self):
        return self.parm('farm_stepsize').eval()

    def get_batch_size(self):
        return self.parm('farm_batchsize').eval()

    def get_aov_names(self):
        root = self.native().node('OUT/stage_OUT').stage().GetPseudoRoot()
        return [
            aov_path.rsplit('/', 1)[-1].lower()
            for aov_path in util.list_render_vars(root)
        ]
    
    
    def submit(self):
        
        # Import the job module
        from tumblepipe.farm.jobs.houdini.export_render import (
            job as export_render_job
        )

        # Get the entity
        context = _entity_from_context_json()
        assert context is not None, 'Invalid workfile path'

        # Parameters
        node_path = self.node('OUT/stage_OUT').path()
        frame_range = self.get_frame_range()
        render_range = frame_range.full_range()
        priority = self.get_priority()
        pool_name = self.get_pool_name()
        step_size = self.get_step_size()
        batch_size = self.get_batch_size()
        aov_names = self.get_aov_names()

        # Open temporary directory
        root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Save the work file and copy it to the temporary directory
            input_path = temp_path / 'workfile.hip'
            relative_input_path = input_path.relative_to(temp_path)
            hou.hipFile.save()
            shutil.copyfile(hou.hipFile.path(), input_path)

            # Prepare tasks
            tasks = dict()

            # Add the export task
            tasks['export'] = dict(
                priority = priority,
                input_path = path_str(relative_input_path),
                node_path = node_path,
                channel_name = 'exports'
            )

            # Add the render task
            tasks['full_render'] = dict(
                priority = priority,
                denoise = False,
                channel_name = 'previews'
            )


            # Save the render settings
            render_settings_path = temp_path / 'render_settings.json'
            relative_render_settings_path = (
                render_settings_path
                .relative_to(temp_path)
            )
            store_json(render_settings_path, dict(
                variant_names = ['main'],
                aov_names = aov_names,
                overrides = dict()
            ))

            # Submit the job
            export_render_job.submit(dict(
                entity = dict(
                    uri = str(context.entity_uri),
                    department = context.department_name
                ),
                settings = dict(
                    user_name = get_user_name(),
                    purpose = 'turntable',
                    pool_name = pool_name,
                    variant_name = 'main',
                    render_department_name = 'render',
                    render_settings_path = path_str(
                        relative_render_settings_path
                    ),
                    tile_count = 1,  # No tiling needed for turntable previews
                    first_frame = render_range.first_frame,
                    last_frame = render_range.last_frame,
                    step_size = step_size,
                    batch_size = batch_size
                ),
                tasks = tasks
            ), {
                input_path: relative_input_path,
                render_settings_path: relative_render_settings_path
            })

def create(scene, name):
    return ns.create_node(scene, name, LookdevStudio, 'lookdev_studio')

def submit():
    raw_node = hou.pwd()
    node = LookdevStudio(raw_node)
    node.submit()

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)