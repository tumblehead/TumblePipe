"""Config schema of the playblast pipeline (``pipelines/playblast.py``,
whose docstring shows the config): a shot's farm playblast and its notify."""

from tumblepipe.farm.checks import (
    check_int,
    check_list,
    check_str,
    is_int,
    valid_entity,
    valid_frame_range,
)
from tumblepipe.farm.specs import playblast as playblast_spec


def _valid_settings(settings) -> bool:
    if not valid_frame_range(settings): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'purpose'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_int(settings, 'priority'): return False
    if not check_str(settings, 'input_path'): return False
    if not check_int(settings, 'fps'): return False
    if not check_list(settings, 'res'): return False
    if len(settings['res']) != 2: return False
    if not all(is_int(value) for value in settings['res']): return False
    if not playblast_spec.valid_look(settings.get('look')): return False
    if not check_str(settings, 'channel_name'): return False
    return True


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not _valid_settings(config.get('settings')): return False
    return True
