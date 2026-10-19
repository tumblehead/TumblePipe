"""Config schemas of the notify job (``jobs/notify.py``) and its worker
(``workers/notify.py``), which posts to a project Discord channel.

config = {
    'title': 'notify',
    'priority': 100,
    'pool_name': 'general',
    'user_name': 'user',
    'channel_name': 'renders',
    'message': 'message',
    'command': {
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
}

The worker's context is the config without the job header.
"""

from tumblepipe.farm.checks import check_int, check_str, valid_job_header


def is_valid_command(command) -> bool:
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


def is_valid_context(config) -> bool:
    if not isinstance(config, dict): return False
    if not check_str(config, 'user_name'): return False
    if not check_str(config, 'channel_name'): return False
    if not check_str(config, 'message'): return False
    if 'command' not in config: return False
    if not is_valid_command(config['command']): return False
    return True


def is_valid_config(config) -> bool:
    if not valid_job_header(config): return False
    return is_valid_context(config)
