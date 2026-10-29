from tempfile import TemporaryDirectory
from pathlib import Path
import shutil

import hou

from tumblepipe.api import (
    path_str,
    get_user_name,
    api
)
from tumblepipe.util.io import store_json
from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini import prim_queries
from tumblepipe.pipe.houdini.farm_node import FarmNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode
from tumblepipe.pipe.houdini.scene_context import entity_in_contexts, scene_context

class LookdevStudio(FrameRangeNode, FarmNode, ns.Node):

    # The farm turntable range: First/Last only, no source menu, no roll.
    FRAME_RANGE_PARM = None
    FRAME_SETTINGS_PARM = 'farm_frame_settings'
    ROLL_SETTINGS_PARM = None
    POOL_PARM = 'farm_pool'
    PRIORITY_PARM = 'farm_priority'
    BATCH_SIZE_PARM = 'farm_batchsize'

    def get_step_size(self):
        return self.parm('farm_stepsize').eval()

    def get_aov_names(self):
        root = self.native().node('OUT/stage_OUT').stage().GetPseudoRoot()
        return [
            aov_path.rsplit('/', 1)[-1].lower()
            for aov_path in prim_queries.list_render_vars(root)
        ]

    def submit(self):

        # Import the job module
        from tumblepipe.farm.pipelines import export_render as export_render_job

        # A turntable renders one asset, from that asset's workfile. A
        # Multi's workfile records the group, which names no entity for the
        # farm to resolve.
        context = scene_context()
        entity_uri = entity_in_contexts(context, contexts=('assets',))
        if entity_uri is None:
            raise self._error(
                'a turntable submits from one asset\'s workfile; this scene '
                f'is {context.entity_uri if context else "not a pipeline workfile"}'
            )

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
        root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
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
                channel_names = ['main'],
                aov_names = aov_names,
                overrides = dict()
            ))

            # Submit the job
            export_render_job.submit(dict(
                entity = dict(
                    uri = str(entity_uri),
                    department = context.department_name
                ),
                settings = dict(
                    user_name = get_user_name(),
                    purpose = 'turntable',
                    pool_name = pool_name,
                    channel_name = 'main',
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

# HDA callbacks ---------------------------------------------------------------
# The viewport helpers below used to live in the HDA's PythonModule.

TURNTABLE_CAMERA = '/cameras/turntable_camera'


def on_created(raw_node):
    ns.set_node_style(raw_node)
    raw_node.setOutputForViewFlag(1)


def _scene_viewer():
    return hou.ui.curDesktop().paneTabOfType(hou.paneTabType.SceneViewer)


def submit_to_farm(kwargs):
    """Submit the turntable with the batch-render switch on for the submit."""
    from tumblepipe.pipe.houdini.ui import farm_submit
    raw_node = kwargs['node']

    def _submit():
        batchrender = raw_node.parm('batchrender')
        batchrender.set(1)
        try:
            LookdevStudio(raw_node).submit()
        finally:
            batchrender.set(0)

    farm_submit.submit('the turntable', 'Submit Turntable', _submit)


def set_view_transform(kwargs):
    scene_viewer = _scene_viewer()
    scene_viewer.setUsingOCIO(True)
    scene_viewer.setOCIODisplayView(display="sRGB - Display", view="ACES 1.0 - SDR Video")


def dive_to_node(kwargs, node_path):
    """Open a floating network editor on the parent of ``node_path``."""
    node = kwargs['node'].node(node_path)
    panel = hou.ui.curDesktop().createFloatingPanel(hou.paneTabType.NetworkEditor)
    panel.setName("Edit Turntable Lights")
    panel.setSize((1500, 1500))
    pane = panel.panes()[-1]
    pane.setShowPaneTabs(False)
    pane.showPaneTabsStow(False)
    tab = panel.paneTabs()[-1]
    tab.setPwd(node.parent())
    tab.setShowNetworkControls(False)


def toggle_render_view(kwargs):
    # render names: 'Houdini VK', 'Karma CPU', 'Karma XPU', 'Storm'
    scene_viewer = _scene_viewer()
    if scene_viewer.currentHydraRenderer() == "Houdini VK":
        scene_viewer.setHydraRenderer('Karma XPU')
    else:
        scene_viewer.setHydraRenderer('Houdini VK')


def _turntable_range(raw_node):
    return (
        raw_node.evalParm("asset_turn_framerangemin"),
        raw_node.evalParm("light_turn_framerangemax"),
    )


def set_timeline_to_loop(kwargs):
    first, last = _turntable_range(kwargs['node'])
    hou.playbar.setFrameRange(first, last)
    hou.playbar.setPlaybackRange(first, last)


def set_view_camera(kwargs):
    _scene_viewer().viewports()[-1].setCamera(TURNTABLE_CAMERA)


def playblast(kwargs):
    """Flipbook the turntable through the turntable camera into MPlay."""
    raw_node = kwargs['node']
    set_view_camera(kwargs)
    scene_viewer = _scene_viewer()
    viewport = scene_viewer.curViewport()
    flipbook_options = scene_viewer.flipbookSettings()
    flipbook_options.useResolution(True)
    flipbook_options.resolution((
        raw_node.evalParm("render_resolutionx"),
        raw_node.evalParm("render_resolutiony"),
    ))
    flipbook_options.frameRange(_turntable_range(raw_node))
    flipbook_options.output("")  # Empty means send to MPlay
    flipbook_options.appendFramesToCurrent(True)
    flipbook_options.antialias(hou.flipbookAntialias.Good)
    viewport.settings().setLighting(hou.viewportLighting.HighQualityWithShadows)
    scene_viewer.flipbook(viewport, flipbook_options)


def apply_slapcomp(kwargs):
    viewport = _scene_viewer().curViewport()
    viewport.setSlapcomp(kwargs['node'].path())


_TRANSFORM_PARMS = (
    "Translate_Camerax",
    "Translate_Cameray",
    "Translate_Cameraz",
    "rotate_camerax",
    "rotate_cameray",
    "rotate_cameraz",
    "asset_angle",
    "light_rotation",
    "asset_offsetx",
    "asset_offsety",
    "asset_offsetz",
)


def reset_transforms(kwargs):
    raw_node = kwargs['node']
    for parm_name in _TRANSFORM_PARMS:
        raw_node.parm(parm_name).set(0)
    raw_node.parm("focalLength").set(35)

