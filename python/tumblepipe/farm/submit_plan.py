"""Run a Farm Submit grid submission in its own process, so Houdini is free at once.

The Farm Submit dialog does only the cheap part of a submission in Houdini: it
resolves each entity's settings and writes them to a **plan** file. Launching
the plan hands the expensive part — bundling workfiles, snapshotting staged
stages, talking to Deadline — to a separate Python process, and the dialog
closes. A small status window reads the **progress** file the process writes.

Why a process and not a thread: ``submit_entity_batch`` runs on the
process-global ``tumblepipe.api`` client and config cache, and Qt objects
crossing threads have crashed this pipeline before
(``designs/qt-thread-safety.md``). A process shares nothing, and it keeps
going if Houdini is closed or crashes straight after Submit.

The process is Houdini's own bundled Python (``$HFS/python3XY``), not hython:
it carries ``pxr`` for the snapshots and takes **no licence**.

Files, both in one per-submission folder under ``temp:/farm_submissions/``::

    plan.json       {'version', 'created', 'env', 'houdini_version', 'rows': [...]}
    progress.jsonl  one JSON event per line:
                    {'event': 'start', 'total': N}
                    {'event': 'row', 'uri': ..., 'status': 'running'}
                    {'event': 'row', 'uri': ..., 'status': 'done', 'jobs': N}
                    {'event': 'row', 'uri': ..., 'status': 'failed', 'error': '...'}
                    {'event': 'end', 'done': N, 'failed': N}
    runner.log      the process's stdout and stderr

Each plan row is exactly the config ``batch_submit.submit_entity_batch``
takes: ``{'entity': {'uri', 'name', 'context'}, 'settings': {...}}``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

PLAN_VERSION = 1
PLAN_NAME = 'plan.json'
PROGRESS_NAME = 'progress.jsonl'
LOG_NAME = 'runner.log'

# The project the plan was made in. The runner re-applies these before
# importing the pipeline, so a project switch in the launching session
# cannot redirect a submission already under way.
PROJECT_ENV_KEYS = (
    'TH_PROJECT_PATH', 'TH_CONFIG_PATH', 'TH_EXPORT_PATH', 'TH_PIPELINE_PATH',
    'TH_USER', 'OCIO',
)


# ── plan and progress files ───────────────────────────────

def make_plan(rows: list[dict], *, env: dict, houdini_version: str | None) -> dict:
    return {
        'version': PLAN_VERSION,
        'created': dt.datetime.now().isoformat(timespec='seconds'),
        'env': {key: env[key] for key in PROJECT_ENV_KEYS if env.get(key)},
        'houdini_version': houdini_version,
        'rows': list(rows),
    }


def write_plan(directory: Path, plan: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PLAN_NAME
    path.write_text(json.dumps(plan, indent=2), encoding='utf-8')
    return path


def read_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    if plan.get('version') != PLAN_VERSION:
        raise ValueError(
            f"{path} is a version {plan.get('version')!r} plan; this runner "
            f"reads version {PLAN_VERSION}"
        )
    return plan


def emit(progress_path: Path, **event) -> None:
    """Append one event. Flushed per line so a reader never waits on a buffer."""
    with open(progress_path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(event) + '\n')
        handle.flush()


def read_events(progress_path: Path, offset: int = 0) -> tuple[list[dict], int]:
    """Events appended since ``offset``, and the offset to read from next.

    Only whole lines are consumed: a line the runner is halfway through
    writing is left for the next read.
    """
    try:
        with open(progress_path, 'rb') as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return [], offset
    events = []
    consumed = 0
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n'):
            break
        consumed += len(line)
        text = line.decode('utf-8', errors='replace').strip()
        if not text:
            continue
        try:
            events.append(json.loads(text))
        except ValueError:
            continue
    return events, offset + consumed


def fold_events(events: list[dict], state: dict | None = None) -> dict:
    """Fold events into ``{'total', 'finished', 'rows': {uri: event}}``."""
    state = state if state is not None else {'total': None, 'finished': False, 'rows': {}}
    for event in events:
        kind = event.get('event')
        if kind == 'start':
            state['total'] = event.get('total')
        elif kind == 'row' and 'uri' in event:
            state['rows'][event['uri']] = event
        elif kind == 'end':
            state['finished'] = True
    return state


# ── launching ─────────────────────────────────────────────

def houdini_python(hfs: Path, version: tuple[int, int]) -> Path | None:
    """Houdini's bundled interpreter for ``version``, or None if absent."""
    major, minor = version
    candidates = [
        hfs / f'python{major}{minor}' / 'python.exe',                  # Windows
        hfs / 'python' / 'bin' / f'python{major}.{minor}',            # Linux
        hfs / 'python' / 'bin' / 'python3',
        hfs.parent / 'Frameworks' / 'Python.framework' / 'Versions'  # macOS
        / f'{major}.{minor}' / 'bin' / f'python{major}.{minor}',
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def interpreter() -> Path:
    """The Python to run the plan with.

    Outside Houdini (the Desktop-launched Farm Submit window) this already *is*
    Houdini's bundled Python. Inside, ``sys.executable`` is the Houdini
    binary, so the bundled one of the same version is found under ``$HFS``.
    """
    executable = Path(sys.executable)
    if executable.stem.lower().startswith('python'):
        return executable
    hfs = os.environ.get('HFS')
    if hfs:
        found = houdini_python(Path(hfs), sys.version_info[:2])
        if found is not None:
            return found
    raise RuntimeError(
        "Cannot find Houdini's bundled Python to run the submission with "
        f"(HFS={hfs!r}, Python {sys.version_info[0]}.{sys.version_info[1]})."
    )


def child_env(plan: dict, base: dict | None = None) -> dict:
    """The launching environment, pinned to the plan's project and Houdini."""
    env = dict(os.environ if base is None else base)
    env.update(plan.get('env', {}))
    if plan.get('houdini_version'):
        # Farm jobs are keyed on the submitter's Houdini major; outside hou
        # this is the only way the job environment learns it.
        env['TH_HOUDINI_VERSION'] = plan['houdini_version']
    package_root = str(Path(__file__).resolve().parents[2])
    parts = [package_root] + [
        p for p in env.get('PYTHONPATH', '').split(os.pathsep) if p and p != package_root
    ]
    env['PYTHONPATH'] = os.pathsep.join(parts)
    env['PYTHONUTF8'] = '1'
    return env


def launch(plan_path: Path) -> subprocess.Popen:
    """Start the runner on ``plan_path``, detached, and return at once."""
    plan_path = Path(plan_path)
    plan = read_plan(plan_path)
    log = open(plan_path.parent / LOG_NAME, 'ab')
    flags = 0
    if sys.platform == 'win32':
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    try:
        return subprocess.Popen(
            [str(interpreter()), '-m', 'tumblepipe.farm.submit_plan', str(plan_path)],
            # The plan folder, not the caller's: `-m` puts the working
            # directory first on sys.path, and a module there named like a
            # stdlib one (asset_browser/types.py) breaks the interpreter.
            cwd=str(plan_path.parent),
            env=child_env(plan),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=sys.platform != 'win32',
        )
    finally:
        log.close()


# ── running (in the child) ────────────────────────────────

def _add_houdini_dll_directory() -> None:
    """Let ``pxr`` load: Python 3.8+ on Windows ignores PATH for DLLs."""
    hfs = os.environ.get('HFS')
    if hfs and hasattr(os, 'add_dll_directory'):
        bin_dir = Path(hfs) / 'bin'
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))


