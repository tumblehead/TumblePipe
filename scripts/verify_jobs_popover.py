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
     and the ended batches list failures first
  4. the button (the harness keeps its name from the Jobs popover a click
     opened through 1.112): the catalog gives no popover; the hover is one
     summary, the recent jobs and then what ended, the failure first; the
     right-click menu is Open Farm Manager and each ended batch's main
     action, and the failed batch's opens the Farm Manager at the failed
     job; every click on the real button in TumbleTrove's QuickActionsBar
     opens the Farm Manager and no popover, clears the dot and takes the
     ended batches out of the hover
  5. the announcement: one status line, an OS notification and a flash of
     Houdini's taskbar button only when Houdini is not in front, a pulse
     asked only of a catalog that takes one
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

    print('4. the button: its hover, menu and click')
    from tumblepipe.asset_browser import farm_jobs
    from tumblepipe.asset_browser.catalog import PipelineCatalog

    opened: list[str] = []
    scene = SimpleNamespace(open_farm_manager=lambda refresh_cb=None, select='': opened.append(select))
    host = SimpleNamespace(_farm_activity=watcher, _scene=scene)
    check('the hook gives the dot', PipelineCatalog.get_quick_action_dot(host, 'farm_manager') == 'danger')
    check('no dot on another action', PipelineCatalog.get_quick_action_dot(host, 'save') == '')
    check('the catalog gives the button no popover',
          PipelineCatalog.get_quick_action_popover(host, 'farm_manager') is None)

    # The hover: the one summary, naming what ended since the artist looked.
    hover = PipelineCatalog.get_quick_action_hover_html(host, 'farm_manager')
    check('the hover has the recent jobs once and the ended batches once',
          hover.count('Your jobs:') == 1 and hover.count(finished.HOVER_TITLE) == 1, hover)
    check('the hover names the failure before the finished batch',
          failed is not None and 0 <= hover.find('sh020') < hover.find('sh010'), hover)

    # The right-click menu: the click, then each ended batch's main action.
    menu = PipelineCatalog.get_quick_action_menu_items(host, 'farm_manager')
    labels = [label for label, _action in menu]
    check('the menu is Open Farm Manager, a rule, then both batches, the failure first',
          labels[:2] == [farm_jobs.MENU_OPEN, farm_jobs.MENU_SEPARATOR]
          and len(labels) == 2 + len(finished.menu_entries(watcher.session.reports()))
          and len({label.split(': ')[0] for label in labels[2:]}) == 2
          and failed is not None and labels[2] == f'{failed.title}: {finished.SHOW_LABEL}', str(labels))
    if len(menu) > 2:
        menu[2][1]()
    check("the failed batch's entry opens the Farm Manager at the failed job",
          failed is not None and opened == [failed.select_id], str(opened))
    check('which does not mark the batches seen',
          PipelineCatalog.get_quick_action_dot(host, 'farm_manager') == 'danger')
    opened.clear()

    # The click, through TumbleTrove's own quick actions bar where it has
    # one that asks for a popover (0.60.0): it must run the action.
    from PySide6.QtWidgets import QPushButton
    from tumbletrove.asset_browser.ui.quick_actions_bar import QuickActionEntry, QuickActionsBar
    button = QPushButton()
    wiring = dict(
        button=button, label='Farm Manager', icon=None,
        run=lambda: PipelineCatalog.execute_quick_action(host, 'farm_manager'),
        menu_items=lambda: PipelineCatalog.get_quick_action_menu_items(host, 'farm_manager'),
    )
    if hasattr(QuickActionsBar, 'activate'):
        entry = QuickActionEntry(
            **wiring, action_id='farm_manager',
            popover=lambda: PipelineCatalog.get_quick_action_popover(host, 'farm_manager'),
            popover_opened=lambda: PipelineCatalog.quick_action_popover_opened(host, 'farm_manager'),
        )
        bar = QuickActionsBar(lambda menu, items: None)
        bar.set_contents('', [entry])
        button.clicked.connect(lambda checked=False: bar.activate(entry))
        bar.resize(400, 40)
        bar.show()
        pump(app, 0.1)
        for click in (1, 2):
            button.click()
            pump(app, 0.1)
            check(f'click {click} on the button opens the Farm Manager, with no job selected',
                  opened == [''] * click, str(opened))
            check(f'click {click} opens no popover', bar.popover() is None)
        bar.stop()
        bar.close()
    else:
        # Before 0.60.0 the bar knows no popovers: the button runs the action.
        entry = QuickActionEntry(**wiring)
        entry.run()
        entry.run()
        check('a click opens the Farm Manager (a TumbleTrove without popovers)', opened == ['', ''], str(opened))
    opened.clear()
    check('opening the Farm Manager from the button clears the dot',
          PipelineCatalog.get_quick_action_dot(host, 'farm_manager') == '')
    hover = PipelineCatalog.get_quick_action_hover_html(host, 'farm_manager')
    check('and the hover is the recent jobs alone',
          hover.count('Your jobs:') == 1 and finished.HOVER_TITLE not in hover, hover)

    print('5. the announcement')
    statuses, notified, pulses, flashes = [], [], [], []
    sent = threading.Event()

    def notify(title: str, body: str, folder: str = '') -> None:
        notified.append((title, body))
        sent.set()

    catalog = SimpleNamespace(request_quick_action_pulse=pulses.append)
    said = farm_jobs.announce(catalog, ended, in_front=lambda: False, notify=notify,
                              status=lambda text, bad: statuses.append((text, bad)),
                              flash=lambda: flashes.append(1) or '')
    sent.wait(2.0)
    check('one status line, a warning for the failure', len(statuses) == 1 and statuses[0][1], str(statuses))
    check('the OS hears it when Houdini is not in front', notified == [(said.title, said.line)], str(notified))
    check('one pulse is asked for', pulses == ['farm_manager'], str(pulses))
    check("Houdini's taskbar button is flashed when it is not in front", flashes == [1], str(flashes))
    notified.clear()
    flashes.clear()
    farm_jobs.announce(SimpleNamespace(), ended, in_front=lambda: True, notify=notify,
                       status=lambda text, bad: None,
                       flash=lambda: flashes.append(1) or '')
    time.sleep(0.2)
    check('no OS notification and no flash while Houdini is in front, no pulse without the hook',
          notified == [] and flashes == [], f'{notified} {flashes}')
    # The real flash, in a Qt application whose "Houdini main window" is a
    # widget of ours: alert() is asked for it, on the main thread.
    from PySide6.QtWidgets import QWidget
    window = QWidget()
    window.show()
    alerted = []
    why = farm_jobs.flash_taskbar(reduced=lambda: False, window=lambda: window,
                                  alert=alerted.append)
    check("the taskbar flash is asked of Houdini's window on the main thread",
          why == '' and alerted == [window], f'{why!r} {alerted}')
    why = farm_jobs.flash_taskbar(reduced=lambda: False, window=lambda: window)
    pump(app, 0.1)
    check('the real QApplication.alert call runs without error', why == '', repr(why))
    check('Reduce motion keeps the flash back', farm_jobs.flash_taskbar(
        reduced=lambda: True, window=lambda: window, alert=alerted.append) != '' and alerted == [window])
    seen_threads = []
    done = threading.Event()

    def from_worker():
        farm_jobs.flash_taskbar(
            reduced=lambda: False, window=lambda: window,
            alert=lambda w: seen_threads.append(threading.current_thread()),
            hop=lambda f: (seen_threads.append('hopped'), done.set()))
    threading.Thread(target=from_worker).start()
    done.wait(2.0)
    check("a flash asked from a worker thread is handed to the main thread, not called there",
          seen_threads == ['hopped'], str(seen_threads))
    window.close()

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
    # Only mine (on whenever the user is known) and Only this project hide
    # a colleague's job and another project's: Cinedex's Show Farm Job
    # opened on nothing for a shot someone else submitted.
    win._jobs.clearSelection()
    win._current_job = None
    win._user, win._project = 'someone-else', 'OtherProject'
    win._mine.setEnabled(True)
    win._mine.setChecked(True)
    win._this_project.setEnabled(True)
    win._this_project.setChecked(True)
    win._sync_jobs()  # both were already ticked: no toggle, so re-read the filters
    pump(app, 0.3)
    hidden = failed is not None and failed.select_id not in win._job_items
    win.select_job(failed.select_id if failed else '')
    pump(app, 0.3)
    check('select_job selects a job Only mine and Only this project hid',
          hidden and win.selected_job_id() == failed.select_id
          and not win._mine.isChecked() and not win._this_project.isChecked(),
          f'hidden={hidden} selected={win.selected_job_id()!r}')
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
