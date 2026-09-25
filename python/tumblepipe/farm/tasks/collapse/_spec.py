"""Config schema for the collapse task family.

Shared by ``collapse.py`` (worker CLI), ``collapse_houdini.py`` (the hython
half) and ``task.py`` (submit-side task builder).

A collapse task runs *after* a submission's publish chain and staged build.
It snapshots the entity's freshly built staged stage for the previews the
submission asked for — the render channels, the playblast, or both — and then
submits those preview jobs itself, the way the ``stage`` task submits its
render. Snapshotting at submit time instead is what made a publish + preview
submission preview the previous publish (see ``farm/jobs/houdini/_preview.py``).

config = {
    'entity': {
        'uri': 'entity:/shots/sequence/shot',
        'department': 'string'          # the preview cut, for the task title
    },
    'settings': {
        'user_name': 'string',
        'pool_name': 'string',
        'priority': 'int'
    },
    'render': {                          # optional
        'department': 'string',          # cut: pool departments up to this one
        'variant_names': ['string'],
        'overrides': {'karma:attr': value},
        'task_key': 'full_render' | 'partial_render',
        'pool_name': 'string',
        'priority': 'int',
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'batch_size': 'int',
        'denoise': 'bool',
        'copy_to_edit': 'bool'
    },
    'playblast': {                       # optional
        'department': 'string',
        'pool_name': 'string',
        'priority': 'int',
        'res': ['int', 'int']
    }
}

At least one of ``render`` / ``playblast`` must be present.
"""

from tumblepipe.farm._common import (
    valid_entity,
    check_str,
    check_int,
    check_bool,
    check_list,
    is_int,
)
from tumblepipe.config.channels import has_channel_names_key

RENDER_TASK_KEYS = ('full_render', 'partial_render')


def _valid_settings(settings):
    if not isinstance(settings, dict): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_int(settings, 'priority'): return False
    return True


def _valid_render(render):
    if not isinstance(render, dict): return False
    if not check_str(render, 'department'): return False
    if not has_channel_names_key(render): return False
    if not isinstance(render.get('overrides', {}), dict): return False
    if render.get('task_key') not in RENDER_TASK_KEYS: return False
    if not check_str(render, 'pool_name'): return False
    for key in ('priority', 'tile_count', 'first_frame', 'last_frame', 'batch_size'):
        if not check_int(render, key): return False
    if not check_bool(render, 'denoise'): return False
    if not check_bool(render, 'copy_to_edit'): return False
    return True


def _valid_playblast(playblast):
    if not isinstance(playblast, dict): return False
    if not check_str(playblast, 'department'): return False
    if not check_str(playblast, 'pool_name'): return False
    if not check_int(playblast, 'priority'): return False
    if not check_list(playblast, 'res'): return False
    if len(playblast['res']) != 2: return False
    if not all(is_int(value) for value in playblast['res']): return False
    return True


def is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not _valid_settings(config.get('settings')): return False
    has_render = 'render' in config
    has_playblast = 'playblast' in config
    if not (has_render or has_playblast): return False
    if has_render and not _valid_render(config['render']): return False
    if has_playblast and not _valid_playblast(config['playblast']): return False
    return True
