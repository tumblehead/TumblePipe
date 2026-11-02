"""Our viewport renderer, hdThPlayblast (src/delegate), as husk runs it: the
farm playblast worker and the in-session th::playblast node both render
through these arguments and this environment."""

import os
from pathlib import Path

from tumblepipe.api import path_str

# The viewport renderer: hdThPlayblast (src/delegate), our own Hydra render
# delegate, shipped under plugins/houdini<major>/ and registered by
# viewport_env rather than by the farm env (farm.env.get_base_env), which is
# built on the submitting machine. It rasterises on the CPU with the viewport's look --
# display colours and textures, shaded as the look's 'shading' says, with
# ambient occlusion -- and husk takes no licence for it: Houdini's licence is
# only checked out for Karma. HideAndReek/010 (6.8M triangles) renders in
# ~0.34 s a frame at 1280x720 and eight samples a pixel; with husk updating
# the stage and its 40 animated meshes, a frame takes 0.4-0.9 s.
#
# --purpose geometry,proxy draws what the viewport draws: proxies where an
# asset has them, never its render-purpose meshes.
#
# --allowed-procedurals none: the delegate draws meshes only, so the curves
# Houdini's procedurals make (hair grooms) never reach the picture, yet husk
# runs every animated one before each frame and swaps its result into the
# stage, which redirties every prim on it. On HideAndReek/010 (three felt
# grooms) that was ~3 s a frame and every mesh rebuilt; without them a frame
# rebuilds only its animated meshes and the picture is the same.
VIEWPORT_DELEGATE = 'HdThPlayblastRendererPlugin'


def viewport_args() -> list[str]:
    """husk's arguments for the viewport renderer."""
    return ['-R', VIEWPORT_DELEGATE, '--purpose', 'geometry,proxy',
            '--allowed-procedurals', 'none']


def viewport_env(env: dict, pipeline_path: Path, houdini_major: int,
                 shading: str = 'lit', blur: tuple[bool, bool, int] = (False, False, 16),
                 fps: int = 24) -> dict:
    """``env`` with the viewport delegate registered for USD, ahead of the
    resolver, and told how to shade (``specs.playblast.PLAYBLAST_SHADINGS``)
    and blur (``specs.playblast.look_blur``: motion blur, depth of field,
    blur samples) at the shot's ``fps``. The delegate reads these TH_PLAYBLAST_*
    variables only where the stage's render settings say nothing; the farm
    playblast's settings prim (render_settings.
    generate_playblast_settings_section) says nothing, so the dialog's look
    decides."""
    motion_blur, dof, blur_samples = blur
    resources = (
        pipeline_path / 'plugins' / f'houdini{houdini_major}'
        / 'hdThPlayblast' / 'resources'
    )
    current = env.get('PXR_PLUGINPATH_NAME')
    return {
        **env,
        'PXR_PLUGINPATH_NAME': (
            f'{path_str(resources)}{os.pathsep}{current}' if current
            else path_str(resources)
        ),
        'TH_PLAYBLAST_SHADING': shading,
        'TH_PLAYBLAST_MOTION_BLUR': '1' if motion_blur else '0',
        'TH_PLAYBLAST_DOF': '1' if dof else '0',
        'TH_PLAYBLAST_BLUR_SAMPLES': str(blur_samples),
        'TH_PLAYBLAST_FPS': str(fps),
    }
