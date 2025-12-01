from tempfile import TemporaryDirectory
from functools import partial
from pathlib import Path
import datetime as dt
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    get_project_name,
    to_windows_path,
    fix_path,
    path_str,
    default_client
)
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.util.uri import Uri
from tumblehead.config.timeline import BlockRange
from tumblehead.apps.deadline import (
    Deadline,
    Batch,
    Job
)
from tumblehead.pipe.paths import (
    get_frame_path,
    get_next_frame_path,
    get_aov_frame_path,
    get_playblast_path,
    get_daily_path,
    get_layer_playblast_path,
    get_layer_daily_path
)
import tumblehead.farm.tasks.render.task as render_job
import tumblehead.farm.tasks.denoise.task as denoise_job
import tumblehead.farm.tasks.slapcomp.task as slapcomp_job
import tumblehead.farm.tasks.mp4.task as mp4_job
import tumblehead.farm.tasks.notify.task as notify_job
import tumblehead.farm.tasks.edit.task as edit_task

from importlib import reload
reload(render_job)
reload(denoise_job)
reload(slapcomp_job)
reload(mp4_job)
reload(notify_job)
reload(edit_task)

api = default_client()

def _error(msg):
    logging.error(msg)
    return 1

"""
config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'pool_name': 'string',
        'render_layer_names': ['string'],
        'render_department_name': 'string',
        'render_settings_path': 'string',
        'input_path': 'string',
        'tile_count': 'int',
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int'
    },
    'tasks': {
        'partial_render': {
            'priority': 'int',
            'denoise': 'bool',
            'channel_name': 'string'
        },
        'full_render': {
            'priority': 'int',
            'denoise': 'bool',
            'channel_name': 'string'
        }
    }
}
"""

