"""A farm job and its batch, and the files a job runs from: the job model
both backends share (``apps.deadline.Deadline`` submits a batch to Deadline,
``farm.local.backend.LocalFarm`` queues it on this machine).

Import-light: workers import it, so it needs nothing beyond the standard
library and ``tumblepipe.api``.
"""

from pathlib import Path
from uuid import uuid4
import logging
import platform
import re
import shutil

from tumblepipe.api import get_user_name, path_str


# Farm plugin. 'HPM' runs the task from a package resolved on the worker
# (see deadline-hpm-plugin).
PLUGIN = 'HPM'

# The hpm a job runs under, sent as the plugin's HpmVersion (which wins over
# a render node's $HPM_VERSION and the plugin's default): hpm.toml declares
# script `app`/`icon` fields, and an hpm before 0.44.0 refuses the manifest.
# The local runner pins the same (tumblepipe.farm.local.hpm.DEFAULT_VERSION).
HPM_VERSION = 'v0.44.0'

# Matches the .hpm/packages/<name>@<version>/<relative...> segment of a script
# path.
_HPM_PACKAGE_RE = re.compile(r'(?:^|/)\.hpm/packages/([^/]+@[^/]+)/(.+)$')

def hpm_package_spec(script_path):
    """Split an in-package script path into (package_spec, package_relative_path).

    A farm job's script is `farm/workers/<name>.py` of the running package, so
    under HPM the path runs through the submitter's own
    ~/.hpm/packages/<name>@<version>/ store. The HPM plugin needs the package
    identity plus a package-relative ScriptFile so it can re-resolve the same
    version against the *worker's* store instead of trusting the absolute path.

    Raises if the script isn't under an HPM package store — i.e. a dev/editable
    checkout — because that has no reproducible identity to ship to the farm.
    """
    norm = str(script_path).replace('\\', '/')
    match = _HPM_PACKAGE_RE.search(norm)
    if match is None:
        raise RuntimeError(
            'Cannot submit to the HPM farm plugin from a non-HPM install: '
            f'{script_path}. The task script is not under '
            '~/.hpm/packages/<name>@<version>/ — submit from an installed '
            'package version, not a dev/editable checkout.'
        )
    return match.group(1), match.group(2)

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

def write_key_value(path, data, encoding=None):
    """``KEY=VALUE`` lines; ``encoding`` None is the process's default.

    The job's env file is written UTF-8 (the HPM plugin and the local runner
    read UTF-8 first): in the default encoding a non-ASCII value (a user, a
    path) came out UTF-8 from the Farm Submit process and cp1252 from inside
    Houdini, and the reader garbled whichever it did not expect. The job and
    plugin info files ``deadlinecommand`` reads keep the default.
    """
    with open(path, 'w', encoding=encoding) as file:
        for key, value in data.items():
            file.write(f'{key}={value}\n')

def _find_free_job_path(root_path):
    retries = 10
    for _ in range(retries):
        path = root_path / str(uuid4())
        if not path.exists(): return path
    raise RuntimeError(f'Failed to find a free job path in "{root_path}"')

def extra_info_entries(extra_info: dict) -> dict:
    """``extra_info`` as Deadline job-info entries: ``ExtraInfoKeyValue<n>=<key>=<value>``.

    Deadline reads the key up to the first ``=``, and a job-info file is
    one entry per line, so a key with ``=`` or a line break, or a value
    with a line break, is refused rather than written garbled.
    """
    entries = {}
    for index, (key, value) in enumerate(extra_info.items()):
        key, value = str(key), str(value)
        if not key or any(c in key for c in '=\r\n') or any(c in value for c in '\r\n'):
            raise ValueError(f'Cannot write extra info {key!r}={value!r} into a Deadline job')
        entries[f'ExtraInfoKeyValue{index}'] = f'{key}={value}'
    return entries

def job_extra_info(batch, job) -> dict:
    """What ``job`` carries as extra info: its batch's, then its own on top."""
    return {**batch.extra_info, **job.extra_info}

class Batch:
    def __init__(self, name, extra_info = None):
        self._name = name
        self._jobs = list()
        self._deps = dict()
        self._refs = dict()
        # Key/value pairs every job of the batch carries (Deadline's
        # ExtraInfoKeyValue; a local job's extra_info): TumblePipe stamps
        # the project and entity here (farm.stamp).
        self.extra_info = dict(extra_info or {})

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

    def add_jobs_with_deps(
        self,
        jobs: dict[str, 'Job'],
        deps: dict[str, list[str]]
        ):
        """Add multiple jobs with their dependencies.

        Args:
            jobs: Dict mapping job names to Job objects
            deps: Dict mapping job names to lists of dependency job names
        """
        indicies = {
            job_name: self.add_job(job)
            for job_name, job in jobs.items()
        }
        for job_name, job_deps in deps.items():
            for dep_name in job_deps:
                if dep_name not in indicies:
                    raise ValueError(
                        f'Job "{job_name}" depends on non-existing job '
                        f'"{dep_name}"'
                    )
                self.add_dep(indicies[job_name], indicies[dep_name])

