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
from tumblehead.config.timeline import FrameRange
from tumblehead.util.io import store_json
from tumblehead.util.uri import Uri
from tumblehead.apps.deadline import Deadline
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.houdini.util as util
from tumblehead.pipe.paths import (
    get_workfile_context,
    entity_from_context
)

api = default_client()

class LookdevStudio(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_pool_names(self):
        try: deadline = Deadline()
        except: return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0: return []
        defaults_uri = Uri.parse_unsafe('defaults:/houdini/lops/submit_render')
        default_values = api.config.get_properties(defaults_uri)
        if default_values is None: return []
        if 'pools' not in default_values: return []
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
        ]
    
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
        
        # Import the job modules
        from importlib import reload
        from tumblehead.farm.jobs.houdini.export_render import (
            job as export_render_job
        )
        reload(export_render_job)

        # Get the entity
        workfile_path = Path(hou.hipFile.path())
        context = get_workfile_context(workfile_path)
        assert context is not None, 'Invalid workfile path'
        entity = entity_from_context(context)

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
        root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Save the work file and copy it to the temporary directory
            input_path = temp_path / 'workfile.hip'
            relative_input_path = input_path.relative_to(temp_path)
            hou.hipFile.save()
            shutil.copyfile(workfile_path, input_path)

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
                layer_names = ['main'],
                aov_names = aov_names,
                overrides = dict()
            ))

            # Submit the job
            export_render_job.submit(dict(
                entity = entity.to_json(),
                settings = dict(
                    user_name = get_user_name(),
                    purpose = 'turntable',
                    pool_name = pool_name,
                    render_layer_name = 'main',
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
    node_type = ns.find_node_type('lookdev_studio', 'Lop')
    assert node_type is not None, 'Could not find lookdev_studio node type'
    native = scene.node(name)
    if native is not None: return LookdevStudio(native)
    return LookdevStudio(scene.createNode(node_type.name(), name))

def submit():
    raw_node = hou.pwd()
    node = LookdevStudio(raw_node)
    node.submit()

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)