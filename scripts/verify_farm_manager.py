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
  6. Clean up, against a fake farm (Deadline's job list and delete are
     replaced, and any real deadlinecommand call fails the check): the menu
     counts each kind's batches, a live batch is never offered, a "No" does
     nothing, a "Yes" removes local batches with a request and deletes farm
     ones in one call, and a farm batch requeued while the question was
     open is left alone

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

# Houdini's Qt DLLs live in $HFS/bin, which Python does not search on PATH.
if os.environ.get('HFS') and hasattr(os, 'add_dll_directory'):
    os.add_dll_directory(str(Path(os.environ['HFS']) / 'bin'))

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tumblepipe.farm.local import queue, runner  # noqa: E402
from tumblepipe.farm.manager import rows  # noqa: E402

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


def farm_doc(job_id: str, batch: str, stat: int, submitted: str, entity: str, project: str, **chunks) -> dict:
    """A Deadline ``-Json GetJobs`` job document, stamped with ``project`` and ``entity``."""
    from tumblepipe.farm import stamp
    return {
        '_id': job_id, 'Stat': stat, 'Date': submitted, 'Errs': 0,
        'CompletedChunks': chunks.pop('CompletedChunks', 1 if stat == 3 else 0), **chunks,
        'Props': {
            'Name': job_id, 'Batch': batch, 'User': 'tester', 'Tasks': 1,
            'ExDic': stamp.stamp(project, entity),
        },
    }


