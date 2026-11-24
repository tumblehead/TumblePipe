"""The composite pipeline: cook a comp workfile's ``th::export_comp`` nodes
on the farm, and end in the review chain (``post_render``).

One submit is one composite version of one shot. Every export_comp of the
shot writes into it: Layer ``comp`` (the merged comp) as the version's
cross-channel layer, ``composite/slapcomp/v####``, and a channel layer into
that channel's folder. With preview frames, a *partial* composite does the
first, middle and last frames (or the Preview Frames List,
``settings.preview_frame_list``) first and posts them; the *full* composite
does the whole range, then each per-channel layer gets its review mp4, and
the merged comp is the department's mp4, daily and notify. There is no
oiiotool slapcomp: the merged comp is the cross-channel layer.

The imports are frozen at submit: ``imports`` pins every ``th::import_render``
of the shot to the version it resolved in the artist's session, and each
written layer's ``context.json`` records what it read (``reads``).

A **re-comp** (Farm Submit's Comp column, :func:`recomp_config`) has no
session: it comps the shot's newest composite workfile, ``imports`` is
empty and the worker resolves every import on its own settings when it
runs (``latest`` moves to the newest complete render), then records what it
read. Its one export has no node path: the worker finds the shot's
export_comp on Layer ``comp``. Chained after a render (the render config's
``composite`` block, see ``pipelines/render.py``), it waits for the
render's denoise, or the render when there is none. Its frames are the
render's range (or, alone, the shot's), unless the block names its own
first or last frame; the block also carries the step and whether to comp
the preview frames first, as an export_comp's Submit does
(``specs.composite.valid_recomp``).

A **comp of the submit's render** (#80, :func:`scene_config`) is a re-comp
whose block names a ``scene``: a workfile comp with no ``th::import_render``
(a slap comp in the render scene), saved with the Farm Submit. It comps that
workfile instead of the newest composite workfile, its exports by node
path, and ``renders`` names the frames the render beside it writes (each
channel's denoise, else render, version): the worker points the workfile's
Slap Comp Import nodes at them (``pipe.comp.slap_reads``) and records them
as what it read. It publishes as a composite version like any comp.

config = {
    'entity': {'uri': 'entity:/shots/sequence/shot', 'department': 'string'},
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'priority': 'int',
        'pool_name': 'string',
        'input_path': 'string',         # the comp workfile, relative to the job's data dir
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int',
        'copy_to_edit': 'bool',
        'preview_frame_list': ['int'],  # optional: the partial's frames, in
                                        # order, inside the range and on the
                                        # step (farm.specs.preview_frames); absent:
                                        # first, middle and last (on the step)
        'reads_check_render': 'bool'    # optional, default False: an import
                                        # reads a check render
    },
    'exports': [{'node_path': '/stage/comp/export_comp', 'layer': 'comp'}],
    # A node path '' (a re-comp): the shot's export_comp on that layer.
    'imports': {      # empty for a re-comp: resolved when the worker runs
        '/stage/comp/import_chars': {
            'channel': 'chars', 'department': 'denoise', 'version': 'v0004'
        }
    },
    'follows': [      # optional: a re-comp beside a stepped render, the
                      # render its imports are pinned to when it runs
        {'channel': 'chars', 'department': 'denoise', 'version': 'v0004'}
    ],
    'renders': [      # optional (#80): the render of the same submit
        {'channel': 'chars', 'department': 'denoise', 'version': 'v0004'}
    ],
    'tasks': {
        'full_composite': {'channel_name': 'string'},
        'partial_composite': {'channel_name': 'string'}     # preview frames, optional
    }
}

``tasks.*.channel_name`` is the Discord channel the notify posts to.

A **check comp** (``pipe.comp.is_check_comp``: a Step above 1, or an import
reading a check render, ``settings.reads_check_render``) is written and
encoded like any comp, but never published, as a check render is not: its
frame stacks record ``"published": false``, no daily is written and Copy to
edit is skipped. A re-comp that turns out to read a check render when it
runs is marked so by its worker; its daily mp4 and Copy to edit, queued
before anyone knew, read that flag when they run (``publish_contexts``,
``pipe.comp.unpublished_reason``) and skip with a ``WARNING:`` line, still
exiting 0 (Soren, 2026-11-23). Every composite job carries the comp's
frames (``comp_frames``): a re-comp's worker comps a check render on any of
them (``pipe.comp.check_render_refusal``), holding the frames it lacks, its
first and last outside its range.
"""

