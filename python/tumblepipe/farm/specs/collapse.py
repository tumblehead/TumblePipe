"""Config schemas of the collapse job (``jobs/collapse.py``), its worker
(``workers/collapse.py``) and the worker's hython half
(``workers/collapse_houdini.py``). The job stores its config as the
worker's context; the hython config adds the ``output_dir`` it writes to.

A collapse job runs *after* a submission's publish chain and staged build.
It snapshots the entity's freshly built staged stage for the previews the
submission asked for — the render channels, the playblast, or both — and then
submits those preview jobs itself, the way the ``stage`` task submits its
render. Snapshotting at submit time instead is what made a publish + preview
submission preview the previous publish (see ``farm/pipelines/preview.py``).

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
    'render': {                          # optional: a shot's render
        'request': {...},                # specs.render_pipeline.valid_request,
                                         # submitted over the snapshot
        'overrides': {'karma:attr': value}   # baked into the snapshot
    },
    'turntable': {                       # optional, never with render: an
                                         # asset's turntable (#53)
        'department': 'string',
        'channel_names': ['string'],
        'overrides': {'karma:attr': value},
        'task_keys': ['full_render' | 'partial_render'],  # one or both
        'pool_name': 'string',
        'priority': 'int',
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int',
        'denoise': 'bool',
        'copy_to_edit': 'bool',
        'cut_range': ['int', 'int'],     # optional: specs.render_pipeline.valid_cut_range
        'preview_frame_list': ['int']    # optional: specs.render_pipeline.valid_preview_frame_list
    },
    'playblast': {                       # optional
        'department': 'string',
        'pool_name': 'string',
        'priority': 'int',
        'res': ['int', 'int'],
        'look': {...}                    # specs.playblast.valid_look
    }
}

At least one of ``render``, ``turntable`` and ``playblast`` is present,
never both ``render`` and ``turntable``.

A ``turntable`` is an asset's render (#53): the worker snapshots the asset
and builds a ``th::lookdev_studio`` workfile per channel, exports it as the
export job exports a studio's (``export_houdini``), then submits the render
a studio's Submit Render To Farm does, on its ``step_size``. Its preview
frames sit on that step, and it carries no composite.
"""

from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs import repair
from tumblepipe.farm.checks import (
    valid_entity,
    check_str,
    check_int,
    check_bool,
    check_list,
    is_int,
    valid_channel_names,
)
from tumblepipe.farm.specs.playblast import valid_look
from tumblepipe.farm.specs.render_pipeline import (
    RENDER_TASK_KEYS,
    valid_cut_range,
    valid_preview_frame_list,
    valid_request,
)


def _valid_settings(settings):
    if not isinstance(settings, dict): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_int(settings, 'priority'): return False
    return True


def _valid_render(render):
    """A shot's render: the render request its worker submits over the
    snapshot, and the Karma overrides baked into the snapshot's
    render-settings prim."""
    if not isinstance(render, dict): return False
    request = render.get('request')
    if not valid_request(request): return False
    # Only the render chains: the collapse job is the request's one carrier.
    if not set(request['tasks']) <= set(RENDER_TASK_KEYS): return False
    return isinstance(render.get('overrides'), dict)


def _valid_turntable(turntable):
    """An asset's turntable (#53): the studio's frames on its own step,
    never a Frame list, a repair (#92) or a comp."""
    if not isinstance(turntable, dict): return False
    if not check_str(turntable, 'department'): return False
    if not valid_channel_names(turntable): return False
    if not isinstance(turntable.get('overrides'), dict): return False
    task_keys = turntable.get('task_keys')
    if not isinstance(task_keys, list) or not task_keys: return False
    if any(key not in RENDER_TASK_KEYS for key in task_keys): return False
    if not check_str(turntable, 'pool_name'): return False
    for key in ('priority', 'tile_count', 'first_frame', 'last_frame', 'step_size', 'batch_size'):
        if not check_int(turntable, key): return False
    if turntable['step_size'] < 1: return False
    if not check_bool(turntable, 'denoise'): return False
    if not check_bool(turntable, 'copy_to_edit'): return False
    if not valid_cut_range(turntable): return False
    if not valid_preview_frame_list(turntable, turntable['step_size']): return False
    return not set(turntable) & {frame_ranges.CONFIG_KEY, repair.CONFIG_KEY, 'composite'}


def _valid_playblast(playblast):
    if not isinstance(playblast, dict): return False
    if not check_str(playblast, 'department'): return False
    if not check_str(playblast, 'pool_name'): return False
    if not check_int(playblast, 'priority'): return False
    if not check_list(playblast, 'res'): return False
    if len(playblast['res']) != 2: return False
    if not all(is_int(value) for value in playblast['res']): return False
    if not valid_look(playblast.get('look')): return False
    return True


def is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not _valid_settings(config.get('settings')): return False
    has_render = 'render' in config
    has_turntable = 'turntable' in config
    has_playblast = 'playblast' in config
    if not (has_render or has_turntable or has_playblast): return False
    if has_render and has_turntable: return False
    if has_render and not _valid_render(config['render']): return False
    if has_turntable and not _valid_turntable(config['turntable']): return False
    if has_playblast and not _valid_playblast(config['playblast']): return False
    return True


is_valid_context = is_valid_config


def is_valid_houdini_config(config):
    if not is_valid_config(config): return False
    if not check_str(config, 'output_dir'): return False
    return True
