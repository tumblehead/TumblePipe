"""The machine's local farm runner: works the queue one task at a time.

    python -m tumblepipe.farm.local.runner

One per machine, held by ``runner.lock`` (an OS file lock, so a runner that
crashed never leaves a stale one). :func:`ensure_running` starts it, detached,
when a batch is queued or a control request made; it stops by itself once the
queue has nothing left to do. Before stopping it lets go of the lock and looks
once more, so a batch queued in that moment is never stranded: either this
runner sees it, or the submitter saw the lock free and started a new one.

One task at a time, below normal priority: a workstation's GPU and licences
are the artist's first.
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from tumblepipe.farm.local import hpm as hpm_tool
from tumblepipe.farm.local import queue
from tumblepipe.farm.local import task as local_task

LOCK_NAME = 'runner.lock'
LOG_NAME = 'runner.log'
IDLE_SECONDS = 20.0
POLL_SECONDS = 0.5


# ── the lock ──────────────────────────────────────────────

class Lock:
    """A non-blocking exclusive lock on a file, released when its process ends."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._handle = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, 'a+b')
        try:
            if sys.platform == 'win32':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == 'win32':
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def is_running(root_path: Path | None = None) -> bool:
    """Whether a runner holds this machine's queue right now."""
    lock = Lock(Path(root_path or queue.root()) / LOCK_NAME)
    if lock.acquire():
        lock.release()
        return False
    return True


# ── the runner ────────────────────────────────────────────

