from pathlib import Path
from uuid import uuid4
import platform
import logging
import getpass
import shutil

from tumblehead.api import fix_path, to_windows_path, path_str
from tumblehead.apps import app

def log_progress(iterable):
    try:
        items = list(iterable)
    except Exception as e:
        iterable_info = f"type: {type(iterable)}"
        if hasattr(iterable, '__dict__'):
            try:
                iterable_info += f", attrs: {iterable.__dict__}"
            except:
                pass
        raise ValueError(f"Failed to convert iterable to list: {e}. Iterable {iterable_info}")
    
    total = len(items)
    if total == 0:
        return
    
    prev_progress = 0
    for index, item in enumerate(items):
        yield item
        progress = int((index + 1) / total * 100)
        if progress != prev_progress:
            print(f'Progress: {progress}')
            prev_progress = progress

def _walk_path(path: Path):
    if path.is_file(): yield path; return
    for subpath in path.iterdir():
        for subsubpath in _walk_path(subpath):
            yield subsubpath

def _copy_path(from_path: Path, to_path: Path):
    if from_path.is_file():
        to_path.parent.mkdir(parents = True, exist_ok = True)
        shutil.copyfile(
            from_path,
            to_path
        )
        return
    for from_file_path in _walk_path(from_path):
        to_file_path = to_path / from_file_path.relative_to(from_path)
        to_file_path.parent.mkdir(parents = True, exist_ok = True)
        shutil.copyfile(
            from_file_path,
            to_file_path
        )

def _write_key_value(path, data):
    with open(path, 'w') as file:
        for key, value in data.items():
            file.write(f'{key}={value}\n')

def _parse_submission(output):
    for line in output.split('\n'):
        if not line.startswith('JobID='): continue
        return line[6:].strip()
    raise ValueError('JobID not found')

def _parse_output(output):
    raw_jobs = list(filter(
        lambda part: len(part) != 0,
        map(
            lambda part: part.strip(),
            output.replace('\r\n', '\n').split('\n\n')
        )
    ))
    return list(map(
        lambda raw_job: dict([
            tuple(part.split('=', 1))
            for part in raw_job.split('\n')
        ]),
        raw_jobs
    ))

def _parse_groups(output):
    return list(filter(
        lambda item: len(item) != 0,
        output.replace('\r\n', '\n').split('\n')
    ))

def _parse_pools(output):
    return list(filter(
        lambda item: len(item) != 0,
        output.replace('\r\n', '\n').split('\n')
    ))

def _get_deadline_path():
    raw_path = app.call(['cmd.exe', '/c', 'echo', '%DEADLINE_PATH%']).splitlines()[-1]
    assert raw_path is not None, 'Deadline path not found'
    bin_path = fix_path(Path(raw_path.replace('\\', '/')))
    assert bin_path.exists(), f'Invalid Deadline installation path: "{bin_path}"'
    return bin_path / 'deadlinecommand.exe'

def _get_repository_path(deadline_path):
    raw_path = app.call([str(deadline_path), 'GetRepositoryPath']).strip()
    assert raw_path is not None, 'Repository path not found'
    return fix_path(Path(raw_path.replace('\\', '/')))

def _find_free_job_path(root_path):
    retries = 10
    for _ in range(retries):
        path = root_path / str(uuid4())
        if not path.exists(): return path
    assert False, f'Failed to find free job path in "{root_path}"'

class Batch:
    def __init__(self, name):
        self._name = name
        self._jobs = list()
        self._deps = dict()
        self._refs = dict()
    
    def get_name(self):
        return self._name
    
    def add_job(self, job):
        assert isinstance(job, Job), f'Invalid job type {type(job)}'
        index = len(self._jobs)
        self._jobs.append(job)
        self._deps[index] = set()
        self._refs[index] = set()
        return index

    def get_job(self, index):
        return self._jobs[index]

    def add_dep(self, first_job, second_job):
        self._deps[first_job].add(second_job)
        self._refs[second_job].add(first_job)
    
    def get_deps(self, index):
        return self._deps[index]
    
    def _roots(self):
        return {
            index
            for index in range(len(self._jobs))
            if len(self._deps[index]) == 0
        }
    
    def topological_order(self):
        order = list()
        visited = set()
        worklist = list(self._roots())
        while len(worklist) != 0:
            index = worklist.pop()
            if index in visited: continue
            remain_deps = self._deps[index] - visited
            if len(remain_deps) != 0:
                worklist += list(remain_deps)
                continue
            order.append(index)
            visited.add(index)
            worklist += list(self._refs[index] - visited)
        return order

