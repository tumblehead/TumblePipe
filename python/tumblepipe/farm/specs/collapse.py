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
    'render': {                          # optional
        'department': 'string',          # cut: pool departments up to this one
        'channel_names': ['string'],
        'overrides': {'karma:attr': value},
        'task_keys': ['full_render' | 'partial_render'],  # one or both
        'pool_name': 'string',
        'priority': 'int',
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'batch_size': 'int',
        'denoise': 'bool',
        'copy_to_edit': 'bool',
        'cut_range': ['int', 'int'],     # optional: specs.render_pipeline.valid_cut_range
        'preview_frame_list': ['int'],  # optional: specs.render_pipeline.valid_preview_frame_list
        'frame_ranges': [['int', 'int']],  # optional, shots only: a multi-range
                                         # render (farm.specs.frame_ranges, #84)
        'composite': {...},              # optional: specs.composite.valid_recomp
        'turntable': 'bool',             # optional: an asset's turntable (#53)
        'step_size': 'int'               # optional, turntables only (default 1)
    },
    'playblast': {                       # optional
        'department': 'string',
        'pool_name': 'string',
        'priority': 'int',
        'res': ['int', 'int'],
        'look': {...}                    # optional, specs.playblast.valid_look
    }
}

At least one of ``render`` / ``playblast`` must be present.

A ``turntable`` render is an asset's (#53): the worker snapshots the asset
and builds a ``th::lookdev_studio`` workfile per channel, exports it as the
export job exports a studio's (``export_houdini``), then submits the render
a studio's Submit Render To Farm does, on its ``step_size``. Its preview
frames sit on that step, and it carries no composite.
"""

from tumblepipe.farm.specs import frame_ranges
from tumblepipe.farm.specs.composite import valid_recomp
from tumblepipe.farm.checks import (
    valid_entity,
    check_str,
    check_int,
    check_bool,
    check_list,
    is_int,
    valid_channel_names,
)
from tumblepipe.farm.specs.playblast import DEFAULT_LOOK, valid_look
from tumblepipe.farm.specs.render_pipeline import (
    RENDER_TASK_KEYS,
    valid_cut_range,
    valid_preview_frame_list,
)


def playblast_look(playblast: dict) -> dict:
    """The look a collapse config's playblast asks for (``DEFAULT_LOOK``
    when it was submitted before the look existed)."""
    return dict(playblast.get('look', DEFAULT_LOOK))


def _valid_settings(settings):
    if not isinstance(settings, dict): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_int(settings, 'priority'): return False
    return True


def _valid_render(render):
    if not isinstance(render, dict): return False
    if not check_str(render, 'department'): return False
    if not valid_channel_names(render): return False
    if not isinstance(render.get('overrides'), dict): return False
    task_keys = render.get('task_keys')
    if not isinstance(task_keys, list) or not task_keys: return False
    if any(key not in RENDER_TASK_KEYS for key in task_keys): return False
    if not check_str(render, 'pool_name'): return False
    for key in ('priority', 'tile_count', 'first_frame', 'last_frame', 'batch_size'):
        if not check_int(render, key): return False
    if not check_bool(render, 'denoise'): return False
    if not check_bool(render, 'copy_to_edit'): return False
    if not valid_cut_range(render): return False
    if not frame_ranges.valid_entry(render): return False
    if 'turntable' in render and not check_bool(render, 'turntable'): return False
    if render.get('turntable'):
        # A turntable renders the studio's frames, never a frame list.
        if frame_ranges.CONFIG_KEY in render: return False
        if 'step_size' in render and not check_int(render, 'step_size'): return False
        step = render.get('step_size', 1)
        if step < 1: return False
        if not valid_preview_frame_list(render, step): return False
        # A turntable is an asset's: no shot comp follows it.
        return 'composite' not in render
    if 'step_size' in render: return False
    # The direct render it submits renders every frame (step 1).
    if not valid_preview_frame_list(render): return False
    if 'composite' in render:
        if not valid_recomp(render['composite']): return False
        # A multi-range render is not published: no comp follows it.
        if frame_ranges.CONFIG_KEY in render: return False
    return True


def _valid_playblast(playblast):
    if not isinstance(playblast, dict): return False
    if not check_str(playblast, 'department'): return False
    if not check_str(playblast, 'pool_name'): return False
    if not check_int(playblast, 'priority'): return False
    if not check_list(playblast, 'res'): return False
    if len(playblast['res']) != 2: return False
    if not all(is_int(value) for value in playblast['res']): return False
    # Optional: a collapse job submitted before the look existed has none.
    if 'look' in playblast and not valid_look(playblast['look']): return False
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


is_valid_context = is_valid_config


def is_valid_houdini_config(config):
    if not is_valid_config(config): return False
    if not check_str(config, 'output_dir'): return False
    return True
