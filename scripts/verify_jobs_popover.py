"""Headless harness for the Jobs popover (``farm.manager.finished``,
``asset_browser.farm_jobs``, the catalog's quick-action hooks and the Farm
Manager's ``select_job``).

Drives a real FarmActivity against a made-up local queue (in a temp
``TH_LOCAL_FARM_DIR``; the farm is switched off, so no deadlinecommand
runs) through a batch of this session that renders and then finishes, and
one that fails. Checks:

  1. a batch still rendering keeps the watcher reading and says nothing
  2. the batch finishing is reported once, with its line; a failed batch
     after it is reported with the failed frame, worker and error
  3. the Farm Manager button's dot is ``danger`` while a failure is unseen,
     and the popover lists failures first
  4. with TumbleTrove 0.60.0: the catalog's popover renders in TumbleTrove's
     QuickActionPopoverPanel with the session's rows and buttons; its Show
     in Jobs reaches the scene with the failed job; opening it clears the
     dot. With an older TumbleTrove the popover hook answers None (the click
     opens the Farm Manager as before)
  5. the announcement: one status line, an OS notification only when
     Houdini is not in front, a pulse asked only of a catalog that takes one
  6. the Farm Manager's select_job selects a finished job that "Hide
     finished" would keep out, and holds an unknown one until a read brings
     it in

Run (no Houdini licence: Houdini's bundled Python, not hython; TumbleTrove's
python/ on PYTHONPATH):

    QT_QPA_PLATFORM=offscreen "$HFS/python313/python.exe" scripts/verify_jobs_popover.py
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))

TEMP = Path(tempfile.mkdtemp(prefix='jobs-popover-verify-'))
os.environ['TH_LOCAL_FARM_DIR'] = str(TEMP / 'local_farm')
os.environ['TH_USER'] = 'tester'
os.environ.setdefault('TH_PROJECT_PATH', str(TEMP / 'proj'))

if os.environ.get('HFS') and hasattr(os, 'add_dll_directory'):
    os.add_dll_directory(str(Path(os.environ['HFS']) / 'bin'))

from PySide6.QtWidgets import QApplication  # noqa: E402

from tumblepipe.farm.local import queue  # noqa: E402
from tumblepipe.farm.manager import activity, finished  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = '') -> None:
    _checks.append((name, bool(condition), detail))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f' — {detail}' if detail and not condition else ''))


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def make_batch(root: Path, name: str, status: str) -> Path:
    """A one-job, one-task local batch in ``status``, submitted now."""
    job = {
        'index': 0, 'name': 'job 0', 'deps': [], 'tasks': [[1009, 1012]],
        'package': 'tumblepipe@1.0.0', 'script': 'python/workers/render.py',
        'arguments': 'ctx.json', 'data_dir': str(TEMP / 'data'), 'env_file': None,
        'manifest': str(TEMP / 'hpm.toml'), 'timeout': 0, 'outputs': [],
    }
    batch = {
        'version': queue.BATCH_VERSION, 'id': queue.new_batch_id(), 'name': name,
        'user': 'tester', 'machine': 'rn04', 'submitted': queue.now(),
        'job_dir': str(TEMP / 'jobs'), 'jobs': [job],
    }
    folder = queue.add_batch(root, batch)
    set_status(folder, status, error='out of GPU memory')
    time.sleep(1.1)  # Batch ids sort by the second.
    return folder


def set_status(folder: Path, status: str, error: str = '') -> None:
    batch = queue.read_batch(folder)
    state = queue.read_state(folder, batch)
    task = state['jobs']['0']['tasks'][0]
    task.update(
        status=status, started=task.get('started') or queue.now(),
        finished=queue.now() if status in (queue.COMPLETED, queue.FAILED) else None,
        progress=100 if status == queue.COMPLETED else 40,
        error=error if status == queue.FAILED else None,
    )
    queue.write_state(folder, state)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    root = queue.root()
    root.mkdir(parents=True, exist_ok=True)
    _user, project = activity.identity()
    finished.mark_session_start(dt.datetime.now() - dt.timedelta(seconds=5))

    ended: list = []
    watcher = activity.FarmActivity(
        farm_rows=lambda: [], farm_ready=lambda: False, on_finished=ended.extend,
    )

    print('1. a batch still rendering')
    shot = make_batch(root, f'{project} render sh010 tester', queue.RENDERING)
    watcher.poll()
    check('nothing is announced while it renders', ended == [], str(ended))
    check('the watcher keeps reading for it', watcher.session.watching and not watcher.idle())

    print('2. it finishes; another fails')
    set_status(shot, queue.COMPLETED)
    watcher.poll()
    check('the finished batch is announced once', len(ended) == 1 and ended[0].state == finished.FINISHED,
          str(ended))
    watcher.poll()
    check('and not again on the next read', len(ended) == 1, str(len(ended)))
    check('its line reads like a sentence', bool(ended and ended[0].line), repr(ended and ended[0].line))
    make_batch(root, f'{project} render sh020 tester', queue.FAILED)
    watcher.poll()
    failed = ended[-1] if len(ended) == 2 else None
    check('the failed batch is announced', failed is not None and failed.state == finished.FAILED,
          str(ended))
    line = failed.line if failed else ''
    check('its line names the frames, the worker and the error',
          '1009' in line and 'rn04' in line and 'out of GPU memory' in line, repr(line))

    print('3. the dot and the order')
    session = watcher.session
    check('the dot is danger while a failure is unseen', session.dot() == 'danger', session.dot())
    keys = [r.key for r in session.reports()]
    check('failures first', failed is not None and keys[:1] == [failed.key], str(keys))

    print('4. the catalog hooks and TumbleTrove\'s popover')
    from tumblepipe.asset_browser import farm_jobs
    from tumblepipe.asset_browser.catalog import PipelineCatalog

    opened: list[str] = []
    scene = SimpleNamespace(open_farm_manager=lambda refresh_cb=None, select='': opened.append(select))
    host = SimpleNamespace(_farm_activity=watcher, _scene=scene)
    check('the hook gives the dot', PipelineCatalog.get_quick_action_dot(host, 'farm_manager') == 'danger')
    check('no dot on another action', PipelineCatalog.get_quick_action_dot(host, 'save') == '')
    model = PipelineCatalog.get_quick_action_popover(host, 'farm_manager')
    if farm_jobs.popover_api() is None:
        check('an older TumbleTrove gets no popover (the click opens the Farm Manager)', model is None)
    else:
        from tumbletrove.asset_browser.ui.quick_action_popover import QuickActionPopoverPanel
        check('the popover is titled', model is not None and model.title == finished.POPOVER_TITLE)
        check('it lists both batches, the failure first', model is not None
              and [r.id for r in model.rows] == keys, str(model and [r.id for r in model.rows]))
        clicked: list[tuple[str, str]] = []

        def on_button(row_id: str, button_id: str) -> None:
            clicked.append((row_id, button_id))
            PipelineCatalog.execute_quick_action_popover_button(host, 'farm_manager', row_id, button_id)

        panel = QuickActionPopoverPanel(model, lambda: None, on_button, lambda: None)
        panel.show()
        pump(app, 0.2)
        show = panel._buttons.get((failed.key, finished.SHOW)) if failed else None
        check('the failed row has Show in Jobs', show is not None and show.text() == finished.SHOW_LABEL,
              str(sorted(panel._buttons)))
        if show is not None:
            show.click()
            pump(app, 0.2)
        check('Show in Jobs opens the Farm Manager at the failed job',
              failed is not None and opened == [failed.select_id], f'{clicked} {opened}')
        import shiboken6
        check('a button click closes the popover', not shiboken6.isValid(panel) or not panel.isVisible())
        if shiboken6.isValid(panel):
            panel.close()
            pump(app, 0.1)
    PipelineCatalog.quick_action_popover_opened(host, 'farm_manager')
    check('opening the popover clears the dot', PipelineCatalog.get_quick_action_dot(host, 'farm_manager') == '')

    print('5. the announcement')
    statuses, notified, pulses = [], [], []
    sent = threading.Event()

    def notify(title: str, body: str) -> None:
        notified.append((title, body))
        sent.set()

    catalog = SimpleNamespace(request_quick_action_pulse=pulses.append)
    said = farm_jobs.announce(catalog, ended, in_front=lambda: False, notify=notify,
                              status=lambda text, bad: statuses.append((text, bad)))
    sent.wait(2.0)
    check('one status line, a warning for the failure', len(statuses) == 1 and statuses[0][1], str(statuses))
    check('the OS hears it when Houdini is not in front', notified == [(said.title, said.line)], str(notified))
    check('one pulse is asked for', pulses == ['farm_manager'], str(pulses))
    notified.clear()
    farm_jobs.announce(SimpleNamespace(), ended, in_front=lambda: True, notify=notify,
                       status=lambda text, bad: None)
    time.sleep(0.2)
    check('no OS notification while Houdini is in front, no pulse without the hook', notified == [])

    print('6. the Farm Manager selects the job')
    from tumblepipe.farm.manager.window import ManagerWindow
    win = ManagerWindow(None)
    win.show()
    win._hide_finished.setChecked(True)
    win._refresh_local()
    pump(app, 1.0)
    win.select_job(failed.select_id if failed else '')
    pump(app, 0.3)
    check('select_job selects a finished job Hide finished hid',
          failed is not None and win.selected_job_id() == failed.select_id, win.selected_job_id())
    win.select_job('local:not-read-yet/0')
    check('an unknown job is held until a read brings it in',
          win._pending_select == 'local:not-read-yet/0'
          and win.selected_job_id() == (failed.select_id if failed else ''))
    win.close()
    pump(app, 0.2)

    failures = [name for name, ok, _ in _checks if not ok]
    print(f"\n{len(_checks) - len(failures)}/{len(_checks)} checks passed")
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        code = main()
    finally:
        shutil.rmtree(TEMP, ignore_errors=True)
    sys.exit(code)
