"""Headless QTest harness for the ProcessDialog execution UX.

Drives a real ProcessDialog + ProcessExecutor with fake tasks — no Houdini
required (the process UI stack is pure qtpy).

Pins down the fixes for the stuck-looking "Running: Export (render)" publish
(tumblepipe__bugs 2026-07-08):

  1. the status label names the running child task, not just its group
  2. report_progress() breadcrumbs from task code reach the status label
  3. cancelling with enabled steps still pending warns that the result is
     incomplete (and mentions staged builds when Build USD is among them)
  4. reset_all_status() also resets child task statuses

...and that the Version column reports the version each export wrote, rather
than the one it superseded:

  5. a finished export shows the version it wrote (and a task that reports no
     version — validation, farm submission — keeps whatever was on disk)

Run:
    cd scripts
    uv run --python 3.12 --with pyside6 --with qtpy python verify_process_dialog_ux.py
"""

from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))

from qtpy import QtWidgets

from tumblepipe.util.progress import report_progress
from tumblepipe.pipe.houdini.ui.process_task import ProcessTask, TaskStatus
from tumblepipe.pipe.houdini.ui.process_dialog import ProcessDialog
from tumblepipe.util.uri import Uri

SHOT_URI = Uri.parse_unsafe('entity:/shots/000/sh020_Clash')

_checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = ''):
    _checks.append((name, condition, detail))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not condition else ''))


def make_task(
    description: str,
    department: str,
    execute_local,
    children=None,
    task_type='export',
    current_version=None,
) -> ProcessTask:
    import uuid
    return ProcessTask(
        id=str(uuid.uuid4()),
        uri=SHOT_URI,
        department=department,
        task_type=task_type,
        description=description,
        execute_local=execute_local,
        children=children,
        current_version=current_version,
    )


def version_cell(dialog: ProcessDialog, task: ProcessTask) -> str:
    """Read the text shown in a task row's Version column."""
    model = dialog._model
    item = model._task_items[task.id]
    return item.parent().child(item.row(), model.COLUMN_VERSION).text()


def run_dialog(tasks: list[ProcessTask]) -> ProcessDialog:
    """Execute the tasks through a real dialog, pumping the event loop."""
    dialog = ProcessDialog('Publish', tasks)
    done = []
    dialog._executor.all_completed.connect(lambda: done.append(True))
    dialog._on_execute_clicked()
    deadline = time.monotonic() + 10.0
    while not done and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    if not done:
        raise RuntimeError('executor did not complete within 10s')
    return dialog


