"""Config schemas of the playblast job (``jobs/playblast.py``) and its worker
(``workers/playblast.py``).

A playblast is monolithic: unlike a render (frames chunked across many
Deadline tasks), the mp4 encode needs every frame, so one task renders the
whole range and encodes the video. It renders with the look's renderer: the
viewport look (hdThPlayblast, no licence) or Karma XPU in a preview mode (see
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
        'renderer': 'viewport' | 'karma',   # optional, absent = 'karma'
        'light': 'dome' | 'headlight' | 'scene',   # karma only
        'samples': 'int',          # karma only
        'materials': 'bool'        # karma only: scene materials, not clay
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
#               viewport's look of the scene's display colours and textures
#               under a headlight, on the CPU, with no licence. The default.
#   karma    -- Karma XPU in a preview mode, lit and sampled by the look's
#               other keys. Takes a Karma licence and an NVIDIA GPU.
PLAYBLAST_RENDERERS = ('viewport', 'karma')

# The look a playblast gets when its submission names none: a collapse job
# submitted before the look existed. The dialog's defaults
# (farm.submit.settings) match it; tests/test_submit_settings.py pins that.
DEFAULT_LOOK = {'renderer': 'viewport', 'light': 'dome', 'samples': 32, 'materials': False}


def look_renderer(look: dict) -> str:
    """The renderer of a valid look. A look submitted before the renderer
    existed was asked of Karma, and keeps it."""
    return look.get('renderer', 'karma')


def valid_look(look) -> bool:
    """A playblast look: its renderer, how it is lit, its samples, clay or
    materials."""
    if not isinstance(look, dict): return False
    if look_renderer(look) not in PLAYBLAST_RENDERERS: return False
    if not check_str(look, 'light'): return False
    if look['light'] not in PLAYBLAST_LIGHTS: return False
    if not check_int(look, 'samples'): return False
    if look['samples'] < 1: return False
    if not check_bool(look, 'materials'): return False
    return True


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