def run(plan_path: Path) -> int:
    plan_path = Path(plan_path)
    progress_path = plan_path.parent / PROGRESS_NAME
    plan = read_plan(plan_path)
    os.environ.update(plan.get('env', {}))
    _add_houdini_dll_directory()

    rows = plan['rows']
    emit(progress_path, event='start', total=len(rows))
    try:
        from tumblepipe.farm.jobs.houdini.batch_submit import submit_entity_batch
    except Exception as error:
        for row in rows:
            emit(progress_path, event='row', uri=row['entity']['uri'],
                 status='failed', error=f'Could not load the submitter: {error}')
        emit(progress_path, event='end', done=0, failed=len(rows))
        return 1

    done = failed = 0
    for row in rows:
        uri = row['entity']['uri']
        emit(progress_path, event='row', uri=uri, status='running')
        try:
            job_ids = submit_entity_batch(row)
        except Exception as error:
            failed += 1
            print(f'{uri}: {error}', flush=True)
            emit(progress_path, event='row', uri=uri, status='failed', error=str(error))
            continue
        done += 1
        emit(progress_path, event='row', uri=uri, status='done', jobs=len(job_ids or []))
    emit(progress_path, event='end', done=done, failed=failed)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print('usage: python -m tumblepipe.farm.submit_plan <plan.json>', file=sys.stderr)
        return 2
    return run(Path(argv[0]))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
