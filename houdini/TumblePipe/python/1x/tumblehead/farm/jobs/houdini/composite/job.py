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
from tumblehead.apps.deadline import (
    Deadline,
    Batch,
    Job
)
from tumblehead.pipe.paths import (
    Entity,
    ShotEntity,
    get_frame_path,
    get_next_frame_path,
    get_aov_frame_uri,
    get_aov_frame_path,
    get_playblast_path,
    get_daily_path,
    get_layer_playblast_path,
    get_layer_daily_path,
    get_render_context
)
import tumblehead.farm.tasks.composite.task as composite_job
import tumblehead.farm.tasks.slapcomp.task as slapcomp_job
import tumblehead.farm.tasks.mp4.task as mp4_job
import tumblehead.farm.tasks.notify.task as notify_job
import tumblehead.farm.tasks.edit.task as edit_task

from importlib import reload
reload(composite_job)
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
        'node_path': 'string',
        'layer_names': ['main', 'mirror'],
        'aov_names': ['beauty', 'alpha'],
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
        if 'layer_names' in settings:
            if not isinstance(settings['layer_names'], list): return False
            if len(settings['layer_names']) == 0: return False
        if not _check_int(settings, 'first_frame'): return False
        if not _check_int(settings, 'last_frame'): return False
        if not _check_int(settings, 'step_size'): return False
        if not _check_int(settings, 'batch_size'): return False
        return True
    
    def _valid_jobs(tasks):

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
    if not _valid_jobs(config['tasks']): return False
    return True