def main() -> int:
    # Keep a reference for the harness' lifetime (F841-exempt dummy name)
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    # Capture QMessageBox.warning calls (cancel-incomplete warning)
    warnings = []
    QtWidgets.QMessageBox.warning = staticmethod(
        lambda parent, title, text, *a, **k: warnings.append((title, text))
    )

    # --- Case 1 + 2: child label and progress breadcrumbs -----------------
    label_during_child = []
    label_during_progress = []

    dialog_holder = []

    def child_body():
        label_during_child.append(dialog_holder[0]._status_label.text())
        report_progress('cooking frames 1080-1244')
        label_during_progress.append(dialog_holder[0]._status_label.text())

    child = make_task('Export (chars)', 'render', child_body)
    parent = make_task('Export (render)', 'render', None, children=[child], task_type='export_group')
    dialog_holder.append(ProcessDialog('Publish', [parent]))
    dialog = dialog_holder[0]
    done = []
    dialog._executor.all_completed.connect(lambda: done.append(True))
    dialog._on_execute_clicked()
    deadline = time.monotonic() + 10.0
    while not done and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    check(
        'status label names the running child',
        label_during_child == ['Running: Export (render) — Export (chars)'],
        repr(label_during_child),
    )
    check(
        'report_progress breadcrumb reaches the label',
        label_during_progress == ['Running: Export (render) — Export (chars): cooking frames 1080-1244'],
        repr(label_during_progress),
    )
    check('run completed', bool(done))
    check('no spurious warning on a clean run', not warnings, repr(warnings))

    # --- Case 4: reset_all_status resets children -------------------------
    check('child completed after run', child.status == TaskStatus.COMPLETED, str(child.status))
    dialog._model.reset_all_status()
    check('reset_all_status resets child status', child.status == TaskStatus.PENDING, str(child.status))

    # --- Case 3: cancel leaves Build USD unrun → warning ------------------
    warnings.clear()
    cancel_holder = []

    def cancelling_body():
        cancel_holder[0]._on_cancel_clicked()

    export_task = make_task('Export (render)', 'render', cancelling_body)
    build_task = make_task('Build USD', 'staged', lambda: None, task_type='build_group')
    cancel_holder.append(ProcessDialog('Publish', [export_task, build_task]))
    cancel_dialog = cancel_holder[0]
    done2 = []
    cancel_dialog._executor.all_completed.connect(lambda: done2.append(True))
    cancel_dialog._on_execute_clicked()
    deadline = time.monotonic() + 10.0
    while not done2 and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    check('cancelled run completed', bool(done2))
    check('export completed before cancel took effect', export_task.status == TaskStatus.COMPLETED, str(export_task.status))
    check('build task left pending', build_task.status == TaskStatus.PENDING, str(build_task.status))
    check('cancel-incomplete warning shown', len(warnings) == 1, repr(warnings))
    if warnings:
        title, text = warnings[0]
        check('warning lists the unrun step', 'Build USD' in text, text)
        check('warning mentions staged files', 'Staged files were not built' in text, text)
        check('warning title uses dialog title', title == 'Publish incomplete', title)
    check(
        'status label reports the cancel',
        cancel_dialog._status_label.text() == 'Cancelled: 1 completed, 1 step(s) did not run',
        cancel_dialog._status_label.text(),
    )

    # --- Case 5: Version column shows the version the export wrote ---------
    # The column is seeded with the version already on disk (the one about to
    # be superseded). Once the export reports the version it wrote, the row
    # must show that instead — otherwise a finished v0008 publish still reads
    # v0007.
    exporting = make_task('Export (chars)', 'render', lambda: 'v0008', current_version='v0007')
    first_ever = make_task('Export (props)', 'render', lambda: 'v0001')
    validating = make_task('Validate', 'render', lambda: None, task_type='validation', current_version='v0007')
    version_dialog = run_dialog([exporting, first_ever, validating])

    check(
        'exported version replaces the superseded one in the column',
        version_cell(version_dialog, exporting) == 'v0008',
        version_cell(version_dialog, exporting),
    )
    check(
        'task carries the version it wrote',
        exporting.exported_version == 'v0008' and exporting.current_version == 'v0008',
        f'{exporting.exported_version} / {exporting.current_version}',
    )
    check(
        "first-ever export replaces the '-' placeholder",
        version_cell(version_dialog, first_ever) == 'v0001',
        version_cell(version_dialog, first_ever),
    )
    check(
        'a task that reports no version leaves its column untouched',
        version_cell(version_dialog, validating) == 'v0007' and validating.exported_version is None,
        version_cell(version_dialog, validating),
    )

    # A grouped export: children report versions, the group row itself does not.
    grouped_child = make_task('Export (chars)', 'render', lambda: 'v0003', current_version='v0002')
    group = make_task('Export (render)', 'render', None, children=[grouped_child], task_type='export_group')
    grouped_dialog = run_dialog([group])
    check(
        'grouped child export updates its version column',
        version_cell(grouped_dialog, grouped_child) == 'v0003',
        version_cell(grouped_dialog, grouped_child),
    )
    check(
        'group row itself reports no version',
        version_cell(grouped_dialog, group) == '-',
        version_cell(grouped_dialog, group),
    )

    failed = [name for name, ok, _ in _checks if not ok]
    print()
    print(f"{len(_checks) - len(failed)}/{len(_checks)} checks passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
