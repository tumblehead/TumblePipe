from dataclasses import dataclass
from pathlib import Path
import datetime as dt
import tempfile
import logging
import sys
import os

from .api import fix_path, path_str, Client
from .util.uri import Uri
from .config.timeline import get_frame_range
from .config.department import list_departments
from .apps.micromamba import Micromamba, parse_package_spec
from .apps.deadline import Deadline

class ContextLogHandler:
    def __init__(self, logger, handler):
        self.logger = logger
        self.handler = handler

    def __enter__(self):
        self.logger.addHandler(self.handler)

    def __exit__(self, exc_type, exc_value, traceback):
        self.logger.removeHandler(self.handler)

def _error(msg: str) -> int:
    logging.error(msg)
    return 1

@dataclass
class Task:
    entity_uri: Uri
    task_path: Path
    log_path: Path

def _run_task(
    api: Client,
    task: Task,
    shot_department_name: str,
    render_department_name: str,
    pool_name: str,
    priority: int,
    ) -> int:

    # Set up logging
    timestamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    entity_display = task.entity_uri.display_name().replace('/', '_')
    log_path = task.log_path / f'{entity_display}_{timestamp}.log'
    
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('[%(levelname)s] %(message)s')

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Script path
    script_path = task.task_path
    if not script_path.exists():
        return _error(f'Task script does not exist: {script_path}')
    
    # Environment spec
    requirements_path = task.task_path / 'requirements.txt'
    env_spec = (
        dict()
        if not requirements_path.exists()
        else dict(
            parse_package_spec(line)
            for line in requirements_path.read_text().splitlines()
        )
    )

    # Prepare micromamba
    m = Micromamba()

    # Create temporary working directory
    with tempfile.TemporaryDirectory() as temp_dir:

        # Run task
        temp_path = Path(temp_dir)
        env_name = temp_path.name

        # Create a python environment
        logging.info(f'Creating environment: {env_name}')
        if not m.create_env(env_name, 10, env_spec):
            return _error('Failed to create environment')
        
        # Run the task in the python environment
        logging.info(f'Running task: {task.task_path} on {task.entity_uri.display_name()}')
        frame_range = get_frame_range(task.entity_uri)
        render_range = frame_range.full_range()
        task_args = [
            str(task.entity_uri),  # entity_uri
            shot_department_name,
            render_department_name,
            pool_name,
            str(priority),
            str(render_range.first_frame),
            str(render_range.last_frame),
            str(1),
            str(5)
        ]
        task_env = {
            'TH_PROJECT_PATH': path_str(api.PROJECT_PATH),
            'TH_PIPELINE_PATH': path_str(api.PIPELINE_PATH),
            'TH_CONFIG_PATH': path_str(api.CONFIG_PATH)
        }
        with ContextLogHandler(logger, file_handler):
            if m.run(temp_path, env_name, script_path, task_args, task_env) != 0:
                logging.info(f'Removing environment: {env_name}')
                if not m.remove_env(env_name):
                    return _error('Failed to remove environment')
                return _error('Failed to run task')
        
        # Clean up the python environment
        logging.info(f'Removing environment: {env_name}')
        if not m.remove_env(env_name):
            return _error('Failed to remove environment')
        
        # Done
        return 0

def _scan_entity_dirs(api: Client, parent_dir: Path) -> list[Path]:
    """Recursively scan directories for valid entity names."""
    result = []
    for child_dir in parent_dir.iterdir():
        if not child_dir.is_dir():
            continue
        entity_name = child_dir.name
        if not api.naming.is_valid_entity_name(entity_name):
            continue
        result.append(child_dir)
        result.extend(_scan_entity_dirs(api, child_dir))
    return result

def _dir_to_entity_uri(shots_dir: Path, entity_dir: Path) -> Uri:
    """Convert a directory path to an entity URI."""
    relative_path = entity_dir.relative_to(shots_dir)
    return Uri.parse_unsafe(f'entity:/shots/{relative_path.as_posix()}')

def _is_leaf_entity(api: Client, entity_dir: Path) -> bool:
    """Check if directory has no valid child entities (is a leaf)."""
    for child_dir in entity_dir.iterdir():
        if not child_dir.is_dir():
            continue
        if api.naming.is_valid_entity_name(child_dir.name):
            return False
    return True

def _matches_pattern(shots_dir: Path, entity_dir: Path, pattern: str) -> bool:
    """Check if entity directory matches the given glob pattern."""
    import fnmatch
    relative_path = entity_dir.relative_to(shots_dir).as_posix()
    return fnmatch.fnmatch(relative_path, pattern)