def _build_partial_composite_job(
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
    layer_names = config['settings']['layer_names']
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

    # Get version name from first layer
    first_layer = layer_names[0]
    receipt_path = get_next_frame_path(
        entity,
        'composite',
        first_layer,
        '####',
        'json',
        purpose
    )
    version_name = receipt_path.parent.name
    title = (
        'partial composite '
        f'{version_name}'
    )

    # Create output paths for each layer (beauty RGBA)
    output_paths = {}
    for layer_name in layer_names:
        output_paths[layer_name] = get_aov_frame_path(
            entity,
            'composite',
            layer_name,
            version_name,
            'beauty',
            '####',
            'exr',
            purpose
        )

    # Create the task
    task = composite_job.build(dict(
        entity = entity.to_json(),
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
        layer_names = layer_names,
        output_paths = {
            output_key: path_str(to_windows_path(aov_path))
            for output_key, aov_path in output_paths.items()
        }
    ), paths, staging_path)

    # Create the framestack context file for each layer
    for layer_name in layer_names:
        layer_receipt_path = get_frame_path(
            entity,
            'composite',
            layer_name,
            version_name,
            '####',
            'json',
            purpose
        )
        context_path = layer_receipt_path.parent / 'context.json'
        store_json(context_path, dict(
            entity = entity.to_json(),
            render_layer_name = layer_name,
            render_department_name = 'composite',
            version_name = version_name,
            first_frame = first_frame,
            last_frame = last_frame,
            step_size = step_size
        ))

    # Done
    return task, version_name

def _build_full_composite_job(
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
    layer_names = config['settings']['layer_names']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']
    batch_size = config['settings']['batch_size']

    # Find receipt path and version name using first layer
    first_layer = layer_names[0]
    def _receipt_path(version_name):
        if version_name is None:
            output_frame_path = get_next_frame_path(
                entity,
                'composite',
                first_layer,
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
                first_layer,
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

    # Create output paths for each layer (beauty RGBA)
    output_paths = {}
    for layer_name in layer_names:
        output_paths[layer_name] = get_aov_frame_path(
            entity,
            'composite',
            layer_name,
            version_name,
            'beauty',
            '####',
            'exr',
            purpose
        )

    # Create the task
    task = composite_job.build(dict(
        entity = entity.to_json(),
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
        layer_names = layer_names,
        output_paths = {
            output_key: path_str(to_windows_path(aov_path))
            for output_key, aov_path in output_paths.items()
        }
    ), paths, staging_path)

    # Create the framestack context file for each layer
    for layer_name in layer_names:
        layer_receipt_path = get_frame_path(
            entity,
            'composite',
            layer_name,
            version_name,
            '####',
            'json',
            purpose
        )
        context_path = layer_receipt_path.parent / 'context.json'
        store_json(context_path, dict(
            entity = entity.to_json(),
            render_layer_name = layer_name,
            render_department_name = 'composite',
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
    entity = Entity.from_json(config['entity'])
    assert entity is not None, 'Invalid entity in config'
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']

    # Extract entity fields
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            pass
        case _:
            assert False, f'Edit task only supports shot entities: {entity}'

    # Create single edit task that will resolve AOVs at runtime
    title = f'edit {entity}'
    task = edit_task.build(dict(
        title = title,
        priority = 90,
        pool_name = pool_name,
        sequence_name = sequence_name,
        shot_name = shot_name,
        first_frame = first_frame,
        last_frame = last_frame,
        purpose = purpose
    ), dict(), staging_path)

    # Done
    return task

def _build_slapcomp_job(
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

    # Get render context to resolve AOV paths across departments
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            render_context = get_render_context(sequence_name, shot_name, purpose=purpose)
        case _:
            raise NotImplementedError(f"Slapcomp not implemented for entity type: {entity}")

    # Get department priorities for AOV resolution
    shot_departments = api.config.list_shot_department_names()
    render_departments = api.config.list_render_department_names()

    # Resolve latest beauty and alpha AOVs across all departments
    # This will find the best available version of each AOV (prioritizing higher departments)
    latest_aovs = render_context.resolve_latest_aovs(
        shot_departments,
        render_departments,
        min_shot_department=None,
        min_render_department=None,
        aov_filter=lambda aov_name: aov_name in ['beauty', 'alpha']
    )

    # Build input_paths from resolved AOVs in correct layer order
    # Get render layer names in order (background to foreground)
    match entity:
        case ShotEntity(sequence_name, shot_name, _):
            render_layer_names = api.config.list_render_layer_names(sequence_name, shot_name)
        case _:
            raise NotImplementedError(f"Slapcomp layer ordering not implemented for entity type: {entity}")

    # Build ordered input_paths dict using render layer order
    input_paths = {}
    for layer_name in render_layer_names:
        if layer_name not in latest_aovs:
            continue
        layer_aovs = latest_aovs[layer_name]
        input_paths[layer_name] = {}
        for aov_name, (render_dept, aov_version, aov, shot_dept) in layer_aovs.items():
            # Get the frame path pattern with ####
            aov_frame_path = aov.get_aov_frame_path('####')
            input_paths[layer_name][aov_name] = aov_frame_path
            logging.debug(f'  Resolved {layer_name}/{aov_name}: {render_dept}/{aov_version}')

    if not input_paths:
        raise RuntimeError('No AOVs resolved for slapcomp. Ensure layers have been rendered.')

    # Output paths
    receipt_path = get_next_frame_path(
        entity,
        'composite',
        'slapcomp',
        '####',
        'json',
        purpose
    )
    slapcomp_version_name = receipt_path.parent.name
    title = (
        f'slapcomp composite '
        f'{slapcomp_version_name}'
    )
    output_path = get_frame_path(
        entity,
        'composite',
        'slapcomp',
        slapcomp_version_name,
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
        entity = entity.to_json(),
        render_department_name = 'composite',
        render_layer_name = 'slapcomp',
        version_name = slapcomp_version_name,
        first_frame = first_frame,
        last_frame = last_frame,
        step_size = step_size
    ))

    # Done
    return task, slapcomp_version_name

def _build_layer_mp4_job(
    config: dict,
    staging_path: Path,
    layer_name: str,
    version_name: str
    ):
    """Build MP4 job for a single render layer"""
    logging.debug(f'Creating layer mp4 task for {layer_name}')

    # Config
    entity = Entity.from_json(config['entity'])
    purpose = config['settings']['purpose']
    priority = config['settings']['priority']
    pool_name = config['settings']['pool_name']
    first_frame = config['settings']['first_frame']
    last_frame = config['settings']['last_frame']
    step_size = config['settings']['step_size']

    # Parameters
    playblast_path = get_layer_playblast_path(entity, layer_name, version_name, purpose)
    daily_path = get_layer_daily_path(entity, layer_name, purpose)
    title = f'mp4 {layer_name} {version_name}'
    input_path = get_aov_frame_path(
        entity,
        'composite',
        layer_name,
        version_name,
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
    return task

def _build_slapcomp_mp4_job(
    config: dict,
    staging_path: Path,
    slapcomp_version_name: str
    ):
    """Build MP4 job for slapcomp output"""
    logging.debug('Creating slapcomp mp4 task')

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
    task = notify_job.build(dict(
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

def _build_full_notify_job(
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
    task = notify_job.build(dict(
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

def _build_slapcomp_notify_job(
    config: dict,
    staging_path: Path,
    slapcomp_version_name: str
    ):
    """Build notify job for slapcomp output"""
    logging.debug('Creating slapcomp notify task')

    # Config
    entity = Entity.from_json(config['entity'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']
    pool_name = config['settings']['pool_name']
    channel_name = config['tasks']['full_composite']['channel_name']

    # Parameters
    title = f'notify slapcomp {slapcomp_version_name}'
    message = f'{entity} - slapcomp - {slapcomp_version_name}'
    video_path = get_playblast_path(
        entity,
        slapcomp_version_name,
        purpose
    )

    # Create the task
    task = notify_job.build(dict(
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
    entity = Entity.from_json(config['entity'])
    user_name = config['settings']['user_name']
    purpose = config['settings']['purpose']

    # Parameters
    project_name = get_project_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Get deadline ready
    try: farm = Deadline()
    except: return _error('Could not connect to Deadline')

    # Open temporary directory
    root_temp_path = fix_path(api.storage.resolve('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        logging.info(f'Temporary directory: {temp_path}')

        # Batch
        batch = Batch(
            f'{project_name} '
            f'{purpose} '
            f'{entity} '
            f'{user_name} '
            f'{timestamp}'
        )

        # Prepare adding jobs
        jobs = dict()
        deps = dict()
        def _add_job(job_name, job, job_deps):
            jobs[job_name] = job
            deps[job_name] = job_deps

        # Initial partial composite job
        render_version_name = None
        if 'partial_composite' in config['tasks']:
            render_result = _build_partial_composite_job(
                config,
                paths,
                temp_path
            )
            composite_job, render_version_name = render_result
            notify_job = _build_partial_notify_job(
                config,
                temp_path,
                render_version_name
            )
            _add_job('partial_composite', composite_job, [])
            _add_job('partial_notify', notify_job, ['partial_composite'])

        # Following full composite jobs
        if 'full_composite' in config['tasks']:
            render_result = _build_full_composite_job(
                config,
                paths,
                temp_path,
                render_version_name
            )
            composite_job, render_version_name = render_result

            # Get layer names for creating per-layer MP4 jobs
            layer_names = config['settings']['layer_names']

            # Create MP4 jobs for each layer
            layer_mp4_jobs = []
            for layer_name in layer_names:
                layer_mp4 = _build_layer_mp4_job(
                    config,
                    temp_path,
                    layer_name,
                    render_version_name
                )
                job_name = f'layer_mp4_{layer_name}'
                _add_job(job_name, layer_mp4, ['full_composite'])
                layer_mp4_jobs.append(job_name)

            edit_job = _build_edit_job(
                config,
                temp_path
            )
            slapcomp_result = _build_slapcomp_job(
                config,
                temp_path,
                render_version_name
            )
            slapcomp_job, slapcomp_version_name = slapcomp_result
            slapcomp_mp4_result = _build_slapcomp_mp4_job(
                config,
                temp_path,
                slapcomp_version_name
            )
            slapcomp_mp4, _ = slapcomp_mp4_result
            slapcomp_notify = _build_slapcomp_notify_job(
                config,
                temp_path,
                slapcomp_version_name
            )
            _add_job('full_composite', composite_job, (
                [] if 'partial_composite' not in config['tasks'] else
                ['partial_composite']
            ))
            _add_job('edit', edit_job, ['full_composite'])
            _add_job('slapcomp', slapcomp_job, ['full_composite'])
            _add_job('slapcomp_mp4', slapcomp_mp4, ['slapcomp'])
            _add_job('slapcomp_notify', slapcomp_notify, ['slapcomp_mp4'])

        # Add jobs
        _add_jobs(batch, jobs, deps)

        # Submit
        farm.submit(batch, api.storage.resolve('export:/other/jobs'))

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