class Job:
    def __init__(self,
        script_path,
        requirements_path,
        *args
        ):

        # Asserts
        assert script_path is not None, 'Script path not set'
        assert isinstance(script_path, Path), 'Invalid script path type'

        # Members
        self.name = None
        self.pool = None
        self.group = None
        self.comment = ''
        self.priority = 10
        self.frames = list()
        self.start_frame = 1
        self.end_frame = 1
        self.step_size = 1
        self.chunk_size = 1
        self.min_frame_time = None
        self.max_frame_time = None
        self.pre_job_script_path = None
        self.post_job_script_path = None
        self.pre_task_script_path = None
        self.post_task_script_path = None
        self.env = dict()
        self.paths = dict()
        self.output_paths = list()
        self._script_path = script_path
        self._requirements_path = requirements_path
        self._args = args
    
    def _frames(self):
        if len(self.frames) != 0: return ','.join(map(str, self.frames))
        return f'{self.start_frame}-{self.end_frame}x{self.step_size}'
    
    def job_info(self) -> dict:
        assert self.name is not None, 'Job name not set'
        assert self.pool is not None, 'Job pool not set'
        assert self.group is not None, 'Job group not set'
        assert 0 <= self.priority <= 100, f'Invalid priority value: {self.priority}'
        min_frame_time = (
            self.min_frame_time * 60
            if self.min_frame_time is not None else 0
        )
        max_frame_time = (
            self.max_frame_time * 60
            if self.max_frame_time is not None else 0
        )
        return {
            'Name': self.name,
            'Pool': self.pool,
            'Group': self.group,
            'Priority': str(self.priority),
            'Comment': self.comment,
            'UserName': getpass.getuser(),
            'MachineName': platform.node(),
            'InitialStatus': 'Active',
            'Plugin': 'UV',
            'Frames': self._frames(),
            'ChunkSize': str(self.chunk_size),
            'EnableFrameTimeouts': 'true',
            'MinRenderTimeSeconds': str(min_frame_time),
            'TaskTimeoutSeconds': str(max_frame_time),
            'PreJobScript': (
                '' if self.pre_job_script_path is None else
                path_str(self.pre_job_script_path
            )),
            'PostJobScript': (
                '' if self.post_job_script_path is None else
                path_str(self.post_job_script_path)
            ),
            'PreTaskScript': (
                '' if self.pre_task_script_path is None else
                path_str(self.pre_task_script_path)
            ),
            'PostTaskScript': (
                '' if self.post_task_script_path is None else
                path_str(self.post_task_script_path)
            )
        } | {
            f'OutputDirectory{index}': path_str(output_path.parent)
            for index, output_path in enumerate(self.output_paths)
        } | {
            f'OutputFilename{index}': output_path.name
            for index, output_path in enumerate(self.output_paths)
        }

    def plugin_info(self, job_path, env_file_path = None):
        result = {
            'ScriptFile': path_str(self._script_path),
            'Arguments': ' '.join(filter(
                lambda part: len(part) != 0,
                self._args
            )),
            'StartupDirectory': path_str(job_path / 'data')
        }
        if self._requirements_path is not None:
            result['RequirementsFile'] = path_str(self._requirements_path)
        if env_file_path is not None:
            result['EnvironmentFile'] = path_str(env_file_path)
        return result

DEADLINE_PATH = None
REPOSITORY_PATH = None
POOL_NAMES = None
GROUP_NAMES = None