def main(
    api: Client,
    dry_run: bool,
    task_path: Path,
    entity_pattern: str,
    shot_department_name: str,
    render_department_name: str,
    pool_name: str,
    priority: int
    ):

    # Paths
    shots_dir = api.storage.resolve(Uri.parse_unsafe('shots:/'))
    log_dir = api.storage.resolve(Uri.parse_unsafe(f'export:/other/logs/{task_path.parent.name}'))
    log_dir.mkdir(parents = True, exist_ok = True)

    # Task list
    tasks: list[Task] = list()

    # Scan all entity directories
    entity_dirs = _scan_entity_dirs(api, shots_dir)

    # Filter by pattern if provided
    if entity_pattern != '*':
        entity_dirs = [d for d in entity_dirs if _matches_pattern(shots_dir, d, entity_pattern)]

    # Create tasks for leaf entities (directories with no valid child entities)
    for entity_dir in entity_dirs:
        if _is_leaf_entity(api, entity_dir):
            entity_uri = _dir_to_entity_uri(shots_dir, entity_dir)
            tasks.append(Task(
                entity_uri = entity_uri,
                task_path = task_path,
                log_path = log_dir
            ))
    
    # Run tasks
    for task in tasks:
        if dry_run:
            logging.info(f'Would run task: {task.task_path} on {task.entity_uri.display_name()}')
        else:
            logging.info(f'Running task: {task.task_path} on {task.entity_uri.display_name()}')
            _run_task(
                api,
                task,
                shot_department_name,
                render_department_name,
                pool_name,
                priority
            )

    # Done
    return 0

def _is_valid_entity_pattern(pattern: str) -> bool:
    """Check if the entity pattern is valid (basic validation)."""
    if pattern == '*':
        return True
    # Allow alphanumeric, underscore, forward slash, and glob wildcards
    allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_/*?[]')
    return all(c in allowed_chars for c in pattern)

def cli():
    import argparse
    parser = argparse.ArgumentParser(description='Batch processing on shots.')
    parser.add_argument('--dry-run', '-d', action='store_true', help='Print commands instead of running them.')
    parser.add_argument('user_name', type=str, help='User name to use.')
    parser.add_argument('project_path', type=str, help='Path to the project directory.')
    parser.add_argument('pipeline_path', type=str, help='Path to the pipeline directory.')
    parser.add_argument('task', help='Task to perform.')
    parser.add_argument('entity_pattern', default='*', help='Entity pattern to process (e.g., "*", "seq01/*", "season01/*/seq01/*").')
    parser.add_argument('shot_department', type=str, help='Shot department to use.')
    parser.add_argument('render_department', type=str, help='Render department to use.')
    parser.add_argument('pool', type=str, help='Pool to use.')
    parser.add_argument('priority', type=int, help='Priority to use.')
    args = parser.parse_args()

    # Check user name
    user_name = args.user_name
    os.environ['TH_USER'] = user_name

    # Check project path
    project_path = fix_path(Path(args.project_path))
    if not project_path.exists():
        parser.error(f'Project path does not exist: {project_path}')
    os.environ['TH_PROJECT_PATH'] = str(project_path)

    # Check pipeline path
    pipeline_path = fix_path(Path(args.pipeline_path))
    if not pipeline_path.exists():
        parser.error(f'Pipeline path does not exist: {pipeline_path}')
    os.environ['TH_PIPELINE_PATH'] = str(pipeline_path)

    # Setup api
    api = Client(project_path, pipeline_path)

    # Check task
    task_name = args.task
    task_path = Path(__file__).parent / 'tasks' / task_name / 'task.py'
    if not task_path.exists():
        parser.error(f'Invalid task: {task_name}')

    # Check entity pattern
    entity_pattern = args.entity_pattern
    if not _is_valid_entity_pattern(entity_pattern):
        parser.error(f'Invalid entity pattern: {entity_pattern}')

    # Check shot department
    shot_departments = list_departments('shots')
    shot_department_names = [d.name for d in shot_departments]
    shot_department_name = args.shot_department
    if shot_department_name not in shot_department_names:
        parser.error(f'Invalid shot department: {shot_department_name}')

    # Check render department
    render_departments = api.config.list_render_departments()
    render_department_names = [d.name for d in render_departments]
    render_department_name = args.render_department
    if render_department_name not in render_department_names:
        parser.error(f'Invalid render department: {render_department_name}')

    # Prepare deadline
    try: deadline = Deadline()
    except: parser.error('Failed to connect to Deadline')

    # Check pool
    pool_names = deadline.list_pools()
    pool_name = args.pool
    if pool_name not in pool_names:
        parser.error(f'Invalid pool: {pool_name}')

    # Check priority
    priority = args.priority
    if not 0 <= priority <= 100:
        parser.error(f'Invalid priority: {priority}')

    # Run main
    return main(
        api,
        args.dry_run,
        task_path,
        entity_pattern,
        shot_department_name,
        render_department_name,
        pool_name,
        priority
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '[%(levelname)s] %(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())