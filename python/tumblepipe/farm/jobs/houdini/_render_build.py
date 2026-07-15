"""Shared per-job builders for the render and cloud_render job families.

``render/job.py`` (multi-layer local render) and ``cloud_render/job.py``
(single-layer cloud render) build the same chain of farm tasks: render ->
denoise -> slapcomp -> mp4 -> notify. The builder bodies here originate from
``render/job.py`` verbatim; the family-specific deltas are passed in by the
callers as explicit keyword arguments:

- ``render_task``       — the task module providing ``build()`` for render
                          jobs (``tasks.render.task`` vs
                          ``tasks.cloud_render.task``); never imported here.
- ``priority``          — each family computes its own priority from its own
                          config schema (render: per-task priorities under
                          ``config['tasks']``; cloud_render: a single
                          ``config['settings']['priority']`` or literal 90).
- ``render_task_extra`` — extra keys merged into the render task build config
                          (render: ``tile_count``; cloud_render:
                          ``archive_path``).
- ``title`` / ``message`` / ``log_label`` — the mp4 and playblast-notify
                          shapes differ only in their strings.

The orchestrating ``build()`` / ``submit()`` / ``_is_valid_config`` stay in
the per-family job modules; they genuinely differ.

These modules run as standalone scripts on the farm, so imports are absolute
(``tumblepipe`` is already importable at that point, same as ``_common``).
"""

from pathlib import Path
import logging

from tumblepipe.api import (
    to_windows_path,
    path_str
)
from tumblepipe.util.io import (
    load_json,
    store_json
)
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import BlockRange
from tumblepipe.pipe.paths import (
    get_frame_path,
    get_next_frame_path,
    get_aov_frame_path,
    get_playblast_path,
    get_daily_path
)
import tumblepipe.farm.tasks.denoise.task as denoise_job
import tumblepipe.farm.tasks.slapcomp.task as slapcomp_job
import tumblepipe.farm.tasks.mp4.task as mp4_job
import tumblepipe.farm.tasks.notify.task as notify_job

