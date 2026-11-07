"""Config schemas of the Render Edit job (``jobs/edit_render.py``) and its
worker (``workers/edit_render.py``), which renders a published edit version
with ``th_player --render`` and encodes it into one mp4.

config = {
    'title': 'render edit Main v0004',
    'priority': 50,
    'pool_name': 'general',
    'edit_uri': 'entity:/edits/Main',
    'version': 'v0004',
    'output_path': 'path/to/Main_edit_v0004.mp4'
}

The worker renders the whole edit itself, so its context is the config
without the job header.

What ``th_player --render`` writes beside its frames, ``render.json``:

render_info = {
    'fps': 24,
    'frames': 90,
    'width': 1920,
    'height': 1080,
    'start_timecode': '01:00:00:00',
    'audio': 'edit.wav'             # or None, when the edit has no sound
}
"""

import re

from tumblepipe import naming
from tumblepipe.farm.checks import (
    check_str,
    is_int,
    valid_job_header,
)
from tumblepipe.util.uri import Uri

#: What the player names its outputs in the render folder.
RENDER_INFO_NAME = 'render.json'
FRAMES_PATTERN = 'edit.%04d.png'

_TIMECODE = re.compile(r'^\d{2}:\d{2}:\d{2}[:;]\d{2}$')


def is_edit_uri(value) -> bool:
    """Whether ``value`` names an edit, ``entity:/edits/<name>``."""
    if not isinstance(value, str):
        return False
    try:
        uri = Uri.parse(value)
    except ValueError:
        return False
    return uri.purpose == 'entity' and len(uri.segments) == 2 and uri.segments[0] == 'edits'


def is_valid_context(config) -> bool:
    if not isinstance(config, dict): return False
    if not is_edit_uri(config.get('edit_uri')): return False
    if not check_str(config, 'version'): return False
    if not naming.is_valid_version_name(config['version']): return False
    if not check_str(config, 'output_path'): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    return is_valid_context(config)


def _positive_int(info: dict, key: str) -> bool:
    return is_int(info.get(key)) and info[key] > 0


def is_valid_render_info(info) -> bool:
    """Whether ``info`` is a ``render.json`` the worker can encode."""
    if not isinstance(info, dict): return False
    for key in ('fps', 'frames', 'width', 'height'):
        if not _positive_int(info, key): return False
    timecode = info.get('start_timecode')
    if not isinstance(timecode, str) or not _TIMECODE.match(timecode): return False
    audio = info.get('audio')
    if audio is not None and not (isinstance(audio, str) and audio): return False
    return True