class Deadline:
    def __init__(self):
        global DEADLINE_PATH, REPOSITORY_PATH
        DEADLINE_PATH = (
            _get_deadline_path()
            if DEADLINE_PATH is None else
            DEADLINE_PATH
        )
        REPOSITORY_PATH = (
            _get_repository_path(DEADLINE_PATH)
            if REPOSITORY_PATH is None else
            REPOSITORY_PATH
        )
    
    def list_pools(self, refresh = False):
        global POOL_NAMES
        if not (POOL_NAMES is None or refresh): return POOL_NAMES.copy()
        POOL_NAMES = _parse_pools(app.call([
            str(DEADLINE_PATH),
            'Pools'
        ]))
        return POOL_NAMES.copy()
    
    def list_groups(self, refresh = False):
        global GROUP_NAMES
        if not (GROUP_NAMES is None or refresh): return GROUP_NAMES.copy()
        GROUP_NAMES = _parse_groups(app.call([
            str(DEADLINE_PATH),
            'Groups'
        ]))
        return GROUP_NAMES.copy()
    
    def find_jobs(self, **filters):
        all_jobs = _parse_output(app.call([
            str(DEADLINE_PATH),
            'GetJobs'
        ]))
        if len(filters) == 0: return all_jobs
        return list(filter(
            lambda job: all(
                job[key] == value
                for key, value in filters.items()
            ),
            all_jobs
        ))

    def find_tasks(self, job_id, **filters):
        all_tasks = _parse_output(app.call([
            str(DEADLINE_PATH),
            'GetJobTasks',
            job_id
        ]))
        if len(filters) == 0: return all_tasks
        return list(filter(
            lambda task: all(
                task[key] == value
                for key, value in filters.items()
            ),
            all_tasks
        ))
    
    def suspend_jobs(self, *job_ids):
        if len(job_ids) == 0: return False
        return_code = app.run([
            str(DEADLINE_PATH),
            'SuspendJob',
            ','.join(job_ids)
        ])
        return return_code == 0

    def resume_jobs(self, *job_ids):
        if len(job_ids) == 0: return False
        return_code = app.run([
            str(DEADLINE_PATH),
            'ResumeJob',
            ','.join(job_ids)
        ])
        return return_code == 0
    
    def remove_job(self, job_id):
        app.run([
            str(DEADLINE_PATH),
            'DeleteJob',
            job_id
        ])
    
    def suspend_tasks(self, job_id, *task_ids):
        if len(task_ids) == 0: return False
        return_code = app.run([
            str(DEADLINE_PATH),
            'SuspendJobTasks',
            job_id,
            ','.join(task_ids)
        ])
        return return_code == 0
    
    def resume_tasks(self, job_id, *task_ids):
        if len(task_ids) == 0: return False
        return_code = app.run([
            str(DEADLINE_PATH),
            'ResumeJobTasks',
            job_id,
            ','.join(task_ids)
        ])
        return return_code == 0

    def submit(self,
        batch,
        jobs_path
        ):

        # Open work directory
        job_path = _find_free_job_path(jobs_path)
        assert not job_path.exists(), f'Job path already exists: {job_path}'
        job_path.mkdir(parents = True)
        job_data_path = job_path / 'data'
        logging.info(f'Creating job path: {job_path}')

        # Job creation order
        job_ids = dict()
        for job_index in batch.topological_order():
            job = batch.get_job(job_index)

            # Handle existing jobs
            if isinstance(job, str):
                job_ids[job_index] = job
                continue

            # Prepare job info
            job_info_path = (
                job_path /
                f'{str(job_index).zfill(2)}_job_info.job'
            )
            job_info = job.job_info() | {
                'BatchName': batch.get_name(),
                'NetworkRoot': path_str(to_windows_path(REPOSITORY_PATH))
            }
            job_deps = batch.get_deps(job_index)
            if len(job_deps) != 0:
                job_info['JobDependencies'] = ','.join([
                    job_ids[dep_index]
                    for dep_index in job_deps
                ])
            _write_key_value(job_info_path, job_info)
            
            # Prepare environment file
            env_file_path = job_path / f'{str(job_index).zfill(2)}.env'
            _write_key_value(env_file_path, job.env)

            # Prepare plugin info
            plugin_info_path = (
                job_path /
                f'{str(job_index).zfill(2)}_plugin_info.job'
            )
            _write_key_value(
                plugin_info_path,
                job.plugin_info(job_path, env_file_path)
            )

            # Copy files to workspace
            for from_path, rel_to_path in job.paths.items():
                to_path = job_data_path / rel_to_path
                if to_path.exists(): continue
                _copy_path(from_path, to_path)
            
            # Submit job
            job_ids[job_index] = _parse_submission(app.call([
                str(DEADLINE_PATH),
                str(to_windows_path(job_info_path)),
                str(to_windows_path(plugin_info_path))
            ]))
    
    def maintenance(self):
        root_path = Path(__file__).parent.parent
        task_path = (
            root_path /
            'farm' /
            'jobs' /
            'general' /
            'cleanup' /
            'task.py'
        )
        print(app.call([
            str(DEADLINE_PATH),
            '-SubmitCommandLineJob',
            '-executable', 'c:/Windows/System32/wsl.exe',
            '-arguments', f'--shell-type login python3 {task_path}',
            '-priority', '100',
            '-frames', '1',
            '-name', 'Worker Maintenance',
            '-prop', 'MaintenanceJob=True'
        ]))