def build_partial_render_job(
    config: dict,
    paths: dict[Path, Path],
    staging_path: Path,
    *,
    render_task,
    priority: int,
    render_task_extra: dict
    ):
    logging.debug('Creating partial render task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    variant_name = config['settings']['variant_name']
    render_department_name = config['settings']['render_department_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    input_path = Path(config['settings']['input_path'])
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Find middle frame
    frame_range = BlockRange(
        first_frame,
        last_frame,
        step_size
    )
    middle_frame = frame_range.frame(0.5)

    # Get the aov names
    render_settings = load_json(render_settings_path)
    assert render_settings is not None, (
        'Render settings not found: '
        f'{render_settings_path}'
    )
    aov_names = render_settings['aov_names']

    # Parameters
    receipt_path = get_next_frame_path(
        entity_uri,
        render_department_name,
        variant_name,
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        'partial render '
        f'{variant_name} '
        f'{version_name}'
    )
    output_paths = {
        aov_name: get_aov_frame_path(
            entity_uri,
            render_department_name,
            variant_name,
            version_name,
            aov_name,
            '####',
            'exr',
            purpose
        )
        for aov_name in aov_names
    }

    # Create the task
    task = render_task.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        **render_task_extra,
        first_frame = first_frame,
        last_frame = last_frame,
        frames = [first_frame, middle_frame, last_frame],
        step_size = 1,
        batch_size = 1,
        input_path = path_str(to_windows_path(input_path)),
        receipt_path = path_str(to_windows_path(receipt_path)),
        output_paths = {
            aov_name: path_str(to_windows_path(aov_path))
            for aov_name, aov_path in output_paths.items()
        }
    ), paths, staging_path)

    # Create the framestack context file
    context_path = receipt_path.parent / 'context.json'
    store_json(context_path, dict(
        entity = str(entity_uri),
        department = department_name,
        variant = variant_name,
        render_department_name = render_department_name,
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def build_full_render_job(
    config: dict,
    paths: dict[Path, Path],
    staging_path: Path,
    version_name: str,
    *,
    render_task,
    priority: int,
    render_task_extra: dict
    ):
    logging.debug('Creating full render task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    variant_name = config['settings']['variant_name']
    render_department_name = config['settings']['render_department_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    input_path = Path(config['settings']['input_path'])
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']
    batch_size = config['settings']['batch_size']

    # Find receipt path and version name
    def _receipt_path(version_name):
        if version_name is None:
            output_frame_path = get_next_frame_path(
                entity_uri,
                render_department_name,
                variant_name,
                '####',
                'json',
                purpose
            )
            version_name = output_frame_path.parent.name
            return output_frame_path, version_name
        else:
            output_frame_path = get_frame_path(
                entity_uri,
                render_department_name,
                variant_name,
                version_name,
                '####',
                'json',
                purpose
            )
            return output_frame_path, version_name

    # Get the aov names
    render_settings = load_json(render_settings_path)
    assert render_settings is not None, (
        'Render settings not found: '
        f'{render_settings_path}'
    )
    aov_names = render_settings['aov_names']

    # Parameters
    receipt_path, version_name = _receipt_path(version_name)
    title = (
        f'full render '
        f'{variant_name} '
        f'{version_name}'
    )
    output_paths = {
        aov_name: path_str(get_aov_frame_path(
            entity_uri,
            render_department_name,
            variant_name,
            version_name,
            aov_name,
            '####',
            'exr',
            purpose
        ))
        for aov_name in aov_names
    }

    # Create the task
    task = render_task.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        **render_task_extra,
        first_frame = first_frame,
        last_frame = last_frame,
        frames = [],
        step_size = step_size,
        batch_size = batch_size,
        input_path = path_str(to_windows_path(input_path)),
        receipt_path = path_str(to_windows_path(receipt_path)),
        output_paths = {
            aov_name: path_str(to_windows_path(aov_path))
            for aov_name, aov_path in output_paths.items()
        }
    ), paths, staging_path)

    # Create the framestack context file
    context_path = receipt_path.parent / 'context.json'
    store_json(context_path, dict(
        entity = str(entity_uri),
        department = department_name,
        variant = variant_name,
        render_department_name = render_department_name,
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def build_partial_denoise_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    render_version_name: str,
    *,
    priority: int
    ):
    logging.debug('Creating partial denoise task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    variant_name = config['settings']['variant_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Find middle frame
    frame_range = BlockRange(
        first_frame,
        last_frame,
        step_size
    )
    middle_frame = frame_range.frame(0.5)

    # Get the aov names
    render_settings = load_json(render_settings_path)
    assert render_settings is not None, (
        'Render settings not found: '
        f'{render_settings_path}'
    )
    aov_names = render_settings['aov_names']

    # Parameters
    receipt_path = get_next_frame_path(
        entity_uri,
        'denoise',
        variant_name,
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        f'partial denoise '
        f'{variant_name} '
        f'{version_name}'
    )
    input_paths = {
        aov_name: get_aov_frame_path(
            entity_uri,
            render_department_name,
            variant_name,
            render_version_name,
            aov_name,
            '####',
            'exr',
            purpose
        )
        for aov_name in aov_names
    }
    output_paths = {
        aov_name: get_aov_frame_path(
            entity_uri,
            'denoise',
            variant_name,
            version_name,
            aov_name,
            '####',
            'exr',
            purpose
        )
        for aov_name in aov_names
    }

    # Create the task
    task = denoise_job.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        first_frame = first_frame,
        last_frame = last_frame,
        frames = [first_frame, middle_frame, last_frame],
        step_size = 1,
        batch_size = 1,
        input_paths = {
            aov_name: path_str(to_windows_path(aov_path))
            for aov_name, aov_path in input_paths.items()
        },
        receipt_path = path_str(to_windows_path(receipt_path)),
        output_paths = {
            aov_name: path_str(to_windows_path(aov_path))
            for aov_name, aov_path in output_paths.items()
        }
    ), dict(), staging_path)

    # Create the framestack context file
    context_path = receipt_path.parent / 'context.json'
    store_json(context_path, dict(
        entity = str(entity_uri),
        department = department_name,
        render_department_name = 'denoise',
        variant = variant_name,
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def build_full_denoise_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    render_version_name: str,
    *,
    priority: int
    ):
    logging.debug('Creating denoise task')

    # Config parameters
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    variant_name = config['settings']['variant_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Get the aov names
    render_settings = load_json(render_settings_path)
    assert render_settings is not None, (
        'Render settings not found: '
        f'{render_settings_path}'
    )
    aov_names = render_settings['aov_names']

    # Parameters
    receipt_path = get_next_frame_path(
        entity_uri,
        'denoise',
        variant_name,
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        f'full denoise '
        f'{variant_name} '
        f'{version_name}'
    )
    input_paths = {
        aov_name: get_aov_frame_path(
            entity_uri,
            render_department_name,
            variant_name,
            render_version_name,
            aov_name,
            '####',
            'exr',
            purpose
        )
        for aov_name in aov_names
    }
    output_paths = {
        aov_name: get_aov_frame_path(
            entity_uri,
            'denoise',
            variant_name,
            version_name,
            aov_name,
            '####',
            'exr',
            purpose
        )
        for aov_name in aov_names
    }

    # Create the task
    task = denoise_job.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        first_frame = first_frame,
        last_frame = last_frame,
        frames = [],
        step_size = 1,
        batch_size = 20,
        input_paths = {
            aov_name: path_str(to_windows_path(aov_path))
            for aov_name, aov_path in input_paths.items()
        },
        receipt_path = path_str(to_windows_path(receipt_path)),
        output_paths = {
            aov_name: path_str(to_windows_path(aov_path))
            for aov_name, aov_path in output_paths.items()
        }
    ), dict(), staging_path)

    # Create the framestack context file
    context_path = receipt_path.parent / 'context.json'
    store_json(context_path, dict(
        entity = str(entity_uri),
        department = department_name,
        render_department_name = 'denoise',
        variant = variant_name,
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def build_slapcomp_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str,
    *,
    priority: int
    ):
    logging.debug('Creating slapcomp task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Get the aov names
    render_settings = load_json(render_settings_path)
    assert render_settings is not None, (
        'Render settings not found: '
        f'{render_settings_path}'
    )
    variant_names = render_settings['variant_names']
    aov_names = render_settings['aov_names']

    # Paramaters
    input_paths = {
        variant_name: {
            aov_name: get_aov_frame_path(
                entity_uri,
                render_department_name,
                variant_name,
                version_name,
                aov_name,
                '####',
                'exr',
                purpose
            )
            for aov_name in aov_names
        }
        for variant_name in variant_names
    }
    receipt_path = get_next_frame_path(
        entity_uri,
        render_department_name,
        'slapcomp',
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        f'slapcomp '
        f'{render_department_name} '
        f'{version_name}'
    )
    output_path = get_frame_path(
        entity_uri,
        render_department_name,
        'slapcomp',
        version_name,
        '####',
        'exr',
        purpose
    )

    # Create the task
    task = slapcomp_job.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size,
        input_paths = {
            layer_name: {
                aov_name: path_str(to_windows_path(aov_path))
                for aov_name, aov_path in aov_paths.items()
            }
            for layer_name, aov_paths in input_paths.items()
        },
        receipt_path = path_str(to_windows_path(receipt_path)),
        output_path = path_str(to_windows_path(output_path))
    ), dict(), staging_path)

    # Create the framestack context file
    context_path = receipt_path.parent / 'context.json'
    store_json(context_path, dict(
        entity = str(entity_uri),
        department = department_name,
        render_department_name = render_department_name,
        variant = 'slapcomp',
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def build_playblast_mp4_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    slapcomp_version_name: str,
    *,
    title: str,
    log_label: str,
    priority: int
    ):
    logging.debug(f'Creating {log_label} task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    assert entity_uri is not None, 'Invalid entity in config'
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Parameters - use department-level paths for slapcomp
    playblast_path = get_playblast_path(
        entity_uri, render_department_name, slapcomp_version_name, purpose
    )
    daily_path = get_daily_path(entity_uri, render_department_name, purpose)
    input_path = get_frame_path(
        entity_uri,
        render_department_name,
        'slapcomp',
        slapcomp_version_name,
        '####',
        'exr',
        purpose
    )

    # Create the task
    task = mp4_job.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size,
        input_path = path_str(to_windows_path(input_path)),
        output_paths = [
            path_str(to_windows_path(playblast_path)),
            path_str(to_windows_path(daily_path))
        ]
    ), dict(), staging_path)

    # Done
    return task, slapcomp_version_name

def build_partial_notify_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str,
    *,
    priority: int
    ):
    logging.debug('Creating partial notify task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    variant_name = config['settings']['variant_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']
    channel_name = config['tasks']['partial_render']['channel_name']

    # Find middle frame
    frame_range = BlockRange(
        first_frame,
        last_frame,
        step_size
    )
    middle_frame = frame_range.frame(0.5)

    # Parameters
    title = (
        f'notify partial '
        f'{render_department_name} '
        f'{version_name}'
    )
    message = (
        f'{entity_uri} - '
        f'{render_department_name} - '
        f'{version_name}'
    )
    frame_path = get_aov_frame_path(
        entity_uri,
        render_department_name,
        variant_name,
        version_name,
        'beauty',
        '####',
        'exr',
        purpose
    )

    # Create the task
    task = notify_job.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        user_name = user_name,
        channel_name = channel_name,
        message = message,
        command = dict(
            mode = 'partial',
            frame_path = path_str(to_windows_path(frame_path)),
            first_frame = first_frame,
            middle_frame = middle_frame,
            last_frame = last_frame
        )
    ), dict(), staging_path)

    # Done
    return task

def build_playblast_notify_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str,
    *,
    title: str,
    message: str,
    log_label: str,
    priority: int
    ):
    logging.debug(f'Creating {log_label} task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    assert entity_uri is not None, 'Invalid entity in config'
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    channel_name = config['tasks']['full_render']['channel_name']

    # Parameters - final playblast video
    video_path = get_playblast_path(
        entity_uri,
        render_department_name,
        version_name,
        purpose
    )

    # Create the task
    task = notify_job.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        user_name = user_name,
        channel_name = channel_name,
        message = message,
        command = dict(
            mode = 'full',
            video_path = path_str(to_windows_path(video_path))
        )
    ), dict(), staging_path)

    # Done
    return task