def _is_valid_config(config):

    def _is_str(datum):
        return isinstance(datum, str)

    def _is_int(datum):
        return isinstance(datum, int)

    def _is_bool(datum):
        return isinstance(datum, bool)

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True

    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)
    _check_bool = partial(_check, _is_bool)

    def _valid_entity(entity):
        if not isinstance(entity, dict): return False
        if not _check_str(entity, 'uri'): return False
        if not _check_str(entity, 'department'): return False
        return True
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_str(settings, 'user_name'): return False
        if not _check_str(settings, 'purpose'): return False
        if not _check_str(settings, 'pool_name'): return False
        if 'render_layer_names' not in settings: return False
        if not isinstance(settings['render_layer_names'], list): return False
        if not _check_str(settings, 'render_department_name'): return False
        if not _check_str(settings, 'render_settings_path'): return False
        if not _check_str(settings, 'input_path'): return False
        if not _check_int(settings, 'tile_count'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        if not _check_int(settings, 'step_size'): return False
        if not _check_int(settings, 'batch_size'): return False
        return True
    
    def _valid_jobs(tasks):

        def _valid_partial_render(partial_render):
            if not isinstance(partial_render, dict): return False
            if not _check_int(partial_render, 'priority'): return False
            if not _check_bool(partial_render, 'denoise'): return False
            if not _check_str(partial_render, 'channel_name'): return False
            return True
    
        def _valid_full_render(full_render):
            if not isinstance(full_render, dict): return False
            if not _check_int(full_render, 'priority'): return False
            if not _check_bool(full_render, 'denoise'): return False
            if not _check_str(full_render, 'channel_name'): return False
            return True
        
        if not isinstance(tasks, dict): return False
        if 'partial_render' in tasks:
            if not _valid_partial_render(tasks['partial_render']): return False
        if 'full_render' in tasks:
            if not _valid_full_render(tasks['full_render']): return False
        return True
    
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    if 'tasks' not in config: return False
    if not _valid_jobs(config['tasks']): return False
    return True

def _build_partial_render_job(
    config: dict,
    paths: dict[Path, Path],
    staging_path: Path
    ):
    logging.debug('Creating partial render task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    priority = config['tasks']['partial_render']['priority']
    render_layer_name = config['settings']['render_layer_name']
    render_department_name = config['settings']['render_department_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    input_path = Path(config['settings']['input_path'])
    tile_count = config['settings']['tile_count']
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
        render_layer_name,
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        'partial render '
        f'{render_layer_name} '
        f'{version_name}'
    )
    output_paths = {
        aov_name: get_aov_frame_path(
            entity_uri,
            render_department_name,
            render_layer_name,
            version_name,
            aov_name,
            '####',
            'exr',
            purpose
        )
        for aov_name in aov_names
    }
    
    # Create the task
    task = render_job.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        tile_count = tile_count,
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
        render_layer_name = render_layer_name,
        render_department_name = render_department_name,
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def _build_full_render_job(
    config: dict,
    paths: dict[Path, Path],
    staging_path: Path,
    version_name: str
    ):
    logging.debug('Creating full render task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    priority = config['tasks']['full_render']['priority']
    pool_name = config['settings']['pool_name']
    render_layer_name = config['settings']['render_layer_name']
    render_department_name = config['settings']['render_department_name']
    render_settings_path = Path(config['settings']['render_settings_path'])
    input_path = Path(config['settings']['input_path'])
    tile_count = config['settings']['tile_count']
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
                render_layer_name,
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
                render_layer_name,
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
        f'{render_layer_name} '
        f'{version_name}'
    )
    output_paths = {
        aov_name: path_str(get_aov_frame_path(
            entity_uri,
            render_department_name,
            render_layer_name,
            version_name,
            aov_name,
            '####',
            'exr',
            purpose
        ))
        for aov_name in aov_names
    }

    # Create the task
    task = render_job.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        tile_count = tile_count,
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
        render_layer_name = render_layer_name,
        render_department_name = render_department_name,
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def _should_sync_aov(aov_name: str) -> bool:
    """Check if an AOV should be synced to edit"""
    if aov_name == 'beauty':
        return True
    if aov_name.startswith('objid_'):
        return True
    if aov_name.startswith('holdout_'):
        return True
    return False

def _build_edit_job(
    config: dict,
    staging_path: Path
    ):
    """Build single edit job that will resolve and sync all layer/AOV combinations at runtime.

    The AOV resolution happens at task execution time (not submission time) so that newly
    rendered frames are included in the resolution.
    """
    logging.debug('Creating edit task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    assert entity_uri is not None, 'Invalid entity in config'
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']

    # Extract entity fields
    if entity_uri.segments[0] != 'shots':
        assert False, f'Edit task only supports shot entities: {entity_uri}'

    # Create single edit task that will resolve AOVs at runtime
    title = f'edit {entity_uri}'
    task = edit_task.build(dict(
        title = title,
        priority = 90,
        pool_name = pool_name,
        entity_uri = str(entity_uri),
        first_frame = first_frame,
        last_frame = last_frame,
        purpose = purpose
    ), dict(), staging_path)

    # Done
    return task

def _build_partial_denoise_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    render_version_name: str
    ):
    logging.debug('Creating partial denoise task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    priority = config['tasks']['partial_render']['priority']
    render_layer_name = config['settings']['render_layer_name']
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
        render_layer_name,
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        f'partial denoise '
        f'{render_layer_name} '
        f'{version_name}'
    )
    input_paths = {
        aov_name: get_aov_frame_path(
            entity_uri,
            render_department_name,
            render_layer_name,
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
            render_layer_name,
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
        render_layer_name = render_layer_name,
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def _build_full_denoise_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    render_version_name: str
    ):
    logging.debug('Creating denoise task')

    # Config parameters
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    priority = config['tasks']['full_render']['priority']
    pool_name = config['settings']['pool_name']
    render_layer_name = config['settings']['render_layer_name']
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
        render_layer_name,
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        f'full denoise '
        f'{render_layer_name} '
        f'{version_name}'
    )
    input_paths = {
        aov_name: get_aov_frame_path(
            entity_uri,
            render_department_name,
            render_layer_name,
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
            render_layer_name,
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
        render_layer_name = render_layer_name,
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def _build_slapcomp_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str
    ):
    logging.debug('Creating slapcomp task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    department_name = config['entity']['department']
    purpose = config['settings']['purpose']
    priority = config['tasks']['full_render']['priority']
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
    layer_names = render_settings['layer_names']
    aov_names = render_settings['aov_names']

    # Paramaters
    input_paths = {
        layer_name: {
            aov_name: get_aov_frame_path(
                entity_uri,
                render_department_name,
                layer_name,
                version_name,
                aov_name,
                '####',
                'exr',
                purpose
            )
            for aov_name in aov_names
        }
        for layer_name in layer_names
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
        render_layer_name = 'slapcomp',
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def _build_layer_mp4_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    render_version_name: str
    ):
    logging.debug('Creating layer mp4 task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    assert entity_uri is not None, 'Invalid entity in config'
    purpose = config['settings']['purpose']
    priority = config['tasks']['full_render']['priority']
    pool_name = config['settings']['pool_name']
    render_layer_name = config['settings']['render_layer_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Parameters - use layer-specific paths for individual render layers
    playblast_path = get_layer_playblast_path(
        entity_uri,
        render_layer_name,
        render_version_name,
        purpose
    )
    daily_path = get_layer_daily_path(entity_uri, render_layer_name, purpose)
    title = (
        f'mp4 layer '
        f'{render_department_name} '
        f'{render_layer_name} '
        f'{render_version_name}'
    )
    # Input is the beauty AOV from the render layer
    input_path = get_aov_frame_path(
        entity_uri,
        render_department_name,
        render_layer_name,
        render_version_name,
        'beauty',
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
    return task, render_version_name

def _build_slapcomp_mp4_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    slapcomp_version_name: str
    ):
    logging.debug('Creating slapcomp mp4 task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    assert entity_uri is not None, 'Invalid entity in config'
    purpose = config['settings']['purpose']
    priority = config['tasks']['full_render']['priority']
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Parameters - use department-level paths for slapcomp
    playblast_path = get_playblast_path(entity_uri, slapcomp_version_name, purpose)
    daily_path = get_daily_path(entity_uri, purpose)
    title = (
        f'mp4 slapcomp '
        f'{render_department_name} '
        f'{slapcomp_version_name}'
    )
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

def _build_partial_notify_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str
    ):
    logging.debug('Creating partial notify task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    render_layer_name = config['settings']['render_layer_name']
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
        render_layer_name,
        version_name,
        'beauty',
        '####',
        'exr',
        purpose
    )

    # Create the task
    task = notify_job.build(dict(
        title = title,
        priority = 55,
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

def _build_slapcomp_notify_job(
    config: dict,
    staging_path: Path,
    render_department_name: str,
    version_name: str
    ):
    logging.debug('Creating slapcomp notify task')

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    assert entity_uri is not None, 'Invalid entity in config'
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    channel_name = config['tasks']['full_render']['channel_name']

    # Parameters - slapcomp playblast
    video_path = get_playblast_path(
        entity_uri,
        version_name,
        purpose
    )
    title = (
        f'notify slapcomp '
        f'{render_department_name} '
        f'{version_name}'
    )
    message = (
        f'{entity_uri} - '
        f'{render_department_name}/slapcomp - '
        f'{version_name}'
    )

    # Create the task
    task = notify_job.build(dict(
        title = title,
        priority = 60,
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

def _add_jobs(
    batch: Batch,
    jobs: dict[str, Job],
    deps: dict[str, list[str]]
    ):
    indicies = {
        job_name: batch.add_job(job)
        for job_name, job in jobs.items()
    }
    for job_name, job_deps in deps.items():
        if len(job_deps) == 0: continue
        for dep_name in job_deps:
            if dep_name not in indicies:
                logging.warning(
                    f'Job "{job_name}" depends on '
                    f'non-existing job "{dep_name}"'
                )
                continue
            batch.add_dep(
                indicies[job_name],
                indicies[dep_name]
            )

def submit(
    config: dict,
    paths: dict[Path, Path]
    ) -> int:

    # Config
    entity_uri = Uri.parse_unsafe(config['entity']['uri'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    render_layer_names = config['settings']['render_layer_names']

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Get deadline ready
    try: farm = Deadline()
    except: return _error('Could not connect to Deadline')

    # Open temporary directory
    root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        logging.debug(f'Temporary directory: {temp_path}')

        # Batch and jobs
        layers_text = f"[{', '.join(render_layer_names)}]"
        batch = Batch(
            f'{project_name} '
            f'{purpose} '
            f'{entity_uri} '
            f'{layers_text} '
            f'{user_name} '
            f'{timestamp}'
        )

        # Parameters
        render_department_name = config['settings']['render_department_name']

        # Prepare adding jobs
        jobs = dict()
        deps = dict()
        def _add_job(job_name, job, job_deps):
            jobs[job_name] = job
            deps[job_name] = job_deps

        # Track version names per layer
        render_version_names = {}
        denoise_version_names = {}

        # PARTIAL RENDER: Create jobs for each layer
        if 'partial_render' in config['tasks']:
            for layer_name in render_layer_names:
                
                # Create layer-specific config
                layer_config = config.copy()
                layer_config['settings'] = config['settings'].copy()
                layer_config['settings']['render_layer_name'] = layer_name

                # Build partial render job for this layer
                render_result = _build_partial_render_job(layer_config, paths, temp_path)
                render_job, render_version = render_result
                render_version_names[layer_name] = render_version
                _add_job(f'partial_render_{layer_name}', render_job, [])

                if config['tasks']['partial_render']['denoise']:
                    denoise_result = _build_partial_denoise_job(
                        layer_config,
                        temp_path,
                        render_department_name,
                        render_version
                    )
                    denoise_job, denoise_version = denoise_result
                    denoise_version_names[layer_name] = denoise_version
                    _add_job(f'partial_denoise_{layer_name}', denoise_job, [f'partial_render_{layer_name}'])

                    notify_job = _build_partial_notify_job(
                        layer_config,
                        temp_path,
                        'denoise',
                        denoise_version
                    )
                    _add_job(f'partial_notify_{layer_name}', notify_job, [f'partial_denoise_{layer_name}'])
                else:
                    notify_job = _build_partial_notify_job(
                        layer_config,
                        temp_path,
                        render_department_name,
                        render_version
                    )
                    _add_job(f'partial_notify_{layer_name}', notify_job, [f'partial_render_{layer_name}'])

        # FULL RENDER: Create jobs for each layer
        if 'full_render' in config['tasks']:
            for layer_name in render_layer_names:
                # Create layer-specific config
                layer_config = config.copy()
                layer_config['settings'] = config['settings'].copy()
                layer_config['settings']['render_layer_name'] = layer_name

                # Build full render job for this layer
                render_result = _build_full_render_job(
                    layer_config,
                    paths,
                    temp_path,
                    render_version_names.get(layer_name)
                )
                render_job, render_version = render_result
                render_version_names[layer_name] = render_version

                # Determine dependencies
                render_deps = []
                if 'partial_render' in config['tasks']:
                    render_deps = [f'partial_render_{layer_name}']

                _add_job(f'full_render_{layer_name}', render_job, render_deps)

                if config['tasks']['full_render']['denoise']:
                    denoise_result = _build_full_denoise_job(
                        layer_config,
                        temp_path,
                        render_department_name,
                        render_version
                    )
                    denoise_job, denoise_version = denoise_result
                    denoise_version_names[layer_name] = denoise_version
                    _add_job(f'full_denoise_{layer_name}', denoise_job, [f'full_render_{layer_name}'])

                    layer_mp4_result = _build_layer_mp4_job(
                        layer_config,
                        temp_path,
                        'denoise',
                        denoise_version
                    )
                    layer_mp4_job, _ = layer_mp4_result
                    _add_job(f'layer_mp4_{layer_name}', layer_mp4_job, [f'full_denoise_{layer_name}'])
                else:
                    layer_mp4_result = _build_layer_mp4_job(
                        layer_config,
                        temp_path,
                        render_department_name,
                        render_version
                    )
                    layer_mp4_job, _ = layer_mp4_result
                    _add_job(f'layer_mp4_{layer_name}', layer_mp4_job, [f'full_render_{layer_name}'])

            # CROSS-LAYER JOBS: Create once, depend on all layer jobs
            if config['tasks']['full_render']['denoise']:
                # Collect all denoise job names
                all_denoise_jobs = [f'full_denoise_{ln}' for ln in render_layer_names]

                # Edit job depends on all denoise jobs
                edit_job = _build_edit_job(config, temp_path)
                _add_job('edit', edit_job, all_denoise_jobs)

                # Slapcomp depends on all denoise jobs
                # Use version from first layer (all should have same version number)
                first_layer = render_layer_names[0]
                slapcomp_result = _build_slapcomp_job(
                    config,
                    temp_path,
                    'denoise',
                    denoise_version_names[first_layer]
                )
                slapcomp_job, slapcomp_version = slapcomp_result
                _add_job('slapcomp', slapcomp_job, all_denoise_jobs)

                slapcomp_mp4_result = _build_slapcomp_mp4_job(
                    config,
                    temp_path,
                    'denoise',
                    slapcomp_version
                )
                slapcomp_mp4_job, _ = slapcomp_mp4_result
                _add_job('slapcomp_mp4', slapcomp_mp4_job, ['slapcomp'])

                slapcomp_notify_job = _build_slapcomp_notify_job(
                    config,
                    temp_path,
                    'denoise',
                    slapcomp_version
                )
                _add_job('slapcomp_notify', slapcomp_notify_job, ['slapcomp_mp4'])
            else:
                # Collect all render job names
                all_render_jobs = [f'full_render_{ln}' for ln in render_layer_names]

                # Edit job depends on all render jobs
                edit_job = _build_edit_job(config, temp_path)
                _add_job('edit', edit_job, all_render_jobs)

                # Slapcomp depends on all render jobs
                first_layer = render_layer_names[0]
                slapcomp_result = _build_slapcomp_job(
                    config,
                    temp_path,
                    render_department_name,
                    render_version_names[first_layer]
                )
                slapcomp_job, slapcomp_version = slapcomp_result
                _add_job('slapcomp', slapcomp_job, all_render_jobs)

                slapcomp_mp4_result = _build_slapcomp_mp4_job(
                    config,
                    temp_path,
                    render_department_name,
                    slapcomp_version
                )
                slapcomp_mp4_job, _ = slapcomp_mp4_result
                _add_job('slapcomp_mp4', slapcomp_mp4_job, ['slapcomp'])

                slapcomp_notify_job = _build_slapcomp_notify_job(
                    config,
                    temp_path,
                    render_department_name,
                    slapcomp_version
                )
                _add_job('slapcomp_notify', slapcomp_notify_job, ['slapcomp_mp4'])

        # Add jobs
        _add_jobs(batch, jobs, deps)

        # Submit
        farm.submit(batch, api.storage.resolve(Uri.parse_unsafe('export:/other/jobs')))

    # Done
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    args = parser.parse_args()

    # Check config path
    config_path = Path(args.config_path)
    if not config_path.exists():
        return _error(f'Config path not found: {config_path}')
    
    # Load and check config
    config = load_json(config_path)
    if not _is_valid_config(config):
        return _error(f'Invalid config: {config_path}')
    
    # Run submit
    return submit(config)

if __name__ == "__main__":
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())