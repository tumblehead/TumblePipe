"""Config schemas of the playblast job (``jobs/playblast.py``) and its worker
(``workers/playblast.py``).

A playblast is monolithic: unlike a render (frames chunked across many
Deadline tasks), the mp4 encode needs every frame, so one task renders the
whole range and encodes the video. It renders with the look's renderer: the
viewport look (hdThPlayblast, on the CPU) or Karma XPU in a preview mode (see
``workers.playblast.xpu_args``).

config = {
    'title': 'string',
    'priority': 'int',
    'pool_name': 'string',
    'first_frame': 'int',
    'last_frame': 'int',
    'step_size': 'int',
    'fps': 'int',
    'res': ['int', 'int'],
    'look': {                      # see valid_look
        'renderer': 'viewport' | 'karma',
        'shading': 'lit' | 'clay' | 'albedo' | 'occlusion',  # viewport only
        'lighting': 'scene' | 'headlight',  # viewport only
        'cull': 'off' | 'back',    # viewport only
        'transparency': 'on' | 'off',  # viewport only
        'light': 'dome' | 'headlight' | 'scene',   # karma only
        'samples': 'int',          # karma only
        'materials': 'bool',       # karma only: scene materials, not clay
        'motion_blur': 'bool',     # viewport only
        'dof': 'bool',             # viewport only
        'blur_samples': 'int'      # viewport only, BLUR_SAMPLES_RANGE
    },
    'input_path': 'string',        # collapsed staged USD, relative to job data
    'output_paths': ['string']     # versioned playblast mp4 + rolling daily mp4
}

The worker's context is the config without the job header.
"""

from tumblepipe.farm.checks import (
    check_bool,
    check_int,
    check_list,
    check_str,
    check_str_list,
    is_int,
    valid_frame_range,
    valid_job_header,
)
from tumblepipe.pipe.usd.render_settings import PLAYBLAST_LIGHTS

# What renders a playblast:
#   viewport -- hdThPlayblast (src/delegate), our Hydra render delegate: the
#               viewport's look of the scene's display colours and textures,
#               shaded as the look's 'shading' says, on the CPU, with no
#               licence. The default.
#   karma    -- Karma XPU in a preview mode, lit and sampled by the look's
#               other keys. Takes a Karma licence and an NVIDIA GPU.
PLAYBLAST_RENDERERS = ('viewport', 'karma')

# How the viewport renderer shades (hdThPlayblast's composite::Shading), every
# one with ambient occlusion:
#   lit       -- the surface colours under key, fill and rim lights fixed to
#                the camera. The default.
#   clay      -- the same lights on a uniform grey.
#   albedo    -- the surface colours alone, unlit.
#   occlusion -- white, darkened only by the ambient occlusion.
PLAYBLAST_SHADINGS = ('lit', 'clay', 'albedo', 'occlusion')

# What lights the viewport renderer's frame (hdThPlayblast's th:lighting,
# TH_PLAYBLAST_LIGHTING), for the shadings that are lit:
#   scene     -- the scene's own lights (distant, sphere, disk, rect,
#                cylinder and dome) when it has any visible, else the
#                headlight rig. The default.
#   headlight -- key, fill and rim lights fixed to the camera, always.
# Not the look's 'light', which is Karma's.
PLAYBLAST_LIGHTINGS = ('scene', 'headlight')

# Which faces the viewport renderer leaves out (hdThPlayblast's th:cull,
# TH_PLAYBLAST_CULL):
#   off  -- only what the scene's own cull styles ask; a playblast through
#           husk culls nothing. The default.
#   back -- every mesh's back faces, so a single-sided wall seen from
#           behind is see-through (Discord, Martin 2026-11-11).
PLAYBLAST_CULLS = ('off', 'back')

# Whether the viewport renderer shows through see-through surfaces
# (hdThPlayblast's th:transparency, TH_PLAYBLAST_TRANSPARENCY):
#   on  -- a surface whose material's opacity or transmission, or whose
#          display opacity, is under 1 is blended over what is behind it.
#          The default.
#   off -- every surface drawn opaque, whatever its opacity: eyes behind a
#          cornea, a room behind a window, drawn as solid surfaces
#          (Discord, Martin 2026-11-12).
PLAYBLAST_TRANSPARENCIES = ('on', 'off')

# The look Farm Submit's playblast defaults add up to (farm.submit.settings;
# tests/test_submit_settings.py pins that).
DEFAULT_LOOK = {
    'renderer': 'viewport', 'shading': 'lit', 'lighting': 'scene', 'cull': 'off',
    'transparency': 'on',
    'light': 'dome', 'samples': 32, 'materials': False,
    'motion_blur': False, 'dof': True, 'blur_samples': 16,
}

# The viewport renderer's blurs (hdThPlayblast's blur module): motion blur
# over the camera's shutter, depth of field from its lens, each averaged
# over a look's blur_samples images (2 to 64, rounded up to a power of
# two). Depth of field is on by default, as in Houdini's viewport: a camera with no F-Stop
# or Focus Distance draws sharp anyway (Discord, Auguu 2026-11-11: the
# playblast lost the viewport's defocus).
BLUR_SAMPLES_RANGE = (2, 64)


def valid_look(look) -> bool:
    """A playblast look: its renderer, the viewport's shading, lighting,
    culling, transparency and blurs, how Karma lights it, its samples, clay
    or materials."""
    if not isinstance(look, dict): return False
    if look.get('renderer') not in PLAYBLAST_RENDERERS: return False
    if look.get('shading') not in PLAYBLAST_SHADINGS: return False
    if look.get('lighting') not in PLAYBLAST_LIGHTINGS: return False
    if look.get('cull') not in PLAYBLAST_CULLS: return False
    if look.get('transparency') not in PLAYBLAST_TRANSPARENCIES: return False
    if not check_str(look, 'light'): return False
    if look['light'] not in PLAYBLAST_LIGHTS: return False
    if not check_int(look, 'samples'): return False
    if look['samples'] < 1: return False
    if not check_bool(look, 'materials'): return False
    for key in ('motion_blur', 'dof'):
        if not check_bool(look, key): return False
    if not check_int(look, 'blur_samples'): return False
    low, high = BLUR_SAMPLES_RANGE
    return low <= look['blur_samples'] <= high


def is_valid_context(config) -> bool:
    if not valid_frame_range(config): return False
    if not check_int(config, 'fps'): return False
    if not check_list(config, 'res'): return False
    if len(config['res']) != 2: return False
    if not all(is_int(value) for value in config['res']): return False
    if not valid_look(config.get('look')): return False
    if not check_str(config, 'input_path'): return False
    if not check_str_list(config, 'output_paths'): return False
    if len(config['output_paths']) == 0: return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    return is_valid_context(config)
