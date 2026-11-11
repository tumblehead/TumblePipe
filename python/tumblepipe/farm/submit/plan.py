"""Run a Farm Submit grid submission in its own process, so Houdini is free at once.

The Farm Submit dialog does only the cheap part of a submission in Houdini: it
resolves each entity's settings and writes them to a **plan** file. Launching
the plan hands the expensive part — bundling workfiles, snapshotting staged
stages, talking to Deadline — to a separate Python process, and the dialog
stays. Its progress card (asset_browser.farm_submit_ui.SubmissionView) reads the **progress** file the process writes.

Why a process and not a thread: ``submit_entity_batch`` runs on the
process-global ``tumblepipe.api`` client and config cache, and Qt objects
crossing threads have crashed this pipeline before
(*Qt and threads* in docs/development.md). A process shares nothing, and it keeps
going if Houdini is closed or crashes straight after Submit.

The process is Houdini's own bundled Python (``$HFS/python3XY``), not hython:
it carries ``pxr`` for the snapshots and takes **no licence**.

Files, both in one per-submission folder under ``temp:/farm_submissions/``::

    plan.json       {'version', 'created', 'env', 'houdini_version', 'backend',
                     'cleanup', 'rows': [...]}
    progress.jsonl  one JSON event per line:
                    {'event': 'start', 'total': N}
                    {'event': 'row', 'uri': ..., 'status': 'running'}
                    {'event': 'cleanup', 'uri': ..., 'removed': N, 'batches': N,
                     'error': None or '...'}
                    {'event': 'row', 'uri': ..., 'status': 'done', 'jobs': N}
                    {'event': 'row', 'uri': ..., 'status': 'failed', 'error': '...'}
                    {'event': 'row', 'uri': ..., 'status': 'cancelled'}
                    {'event': 'end', 'done': N, 'failed': N, 'cancelled': N}
    runner.log      the process's stdout and stderr
    cancel          written by the progress card's Cancel to stop the run; the runner
                    checks for it between entities, so the entity being
                    submitted finishes and none is left half-submitted

Each plan row is exactly the config ``batch_submit.submit_entity_batch``
takes: ``{'entity': {'uri', 'name', 'context'}, 'settings': {...}}``.
``backend`` is where the batches go (``farm.backend``): the Deadline farm, or
this machine's local farm. A plan without one is a farm plan.

``cleanup`` true clears each entity's old finished jobs before submitting
it (``farm.manager.cleanup.EntityCleanup``): every batch of that entity in
the plan's project, whoever submitted it and whatever its steps, on the
backend the plan submits to, once every job of the batch completed or
failed. A ``cleanup`` event follows the entity's ``running`` one, saying how
many jobs (``removed``) in how many batches were taken off, and ``error``
why not all were. A clean-up that fails never fails or holds up the
submission: the entity is submitted all the same. A plan without the key
cleans nothing up.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path, PurePosixPath

PLAN_VERSION = 1
PLAN_NAME = 'plan.json'
PROGRESS_NAME = 'progress.jsonl'
LOG_NAME = 'runner.log'
CANCEL_NAME = 'cancel'

# The project the plan was made in. The runner re-applies these before
# importing the pipeline, so a project switch in the launching session
# cannot redirect a submission already under way.
PROJECT_ENV_KEYS = (
    'TH_PROJECT_PATH', 'TH_CONFIG_PATH', 'TH_EXPORT_PATH', 'TH_PIPELINE_PATH',
    'TH_USER', 'OCIO',
)


# ── plan and progress files ───────────────────────────────

def make_plan(
    rows: list[dict], *, env: dict, houdini_version: str | None, backend: str = 'farm',
    cleanup: bool = False,
    ) -> dict:
    """The plan of a submission. ``cleanup`` clears each entity's old
    finished jobs before it is submitted (see the module's docstring)."""
    return {
        'version': PLAN_VERSION,
        'created': dt.datetime.now().isoformat(timespec='seconds'),
        'env': {key: env[key] for key in PROJECT_ENV_KEYS if env.get(key)},
        'houdini_version': houdini_version,
        'backend': backend,
        'cleanup': bool(cleanup),
        'rows': list(rows),
    }


def plan_backend(plan: dict) -> str:
    """Where the plan's batches go; ``farm`` for a plan made before there was a choice."""
    from tumblepipe.farm.backend import FARM, LOCAL
    return LOCAL if plan.get('backend') == LOCAL else FARM


def plan_cleanup(plan: dict) -> bool:
    """Whether the plan clears old jobs first; a plan made before there was a choice does not."""
    return plan.get('cleanup') is True


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
    writing is left for the next read, so every consumed line is a whole
    event and one that does not parse raises.
    """
    try:
        with open(progress_path, 'rb') as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        return [], offset  # The runner has not started writing yet.
    events = []
    consumed = 0
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n'):
            break
        consumed += len(line)
        text = line.decode('utf-8').strip()
        if not text:
            continue
        events.append(json.loads(text))
    return events, offset + consumed


def request_cancel(plan_path: Path) -> None:
    """Ask the runner of ``plan_path`` to stop before its next entity."""
    (Path(plan_path).parent / CANCEL_NAME).write_text('', encoding='utf-8')


def fold_events(events: list[dict], state: dict | None = None) -> dict:
    """Fold events into ``{'total', 'finished', 'rows': {uri: event}, 'cleanup': {uri: event}}``.

    ``cleanup`` holds each entity's clean-up event apart from its row
    event, so the row's own status is never replaced by it.
    """
    state = state if state is not None else {
        'total': None, 'finished': False, 'rows': {}, 'cleanup': {},
    }
    for event in events:
        kind = event.get('event')
        if kind == 'start':
            state['total'] = event.get('total')
        elif kind == 'row' and 'uri' in event:
            state['rows'][event['uri']] = event
        elif kind == 'cleanup' and 'uri' in event:
            state.setdefault('cleanup', {})[event['uri']] = event
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
    ]
    # macOS: $HFS is <app>/Frameworks/Houdini.framework/Versions/<v>/Resources
    # and Python.framework sits beside Houdini.framework.
    if len(hfs.parents) > 3:
        frameworks = hfs.parents[3]
        candidates.append(
            frameworks / 'Python.framework' / 'Versions' / f'{major}.{minor}'
            / 'bin' / f'python{major}.{minor}'
        )
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
    env.update(plan['env'])
    if plan['houdini_version']:
        # Farm jobs are keyed on the submitter's Houdini major; outside hou
        # this is the only way the job environment learns it.
        env['TH_HOUDINI_VERSION'] = plan['houdini_version']
    package_root = str(Path(__file__).resolve().parents[3])
    parts = [package_root] + [
        p for p in env.get('PYTHONPATH', '').split(os.pathsep) if p and p != package_root
    ]
    env['PYTHONPATH'] = os.pathsep.join(parts)
    env['PYTHONUTF8'] = '1'
    _find_houdini_frameworks(env)
    return env


def macos_frameworks(hfs: str) -> PurePosixPath | None:
    """The Houdini app's ``Frameworks`` folder when ``hfs`` is a macOS HFS
    (``<app>/Frameworks/Houdini.framework/Versions/<v>/Resources``)."""
    parents = PurePosixPath(hfs).parents
    if len(parents) > 3 and parents[2].name == 'Houdini.framework':
        return parents[3]
    return None


def _find_houdini_frameworks(env: dict) -> None:
    """macOS: the runner is Houdini's bare Python, and USD plugins built
    against Houdini (tumbleResolver) link ``@rpath/Houdini.framework``,
    which only Houdini's own executable finds. Every submit from the Render
    Manager failed "Failed to load plugin 'tumbleResolver'" (Martin,
    2026-11-11); dyld now falls back to the app's Frameworks folder."""
    hfs = env.get('HFS')
    frameworks = macos_frameworks(hfs) if hfs else None
    if frameworks is None: return
    existing = [p for p in env.get('DYLD_FALLBACK_FRAMEWORK_PATH', '').split(':') if p]
    if str(frameworks) in existing: return
    env['DYLD_FALLBACK_FRAMEWORK_PATH'] = ':'.join([str(frameworks)] + existing)


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
            [str(interpreter()), '-m', 'tumblepipe.farm.submit.plan', str(plan_path)],
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
    os.environ.update(plan['env'])
    from tumblepipe.farm.backend import BACKEND_ENV
    os.environ[BACKEND_ENV] = plan_backend(plan)
    _add_houdini_dll_directory()

    rows = plan['rows']
    emit(progress_path, event='start', total=len(rows))
    try:
        from tumblepipe.farm.pipelines.batch_submit import submit_entity_batch
    except Exception as error:
        # Reported, not swallowed: every row fails in the progress card, the
        # traceback goes to runner.log, and the runner exits non-zero.
        traceback.print_exc()
        for row in rows:
            emit(progress_path, event='row', uri=row['entity']['uri'],
                 status='failed', error=f'Could not load the submitter: {error}')
        emit(progress_path, event='end', done=0, failed=len(rows), cancelled=0)
        return 1
    return run_rows(
        rows, progress_path, plan_path.parent / CANCEL_NAME, submit_entity_batch,
        cleanup=_entity_cleanup(plan),
    )


def _entity_cleanup(plan: dict):
    """The plan's clean-up (``cleanup.EntityCleanup``), or None when it asks for none.

    Built after the plan's environment is applied, so it reads the plan's
    project. One that cannot be built is reported on every entity by
    :func:`clean_up`, and the submission goes on.
    """
    if not plan_cleanup(plan):
        return None
    try:
        from tumblepipe.api import get_project_name
        from tumblepipe.farm.manager.cleanup import EntityCleanup
        return EntityCleanup(plan_backend(plan), get_project_name())
    except Exception as error:
        traceback.print_exc()
        message = f'Could not set up the clean-up: {error}'

        def _failed(_uri: str):
            raise RuntimeError(message)
        return _failed


def clean_up(cleanup, uri: str, progress_path: Path) -> None:
    """Clear ``uri``'s old finished jobs with ``cleanup`` and emit what it did.

    Never raises: a clean-up that fails is printed to runner.log and
    reported in its event, and the entity is submitted all the same.
    """
    try:
        result = cleanup(uri)
        removed, batches = int(result.jobs), int(result.batches)
        error = str(result.error) if result.error else None
    except Exception as exception:
        print(f'{uri}: cleaning up old jobs failed: {exception}', flush=True)
        traceback.print_exc()
        removed, batches = 0, 0
        error = str(exception) or type(exception).__name__
    else:
        if error:
            print(f'{uri}: cleaning up old jobs stopped short: {error}', flush=True)
        elif removed:
            print(f'{uri}: removed {removed} old job(s) in {batches} batch(es)', flush=True)
    try:
        emit(progress_path, event='cleanup', uri=uri, removed=removed, batches=batches, error=error)
    except OSError:
        traceback.print_exc()


def run_rows(rows: list[dict], progress_path: Path, cancel_path: Path, submit, cleanup=None) -> int:
    """Submit ``rows`` one by one with ``submit``, stopping once ``cancel_path`` exists.

    ``cleanup`` (``cleanup.EntityCleanup``, or None for none) is called with
    each entity's URI just before it is submitted; see :func:`clean_up`.
    """
    done = failed = cancelled = 0
    for index, row in enumerate(rows):
        if cancel_path.exists():
            for rest in rows[index:]:
                emit(progress_path, event='row', uri=rest['entity']['uri'], status='cancelled')
            cancelled = len(rows) - index
            break
        uri = row['entity']['uri']
        emit(progress_path, event='row', uri=uri, status='running')
        if cleanup is not None:
            clean_up(cleanup, uri, progress_path)
        try:
            job_ids = submit(row)
        except Exception as error:
            # One entity failing must not stop the others: it is reported in
            # the progress card, its traceback goes to runner.log, and the
            # runner exits non-zero.
            failed += 1
            print(f'{uri}: {error}', flush=True)
            traceback.print_exc()
            emit(progress_path, event='row', uri=uri, status='failed', error=str(error))
            continue
        done += 1
        emit(progress_path, event='row', uri=uri, status='done', jobs=len(job_ids))
    emit(progress_path, event='end', done=done, failed=failed, cancelled=cancelled)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print('usage: python -m tumblepipe.farm.submit.plan <plan.json>', file=sys.stderr)
        return 2
    return run(Path(argv[0]))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