def verify_cleanup(app: QApplication, win, root: Path, project: str, done: Path, live: Path) -> None:
    from PySide6.QtWidgets import QMessageBox
    from tumblepipe.farm.manager import deadline_jobs, rows
    from tumblepipe.farm.manager import window as manager_window

    sh010, sh020 = 'entity:/shots/sq010/sh010', 'entity:/shots/sq010/sh020'
    older = f'{project} render {sh010} tester 2026/11/01 10:00:00'
    newer = f'{project} render {sh010} tester 2026/11/02 10:00:00'
    failed = f'{project} playblast {sh020} tester 2026/11/02 11:00:00'
    rendering = f'{project} publish {sh020} tester 2026/11/02 12:00:00'
    requeued = f'{project} render {sh020} tester 2026/11/02 13:00:00'
    docs = [
        farm_doc('a1', older, 3, '2026-11-01T10:00:00.000+01:00', sh010, project),
        farm_doc('a2', older, 3, '2026-11-01T10:00:00.000+01:00', sh010, project),
        farm_doc('b1', newer, 3, '2026-11-02T10:00:00.000+01:00', sh010, project),
        farm_doc('c1', failed, 4, '2026-11-02T11:00:00.000+01:00', sh020, project, FailedChunks=1),
        farm_doc('c2', failed, 3, '2026-11-02T11:00:00.000+01:00', sh020, project),
        farm_doc('d1', rendering, 4, '2026-11-02T12:00:00.000+01:00', sh020, project, RenderingChunks=1),
        farm_doc('d2', rendering, 3, '2026-11-02T12:00:00.000+01:00', sh020, project),
        farm_doc('e1', requeued, 3, '2026-11-02T13:00:00.000+01:00', sh020, project),
    ]
    deleted: list[list[str]] = []
    live_calls: list = []
    requeue = {'now': False}

    def fetch_jobs():
        out = []
        for doc in docs:
            if any(doc['_id'] in ids for ids in deleted):
                continue
            if requeue['now'] and doc['_id'] == 'e1':
                doc = dict(doc, Stat=1, QueuedChunks=1, CompletedChunks=0)
            out.append(rows.farm_job_row(doc))
        return out

    def control(command, ids):
        deleted.append(list(ids)) if command == 'delete' else live_calls.append((command, ids))
        return ''

    def no_real_deadline(*args, **kwargs):
        live_calls.append(args)
        raise AssertionError('the clean-up check reached for the real deadlinecommand')

    # Let a real (read-only) farm read still under way from step 5 land
    # first, and select nothing, so no farm job's tasks are read from here on.
    win._jobs.clearSelection()
    deadline = time.monotonic() + 60
    while win._futures and time.monotonic() < deadline:
        pump(app, 0.1)
    saved = (deadline_jobs.fetch_jobs, deadline_jobs.fetch_tasks, deadline_jobs.control,
             deadline_jobs._command, QMessageBox.question)
    deadline_jobs.fetch_jobs, deadline_jobs.control = fetch_jobs, control
    deadline_jobs.fetch_tasks = lambda job_id: []
    deadline_jobs._command = no_real_deadline
    asked: list[str] = []
    answer = {'value': QMessageBox.No}

    def question(parent, title, text, *args, **kwargs):
        asked.append(text)
        requeue['now'] = requeue.get('on_ask', False)
        return answer['value']

    QMessageBox.question = staticmethod(question)
    try:
        win._mine.setChecked(True)
        win._this_project.setChecked(True)
        win._hide_finished.setChecked(True)
        win._farm_available, win._farm_error, win._farm_loading = True, None, False
        win._farm_rows = []
        win._refresh_farm()
        deadline = time.monotonic() + 10
        while not win._farm_rows and time.monotonic() < deadline:
            pump(app, 0.1)
        check('the fake farm lists', len(win._farm_rows) == len(docs), str(len(win._farm_rows)))
        completed = make_batch(root, f'{project} render sh030 tester', [[queue.COMPLETED]], [[]])
        win._refresh_local()

        win._update_cleanup_menu()
        counts = {kind: len(win._cleanup_candidates(kind)) for kind, _l, _t in manager_window.CLEANUP_ITEMS}
        # Local: done (a failed job), completed, live (rendering). Farm: older
        # and newer (completed, same work), failed, rendering (a job still
        # rendering), requeued (completed, for now).
        check('Remove completed counts the completed batches (Hide finished does not hide them from it)',
              counts['completed'] == 4, str(counts))
        check('Remove failed counts the failed ones, never the batch still rendering',
              counts['failed'] == 2, str(counts))
        check('Remove completed and failed counts both', counts['finished'] == 6, str(counts))
        check('Remove superseded counts only the older run of the same work', counts['superseded'] == 1, str(counts))
        action = win._cleanup_actions['completed']
        check('the menu item says how many batches', action.text() == 'Remove completed (4 batches)', action.text())
        offered = {r.key for kind in counts for r in win._cleanup_candidates(kind)}
        check('a batch with a job not ended is never offered',
              f'local:{live.name}' not in offered and f'farm:{rendering}' not in offered, str(offered))

        win._search.setText('nothing matches this')
        pump(app, 0.1)
        win._update_cleanup_menu()
        check('the search filter narrows it (an item with nothing is greyed)',
              not win._cleanup_actions['completed'].isEnabled())
        win._search.setText('')
        pump(app, 0.1)

        win._clean_up('finished')
        check('it asks first, naming the batches on both sides',
              len(asked) == 1 and 'Deleted from the Deadline farm:' in asked[-1]
              and "Taken off this machine's list:" in asked[-1] and 'SQ010/SH010' in asked[-1].upper(),
              asked[-1] if asked else '')
        check('a "No" removes nothing',
              not deleted and not any(d['command'] == 'remove'
                                      for f in (done, completed, live) for _p, d in queue.pending_requests(f)))

        answer['value'] = QMessageBox.Yes
        win._clean_up('superseded')
        deadline = time.monotonic() + 10
        while not deleted and time.monotonic() < deadline:
            pump(app, 0.1)
        check('Remove superseded deletes the older run only, whole',
              deleted == [['a1', 'a2']], str(deleted))
        # The window reads the farm again once the delete is answered.
        deadline = time.monotonic() + 10
        while any(r.id == 'a1' for r in win._farm_rows) and time.monotonic() < deadline:
            pump(app, 0.1)

        requeue['on_ask'] = True  # e1 is requeued while the next question is open
        before = len(deleted)
        win._clean_up('finished')
        deadline = time.monotonic() + 10
        while len(deleted) == before and time.monotonic() < deadline:
            pump(app, 0.1)
        pump(app, 0.3)
        farm_ids = sorted(i for ids in deleted[before:] for i in ids)
        check('the rest is deleted in one call, the requeued batch left alone',
              len(deleted) == before + 1 and farm_ids == ['b1', 'c1', 'c2'], str(deleted))
        removed = {f.name for f in (done, completed, live)
                   if any(d['command'] == 'remove' for _p, d in queue.pending_requests(f))}
        check('local finished batches get a remove request, the live one none',
              removed == {done.name, completed.name}, str(removed))
        message = win._message.text()
        check('the footer says what went and what was left',
              'deleted from the farm' in message and '1 left' in message, message)
        check('nothing reached the real Deadline', not live_calls, str(live_calls))
    finally:
        (deadline_jobs.fetch_jobs, deadline_jobs.fetch_tasks, deadline_jobs.control,
         deadline_jobs._command, QMessageBox.question) = saved


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

    print('1b. status dots, red failed names, the legend')
    mw = manager_window
    red = QColor(mw.DANGER).name()

    def name_colour(item, column=mw.NAME):
        return item.foreground(column).color().name()

    failed_job = win._job_items[f'{done_id}/1']
    done_job = win._job_items[f'{done_id}/0']
    check('every job and batch row has a status dot before its name',
          all(not item.icon(mw.NAME).isNull()
              for item in list(win._job_items.values()) + list(win._batch_items.values())))
    check("the dot is the state's own (one icon per state)",
          failed_job.icon(mw.NAME).cacheKey() == mw.status_dot(queue.FAILED).cacheKey()
          and done_job.icon(mw.NAME).cacheKey() == mw.status_dot(queue.COMPLETED).cacheKey())
    check("a failed job's name is red", name_colour(failed_job) == red, name_colour(failed_job))
    check('its batch, failed too, has a red name', name_colour(batches[done_id]) == red,
          name_colour(batches[done_id]))
    check("a completed job's name is not red", name_colour(done_job) != red, name_colour(done_job))
    check("a live batch's name is not red", name_colour(batches[live_id]) != red)
    check('the legend lists every state but unknown, in order',
          list(win._legend) == list(rows.LEGEND), str(list(win._legend)))
    check('each legend state has a colour and a tip',
          all(state in mw.STATUS_COLOURS and mw.STATUS_TIPS.get(state) for state in rows.LEGEND))
    colours = [mw.STATUS_COLOURS[state] for state in rows.LEGEND]
    check('no two legend states share a colour', len(set(colours)) == len(colours), str(colours))
    check('the legend is shown', all(label.isVisible() for label in win._legend.values()))

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
    failed_task = win._task_items[0]
    check('the failed task has its dot and a red number',
          not failed_task.icon(manager_window.T_TASK).isNull()
          and failed_task.foreground(manager_window.T_TASK).color().name() == QColor(manager_window.DANGER).name())

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

    print('6. clean up (a fake farm: nothing reaches Deadline)')
    verify_cleanup(app, win, root, project, done, live)

    print('7. a failed batch still working says both; short columns fit (#46)')
    mixed = make_batch(root, f'{project} render sh030 tester', [[queue.FAILED], [queue.RENDERING]], [[], []])
    win._refresh_local()
    mixed_id = f'local:{mixed.name}'
    deadline = time.monotonic() + 10
    while mixed_id not in win._batch_items and time.monotonic() < deadline:
        pump(app, 0.3)
    item = win._batch_items.get(mixed_id)
    check('a batch with a failed and a rendering job reads "rendering · 1 failed"',
          item is not None and item.text(mw.STATUS) == 'rendering · 1 failed',
          item.text(mw.STATUS) if item is not None else 'not listed')
    check('and keeps its failed dot', item is not None
          and item.icon(mw.NAME).cacheKey() == mw.status_dot(queue.FAILED).cacheKey())
    header = win._jobs.header()
    check('Status, Submitted, Where, Tasks, User and Errors fit their text',
          all(header.sectionResizeMode(c) == mw.QHeaderView.ResizeToContents
              for c in (mw.STATUS, mw.SUBMITTED, mw.WHERE_COL, mw.TASKS, mw.USER, mw.ERRORS)))
    check('the Submitted cell carries its full time as a tooltip',
          item is not None and item.toolTip(mw.SUBMITTED) != '')

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