class Runner:
    """Works the queue under ``root_path``.

    Args:
        hpm_command: The hpm to run, as argv; the managed one by default.
        environ: The environment tasks are built from (cleaned per task).
        idle_seconds: How long to wait with nothing to do before stopping.
    """

    def __init__(
        self,
        root_path: Path,
        hpm_command: list[str] | None = None,
        environ: dict | None = None,
        idle_seconds: float = IDLE_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        log: Callable[[str], None] = print,
    ) -> None:
        self._root = Path(root_path)
        self._hpm_command = hpm_command
        self._environ = dict(os.environ if environ is None else environ)
        self._idle_seconds = idle_seconds
        self._poll_seconds = poll_seconds
        self._log = lambda msg: log(f'[{dt.datetime.now():%H:%M:%S}] {msg}')
        self._remove_after: set[Path] = set()

    # ── entry points ──

    def run(self) -> int:
        """Hold the lock and work until idle; 0 when another runner has it."""
        lock = Lock(self._root / LOCK_NAME)
        while True:
            if not lock.acquire():
                return 0
            try:
                self._log(f'Runner {os.getpid()} working {self._root}')
                self.recover()
                self.drain(wait=True)
            finally:
                lock.release()
            # Queued while we were letting go? Then pick it up (or a runner
            # its submitter started already has the lock, and we stop).
            if not self.has_work():
                self._log('Queue empty; stopping')
                return 0

    def drain(self, wait: bool = False) -> None:
        """Run tasks until none is runnable (and ``idle_seconds`` passed, with ``wait``)."""
        idle_since = None
        while True:
            if self.step():
                idle_since = None
                continue
            if not wait:
                return
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since >= self._idle_seconds:
                return
            time.sleep(self._poll_seconds)

    def step(self) -> bool:
        """Apply control requests, then run one task; False when there was none."""
        self.apply_all_controls()
        for folder in queue.list_batch_dirs(self._root):
            try:
                batch = queue.read_batch(folder)
                state = queue.read_state(folder, batch)
                picked = queue.next_task(batch, state)
            except Exception as error:
                self._log(f'Skipping unreadable batch {folder.name}: {error}')
                continue
            if picked is not None:
                self.run_task(folder, batch, state, *picked)
                self._finish_removals()
                return True
        return False

    def has_work(self) -> bool:
        for folder in queue.list_batch_dirs(self._root):
            try:
                if queue.pending_requests(folder):
                    return True
                batch = queue.read_batch(folder)
                if queue.next_task(batch, queue.read_state(folder, batch)) is not None:
                    return True
            except Exception:
                continue
        return False

    def recover(self) -> None:
        """Requeue tasks a crashed runner left rendering, as Deadline requeues orphaned tasks."""
        for folder in queue.list_batch_dirs(self._root):
            try:
                batch = queue.read_batch(folder)
                state = queue.read_state(folder, batch)
            except Exception:
                continue
            changed = False
            for job in state['jobs'].values():
                for task in job['tasks']:
                    if task['status'] == queue.RENDERING:
                        task.update(status=queue.QUEUED, progress=0, error='requeued: the runner stopped while it rendered')
                        changed = True
            if changed:
                queue.write_state(folder, state)

    # ── control requests ──

    def apply_all_controls(self, current=None) -> bool:
        """Apply every batch's requests; True when ``current`` (folder, batch, state, job, task) was stopped."""
        stopped_current = False
        for folder in queue.list_batch_dirs(self._root):
            requests = queue.pending_requests(folder)
            if not requests:
                continue
            is_current = current is not None and folder == current[0]
            try:
                batch = current[1] if is_current else queue.read_batch(folder)
                state = current[2] if is_current else queue.read_state(folder, batch)
            except Exception as error:
                self._log(f'Cannot apply requests to {folder.name}: {error}')
                continue
            for path, data in requests:
                command, job = data.get('command'), data.get('job')
                try:
                    stopped = queue.apply_command(batch, state, command, job)
                except ValueError as error:
                    self._log(f'{folder.name}: ignoring request {path.name}: {error}')
                else:
                    self._log(f'{folder.name}: {command} job {job if job is not None else "(all)"}')
                    if command == queue.REMOVE:
                        self._remove_after.add(folder)
                    if is_current and (current[3], current[4]) in stopped:
                        stopped_current = True
                path.unlink(missing_ok=True)
            queue.write_state(folder, state)
        if current is None:
            self._finish_removals()
        return stopped_current

    def _finish_removals(self) -> None:
        for folder in list(self._remove_after):
            self._remove_after.discard(folder)
            shutil.rmtree(folder, ignore_errors=True)
            self._log(f'Removed batch {folder.name}')

    # ── one task ──

    def _hpm(self) -> list[str]:
        if self._hpm_command is not None:
            return list(self._hpm_command)
        return [str(hpm_tool.executable(self._root, self._environ, log=self._log))]

    def run_task(self, folder: Path, batch: dict, state: dict, job_index: int, task_index: int) -> None:
        job = next(j for j in batch['jobs'] if j['index'] == job_index)
        task = state['jobs'][str(job_index)]['tasks'][task_index]
        first, last = job['tasks'][task_index]
        task.update(
            status=queue.RENDERING, progress=0, started=queue.now(), finished=None,
            exit_code=None, error=None, attempts=task.get('attempts', 0) + 1,
        )
        queue.write_state(folder, state)
        self._log(f"{folder.name}: {job['name']} task {task_index} ({first}-{last})")

        log_path = queue.log_path(folder, job_index, task_index)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        current = (folder, batch, state, job_index, task_index)
        code, error = None, None
        job_dir = None
        with open(log_path, 'a', encoding='utf-8') as log_file:
            def write(line: str) -> None:
                log_file.write(line + '\n')
                log_file.flush()

            write(f" Attempt {task['attempts']} on {platform.node()} at {task['started']} ".center(100, '='))
            try:
                write(' Preparing HPM task '.center(100, '='))
                command_hpm = self._hpm()
                write(f'hpm executable: {" ".join(command_hpm)}')
                env = local_task.task_env(self._environ, job.get('env_file'), job['data_dir'])
                if job.get('env_file'):
                    write(f"Loading job environment from: {job['env_file']}")
                job_dir = local_task.prepare_job_dir(job['manifest'])
                write(f'Prepared job dir: {job_dir}')

                write(' Installing HPM job manifest '.center(100, '='))
                code, stopped = self._watch(
                    local_task.install_command(command_hpm, job_dir), env, job_dir, write, current, None,
                )
                if stopped is None and code != 0:
                    error = f'hpm install failed for the job manifest (exit code {code})'
                elif stopped is None:
                    write(' Running task '.center(100, '='))
                    write(f"Context args: {' '.join(local_task.context_arguments(job['arguments'], job['data_dir']))}")
                    write(f'Frame range: {first}-{last}')
                    command = local_task.run_command(
                        command_hpm, job_dir, job['arguments'], job['data_dir'], first, last,
                    )
                    code, stopped = self._watch(command, env, job_dir, write, current, job.get('timeout') or 0)
                    if stopped == 'timeout':
                        error = f"timed out after {job['timeout'] // 60} minutes"
                    elif stopped is None and code != 0:
                        error = f'Task failed with return code: {code}'
            except Exception as exception:
                error = f'{type(exception).__name__}: {exception}'
                write(error)
            finally:
                if job_dir is not None:
                    shutil.rmtree(job_dir, ignore_errors=True)
            if task['status'] == queue.RENDERING:
                if error is None:
                    task.update(status=queue.COMPLETED, progress=100)
                else:
                    task.update(status=queue.FAILED, error=error)
            task.update(finished=queue.now(), exit_code=code)
            write(f" {task['status']} at {task['finished']} ".center(100, '='))
        queue.write_state(folder, state)
        self._log(f"{folder.name}: {job['name']} task {task_index} {task['status']}" + (f': {error}' if error else ''))

    def _watch(self, command, env, cwd, write, current, timeout):
        """Run ``command`` to its end; ``(exit code, why it was stopped or None)``."""
        folder, _batch, state, job_index, task_index = current
        task = state['jobs'][str(job_index)]['tasks'][task_index]
        process = local_task.Process(command, env, cwd, write)
        started = time.monotonic()
        reported = None
        while True:
            code = process.poll()
            if code is not None:
                return code, None
            time.sleep(self._poll_seconds)
            if self.apply_all_controls(current):
                write(f"Stopped: the task was {task['status']} from the Farm Manager")
                process.kill()
                return process.poll(), 'request'
            if timeout and time.monotonic() - started > timeout:
                write(f'Task timed out after {timeout} s')
                process.kill()
                return process.poll(), 'timeout'
            if process.progress is not None and process.progress != reported:
                reported = process.progress
                task['progress'] = reported
                queue.write_state(folder, state)


