"""Live playblasts: the live folder, the frame order, live.json and the
background render, without Houdini.

A live playblast renders the animator's shot while they keep working, so the
player can show it beside the shots around it (designs/shot-dashboard-live-
playblast.md §5). Houdini writes a cache of the shot and starts husk on it
(``pipe.houdini.live_playblast``); this module owns everything after that:

- where the frames go: one folder per project, shot and department on local
  disk, never the network (``live_folder``);
- in which order: the current frame to the end, then the start, so the
  frames being looked at land first (``frame_order``);
- ``live.json``, which the player reads: what is being rendered, the run it
  belongs to, the frames this run has finished, and its state. Written
  atomically, so a reader never sees half a file;
- the husk process itself (``start``): one per folder, a new run cancels the
  one before it, and a thread follows its output to keep live.json current.

Frames of an older run are replaced in place; ``run`` tells the player which
are current.

A husk outlives a Houdini that is killed (or crashes): it renders on, and
nothing is left to finish its live.json, which would say "rendering" for
ever. So live.json names the husk (``pid``, ``program``): a new run stops a
husk an earlier Houdini left going before it starts, and ``settle`` turns a
"rendering" whose husk is gone into what its frames on disk show.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

FRAME_PATTERN = 'frame.$F4.jpg'
STATE_FILE = 'live.json'
LOG_FILE = 'husk.log'

RENDERING = 'rendering'
DONE = 'done'
FAILED = 'failed'
CANCELLED = 'cancelled'
STATES = (RENDERING, DONE, FAILED, CANCELLED)


# ── where ───────────────────────────────────────────────────


def live_root(environ=None, platform: str | None = None) -> Path:
    """The folder every live playblast of this user goes under:
    ``%LOCALAPPDATA%/tumblepipe/live`` on Windows, ``$XDG_STATE_HOME`` (or
    ``~/.local/state``) on Linux, ``~/Library/Application Support`` on macOS."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    home = Path(environ.get('HOME') or environ.get('USERPROFILE') or Path.home())
    if platform.startswith('win'):
        base = Path(environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
    elif platform == 'darwin':
        base = home / 'Library' / 'Application Support'
    else:
        base = Path(environ.get('XDG_STATE_HOME') or home / '.local' / 'state')
    return base / 'tumblepipe' / 'live'


def project_key(project_path: str) -> str:
    """A short name for a project's folder under the live root, the same
    whichever way its path is spelled (slashes, a trailing one, case)."""
    spelled = str(project_path).replace('\\', '/').rstrip('/').lower()
    return hashlib.sha1(spelled.encode('utf-8')).hexdigest()[:12]


def folder_name(uri_segments, department: str) -> str:
    """``SQ010_SH020_animation`` for entity:/shots/SQ010/SH020 in animation:
    the shot's segments after its context, then the department."""
    segments = list(uri_segments)[1:]
    if not segments or not department:
        raise ValueError('a live playblast needs a shot and a department')
    return '_'.join([*segments, department])


def live_folder(root: Path, project_path: str, uri_segments, department: str) -> Path:
    return Path(root) / project_key(project_path) / folder_name(uri_segments, department)


def frame_file(folder: Path, frame: int) -> Path:
    return Path(folder) / FRAME_PATTERN.replace('$F4', f'{frame:04d}')


# ── what order ──────────────────────────────────────────────


def frame_order(first: int, last: int, step: int, current: int) -> list[int]:
    """The shot's frames (``first`` to ``last`` by ``step``), from the
    current frame to the end, then from the start up to it. A current frame
    outside the range starts at the nearer end; one between two steps starts
    at the next frame."""
    if step <= 0:
        raise ValueError(f'step must be positive, got {step}')
    if first > last:
        raise ValueError(f'first ({first}) after last ({last})')
    frames = list(range(first, last + 1, step))
    split = next((i for i, f in enumerate(frames) if f >= current), len(frames))
    if split == len(frames):
        split = len(frames) - 1
    return frames[split:] + frames[:split]


# ── live.json ───────────────────────────────────────────────


def write_state(folder: Path, state: dict) -> None:
    """Write live.json atomically: a temp file in the folder, then a rename,
    so the player never reads half of it."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / f'.{STATE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp'
    temp.write_bytes(json.dumps(state, indent=1).encode('utf-8'))
    for attempt in range(20):
        try:
            os.replace(temp, folder / STATE_FILE)
            return
        except PermissionError:
            # Windows refuses the rename while a reader has the file open.
            time.sleep(0.01 * (attempt + 1))
    os.replace(temp, folder / STATE_FILE)


def read_state(folder: Path) -> dict | None:
    try:
        return json.loads((Path(folder) / STATE_FILE).read_bytes().decode('utf-8'))
    except (FileNotFoundError, ValueError):
        return None


def done_frames(folder: Path, order: list[int], since: float) -> list[int]:
    """The frames of ``order`` this run has written: their file exists and
    is no older than the run's start."""
    done = []
    for frame in order:
        try:
            if frame_file(folder, frame).stat().st_mtime >= since:
                done.append(frame)
        except FileNotFoundError:
            pass
    return done


# ── a husk another Houdini left ─────────────────────────────


def _program(command: list[str]) -> str:
    """How live.json names the program: ``husk`` for .../bin/husk.exe."""
    return Path(command[0]).stem.lower()


def _windows_process(pid: int, access: int):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32, kernel32.OpenProcess(access, False, pid)


def _process_program(pid) -> str | None:
    """The program process ``pid`` runs, as ``_program`` names it; None when
    no process has that pid (or it has ended)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if sys.platform.startswith('win'):
        import ctypes
        from ctypes import wintypes
        kernel32, handle = _windows_process(pid, 0x1000)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(wintypes.HANDLE(handle), ctypes.byref(code)) \
                    or code.value != 259:  # STILL_ACTIVE
                return None
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                    wintypes.HANDLE(handle), 0, buffer, ctypes.byref(size)):
                return None
            return _program([buffer.value])
        finally:
            kernel32.CloseHandle(handle)
    proc = Path('/proc') / str(pid)
    if proc.is_dir():
        try:
            if (proc / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                return None  # ended, not yet reaped
            return _program([os.readlink(proc / 'exe')])
        except (OSError, IndexError):
            return None
    try:
        out = subprocess.run(['ps', '-p', str(pid), '-o', 'stat=,comm='],
                             capture_output=True, text=True, timeout=10).stdout.split(None, 1)
    except (OSError, subprocess.SubprocessError):
        return None
    if len(out) < 2 or out[0].startswith('Z'):
        return None
    return _program([out[1].strip()])


def husk_alive(state: dict | None) -> bool:
    """Whether the husk live.json names is still running: a process with its
    pid running its program (a pid since given to something else does not
    count)."""
    state = state or {}
    program = state.get('program')
    return bool(program) and _process_program(state.get('pid')) == program


def _kill(pid: int) -> None:
    if sys.platform.startswith('win'):
        from ctypes import wintypes
        kernel32, handle = _windows_process(pid, 0x0001)  # TERMINATE
        if handle:
            try:
                kernel32.TerminateProcess(wintypes.HANDLE(handle), 1)
            finally:
                kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def stop_leftover(folder: Path, timeout: float = 10.0) -> bool:
    """Stop the husk an earlier Houdini left rendering into ``folder`` (one
    this process is not following), and wait for it to end. Whether there
    was one."""
    if running(folder) is not None:
        return False
    state = read_state(folder)
    if (state or {}).get('state') != RENDERING or not husk_alive(state):
        return False
    _kill(state['pid'])
    deadline = time.monotonic() + timeout
    while husk_alive(state) and time.monotonic() < deadline:
        time.sleep(0.05)
    return True


def settle(folder: Path) -> dict | None:
    """live.json, with a "rendering" whose husk is gone (its Houdini was
    killed) settled: ``done`` from the frames on disk this run wrote, and the
    state ``done`` if that is all of them, else ``cancelled``. Written back,
    so every reader sees the same. A render still going is left as it is."""
    state = read_state(folder)
    if (state or {}).get('state') != RENDERING or running(folder) is not None:
        return state
    if husk_alive(state):
        return state
    order = list(state.get('order', []))
    done = done_frames(folder, order, float(state.get('started_at', float('inf'))))
    settled = {**state, 'done': done,
               'state': DONE if order and len(done) == len(order) else CANCELLED}
    write_state(folder, settled)
    return settled


# ── the render ──────────────────────────────────────────────


@dataclass
class LiveRun:
    """A husk rendering into ``folder``: run ``run`` of it."""
    folder: Path
    run: int
    process: subprocess.Popen
    thread: threading.Thread
    cancelled: threading.Event

    def wait(self, timeout: float | None = None) -> str:
        """Wait for the render to end (and live.json to say so); its state."""
        self.thread.join(timeout)
        return (read_state(self.folder) or {}).get('state', RENDERING)


_RUNS: dict[str, LiveRun] = {}
_RUNS_LOCK = threading.Lock()

#: husk's progress lines at --verbose 'a': "ALF_PROGRESS 42%".
_PROGRESS = re.compile(r'ALF_PROGRESS\s+\d+%')


def _key(folder: Path) -> str:
    return os.path.normcase(os.path.abspath(folder))


def running(folder: Path) -> LiveRun | None:
    with _RUNS_LOCK:
        run = _RUNS.get(_key(folder))
    if run is None or run.process.poll() is not None and not run.thread.is_alive():
        return None
    return run


def cancel(folder: Path, timeout: float = 10.0) -> bool:
    """Stop the render into ``folder``, if one is going, and wait for its
    live.json to say so. Whether one was going."""
    with _RUNS_LOCK:
        run = _RUNS.get(_key(folder))
    if run is None or not run.thread.is_alive():
        return False
    run.cancelled.set()
    try:
        run.process.kill()
    except OSError:
        pass
    run.thread.join(timeout)
    return True


def cancel_all(timeout: float = 5.0) -> int:
    """Stop every render this process started (Houdini quitting); how many
    were going."""
    with _RUNS_LOCK:
        folders = [run.folder for run in _RUNS.values()]
    return sum(cancel(folder, timeout) for folder in folders)


def next_run(folder: Path) -> int:
    return int((read_state(folder) or {}).get('run', 0)) + 1


def start(folder: Path, command: list[str], env: dict | None, order: list[int],
          info: dict, on_end=None) -> LiveRun:
    """Start ``command`` (husk, rendering ``order`` into ``folder``) in the
    background, after cancelling a render already going there. ``info`` is
    what live.json says about the render (uri, department, range, fps,
    resolution, user, workfile); this adds the run, when it started, the
    frames done and the state, and keeps them current until husk ends; and
    names the husk (``pid``, ``program``), so a later Houdini can stop it.
    ``on_end(state, folder)`` is called once, from the render's thread, when
    it has ended and live.json says so."""
    folder = Path(folder)
    cancel(folder)
    stop_leftover(folder)
    folder.mkdir(parents=True, exist_ok=True)
    run = next_run(folder)
    started = time.time()
    state = {
        **info,
        'run': run,
        'started': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(started)),
        'started_at': started,
        'order': list(order),
        'done': [],
        'state': RENDERING,
        'program': _program(command),
    }
    # Open for the render's whole life: follow() writes husk's output to it
    # from its thread and closes it when husk ends.
    log = open(folder / LOG_FILE, 'w', encoding='utf-8', errors='replace')  # noqa: SIM115
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        process = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, errors='replace',
            creationflags=flags,
        )
    except OSError as error:
        log.write(f'could not start husk: {error}\n')
        log.close()
        write_state(folder, {**state, 'state': FAILED, 'error': str(error)})
        raise
    # Written once husk has a pid: a reader never sees "rendering" with no
    # husk named, which settle() would take for one whose Houdini died.
    state['pid'] = process.pid
    write_state(folder, state)
    cancelled = threading.Event()

    def follow():
        last_write = 0.0
        try:
            for line in process.stdout:
                log.write(line)
                if _PROGRESS.search(line) and time.monotonic() - last_write > 0.2:
                    state['done'] = done_frames(folder, order, started)
                    write_state(folder, state)
                    last_write = time.monotonic()
            process.wait()
        finally:
            log.close()
            done = done_frames(folder, order, started)
            if cancelled.is_set():
                outcome = CANCELLED
            elif process.returncode == 0 and len(done) == len(order):
                outcome = DONE
            else:
                outcome = FAILED
            # A newer run owns live.json now: leave it alone.
            current = read_state(folder) or {}
            if current.get('run', run) == run:
                write_state(folder, {**state, 'done': done, 'state': outcome,
                                     'returncode': process.returncode})
            if on_end is not None:
                try:
                    on_end(outcome, folder)
                except Exception:  # noqa: BLE001 - a report must not kill the thread's cleanup
                    pass

    thread = threading.Thread(target=follow, name=f'live-playblast-{run}', daemon=True)
    live_run = LiveRun(folder, run, process, thread, cancelled)
    with _RUNS_LOCK:
        _RUNS[_key(folder)] = live_run
    thread.start()
    return live_run
