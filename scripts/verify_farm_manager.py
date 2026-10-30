"""Headless harness for the Farm Manager window (``tumblepipe.farm.manager.window``).

Drives a real ManagerWindow against a made-up local queue (in a temp
``TH_LOCAL_FARM_DIR``) and, when this machine has a Deadline Client, the
real farm (read only: no farm command is sent). Checks:

  1. local batches list as batch rows with their jobs under them, with the
     job states the queue derives (completed / failed / pending / rendering)
  2. selecting a job lists its tasks; the failed task is picked and its log
     is shown, and lines appended to a rendering task's log are followed
  3. Suspend / Retry failed / Remove on local jobs write the control requests
     the runner applies (the runner start is stubbed out)
  4. the filters: "Show: Render farm" hides the local rows
  5. with Deadline: farm jobs arrive off the GUI thread and list as Farm rows

Run (no Houdini licence: Houdini's bundled Python, not hython; see
``headless-qt-verify`` in the repo notes):

    QT_QPA_PLATFORM=offscreen "$HFS/python313/python.exe" scripts/verify_farm_manager.py [shot.png]
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))

TEMP = Path(tempfile.mkdtemp(prefix='farm-manager-verify-'))
os.environ['TH_LOCAL_FARM_DIR'] = str(TEMP / 'local_farm')
os.environ.setdefault('TH_USER', 'tester')

from PySide6.QtWidgets import QApplication  # noqa: E402

from tumblepipe.farm.local import queue, runner  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = '') -> None:
    _checks.append((name, bool(condition), detail))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f' — {detail}' if detail and not condition else ''))


def make_batch(root: Path, name: str, statuses: list[list[str]], deps: list[list[int]]) -> Path:
    jobs = [
        {'index': i, 'name': f'job {i}', 'deps': deps[i],
         'tasks': [[1001 + 10 * t, 1010 + 10 * t] for t in range(len(tasks))],
         'package': 'tumblepipe@1.0.0', 'script': 'python/x.py', 'arguments': 'ctx.json',
         'data_dir': 'J:/jobs/x/data', 'env_file': None, 'manifest': 'J:/jobs/x/00_hpm.toml',
         'timeout': 0, 'outputs': [str(TEMP / 'out' / 'receipt.json')]}
        for i, tasks in enumerate(statuses)
    ]
    batch = {
        'version': queue.BATCH_VERSION, 'id': queue.new_batch_id(), 'name': name,
        'user': 'tester', 'machine': 'here', 'submitted': queue.now(),
        'job_dir': str(TEMP / 'jobs' / 'x'), 'jobs': jobs,
    }
    folder = queue.add_batch(root, batch)
    state = queue.initial_state(batch)
    for i, tasks in enumerate(statuses):
        for t, status in enumerate(tasks):
            state['jobs'][str(i)]['tasks'][t].update(
                status=status, started=queue.now() if status != queue.QUEUED else None,
                progress=40 if status == queue.RENDERING else 0,
                error='Task failed with return code: 1' if status == queue.FAILED else None,
            )
            if status != queue.QUEUED:
                queue.log_path(folder, i, t).write_text(f'log of job {i} task {t}\n', encoding='utf-8')
    queue.write_state(folder, state)
    time.sleep(1.1)  # Batch ids sort by the second.
    return folder


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    root = queue.root()
    project = 'VerifyProj'
    done = make_batch(root, f'{project} render sh010 tester', [[queue.COMPLETED] * 2, [queue.FAILED, queue.COMPLETED]], [[], [0]])
    live = make_batch(root, f'{project} publish sh020 tester', [[queue.RENDERING, queue.QUEUED], [queue.QUEUED]], [[], [0]])
    started = []
    runner.ensure_running = lambda root_path=None: started.append(root_path) or False

    from tumblepipe.farm.manager import window as manager_window
    manager_window.runner.ensure_running = runner.ensure_running
    manager_window._identity = lambda: ('tester', project)
    win = manager_window.ManagerWindow()
    win.show()
    pump(app, 1.5)

    print('1. local batches and jobs')
    batches = win._batch_items
    local_keys = [k for k in batches if k.startswith('local:')]
    check('both local batches listed', len(local_keys) == 2, str(local_keys))
    states = {row.id: row.status for row in win._local_rows}
    done_id = f'local:{done.name}'
    check('finished batch: job 0 completed, job 1 failed',
          [states[f'{done_id}/0'], states[f'{done_id}/1']] == [queue.COMPLETED, queue.FAILED], str(states))
    live_id = f'local:{live.name}'
    check('live batch: job 0 rendering, job 1 pending on it',
          [states[f'{live_id}/0'], states[f'{live_id}/1']] == [queue.RENDERING, queue.PENDING], str(states))
    check('newest batch first', win._jobs.topLevelItem(0).data(0, manager_window.KEY_ROLE) == live_id)

    print('2. tasks and logs')
    win._jobs.clearSelection()
    win._job_items[f'{done_id}/1'].setSelected(True)
    pump(app, 0.3)
    check('tasks of the selected job listed', len(win._tasks) == 2, str(win._tasks))
    task = win._selected_task()
    check('the failed task is picked', task is not None and task.status == queue.FAILED, str(task))
    pump(app, 1.2)
    check('its log is shown', 'log of job 1 task 0' in win._log.toPlainText(), win._log.toPlainText()[:200])
    check('its error is in the task row', 'return code' in win._task_items[0].text(manager_window.T_ERROR))

    win._jobs.clearSelection()
    win._job_items[f'{live_id}/0'].setSelected(True)
    pump(app, 1.2)
    with open(queue.log_path(live, 0, 0), 'a', encoding='utf-8') as handle:
        handle.write('Progress: 60 %\nappended line\n')
    pump(app, 1.5)
    check('a rendering task\'s log is followed', 'appended line' in win._log.toPlainText(), win._log.toPlainText()[-200:])

    print('3. local actions')
    win._run_action('suspend')
    requests = queue.pending_requests(live)
    check('Suspend writes a request for the job', [d for _p, d in requests] == [{'command': 'suspend', 'job': 0}], str(requests))
    check('and starts the runner if it stopped', len(started) == 1)
    win._jobs.clearSelection()
    win._batch_items[done_id].setSelected(True)
    pump(app, 0.2)
    win._run_action('retry')
    requests = [d for _p, d in queue.pending_requests(done)]
    check('Retry on a batch row asks for every job of it',
          requests == [{'command': 'retry', 'job': 0}, {'command': 'retry', 'job': 1}], str(requests))
    check('farm-only actions are disabled for local jobs',
          not win._action_buttons['delete'].isEnabled() and not win._action_buttons['requeue'].isEnabled())

    print('4. filters')
    win._source.setCurrentIndex(win._source.findData('farm'))
    pump(app, 0.3)
    check('"Render farm" hides the local rows', not any(k.startswith('local:') for k in win._batch_items))
    win._source.setCurrentIndex(win._source.findData(None))
    pump(app, 0.3)

    print('5. the Deadline farm')
    from tumblepipe.apps.deadline import deadline_setup_problem
    if deadline_setup_problem() is not None:
        print('  (no Deadline Client here; skipped)')
    else:
        win._mine.setChecked(False)
        win._this_project.setChecked(False)
        deadline = time.monotonic() + 30
        while not win._farm_rows and time.monotonic() < deadline and not win._farm_error:
            pump(app, 0.2)
        check('farm jobs arrive', bool(win._farm_rows), win._farm_error or '')
        check('and list as Farm batch rows', any(k.startswith('farm:') for k in win._batch_items))
        if win._farm_rows:
            failed = next((r for r in win._farm_rows if r.errors), win._farm_rows[0])
            win._jobs.clearSelection()
            item = win._job_items.get(failed.id)
            if item is not None:
                item.parent().setExpanded(True)
                item.setSelected(True)
                deadline = time.monotonic() + 30
                while not win._tasks and time.monotonic() < deadline:
                    pump(app, 0.2)
                check('a farm job\'s tasks load', bool(win._tasks))
                deadline = time.monotonic() + 40
                while (not win._log.toPlainText() or win._log.toPlainText().startswith('Reading')) and time.monotonic() < deadline:
                    pump(app, 0.2)
                text = win._log.toPlainText()
                check('its task reports load', bool(text) and not text.startswith('Could not'), text[:200])

    if len(sys.argv) > 1:
        win.resize(1500, 900)
        pump(app, 0.5)
        win.grab().save(sys.argv[1])
        print(f'  screenshot: {sys.argv[1]}')
    win.close()
    pump(app, 0.2)
    shutil.rmtree(TEMP, ignore_errors=True)

    failed = [name for name, ok, _ in _checks if not ok]
    print(f"\n{len(_checks) - len(failed)}/{len(_checks)} checks passed")
    print(json.dumps({'failed': failed}))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