class Job:
    def __init__(self,
        script_path,
        *args
        ):

        # Asserts
        assert script_path is not None, 'Script path not set'
        assert isinstance(script_path, Path), 'Invalid script path type'

        # Members. The scheduling ones start unset: every task builder sets
        # them, and job_info() refuses a job that forgot one rather than
        # rendering frame 1 at priority 10.
        self.name = None
        self.pool = None
        self.group = None
        self.priority = None
        self.frames = list()
        self.start_frame = None
        self.end_frame = None
        self.step_size = None
        self.chunk_size = None
        self.max_frame_time = None
        # Deadline's EnableFrameTimeouts: the task limit (max_frame_time) is
        # multiplied by the frames in each task. A job that runs its whole
        # range as one task (farm.deadline.run_once) turns it off, so its
        # limit caps the task instead of growing with the shot's length.
        self.frame_timeouts = True
        self.env = dict()
        self.paths = dict()
        self.output_paths = list()
        # Optional hpm.toml content the caller provides for the HPM plugin;
        # submit() writes it into the job dir and hands the plugin its path.
        self.manifest = None
        # The job's own extra info, on top of its batch's (job_extra_info).
        self.extra_info = dict()
        self._script_path = script_path
        self._args = args
    
    def _frames(self):
        if len(self.frames) != 0: return ','.join(map(str, self.frames))
        return f'{self.start_frame}-{self.end_frame}x{self.step_size}'

    def job_info(self) -> dict:
        required = ['name', 'pool', 'group', 'priority', 'chunk_size']
        if len(self.frames) == 0:
            required += ['start_frame', 'end_frame', 'step_size']
        unset = [key for key in required if getattr(self, key) is None]
        if unset:
            raise ValueError(f'Job {self.name!r} has no {", ".join(unset)} set')
        if not 0 <= self.priority <= 100:
            raise ValueError(f'Invalid priority value: {self.priority}')
        max_frame_time = (
            self.max_frame_time * 60
            if self.max_frame_time is not None else 0
        )
        return {
            'Name': self.name,
            'Pool': self.pool,
            'Group': self.group,
            'Priority': str(self.priority),
            'UserName': get_user_name(),
            'MachineName': platform.node(),
            'InitialStatus': 'Active',
            'Plugin': PLUGIN,
            'Frames': self._frames(),
            'ChunkSize': str(self.chunk_size),
            'EnableFrameTimeouts': 'true' if self.frame_timeouts else 'false',
            'TaskTimeoutSeconds': str(max_frame_time),
        } | {
            f'OutputDirectory{index}': path_str(output_path.parent)
            for index, output_path in enumerate(self.output_paths)
        } | {
            f'OutputFilename{index}': output_path.name
            for index, output_path in enumerate(self.output_paths)
        }

    def plugin_info(self, job_path, env_file_path = None):
        arguments = ' '.join(filter(
            lambda part: len(part) != 0,
            self._args
        ))
        # Ship a package identity + package-relative script so the worker
        # re-resolves the same version against its own HPM store.
        package_spec, relative_script = hpm_package_spec(self._script_path)
        result = {
            'Package': package_spec,
            'ScriptFile': relative_script,
            'Arguments': arguments,
            'StartupDirectory': path_str(job_path / 'data'),
            'HpmVersion': HPM_VERSION,
        }
        if env_file_path is not None:
            result['EnvironmentFile'] = path_str(env_file_path)
        return result

def open_job_folder(jobs_path):
    """A new, empty job folder (with its ``data`` dir) under ``jobs_path``."""
    job_path = _find_free_job_path(jobs_path)
    assert not job_path.exists(), f'Job path already exists: {job_path}'
    job_path.mkdir(parents = True)
    (job_path / 'data').mkdir(parents=True, exist_ok=True)
    logging.debug(f'Creating job path: {job_path}')
    return job_path

def write_job_files(job_path, job_index, job):
    """Write ``job``'s env file and manifest, bundle its data; return its plugin info.

    What the HPM plugin reads on the worker, the same for a Deadline
    submission and a local one (``farm.local.backend``).
    """
    # Prepare environment file
    env_file_path = job_path / f'{str(job_index).zfill(2)}.env'
    write_key_value(env_file_path, job.env, encoding='utf-8')

    # Prepare plugin info
    plugin_info = job.plugin_info(job_path, env_file_path)

    # Write the caller-provided manifest into the shared job dir (a
    # reachable job artifact) and point the plugin at it via Manifest.
    if job.manifest is not None:
        manifest_path = (
            job_path /
            f'{str(job_index).zfill(2)}_hpm.toml'
        )
        manifest_path.write_text(job.manifest)
        plugin_info['Manifest'] = path_str(manifest_path)

    # Copy files to workspace
    job_data_path = job_path / 'data'
    for from_path, rel_to_path in job.paths.items():
        to_path = job_data_path / rel_to_path
        if to_path.exists(): continue
        _copy_path(from_path, to_path)
    return plugin_info
