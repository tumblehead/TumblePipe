from dataclasses import dataclass
from pathlib import Path
import datetime as dt
import tempfile
import logging
import sys
import os

from .api import fix_path, path_str, Client
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
    sequence_name: str
    shot_name: str
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
    log_path = task.log_path / f'{task.sequence_name}_{task.shot_name}_{timestamp}.log'
    
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
        logging.info(f'Running task: {task.task_path} on {task.sequence_name} {task.shot_name}')
        frame_range = api.config.get_frame_range(task.sequence_name, task.shot_name)
        render_range = frame_range.full_range()
        task_args = [
            task.sequence_name,
            task.shot_name,
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

def _scan_sequence_dirs(api: Client, shots_dir: Path) -> dict[str, list[Path]]:
    result = dict()
    for sequence_dir in shots_dir.iterdir():
        if not sequence_dir.is_dir(): continue
        sequence_name = sequence_dir.name
        if not api.naming.is_valid_sequence_name(sequence_name): continue
        result[sequence_name] = sequence_dir
    return result

def _scan_shot_dirs(api: Client, sequence_dir: Path) -> dict[str, list[Path]]:
    result = dict()
    for shot_dir in sequence_dir.iterdir():
        if not shot_dir.is_dir(): continue
        shot_name = shot_dir.name
        if not api.naming.is_valid_shot_name(shot_name): continue
        result[shot_name] = shot_dir
    return result

def main(
    api: Client,
    dry_run: bool,
    task_path: Path,
    sequence_arg: str,
    shot_arg: str,
    shot_department_name: str,
    render_department_name: str,
    pool_name: str,
    priority: int
    ):

    # Paths
    shots_dir = api.storage.resolve('shots:/')
    log_dir = api.storage.resolve(f'export:/other/logs/{task_path.parent.name}')
    log_dir.mkdir(parents = True, exist_ok = True)

    # Task list
    tasks: list[Task] = list()

    # Map sequences
    sequence_dirs = _scan_sequence_dirs(api, shots_dir)
    if sequence_arg != '*' and sequence_arg not in sequence_dirs:
        return _error(f'Invalid sequence: {sequence_arg}')
    sequence_names = (
        list(sequence_dirs.keys())
        if sequence_arg == '*'
        else [ sequence_arg ]
    )

    # Iterate over sequences
    for sequence_name in sequence_names:
        sequence_dir = sequence_dirs[sequence_name]

        # Map shots
        shot_dirs = _scan_shot_dirs(api, sequence_dir)
        if shot_arg != '*' and shot_arg not in shot_dirs:
            return _error(f'Invalid shot: {shot_arg}')
        shot_names = (
            list(shot_dirs.keys())
            if shot_arg == '*'
            else [ shot_arg ]
        )

        # Iterate over shots
        for shot_name in shot_names:
            tasks.append(Task(
                sequence_name = sequence_name,
                shot_name = shot_name,
                task_path = task_path,
                log_path = log_dir
            ))
    
    # Run tasks
    for task in tasks:
        if dry_run:
            logging.info(f'Would run task: {task.task_path} on {task.sequence_name} {task.shot_name}')
        else:
            logging.info(f'Running task: {task.task_path} on {task.sequence_name} {task.shot_name}')
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

def _is_valid_sequence_arg(api: Client, sequence_arg: str) -> bool:
    if sequence_arg == '*': return True
    return api.naming.is_valid_sequence_name(sequence_arg)

def _is_valid_shot_arg(api: Client, shot_arg: str) -> bool:
    if shot_arg == '*': return True
    return api.naming.is_valid_shot_name(shot_arg)

def cli():
    import argparse
    parser = argparse.ArgumentParser(description='Batch processing on shots.')
    parser.add_argument('--dry-run', '-d', action='store_true', help='Print commands instead of running them.')
    parser.add_argument('user_name', type=str, help='User name to use.')
    parser.add_argument('project_path', type=str, help='Path to the project directory.')
    parser.add_argument('pipeline_path', type=str, help='Path to the pipeline directory.')
    parser.add_argument('task', help='Task to perform.')
    parser.add_argument('sequence', default='*', help='Sequence/s to process.')
    parser.add_argument('shot', default='*', help='Shot/s to process.')
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

    # Check sequence
    sequence_arg = args.sequence
    if not _is_valid_sequence_arg(api, sequence_arg):
        parser.error(f'Invalid sequence: {sequence_arg}')
    
    # Check shot
    shot_arg = args.shot
    if not _is_valid_shot_arg(api, shot_arg):
        parser.error(f'Invalid shot: {shot_arg}')
    
    # Check shot department
    shot_department_names = api.config.list_shot_department_names()
    shot_department_name = args.shot_department
    if shot_department_name not in shot_department_names:
        parser.error(f'Invalid shot department: {shot_department_name}')

    # Check render department
    render_department_names = api.config.list_render_department_names()
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
        sequence_arg,
        shot_arg,
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