# ── starting it ───────────────────────────────────────────

def _package_python_dir() -> Path:
    return Path(__file__).resolve().parents[3]


def runner_env(environ: dict | None = None) -> dict:
    env = dict(os.environ if environ is None else environ)
    package = str(_package_python_dir())
    parts = [package] + [p for p in env.get('PYTHONPATH', '').split(os.pathsep) if p and p != package]
    env['PYTHONPATH'] = os.pathsep.join(parts)
    env['PYTHONUTF8'] = '1'
    return env


def ensure_running(root_path: Path | None = None) -> bool:
    """Start this machine's runner unless one runs; True when it started one."""
    root_path = Path(root_path or queue.root())
    if is_running(root_path):
        return False
    from tumblepipe.farm.submit.plan import interpreter

    root_path.mkdir(parents=True, exist_ok=True)
    log = open(root_path / LOG_NAME, 'ab')
    flags = 0
    if sys.platform == 'win32':
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    env = runner_env()
    env[queue.ROOT_ENV] = str(root_path)
    try:
        subprocess.Popen(
            [str(interpreter()), '-m', 'tumblepipe.farm.local.runner'],
            # Not the caller's directory: `-m` puts it first on sys.path.
            cwd=str(root_path),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=sys.platform != 'win32',
        )
    finally:
        log.close()
    return True


def main(argv: list[str]) -> int:
    if argv:
        print('usage: python -m tumblepipe.farm.local.runner', file=sys.stderr)
        return 2
    return Runner(queue.root(), log=lambda msg: print(msg, flush=True)).run()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
