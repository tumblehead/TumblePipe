"""Running one local task the way the Deadline HPM plugin runs it on a render node.

The plugin (``deadline-hpm-plugin/HPM.py``), per task:

1. exports ``TH_FARM_DATA`` (the job's shared data dir) and every
   ``KEY=VALUE`` of the job's env file;
2. copies the job's ``hpm.toml`` into a fresh scratch dir and runs
   ``hpm -C <dir> install`` there;
3. runs ``hpm -C <dir> run task -- <context> <first> <last>``, the context
   path made absolute against the data dir, and reads ``Progress: N`` lines;
4. fails the task on a non-zero exit, and removes the scratch dir.

This module does the same. What a render node has and a workstation does
not is the clean environment: the runner inherits whatever session started
it (a Houdini, with its packages, ``PYTHONPATH`` and ``$HFS`` on ``PATH``),
so :func:`clean_env` takes those out first. A task then sees only the
machine's own environment and the job's, as on the farm.
"""

from __future__ import annotations

import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# What a Houdini session, a package env or the pipeline put into the
# environment, which a render node's worker service does not have.
_SESSION_NAMES = {
    'HFS', 'HH', 'HHC', 'HHP', 'HB', 'HD', 'HT', 'HSB', 'HIH', 'HIP',
    'HIPFILE', 'HIPNAME', 'HSITE', 'JOB', 'OCIO', 'VIRTUAL_ENV', 'CONDA_PREFIX',
}
_SESSION_PREFIXES = ('HOUDINI_', 'PYTHON', 'PXR_', 'TH_', 'TT_', 'HPM_', 'USD_')
# Kept although they match a prefix: where the queue is, so a task's own
# submissions (collapse -> playblast) land in this machine's queue; and the
# hpm the runner was told to use.
_KEEP = {'TH_LOCAL_FARM_DIR', 'TH_LOCAL_FARM_HPM', 'HPM_VERSION'}

PROGRESS_RE = re.compile(r'.*Progress: (\d+)%?.*')

if sys.platform == 'win32':
    _BELOW_NORMAL = 0x00004000
    _CREATION_FLAGS = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | _BELOW_NORMAL
    )
else:
    _CREATION_FLAGS = 0


def _is_session_name(name: str) -> bool:
    upper = name.upper()
    if upper in _KEEP:
        return False
    return upper in _SESSION_NAMES or upper.startswith(_SESSION_PREFIXES)


def clean_env(environ: dict) -> dict:
    """``environ`` without the session's Houdini, Python and pipeline variables.

    ``PATH`` keeps its order but loses the Houdini install's directories
    (``$HFS``) and any hpm package env's.
    """
    hfs = (environ.get('HFS') or '').replace('\\', '/').rstrip('/').lower()
    result = {k: v for k, v in environ.items() if not _is_session_name(k)}
    for key in [k for k in result if k.upper() == 'PATH']:
        kept = []
        for entry in result[key].split(os.pathsep):
            norm = entry.replace('\\', '/').rstrip('/').lower()
            if not norm:
                continue
            if hfs and (norm == hfs or norm.startswith(hfs + '/')):
                continue
            if '/.hpm/' in norm + '/':
                continue
            kept.append(entry)
        result[key] = os.pathsep.join(kept)
    return result


def read_env_file(path: Path) -> dict:
    """The job's ``KEY=VALUE`` env file, parsed as the plugin parses it.

    ``apps.deadline.write_job_files`` writes it UTF-8. Jobs written before
    that used the submitting process's default encoding: UTF-8 from the Farm
    Submit process (``PYTHONUTF8=1``), the locale's (cp1252) from inside
    Houdini. So read UTF-8, else the locale's.
    """
    data = Path(path).read_bytes()
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        # The locale's own encoding, not Python's preferred one: under
        # PYTHONUTF8=1 (the runner's) that would be UTF-8 again.
        encoding = getattr(locale, 'getencoding', None)
        text = data.decode(encoding() if encoding else locale.getpreferredencoding(False), errors='replace')
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if key:
            env[key] = value.strip()
    return env


def task_env(base: dict, env_file: str | None, data_dir: str) -> dict:
    """The environment a task runs in: the clean base, ``TH_FARM_DATA``, the job's env file."""
    env = clean_env(base)
    if data_dir:
        env['TH_FARM_DATA'] = data_dir
    if env_file and Path(env_file).is_file():
        env.update(read_env_file(Path(env_file)))
    return env


def context_arguments(arguments: str, data_dir: str) -> list[str]:
    """The task's arguments, the first (the context path) made absolute against the data dir."""
    data_dir = data_dir.rstrip('/\\')
    tokens = arguments.split()
    if tokens and data_dir:
        first = tokens[0]
        is_absolute = bool(re.match(r'^[A-Za-z]:[\\/]', first)) or first.startswith(('/', '\\'))
        if not is_absolute:
            tokens[0] = f'{data_dir}/{first}'
    return tokens


def install_command(hpm: list[str], job_dir: Path) -> list[str]:
    return [*hpm, '-C', str(job_dir), 'install']


def run_command(hpm: list[str], job_dir: Path, arguments: str, data_dir: str, first: int, last: int) -> list[str]:
    return [
        *hpm, '-C', str(job_dir), 'run', 'task', '--',
        *context_arguments(arguments, data_dir), str(first), str(last),
    ]


def prepare_job_dir(manifest: str) -> Path:
    """A scratch dir holding the job's manifest as ``hpm.toml``.

    Per task, as on a render node: ``hpm install`` writes its lock and
    package env next to the manifest, and the shared job dir must stay
    untouched.
    """
    if not manifest or not Path(manifest).is_file():
        raise FileNotFoundError(
            f'The job has no hpm.toml manifest ({manifest!r}); a farm job '
            'always carries one.'
        )
    job_dir = Path(tempfile.mkdtemp(prefix='hpm-job-'))
    shutil.copyfile(manifest, job_dir / 'hpm.toml')
    return job_dir


class Process:
    """A task's child process: output pumped into the task log, progress parsed.

    Runs below normal priority, so the artist's Houdini keeps the machine
    responsive, in its own process group, so :meth:`kill` takes its whole
    tree (hpm, python, hython, husk) with it.
    """

    def __init__(self, command: list[str], env: dict, cwd: Path, log) -> None:
        self.progress: int | None = None
        self._log = log
        kwargs = {}
        if sys.platform != 'win32':
            kwargs['start_new_session'] = True
            kwargs['preexec_fn'] = lambda: os.nice(10)
        self._process = subprocess.Popen(
            command, env=env, cwd=str(cwd),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=_CREATION_FLAGS, **kwargs,
        )
        self._pump = threading.Thread(target=self._read, daemon=True)
        self._pump.start()

    @property
    def pid(self) -> int:
        return self._process.pid

    def _read(self) -> None:
        for raw in self._process.stdout:
            line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
            self._log(line)
            match = PROGRESS_RE.match(line)
            if match:
                self.progress = min(100, int(match.group(1)))

    def poll(self) -> int | None:
        code = self._process.poll()
        if code is not None:
            # Every line is in the log before the exit is reported.
            self._pump.join(timeout=5)
        return code

    def wait(self) -> int:
        code = self._process.wait()
        self._pump.join(timeout=5)
        return code

    def kill(self) -> None:
        if self._process.poll() is not None:
            return
        if sys.platform == 'win32':
            subprocess.run(
                ['taskkill', '/PID', str(self._process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            import signal
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self._process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._pump.join(timeout=5)
