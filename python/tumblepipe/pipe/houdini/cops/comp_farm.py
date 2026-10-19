"""th::build_comp on the farm: submit the comp as a composite job.

The job renders the comp's `render` ROP from a copy of the workfile saved
with the comp switched to its full-resolution render source.
"""

from tempfile import TemporaryDirectory
from pathlib import Path
import shutil

import hou

from tumblepipe.api import path_str, get_user_name, api
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini import playbar
from tumblepipe.pipe.houdini.scene_context import entity_in_contexts, scene_context
from tumblepipe.pipe.houdini.cops.comp_aovs import Source


class _SourceContext:
    """Switch the comp to ``source`` (and update it) for the duration."""

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


def submit(node):
    """Submit ``node`` (a BuildComp) as a composite farm job."""

    # Import the job module
    from tumblepipe.farm.pipelines import composite as composite_job

    # The farm job renders the workfile's own shot. A Multi's workfile
    # records the Multi, and a composite renders one shot: the farm job
    # would take the group URI as a shot and resolve its AOV paths
    # against an entity that does not exist. Submit Jobs is the surface
    # that expands a Multi into its members.
    context = scene_context()
    shot_uri = entity_in_contexts(context, contexts=('shots',))
    if shot_uri is None:
        raise node._error(
            'a composite submits from one shot\'s workfile; this scene '
            f'is {context.entity_uri if context else "not a pipeline workfile"}. '
            'Submit a Multi\'s members from the Asset Browser (Submit Jobs).'
        )

    # Convert context to entity_json for farm submission
    entity_json = {
        'uri': str(shot_uri),
        'department': context.department_name
    }

    # Prepare settings
    user_name = get_user_name()
    pool_name = node.get_pool_name()
    priority = node.get_priority()
    workfile_path = Path(hou.hipFile.path())
    node_path = node.native().node('render').path()
    frame_range = node.get_frame_range()
    render_range = frame_range.full_range()
    step_size = node.get_step_size()
    batch_size = node.get_batch_size()

    # Prepare tasks
    tasks = dict()

    # Maybe add partial render task
    if node.get_submit_partial():
        tasks['partial_composite'] = dict(
            channel_name = 'previews'
        )

    # Maybe add full render task
    if node.get_submit_full():
        tasks['full_composite'] = dict(
            channel_name = 'comp'
        )

    # Open temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Set the update mode to manual
        with playbar.update_mode(hou.updateMode.Manual):

            # Set the source to render
            with _SourceContext(node, Source.Render):

                # Save and copy workfile to temporary directory
                input_path = temp_path / 'workfile.hip'
                relative_input_path = (
                    input_path
                    .relative_to(temp_path)
                )
                hou.hipFile.save()
                shutil.copyfile(workfile_path, input_path)

                # Get layer names for composite job
                layer_names = node.get_channel_names()

                # Submit the job
                composite_job.submit(dict(
                    entity = entity_json,
                    settings = dict(
                        user_name = user_name,
                        purpose = 'render',
                        priority = priority,
                        pool_name = pool_name,
                        input_path = path_str(relative_input_path),
                        node_path = node_path,
                        layer_names = layer_names,
                        first_frame = render_range.first_frame,
                        last_frame = render_range.last_frame,
                        step_size = step_size,
                        batch_size = batch_size,
                        # build_comp has no Copy-to-edit option
                        copy_to_edit = False
                    ),
                    tasks = tasks
                ), {
                    input_path: relative_input_path
                })
