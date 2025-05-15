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
from tumblehead.config import BlockRange
from tumblehead.apps.deadline import Deadline, Batch
from tumblehead.pipe.paths import (
    Entity,
    get_frame_path,
    get_next_frame_path,
    get_aov_frame_path,
    get_playblast_path,
    get_daily_path
)
import tumblehead.farm.tasks.composite.task as composite_task
import tumblehead.farm.tasks.slapcomp.task as slapcomp_task
import tumblehead.farm.tasks.mp4.task as mp4_task
import tumblehead.farm.tasks.notify.task as notify_task

from importlib import reload
reload(composite_task)
reload(slapcomp_task)
reload(mp4_task)
reload(notify_task)

api = default_client()

def _error(msg):
    logging.error(msg)
    return 1

"""
config = {
    'entity': {
        'tag': 'asset',
        'category_name': 'string',
        'asset_name': 'string',
        'department_name': 'string'
    } | {
        'tag': 'shot',
        'sequence_name': 'string',
        'shot_name': 'string',
        'department_name': 'string'
    } | {
        'tag': 'kit',
        'category_name': 'string',
        'kit_name': 'string',
        'department_name': 'string'
    },
    'settings': {
        'user_name': 'string',
        'purpose': 'string',
        'priority': 'int',
        'pool_name': 'string',
        'input_path': 'string',
        'node_path': 'string'
        'first_frame': 'int',
        'last_frame': 'int',
        'step_size': 'int',
        'batch_size': 'int',
    },
    'tasks': {
        'partial_composite': {
            'channel_name': 'string'
        },
        'full_composite': {
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

    def _check(value_checker, data, key):
        if key not in data: return False
        if not value_checker(data[key]): return False
        return True
    
    _check_str = partial(_check, _is_str)
    _check_int = partial(_check, _is_int)

    def _valid_entity(entity):
        if not isinstance(entity, dict): return False
        if 'tag' not in entity: return False
        match entity['tag']:
            case 'asset':
                if not _check_str(entity, 'category_name'): return False
                if not _check_str(entity, 'asset_name'): return False
                if not _check_str(entity, 'department_name'): return False
            case 'shot':
                if not _check_str(entity, 'sequence_name'): return False
                if not _check_str(entity, 'shot_name'): return False
                if not _check_str(entity, 'department_name'): return False
            case 'kit':
                if not _check_str(entity, 'category_name'): return False
                if not _check_str(entity, 'kit_name'): return False
                if not _check_str(entity, 'department_name'): return False
        return True
    
    def _valid_settings(settings):
        if not isinstance(settings, dict): return False
        if not _check_str(settings, 'user_name'): return False
        if not _check_str(settings, 'purpose'): return False
        if not _check_int(settings, 'priority'): return False
        if not _check_str(settings, 'pool_name'): return False
        if not _check_str(settings, 'input_path'): return False
        if not _check_str(settings, 'node_path'): return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        if not _check_int(settings, 'step_size'): return False
        if not _check_int(settings, 'batch_size'): return False
        return True
    
    def _valid_tasks(tasks):

        def _valid_partial_composite(partial_composite):
            if not isinstance(partial_composite, dict): return False
            if not _check_str(partial_composite, 'channel_name'): return False
            return True
    
        def _valid_full_composite(full_composite):
            if not isinstance(full_composite, dict): return False
            if not _check_str(full_composite, 'channel_name'): return False
            return True
        
        if not isinstance(tasks, dict): return False
        if 'partial_composite' in tasks:
            if not _valid_partial_composite(tasks['partial_composite']): return False
        if 'full_composite' in tasks:
            if not _valid_full_composite(tasks['full_composite']): return False
        return True
    
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not _valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    if 'tasks' not in config: return False
    if not _valid_tasks(config['tasks']): return False
    return True

def _build_partial_composite_task(
    config: dict,
    paths: dict[Path, Path],
    staging_path: Path
    ):
    logging.debug('Creating partial composite task')

    # Config
    entity = Entity.from_json(config['entity'])
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    input_path = config['settings']['input_path']
    node_path = config['settings']['node_path']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Find the middle frame
    frame_range = BlockRange(
        first_frame,
        last_frame,
        step_size
    )
    middle_frame = frame_range.frame(0.5)

    # Parameters
    receipt_path = get_next_frame_path(
        entity,
        'composite',
        'main',
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        'partial composite '
        f'{version_name}'
    )
    output_paths = {
        'beauty': get_aov_frame_path(
            entity,
            'composite',
            'main',
            version_name,
            'beauty',
            '####',
            'exr',
            purpose
        )
    }
    
    # Create the task
    task = composite_task.build(dict(
        title = title,
        priority = 90,
        pool_name = pool_name,
        first_frame = first_frame,
        last_frame = last_frame,
        frames = [first_frame, middle_frame, last_frame],
        step_size = 1,
        batch_size = 1,
        receipt_path = path_str(to_windows_path(receipt_path)),
        input_path = path_str(to_windows_path(input_path)),
        node_path = node_path,
        output_paths = {
            aov_name: path_str(to_windows_path(aov_path))
            for aov_name, aov_path in output_paths.items()
        }
    ), paths, staging_path)

    # Create the framestack context file
    context_path = receipt_path.parent / 'context.json'
    store_json(context_path, dict(
        entity = entity.to_json(),
        render_layer_name = 'main',
        render_department_name = 'composite',
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def _build_full_composite_task(
    config: dict,
    paths: dict[Path, Path],
    staging_path: Path,
    version_name: str
    ):
    logging.debug('Creating full composite task')

    # Config
    entity = Entity.from_json(config['entity'])
    purpose = config['settings']['purpose']
    priority = config['settings']['priority']
    pool_name = config['settings']['pool_name']
    input_path = Path(config['settings']['input_path'])
    node_path = config['settings']['node_path']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']
    batch_size = config['settings']['batch_size']

    # Find receipt path and version name
    def _receipt_path(version_name):
        if version_name is None:
            output_frame_path = get_next_frame_path(
                entity,
                'composite',
                'main',
                '####',
                'json',
                purpose
            )
            version_name = output_frame_path.parent.name
            return output_frame_path, version_name
        else:
            output_frame_path = get_frame_path(
                entity,
                'composite',
                'main',
                version_name,
                '####',
                'json',
                purpose
            )
            return output_frame_path, version_name

    # Parameters
    receipt_path, version_name = _receipt_path(version_name)
    title = (
        f'full composite '
        f'{version_name}'
    )
    output_paths = {
        'beauty': path_str(get_aov_frame_path(
            entity,
            'composite',
            'main',
            version_name,
            'beauty',
            '####',
            'exr',
            purpose
        ))
    }

    # Create the task
    task = composite_task.build(dict(
        title = title,
        priority = priority,
        pool_name = pool_name,
        first_frame = first_frame,
        last_frame = last_frame,
        frames = [],
        step_size = step_size,
        batch_size = batch_size,
        receipt_path = path_str(to_windows_path(receipt_path)),
        input_path = path_str(to_windows_path(input_path)),
        node_path = node_path,
        output_paths = {
            aov_name: path_str(to_windows_path(aov_path))
            for aov_name, aov_path in output_paths.items()
        }
    ), paths, staging_path)

    # Create the framestack context file
    context_path = receipt_path.parent / 'context.json'
    store_json(context_path, dict(
        entity = entity.to_json(),
        render_layer_name = 'main',
        render_department_name = 'composite',
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def _build_slapcomp_task(
    config: dict,
    staging_path: Path,
    version_name: str
    ):
    logging.debug('Creating slapcomp task')

    # Config
    entity = Entity.from_json(config['entity'])
    purpose = config['settings']['purpose']
    priority = config['settings']['priority']
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Paramaters
    input_paths = {
        'main': {
            'beauty': get_aov_frame_path(
                entity,
                'composite',
                'main',
                version_name,
                'beauty',
                '####',
                'exr',
                purpose
            )
        }
    }
    receipt_path = get_next_frame_path(
        entity,
        'composite',
        'slapcomp',
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        f'slapcomp composite '
        f'{version_name}'
    )
    output_path = get_frame_path(
        entity,
        'composite',
        'slapcomp',
        version_name,
        '####',
        'exr',
        purpose
    )

    # Create the task
    task = slapcomp_task.build(dict(
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
        entity = entity.to_json(),
        render_department_name = 'composite',
        render_layer_name = 'slapcomp',
        version_name = version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, version_name

def _build_mp4_task(
    config: dict,
    staging_path: Path,
    slapcomp_version_name: str
    ):
    logging.debug('Creating mp4 task')

    # Config
    entity = Entity.from_json(config['entity'])
    purpose = config['settings']['purpose']
    priority = config['settings']['priority']
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Parameters
    playblast_path = get_playblast_path(entity, slapcomp_version_name, purpose)
    daily_path = get_daily_path(entity, purpose)
    title = (
        f'mp4 composite '
        f'{slapcomp_version_name}'
    )
    input_path = get_frame_path(
        entity,
        'composite',
        'slapcomp',
        slapcomp_version_name,
        '####',
        'exr',
        purpose
    )

    # Create the task
    task = mp4_task.build(dict(
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

def _build_partial_notify_task(
    config: dict,
    staging_path: Path,
    version_name: str
    ):
    logging.debug('Creating partial notify task')

    # Config
    entity = Entity.from_json(config['entity'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']
    channel_name = config['tasks']['partial_composite']['channel_name']

    # Find the middle frame
    frame_range = BlockRange(
        first_frame,
        last_frame,
        step_size
    )
    middle_frame = frame_range.frame(0.5)
    
    # Parameters
    title = (
        f'notify partial composite '
        f'{version_name}'
    )
    message = (
        f'{entity} - composite - '
        f'{version_name}'
    )
    frame_path = get_aov_frame_path(
        entity,
        'composite',
        'main',
        version_name,
        'beauty',
        '####',
        'exr',
        purpose
    )

    # Create the task
    task = notify_task.build(dict(
        title = title,
        priority = 90,
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

def _build_full_notify_task(
    config: dict,
    staging_path: Path,
    version_name: str
    ):
    logging.debug('Creating full notify task')

    # Config
    entity = Entity.from_json(config['entity'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    channel_name = config['tasks']['full_composite']['channel_name']

    # Parameters
    title = (
        f'notify full composite '
        f'{version_name}'
    )
    message = (
        f'{entity} - composite - '
        f'{version_name}'
    )
    video_path = get_playblast_path(
        entity,
        version_name,
        purpose
    )

    # Create the task
    task = notify_task.build(dict(
        title = title,
        priority = 90,
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

def _add_tasks(batch, tasks):
    tasks = list(filter(lambda job_layer: len(job_layer) != 0, tasks))
    task_indices = [
        [
            batch.add_job(task)
            for task in job_layer
        ]
        for job_layer in tasks
    ]
    if len(tasks) <= 1: return
    prev_layer = task_indices[0]
    for curr_layer in task_indices[1:]:
        for curr_index in curr_layer:
            for prev_index in prev_layer:
                batch.add_dep(curr_index, prev_index)
        prev_layer = curr_layer

def submit(
    config: dict,
    paths: dict[Path, Path]
    ) -> int:

    # Config
    entity = Entity.from_json(config['entity'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Get deadline ready
    farm = Deadline()

    # Open temporary directory
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        logging.info(f'Temporary directory: {temp_path}')

        # Batch and jobs
        batch_name = (
            f'{project_name} '
            f'{purpose} '
            f'{entity} '
            f'{user_name} '
            f'{timestamp}'
        )
        job_batch = Batch(batch_name)
        job_layers = []

        # Prepare jobs
        job_layer1 = list()
        job_layer2 = list()
        job_layer3 = list()
        job_layer4 = list()
        job_layer5 = list()

        # Initial partial composite job
        if 'partial_composite' in config['tasks']:
            render_result = _build_partial_composite_task(
                config,
                paths,
                temp_path
            )
            composite_task, render_version_name = render_result
            job_layer1.append(composite_task)
            job_layer2.append(_build_partial_notify_task(
                config,
                temp_path,
                render_version_name
            ))

        # Following full composite jobs
        if 'full_composite' in config['tasks']:
            render_result = _build_full_composite_task(
                config,
                paths,
                temp_path,
                render_version_name
            )
            composite_task, render_version_name = render_result
            job_layer2.append(composite_task)
            slapcomp_result = _build_slapcomp_task(
                config,
                temp_path,
                render_version_name
            )
            slapcomp_task, slapcomp_version_name = slapcomp_result
            job_layer3.append(slapcomp_task)
            mp4_result = _build_mp4_task(
                config,
                temp_path,
                slapcomp_version_name
            )
            mp4_task, mp4_version_name = mp4_result
            job_layer4.append(mp4_task)
            job_layer5.append(_build_full_notify_task(
                config,
                temp_path,
                mp4_version_name
            ))

        # Add job layers
        job_layers.append(job_layer1)
        job_layers.append(job_layer2)
        job_layers.append(job_layer3)
        job_layers.append(job_layer4)
        job_layers.append(job_layer5)
        _add_tasks(job_batch, job_layers)

        # Submit
        farm.submit(job_batch, api.storage.resolve('export:/other/jobs'))

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