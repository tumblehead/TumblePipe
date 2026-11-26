from dataclasses import dataclass
from pathlib import Path
import platform
import os
import subprocess
import sys

from tumblepipe.api import path_str
from tumblepipe.apps import app
from tumblepipe.farm.job import (
    write_key_value,
    extra_info_entries,
    job_extra_info,
    open_job_folder,
    write_job_files,
)

def _parse_submission(output):
    for line in output.split('\n'):
        if not line.startswith('JobID='): continue
        return line[6:].strip()
    raise ValueError('JobID not found')

class DeadlineNotInstalled(RuntimeError):
    """This machine has no Deadline Client to submit through."""

_NOT_INSTALLED_HINT = (
    'Rendering, playblasts, compositing and farm publishes are submitted '
    'to a Thinkbox Deadline render farm. Install the Deadline Client on '
    'this machine (its installer sets DEADLINE_PATH) and restart Houdini. '
    'Without a farm, see "Rendering without a farm" in the compositing docs.'
)

def deadline_setup_problem(environ = None):
    """Why this machine cannot submit to Deadline, or None when it can.

    Reads ``DEADLINE_PATH`` (set by the Deadline Client installer) instead of
    asking ``cmd.exe`` to expand it: an unset variable echoes back as the
    literal text ``%DEADLINE_PATH%``, which used to surface to artists as
    ``Invalid Deadline installation path: "%DEADLINE_PATH%"``.
    """
    if environ is None:
        environ = os.environ
    raw_path = environ.get('DEADLINE_PATH', '').strip().strip('"')
    if not raw_path:
        return f'Deadline is not set up on this machine. {_NOT_INSTALLED_HINT}'
    bin_path = Path(raw_path.replace('\\', '/'))
    if not bin_path.exists():
        return (
            f'DEADLINE_PATH points at "{raw_path}", which does not exist. '
            f'{_NOT_INSTALLED_HINT}'
        )
    return None

def deadline_command_name(system = None):
    """The Deadline Client's command-line tool on ``system`` (default: this one).

    ``deadlinecommand.exe`` on Windows, plain ``deadlinecommand`` on macOS
    and Linux, where the hard-coded ``.exe`` named a file that does not
    exist, so submitting (and pruning) found no Deadline there.
    """
    system = (system or platform.system()).lower()
    return 'deadlinecommand.exe' if system == 'windows' else 'deadlinecommand'

def _get_deadline_path():
    problem = deadline_setup_problem()
    if problem is not None:
        raise DeadlineNotInstalled(problem)
    raw_path = os.environ['DEADLINE_PATH'].strip().strip('"')
    bin_path = Path(raw_path.replace('\\', '/'))
    return bin_path / deadline_command_name()

def deadline_command():
    """This machine's ``deadlinecommand``; raises :class:`DeadlineNotInstalled`."""
    return _get_deadline_path()


@dataclass(frozen=True)
class Answer:
    """What one ``deadlinecommand`` run printed, stdout and stderr apart."""
    code: int
    stdout: str
    stderr: str


def query(command, *args: str, timeout: float) -> Answer:
    """Run ``command`` (a ``deadlinecommand``) with ``args`` and read its answer.

    stdout and stderr stay apart: a warning Deadline prints on stderr must
    not reach what the caller parses (``app.run`` merges them). No console
    window opens on Windows, and it reads no stdin. UTF-8, undecodable
    bytes replaced. Whether a non-zero exit is a refusal is the caller's to
    say: a refused ``-Json`` read still prints the envelope that says why.
    """
    result = subprocess.run(
        [str(command), *args],
        stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding='utf-8', errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        timeout=timeout,
    )
    return Answer(result.returncode, result.stdout, result.stderr)

def _get_repository_path(deadline_path):
    raw_path = app.run(
        [str(deadline_path), 'GetRepositoryPath'], echo=False
    ).strip()
    if not raw_path:
        raise RuntimeError('deadlinecommand GetRepositoryPath printed no path')
    return Path(raw_path.replace('\\', '/'))

DEADLINE_PATH = None
REPOSITORY_PATH = None

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
    
    def submit(self,
        batch,
        jobs_path
        ):

        job_path = open_job_folder(jobs_path)

        # Job creation order
        job_ids = dict()
        for job_index in batch.topological_order():
            job = batch.get_job(job_index)

            # Prepare job info
            job_info_path = (
                job_path /
                f'{str(job_index).zfill(2)}_job_info.job'
            )
            job_info = job.job_info() | {
                'BatchName': batch.get_name(),
                'NetworkRoot': path_str(REPOSITORY_PATH)
            } | extra_info_entries(job_extra_info(batch, job))
            job_deps = batch.get_deps(job_index)
            if len(job_deps) != 0:
                job_info['JobDependencies'] = ','.join([
                    job_ids[dep_index]
                    for dep_index in job_deps
                ])
            write_key_value(job_info_path, job_info)

            plugin_info = write_job_files(job_path, job_index, job)
            plugin_info_path = (
                job_path /
                f'{str(job_index).zfill(2)}_plugin_info.job'
            )
            write_key_value(plugin_info_path, plugin_info)

            # Submit job
            job_ids[job_index] = _parse_submission(app.run([
                str(DEADLINE_PATH),
                str(job_info_path),
                str(plugin_info_path)
            ], echo=False))

        return list(job_ids.values())