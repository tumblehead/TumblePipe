"""Headless harness for the publish dialog's Run on modes (``pipe.houdini.ui.process_dialog``).

Drives a real ProcessDialog and ProcessExecutor over made-up tasks whose
farm callbacks record ``$TH_FARM_BACKEND`` instead of submitting. Checks:

  1. the three modes: In this session / This machine / Render farm; farm-only
     tasks open on the farm the artist chose last (the shared pref)
  2. moving between the two farms keeps the artist's ticks
  3. Execute on This machine submits every farm task, children too, under
     ``TH_FARM_BACKEND=local``, restores the variable after, remembers the
     choice, and shows the Farm Manager button
  4. Execute on Render farm without a Deadline Client says why and submits
     nothing
  5. In this session runs the local callbacks with the variable untouched

Run (no Houdini licence: Houdini's bundled Python, not hython; see
``headless-qt-verify`` in the repo notes):

    QT_QPA_PLATFORM=offscreen "$HFS/python313/python.exe" scripts/verify_process_run_on.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import types
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))

TEMP = Path(tempfile.mkdtemp(prefix='process-run-on-verify-'))
os.environ['HOUDINI_USER_PREF_DIR'] = str(TEMP / 'prefs')
os.environ.pop('TH_FARM_BACKEND', None)
os.environ.pop('DEADLINE_PATH', None)

# hou.ui.displayMessage is the only hou the dialog reaches (the Deadline
# warning); record it instead.
_messages: list[str] = []
# The button each displayMessage "clicks", in order; 0 once they run out.
_answers: list[int] = []
_defaults: list[int | None] = []


def _display_message(text, **kw):
    _messages.append(text)
    _defaults.append(kw.get('default_choice'))
    return _answers.pop(0) if _answers else 0


_hou = types.ModuleType('hou')
_hou.severityType = types.SimpleNamespace(Warning='warning', Error='error', Message='message')
_hou.ui = types.SimpleNamespace(displayMessage=_display_message)
sys.modules['hou'] = _hou

from PySide6.QtWidgets import QApplication  # noqa: E402

from tumblepipe.asset_browser import prefs  # noqa: E402
from tumblepipe.farm import backend as farm_backend  # noqa: E402
from tumblepipe.pipe.houdini.ui.process_dialog import ProcessDialog  # noqa: E402
from tumblepipe.pipe.houdini.ui.process_task import ProcessTask, TaskStatus  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = '') -> None:
    _checks.append((name, bool(condition), detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f'  ({detail})' if detail and not condition else ''))


def pump(app: QApplication, dialog: ProcessDialog, seconds: float = 10.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if not dialog._is_executing:
            return
        time.sleep(0.02)


def save_pref(backend: str) -> None:
    loaded = prefs.load_prefs()
    loaded.farm_backend = backend
    prefs.save_prefs(loaded)


def make_tasks(seen: list[tuple[str, str | None]], with_local: bool) -> list[ProcessTask]:
    def recorder(name):
        return lambda: seen.append((name, os.environ.get(farm_backend.BACKEND_ENV)))

    uri = Uri.parse('entity:/shots/SEQ010/SH0010')
    children = [
        ProcessTask(
            id=str(uuid.uuid4()), uri=uri, department='anim', task_type='export',
            description=f'Export ({channel})', channel=channel,
            execute_local=recorder(f'local {channel}') if with_local else None,
            execute_farm=recorder(f'farm {channel}'),
        )
        for channel in ('default', 'chars')
    ]
    group = ProcessTask(
        id=str(uuid.uuid4()), uri=uri, department='anim', task_type='export_group',
        description='Export (anim)', children=children,
        execute_local=(lambda: None) if with_local else None,
        execute_farm=lambda: None,
    )
    for child in children:
        child.parent_id = group.id
    build = ProcessTask(
        id=str(uuid.uuid4()), uri=uri, department='anim', task_type='build',
        description='Build', channel='default',
        execute_local=recorder('local build') if with_local else None,
        execute_farm=recorder('farm build'),
    )
    return [group, build]


def main() -> int:
    app = QApplication.instance() or QApplication([])

    # 1. the modes, and the shared pref picking the farm
    save_pref('local')
    seen: list = []
    farm_only = ProcessDialog('Publish', make_tasks(seen, with_local=False), current_department='anim')
    labels = [farm_only._local_radio.text(), farm_only._machine_radio.text(), farm_only._farm_radio.text()]
    check('three modes', labels == ['In this session', 'This machine', 'Render farm'], str(labels))
    check('farm-only tasks hide In this session', farm_only._local_radio.isHidden() and not farm_only._mode_group.isHidden())
    check('and open on the remembered farm (This machine)', farm_only._machine_radio.isChecked())

    save_pref('farm')
    both = ProcessDialog('Publish', make_tasks(seen, with_local=True), current_department='anim')
    check('with local tasks it opens In this session', both._local_radio.isChecked())

    # 2. moving between the farms keeps the ticks
    both._farm_radio.setChecked(True)
    # Unticked as a click would: a refilter would tick it again.
    from PySide6.QtCore import Qt
    build = both._model.get_tasks()[1]
    both._model._task_items[build.id].setCheckState(Qt.Unchecked)
    app.processEvents()
    before = [t.enabled for t in both._model.get_tasks()]
    both._machine_radio.setChecked(True)
    both._farm_radio.setChecked(True)
    both._machine_radio.setChecked(True)
    after = [t.enabled for t in both._model.get_tasks()]
    check('switching farms keeps the ticks', before == after, f'{before} -> {after}')
    both.close()

    # 3. This machine: every farm task under TH_FARM_BACKEND=local
    seen.clear()
    save_pref('farm')
    dialog = ProcessDialog('Publish', make_tasks(seen, with_local=True), current_department='anim')
    dialog._machine_radio.setChecked(True)
    dialog._execute_button.click()
    pump(app, dialog)
    farm_calls = [entry for entry in seen if entry[0].startswith('farm')]
    check('every farm task ran, children too', [n for n, _ in farm_calls] == ['farm default', 'farm chars', 'farm build'], str(seen))
    check('each under TH_FARM_BACKEND=local', all(v == 'local' for _n, v in farm_calls), str(farm_calls))
    check('and the variable is restored after', farm_backend.BACKEND_ENV not in os.environ)
    check('the choice is remembered', prefs.load_prefs().farm_backend == 'local')
    check('the Farm Manager button shows', not dialog._manager_button.isHidden())
    check('the tasks completed', all(t.status == TaskStatus.COMPLETED for t in dialog._model.get_tasks()),
          str([t.status for t in dialog._model.get_tasks()]))
    dialog.close()

    # 4. Render farm without Deadline: a reason, nothing submitted
    seen.clear()
    _messages.clear()
    dialog = ProcessDialog('Publish', make_tasks(seen, with_local=True), current_department='anim')
    dialog._farm_radio.setChecked(True)
    dialog._execute_button.click()
    pump(app, dialog, 1.0)
    check('no Deadline: the artist is told why', bool(_messages) and 'Deadline' in _messages[0], str(_messages))
    check('and nothing is submitted', not seen, str(seen))
    check('the choice is not remembered', prefs.load_prefs().farm_backend == 'local')
    dialog.close()

    # 5. In this session: the local callbacks, the variable untouched
    seen.clear()
    os.environ[farm_backend.BACKEND_ENV] = 'sentinel'
    dialog = ProcessDialog('Publish', make_tasks(seen, with_local=True), current_department='anim')
    dialog._local_radio.setChecked(True)
    dialog._execute_button.click()
    pump(app, dialog)
    check('In this session runs the local callbacks', seen and all(n.startswith('local') for n, _ in seen), str(seen))
    check('with the variable untouched', all(v == 'sentinel' for _n, v in seen), str(seen))
    check('and no Farm Manager button', dialog._manager_button.isHidden())
    os.environ.pop(farm_backend.BACKEND_ENV, None)
    dialog.close()

    # 6. a node's Submit button (farm_submit.submit): ask, check, submit, answer
    from tumblepipe.pipe.houdini.ui import farm_submit
    calls: list = []

    def node_submit():
        calls.append(os.environ.get(farm_backend.BACKEND_ENV))

    save_pref('farm')
    _messages.clear()
    _answers[:] = [1, 0]  # This machine, then OK
    done = farm_submit.submit('the composite', 'Submit Composite', node_submit)
    check('node submit: This machine runs it under TH_FARM_BACKEND=local', done and calls == ['local'], str(calls))
    check('and restores the variable', farm_backend.BACKEND_ENV not in os.environ)
    check('and says it is queued on this machine', 'queued on this machine' in _messages[-1], _messages[-1])
    check('and remembers the choice', prefs.load_prefs().farm_backend == 'local')

    calls.clear()
    _answers[:] = [2]  # Cancel
    check('Cancel submits nothing', not farm_submit.submit('the composite', 'x', node_submit) and not calls)
    check('the ask offered the remembered choice first', _defaults[-1] == 1, str(_defaults))

    calls.clear()
    _messages.clear()
    _answers[:] = [0]  # Render farm, no Deadline Client
    done = farm_submit.submit('the composite', 'x', node_submit)
    check('Render farm without Deadline says why and submits nothing',
          not done and not calls and 'Deadline' in _messages[-1], str(_messages))

    failed = [name for name, ok, _ in _checks if not ok]
    print(f'\n{len(_checks) - len(failed)}/{len(_checks)} checks passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
