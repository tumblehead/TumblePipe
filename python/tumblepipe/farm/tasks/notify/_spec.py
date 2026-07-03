"""Config schema pieces for the notify task family.

The command block is shared verbatim by ``notify.py`` (worker CLI) and
``task.py`` (submit-side task builder); their outer configs differ (the
builder additionally carries title/priority/pool_name), so only the command
validator lives here.

command = {
    'mode': 'notify'
} | {
    'mode': 'partial',
    'frame_path': 'path/to/frame.####.exr',
    'first_frame': 1,
    'middle_frame': 50,
    'last_frame': 100
} | {
    'mode': 'full',
    'video_path': 'path/to/mp4.mp4'
}
"""

from tumblepipe.farm._common import check_str, check_int


def is_valid_command(command):
    if not isinstance(command, dict): return False
    if 'mode' not in command: return False
    match command['mode']:
        case 'notify': return True
        case 'partial':
            if not check_str(command, 'frame_path'): return False
            if not check_int(command, 'first_frame'): return False
            if not check_int(command, 'middle_frame'): return False
            if not check_int(command, 'last_frame'): return False
        case 'full':
            if not check_str(command, 'video_path'): return False
        case _: return False
    return True