from pathlib import Path
import logging

from tumblepipe.api import path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.farm.checks import (
    check_bool,
    check_int,
    check_str,
    valid_entity,
    valid_frame_range,
)
from tumblepipe.farm.jobs import composite as composite_job
from tumblepipe.farm.specs.composite import valid_recomp  # noqa: F401  (the render config's block)
from tumblepipe.farm.specs.composite import valid_renders
from tumblepipe.farm.specs.render_pipeline import valid_preview_frame_list
from tumblepipe.farm.specs import submission
from tumblepipe.farm.pipelines import post_render
from tumblepipe.farm.specs.preview_frames import CONFIG_KEY as PREVIEW_FRAMES_KEY
from tumblepipe.farm.specs.preview_frames import valid_frame_list
from tumblepipe.farm.pipelines._batch import batch_title, submit_batch
from tumblepipe.pipe import comp
from tumblepipe.pipe.paths import get_frame_path
from tumblepipe.util.uri import Uri

COMPOSITE_DEPARTMENT = comp.COMPOSITE_DEPARTMENT
PARTIAL_PRIORITY = 90

COMPOSITE_TASK_KEYS = ('partial_composite', 'full_composite')
_IMPORT_KEYS = ('channel', 'department', 'version')


def _valid_settings(settings) -> bool:
    if not valid_frame_range(settings): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'purpose'): return False
    if not check_int(settings, 'priority'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_str(settings, 'input_path'): return False
    if not check_int(settings, 'batch_size'): return False
    if not check_bool(settings, 'copy_to_edit'): return False
    if not valid_preview_frame_list(settings, settings['step_size']): return False
    if 'reads_check_render' in settings and not check_bool(settings, 'reads_check_render'):
        return False
    return True


def valid_exports(exports) -> bool:
    """A non-empty list of ``{node_path, layer}``, one node per layer. A
    node path may be empty (a re-comp names the layer only)."""
    if not isinstance(exports, list) or len(exports) == 0: return False
    for export in exports:
        if not isinstance(export, dict): return False
        if not check_str(export, 'node_path'): return False
        if not check_str(export, 'layer'): return False
    layers = [export['layer'] for export in exports]
    return len(set(layers)) == len(layers)


def valid_imports(imports) -> bool:
    """``{node path: {channel, department, version}}``; empty for a
    re-comp, whose worker resolves the imports when it runs."""
    if not isinstance(imports, dict): return False
    for node_path, pin in imports.items():
        if not isinstance(node_path, str) or not node_path: return False
        if not isinstance(pin, dict): return False
        for key in _IMPORT_KEYS:
            if not check_str(pin, key): return False
    return True


def _valid_tasks(tasks) -> bool:
    if not isinstance(tasks, dict): return False
    if 'full_composite' not in tasks: return False
    for key in COMPOSITE_TASK_KEYS:
        if key not in tasks: continue
        if not isinstance(tasks[key], dict): return False
        if not check_str(tasks[key], 'channel_name'): return False
    return True


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not _valid_settings(config.get('settings')): return False
    if not valid_exports(config.get('exports')): return False
    if not valid_imports(config.get('imports')): return False
    if 'renders' in config:
        if not valid_renders(config['renders']): return False
        if config['imports']: return False
    if 'follows' in config:
        # A re-comp beside a stepped render: the render it reads.
        if not valid_renders(config['follows']): return False
        if 'renders' in config or config['imports']: return False
    if not _valid_tasks(config.get('tasks')): return False
    return True


def default_preview_frames(first_frame: int, last_frame: int, step_size: int) -> tuple[int, ...] | None:
    """A stepped comp's preview with no Preview Frames List: its first,
    middle and last frame *on its step*. The range's last frame can be off
    the step, a frame the comp never renders and a check render does not
    have. None on Step 1 (or with no frames): the shot's own first, middle
    and last."""
    if step_size <= 1 or first_frame > last_frame:
        return None
    frames = comp.frames_of((first_frame, last_frame, step_size))
    return frames[0], frames[(len(frames) - 1) // 2], frames[-1]


def _shot(config: dict, staging_path: Path) -> post_render.Shot:
    settings = config['settings']
    first, last, step = settings['first_frame'], settings['last_frame'], settings['step_size']
    return post_render.Shot(
        entity_uri = Uri.parse(config['entity']['uri']),
        department = config['entity']['department'],
        purpose = settings['purpose'],
        user_name = settings['user_name'],
        pool_name = settings['pool_name'],
        first_frame = first,
        last_frame = last,
        step_size = step,
        staging_path = staging_path,
        preview_frames = (tuple(settings.get(PREVIEW_FRAMES_KEY) or ())
                          or default_preview_frames(first, last, step)),
        check_render = comp.is_check_comp(step, settings.get('reads_check_render', False)),
    )


def layer_output_path(shot: post_render.Shot, layer: str, version_name: str) -> Path:
    """Where an export_comp's Layer writes (``pipe.comp.layer_output_path``)."""
    return comp.layer_output_path(shot.entity_uri, layer, version_name, shot.purpose)


def next_version(shot: post_render.Shot, layers: list[str]) -> str:
    """The composite version a submit writes (``pipe.comp.next_composite_version``)."""
    return comp.next_composite_version(shot.entity_uri, layers, shot.purpose)


def _receipt_folder(layers: list[str]) -> str:
    """The folder the per-frame receipts go in: the merged layer's when the
    submit writes one, else the first layer's."""
    if comp.MERGED_LAYER in layers: return comp.MERGED_FOLDER
    return comp.output_folder(layers[0])


def _composite_job(config, shot, paths, version_name, partial) -> Job:
    """The composite of every export (partial: the preview frames) into
    ``version_name``."""
    settings = config['settings']
    exports = config['exports']
    layers = [export['layer'] for export in exports]
    uri, purpose = shot.entity_uri, shot.purpose
    receipt_path = get_frame_path(
        uri, COMPOSITE_DEPARTMENT, _receipt_folder(layers), version_name,
        '####', 'json', purpose
    )
    return composite_job.build(dict(
        entity = config['entity'],
        title = f"{'partial' if partial else 'full'} composite {version_name}",
        priority = PARTIAL_PRIORITY if partial else settings['priority'],
        pool_name = shot.pool_name,
        first_frame = shot.first_frame,
        last_frame = shot.last_frame,
        frames = list(shot.check_frames) if partial else [],
        step_size = 1 if partial else shot.step_size,
        batch_size = 1 if partial else settings['batch_size'],
        receipt_path = path_str(receipt_path),
        input_path = settings['input_path'],
        # The whole comp's frames, a partial's too (its own are the preview
        # frames on step 1): what a re-comp's worker checks a check render
        # against.
        comp_frames = [shot.first_frame, shot.last_frame, shot.step_size],
        exports = [
            dict(
                node_path = export['node_path'],
                layer = export['layer'],
                output_path = path_str(layer_output_path(shot, export['layer'], version_name)),
            )
            for export in exports
        ],
        imports = config['imports'],
        **({} if 'renders' not in config else dict(renders = config['renders'])),
        **({} if 'follows' not in config else dict(follows = config['follows'])),
    ), paths, shot.staging_path)


def _receipt_path(shot, layer: str, version_name: str) -> Path:
    """A written layer's receipt pattern; its context.json sits beside it."""
    return get_frame_path(
        shot.entity_uri, COMPOSITE_DEPARTMENT, comp.output_folder(layer),
        version_name, '####', 'json', shot.purpose
    )


def _context_path(shot, layer: str, version_name: str) -> Path:
    """A written layer's context.json, where the worker's ``record_reads``
    says ``"published": false`` once the comp read a check render: the
    daily and Copy to edit read it when they run."""
    return _receipt_path(shot, layer, version_name).parent / 'context.json'


def _write_contexts(config, shot, version_name):
    """Each written layer's context.json, recording what the comp read
    (a re-comp's worker records it once it has resolved; a comp of its
    submit's render reads that render)."""
    if config['imports']:
        reads = comp.provenance(config['imports'])
    elif config.get('renders'):
        reads = comp.render_reads(config['renders'])
    else:
        reads = None
    for export in config['exports']:
        folder = comp.output_folder(export['layer'])
        post_render.write_framestack_context(
            shot,
            _receipt_path(shot, export['layer'], version_name),
            COMPOSITE_DEPARTMENT, folder, version_name,
            reads = reads,
        )


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None,
    prefix: str = ''
    ) -> list[str]:
    """Add the composite jobs of ``config`` to ``jobs``/``deps``, after
    ``depends_on``; returns the names later work should depend on.

    ``prefix`` goes before every job name, so a re-comp can share a batch
    with the render it follows (both have a ``sync``, a ``slapcomp_mp4``).
    """
    depends_on = list(depends_on or [])
    own_jobs, own_deps = {}, {}
    terminal = _build(config, paths, temp_path, own_jobs, own_deps, depends_on)
    names = {name: f'{prefix}{name}' for name in own_jobs}
    for name, job in own_jobs.items():
        jobs[names[name]] = job
        deps[names[name]] = [names.get(dep, dep) for dep in own_deps[name]]
    return [names[name] for name in terminal]


def _build(config, paths, temp_path, jobs, deps, depends_on) -> list[str]:
    settings = config['settings']
    tasks = config['tasks']
    shot = _shot(config, temp_path)
    layers = [export['layer'] for export in config['exports']]
    merged = comp.MERGED_LAYER in layers
    channel_layers = [layer for layer in layers if layer != comp.MERGED_LAYER]
    version_name = next_version(shot, layers)
    _write_contexts(config, shot, version_name)

    full_after = list(depends_on)
    if 'partial_composite' in tasks:
        jobs['partial_composite'] = _composite_job(
            config, shot, paths, version_name, partial=True
        )
        deps['partial_composite'] = list(depends_on)
        jobs['partial_notify'] = post_render.frames_notify_job(
            shot, COMPOSITE_DEPARTMENT, comp.output_folder(layers[0]), version_name,
            tasks['partial_composite']['channel_name'],
            frame_path = layer_output_path(shot, layers[0], version_name),
        )
        deps['partial_notify'] = ['partial_composite']
        full_after = ['partial_composite']

    jobs['full_composite'] = _composite_job(
        config, shot, paths, version_name, partial=False
    )
    deps['full_composite'] = full_after
    terminal_jobs = []
    # The daily and Copy to edit read the comp's published flag when they
    # run: a comp learns only then whether it reads a check render (a
    # re-comp resolves its imports in its worker; Soren, 2026-11-23).
    for layer in channel_layers:
        jobs[f'layer_mp4_{layer}'] = post_render.layer_mp4_job(
            shot, COMPOSITE_DEPARTMENT, layer, version_name, settings['priority'],
            publish_contexts = [_context_path(shot, layer, version_name)],
        )
        deps[f'layer_mp4_{layer}'] = ['full_composite']
        terminal_jobs.append(f'layer_mp4_{layer}')
    if settings['copy_to_edit'] and not shot.publishes:
        logging.warning(
            f'{shot.entity_uri}: Copy to edit skipped: a check comp is not '
            'published (comp.is_check_comp)'
        )
    if settings['copy_to_edit'] and shot.publishes:
        jobs['sync'] = post_render.sync_job_for(shot, publish_contexts = [
            _context_path(shot, layer, version_name) for layer in layers
        ])
        deps['sync'] = ['full_composite']
        terminal_jobs.append('sync')
    if merged:
        jobs['slapcomp_mp4'] = post_render.slapcomp_mp4_job(
            shot, COMPOSITE_DEPARTMENT, version_name, settings['priority'],
            publish_contexts = [_context_path(shot, comp.MERGED_LAYER, version_name)],
        )
        deps['slapcomp_mp4'] = ['full_composite']
        jobs['slapcomp_notify'] = post_render.slapcomp_notify_job(
            shot, COMPOSITE_DEPARTMENT, version_name,
            tasks['full_composite']['channel_name']
        )
        deps['slapcomp_notify'] = ['slapcomp_mp4']
        terminal_jobs.append('slapcomp_notify')
    if not terminal_jobs:
        terminal_jobs = ['full_composite']

    logging.debug(f'Composite jobs: {list(jobs)}')
    return terminal_jobs


RECOMP_INPUT = 'composite_workfile'


#: The Discord channel a re-comp's preview frames post to, as an
#: export_comp's own Submit posts them.
RECOMP_PREVIEW_CHANNEL = 'previews'


class RecompError(Exception):
    """A shot has nothing a re-comp could comp, or the frames asked for make
    no range; the message says why."""


def recomp_preview_frames(block: dict, first_frame: int, last_frame: int) -> list[int] | None:
    """The block's Preview Frames List (``preview_frame_list``, checked by the
    submitter against the comp's frames) if it still fits the
    ``first_frame``..``last_frame`` the comp makes on the block's step, else
    None: the first, middle and last frame. The submitter checked it on the
    same rule (``batch_submit``), so a list that no longer fits is a stale
    block, logged, never a reason to refuse the comp."""
    frames = block.get(PREVIEW_FRAMES_KEY)
    if not frames:
        return None
    if not valid_frame_list(frames, first_frame, last_frame, block.get('step_size', 1)):
        logging.warning(
            f'The comp preview frames {frames} do not fit {first_frame}-'
            f'{last_frame}; previewing the first, middle and last frame'
        )
        return None
    return list(frames)


def recomp_config(
    shot_uri: Uri,
    block: dict,
    *,
    user_name: str,
    purpose: str,
    first_frame: int,
    last_frame: int,
    temp_path: Path,
    paths: dict[Path, Path],
    ) -> dict:
    """The composite config of a re-comp of ``shot_uri``'s newest composite
    workfile (a Multi's, when one covers the shot), scheduled as ``block``
    says. The workfile is bundled with the job (added to ``paths``) as it is
    now. Raises :class:`RecompError` when the shot has no composite
    workfile, or the block's frames make no range.

    ``first_frame``/``last_frame`` are the range the comp follows; the
    block's own frames, step and preview frames apply over it
    (``farm.specs.submission.comp_frames``), as an export_comp's Frame Range, Step and
    Preview Frames do for its own Submit."""
    from tumblepipe.pipe.paths import latest_complete_hip_file_path
    workfile = latest_complete_hip_file_path(shot_uri, COMPOSITE_DEPARTMENT)
    if workfile is None or not workfile.exists():
        raise RecompError(
            f'{shot_uri} has no composite workfile to comp; make one from the '
            'composite template first'
        )
    return _chained_config(
        shot_uri, block, workfile, [dict(node_path = '', layer = comp.MERGED_LAYER)],
        user_name = user_name, purpose = purpose,
        first_frame = first_frame, last_frame = last_frame,
        temp_path = temp_path, paths = paths,
    )


def scene_config(
    shot_uri: Uri,
    block: dict,
    renders: list[dict],
    *,
    user_name: str,
    purpose: str,
    first_frame: int,
    last_frame: int,
    temp_path: Path,
    paths: dict[Path, Path],
    ) -> dict:
    """The composite config of a comp of the render it was submitted with
    (#80): the block's ``scene`` workfile (saved with the Farm Submit) and
    its exports, reading ``renders`` (``[{channel, department, version}]``,
    the frames the render writes, in its channel order). Scheduled as
    :func:`recomp_config` is. Raises :class:`RecompError` when the
    workfile is gone, or the block's frames make no range."""
    scene = block['scene']
    workfile = Path(scene['path'])
    if not workfile.exists():
        raise RecompError(
            f'the comp workfile saved with the submit of {shot_uri} is gone: '
            f'{path_str(workfile)}'
        )
    config = _chained_config(
        shot_uri, block, workfile,
        [dict(node_path = export['node_path'], layer = export['layer']) for export in scene['exports']],
        user_name = user_name, purpose = purpose,
        first_frame = first_frame, last_frame = last_frame,
        temp_path = temp_path, paths = paths,
    )
    config['renders'] = [
        dict(channel = r['channel'], department = r['department'], version = r['version'])
        for r in renders
    ]
    return config


def chained_config(
    shot_uri: Uri,
    block: dict,
    renders: list[dict],
    follows_check_render: bool = False,
    **kwargs,
    ) -> dict:
    """The comp a render carries (its config's ``composite`` block): a comp
    of this render (:func:`scene_config`) when the block names a ``scene``,
    else a re-comp of the shot's newest composite workfile
    (:func:`recomp_config`), which reads the renders on its own.

    Beside a stepped render (``follows_check_render``; Soren, 2026-11-22)
    the comp reads that render, a check render, and so is a check comp
    (``settings.reads_check_render``): a re-comp's imports are pinned to
    ``renders`` when it runs (``follows``), not left to resolve ``latest``,
    which would read the shot's older published render. Farm Submit took
    it on any frames, the frames the render does not make held, its first
    and last outside its range (``farm.specs.submission.stepped_comp_frames``);
    the worker checks the check render again
    (``pipe.comp.check_render_refusal``)."""
    if block.get('scene') is not None:
        config = scene_config(shot_uri, block, renders, **kwargs)
    else:
        config = recomp_config(shot_uri, block, **kwargs)
        if follows_check_render:
            config['follows'] = [
                dict(channel = r['channel'], department = r['department'], version = r['version'])
                for r in renders
            ]
    if follows_check_render:
        config['settings']['reads_check_render'] = True
    return config


def _chained_config(shot_uri, block, workfile: Path, exports: list[dict], *,
                    user_name, purpose, first_frame, last_frame, temp_path, paths) -> dict:
    """A re-comp's config of ``workfile`` (bundled with the job as it is
    now) and ``exports``, scheduled as ``block`` says."""
    import shutil
    follows = (first_frame, last_frame)
    first_frame, last_frame = submission.comp_frames(
        block.get('first_frame'), block.get('last_frame'), follows,
    )
    problems = submission.comp_frame_problems((first_frame, last_frame), follows, inside=False)
    if problems:
        raise RecompError(problems[0])
    shot_name = '_'.join(shot_uri.segments[1:])
    input_path = Path(RECOMP_INPUT) / f'{shot_name}{workfile.suffix}'
    staged = temp_path / input_path
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(workfile, staged)
    paths[staged] = input_path
    tasks = dict(full_composite = dict(channel_name = block['channel_name']))
    step_size = block.get('step_size', 1)
    preview_frame_list = None
    if block.get('preview_frames', False):
        tasks['partial_composite'] = dict(channel_name = RECOMP_PREVIEW_CHANNEL)
        preview_frame_list = recomp_preview_frames(block, first_frame, last_frame)
    return dict(
        entity = dict(uri = str(shot_uri), department = COMPOSITE_DEPARTMENT),
        settings = dict(
            user_name = user_name,
            purpose = purpose,
            priority = block['priority'],
            pool_name = block['pool_name'],
            input_path = path_str(input_path),
            first_frame = first_frame,
            last_frame = last_frame,
            step_size = step_size,
            batch_size = block['batch_size'],
            copy_to_edit = block['copy_to_edit'],
            **({} if preview_frame_list is None
               else {PREVIEW_FRAMES_KEY: preview_frame_list}),
        ),
        exports = exports,
        imports = dict(),
        tasks = tasks,
    )


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the composite pipeline of ``config`` as one batch."""
    settings = config['settings']
    return submit_batch(batch_title(
        settings['purpose'], config['entity']['uri'], settings['user_name']
    ), build, config, paths, entity=config['entity']['uri'])
