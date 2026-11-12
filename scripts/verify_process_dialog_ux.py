"""Headless QTest harness for the ProcessDialog execution UX.

Drives a real ProcessDialog + ProcessExecutor with fake tasks — no Houdini
required (the process UI stack is pure PySide6).

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

...and that a task with nothing to do skips instead of failing (tumblepipe__rfe
2026-07-20, the disconnected render-channel export node):

  6. a child raising TaskSkipped is SKIPPED, its siblings still run, and the
     skip is surfaced in a warning — while a user-unchecked task stays quiet

...and that the footer reads unambiguously before and after a run
(tumblepipe__bugs 2026-09-16, "this window is still confusing"):

...and that the dialog says what its marks mean (#36):

  8. the version column is headed "Current" and a group row shows its
     children's versions; on This machine a finished publish row gets the
     queued mark, not the tick a validation (run here) gets; and the
     validation prompt's "Apply this choice to remaining validations"
     starts ticked

...and that a run validates every shot before it exports any (#56):

  9. every ticked validation runs up front, and one dialog lists the issues
     grouped by shot then department, each shot ticked, with Continue
     Anyway and Cancel Export (the default); an unticked shot exports
     nothing and its rows untick, the kept shots' issues are accepted for
     the run, Cancel Export starts nothing, and a clean pass shows no
     dialog. PREFLIGHT_SCREENSHOT=<png> grabs the dialog to an image.

  7. Execute is a fully styled (bordered, filled) button, so Houdini's style
     paints it rather than dropping it to bare text; a clean run collapses
     the footer to one green Done button that accepts the dialog, while a
     failed or cancelled run keeps Execute for a retry and offers Close

Run:
    cd scripts
    uv run --python 3.12 --with pyside6 python verify_process_dialog_ux.py
"""

from pathlib import Path
import os
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))

from PySide6 import QtWidgets

from tumblepipe.util.errors import TaskSkipped
from tumblepipe.util.progress import report_progress
from tumblepipe.pipe.houdini.ui.process_task import ProcessTask, TaskStatus
from tumblepipe.pipe.houdini.ui.process_dialog import ProcessDialog
from tumblepipe.util.uri import Uri

SHOT_URI = Uri.parse('entity:/shots/000/sh020_Clash')

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


def check_up_front_validation():
    """Case 9 (#56): every ticked validation runs before anything exports,
    and one dialog, grouped by shot and department, asks about them all."""
    import uuid
    from tumblepipe.pipe.houdini.validators.base import ValidationIssue, ValidationSeverity
    from tumblepipe.pipe.houdini.ui import validation_dialog, validation_preflight

    shots = [Uri.parse('entity:/shots/000/sh010'), Uri.parse('entity:/shots/000/sh020'),
             Uri.parse('entity:/shots/000/sh030')]
    error = ValidationIssue('No render camera', '/cameras', ValidationSeverity.ERROR)
    warning = ValidationIssue('Mesh missing rest', '/geo/hero', ValidationSeverity.WARNING)
    other = ValidationIssue('Unbound material', '/geo/rock', ValidationSeverity.ERROR)
    issues_of = {str(shots[0]): [error, warning], str(shots[1]): [other], str(shots[2]): []}

    def build(log):
        tasks = []
        for shot in shots:
            for department in ('layout', 'lighting'):
                key = str(shot)
                validate = ProcessTask(
                    id=str(uuid.uuid4()), uri=shot, department=department,
                    task_type='validate', description=f'Validate ({department})',
                    execute_local=lambda s=key, d=department: log.append(('validate', s, d)),
                    collect_issues=(lambda s=key, d=department:
                                    (log.append(('collect', s, d)) or
                                     (issues_of[s] if d == 'lighting' else []))),
                )
                export = ProcessTask(
                    id=str(uuid.uuid4()), uri=shot, department=department,
                    task_type='export', description=f'Export ({department})',
                    execute_local=lambda s=key, d=department: log.append(('export', s, d)),
                    depends_on=[validate.id],
                )
                tasks += [validate, export]
        return tasks

    grouped = validation_preflight.group_findings(
        validation_preflight.collect_findings(build([]), True))
    dialog = validation_dialog.PreflightValidationDialog(grouped)
    tree = dialog._tree
    check('up front: one row per shot with issues, the clean shot left out',
          tree.topLevelItemCount() == 2, str(tree.topLevelItemCount()))
    first = tree.topLevelItem(0)
    check('up front: a shot row names the shot and its issue count, ticked',
          first.text(0).startswith('000/sh010') and '2 issues' in first.text(0)
          and first.checkState(0) == validation_dialog.Qt.Checked, first.text(0))
    check('up front: under the shot, its department, then its issues',
          first.childCount() == 1 and first.child(0).text(0) == 'lighting'
          and first.child(0).childCount() == 2
          and 'No render camera' in first.child(0).child(0).text(0)
          and first.child(0).child(1).text(0).startswith('[WARNING]'),
          first.child(0).text(0))
    check('up front: the header counts issues and shots',
          dialog._header.text().startswith('Validation found 3 issues in 2 shots'),
          dialog._header.text())
    check('up front: Continue Anyway and Cancel Export, Cancel the default',
          dialog._continue_btn.text() == 'Continue Anyway'
          and dialog._cancel_btn.text() == 'Cancel Export'
          and dialog._cancel_btn.isDefault())
    dialog.set_entity_kept(str(shots[1]), False)
    check('up front: an unticked shot leaves the kept list',
          dialog.kept_entities() == [str(shots[0])], str(dialog.kept_entities()))
    shot_path = os.environ.get('PREFLIGHT_SCREENSHOT')
    if shot_path:
        dialog.resize(760, 480)
        dialog.grab().save(shot_path)

    def run(answer):
        """A real ProcessDialog run with the up-front dialog answered by
        ``answer(dialog)``; returns (process dialog, log, shown)."""
        log, shown = [], []
        saved = validation_dialog.PreflightValidationDialog.exec

        def fake_exec(self):
            shown.append(len([e for e in log if e[0] == 'export']))
            answer(self)
            return 0
        validation_dialog.PreflightValidationDialog.exec = fake_exec
        try:
            process = ProcessDialog('Publish', build(log))
            done = []
            process._executor.all_completed.connect(lambda: done.append(True))
            process._on_execute_clicked()
            deadline = time.monotonic() + 10.0
            while process._is_executing and not done and time.monotonic() < deadline:
                QtWidgets.QApplication.processEvents()
                time.sleep(0.01)
        finally:
            validation_dialog.PreflightValidationDialog.exec = saved
        return process, log, shown

    def skip_second(d):
        d.set_entity_kept(str(shots[1]), False)
        d._on_continue()

    process, log, shown = run(skip_second)
    exported = {(s, d) for kind, s, d in log if kind == 'export'}
    check('run: the up-front dialog showed once, before any export',
          shown == [0], str(shown))
    check('run: every validation ran up front before the first export',
          [e for e in log[:6] if e[0] == 'collect'] == log[:6], str(log[:7]))
    check('run: the unticked shot exported nothing, the others everything',
          exported == {(str(s), d) for s in (shots[0], shots[2]) for d in ('layout', 'lighting')},
          str(sorted(exported)))
    check("run: the unticked shot's rows are unticked",
          not any(t.enabled for t in process._model.get_tasks() if t.uri == shots[1]))
    check('run: validations still ran in place for the kept shots',
          ('validate', str(shots[0]), 'lighting') in log
          and ('validate', str(shots[1]), 'lighting') not in log)
    from tumblepipe.pipe.houdini.ui.process_executor import _validation_session
    check('run: the kept shot\'s issues were accepted for the run, the skipped one\'s not',
          any(a[0] == str(shots[0]) for a in _validation_session.accepted)
          and not any(a[0] == str(shots[1]) for a in _validation_session.accepted))

    process, log, shown = run(lambda d: d._on_cancel())
    check('cancel: nothing exported, the run never started',
          not [e for e in log if e[0] in ('export', 'validate')]
          and not process._is_executing and shown == [0], str(log))
    check('cancel: the status says so and Execute stays available',
          'cancelled at validation' in process._status_label.text()
          and process._execute_button.isEnabled(), process._status_label.text())

    issues_of[str(shots[0])], issues_of[str(shots[1])] = [], []
    process, log, shown = run(lambda d: d._on_cancel())
    check('clean: no issues, no dialog, the run goes ahead',
          shown == [] and len([e for e in log if e[0] == 'export']) == 6, str(shown))


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
        "the group row shows its child's version, kept current",
        version_cell(grouped_dialog, group) == 'v0003',
        version_cell(grouped_dialog, group),
    )

    # --- Case 6: a skipped child must not take its siblings down -----------
    # A disconnected export node has nothing to publish. It raises TaskSkipped
    # rather than crashing on a None stage, and unlike a real failure that
    # must not abort the rest of the department (tumblepipe__rfe 2026-07-20).
    warnings.clear()
    ran = []

    def disconnected_body():
        raise TaskSkipped(
            "Nothing exported for channel 'fx': the node /stage/export1 "
            "has no stage input connected."
        )

    skipped_child = make_task('Export (fx)', 'render', disconnected_body)
    sibling = make_task('Export (chars)', 'render', lambda: ran.append('chars') or 'v0005')
    skip_group = make_task(
        'Export (render)', 'render', None,
        children=[skipped_child, sibling], task_type='export_group',
    )
    skip_dialog = run_dialog([skip_group])

    check(
        'disconnected child is SKIPPED, not FAILED',
        skipped_child.status == TaskStatus.SKIPPED,
        str(skipped_child.status),
    )
    check(
        'the sibling export still ran',
        ran == ['chars'] and sibling.status == TaskStatus.COMPLETED,
        f'{ran} / {sibling.status}',
    )
    check(
        'the group as a whole still completes',
        skip_group.status == TaskStatus.COMPLETED,
        str(skip_group.status),
    )
    check(
        'the skip reason is kept on the task',
        'no stage input connected' in (skipped_child.error_message or ''),
        repr(skipped_child.error_message),
    )
    check(
        'the skip is reported, not swallowed',
        len(warnings) == 1 and 'no stage input connected' in warnings[0][1],
        repr(warnings),
    )
    check(
        'no error report — nothing actually failed',
        skip_dialog._status_label.text().startswith('All tasks completed'),
        skip_dialog._status_label.text(),
    )

    # A user-unchecked task is SKIPPED too, but on purpose — it must stay out
    # of the warning, or every partial publish nags.
    warnings.clear()
    unchecked = make_task('Export (fx)', 'render', lambda: None)
    unchecked.enabled = False
    kept = make_task('Export (chars)', 'render', lambda: 'v0006')
    run_dialog([unchecked, kept])
    check(
        'a deliberately unchecked task raises no warning',
        not warnings,
        repr(warnings),
    )

    # --- Case 7: footer states -----------------------------------------------
    # Before a run: Execute is the call to action and Cancel is Cancel.
    fresh = ProcessDialog('Publish', [make_task('Export (chars)', 'render', lambda: 'v0002')])
    execute_style = fresh._execute_button.styleSheet()
    check(
        'Execute button carries a full stylesheet (border + background)',
        'border:' in execute_style and 'background-color:' in execute_style and 'padding:' in execute_style,
        execute_style,
    )
    check('Execute is the default button', fresh._execute_button.isDefault())
    check(
        'before a run the footer offers Execute and Cancel',
        not fresh._execute_button.isHidden() and fresh._cancel_button.text() == 'Cancel',
        fresh._cancel_button.text(),
    )

    # A clean run: only a green Done remains, and it accepts the dialog.
    clean = run_dialog([make_task('Export (chars)', 'render', lambda: 'v0002')])
    check(
        'clean run hides Execute and the selection buttons',
        clean._execute_button.isHidden()
        and clean._select_all_button.isHidden()
        and clean._select_none_button.isHidden(),
    )
    check('clean run turns Cancel into Done', clean._cancel_button.text() == 'Done', clean._cancel_button.text())
    check('Done is the default button', clean._cancel_button.isDefault())
    check(
        'Done is styled green',
        '#3c9a4e' in clean._cancel_button.styleSheet() and 'border:' in clean._cancel_button.styleSheet(),
        clean._cancel_button.styleSheet(),
    )
    check(
        'clean run locks the execution mode',
        not clean._local_radio.isEnabled()
        and not clean._machine_radio.isEnabled()
        and not clean._farm_radio.isEnabled(),
    )
    # Ticking a row afterwards must not overwrite the result line with
    # "Ready to execute" — there is no Execute button to honour it.
    clean._model.itemChanged.emit(clean._model.item(0))
    check(
        'the result line survives later tree edits',
        clean._status_label.text().startswith('All tasks completed'),
        clean._status_label.text(),
    )
    clean._on_cancel_clicked()
    check('Done accepts the dialog', clean.result() == QtWidgets.QDialog.Accepted, str(clean.result()))

    # A cancelled run keeps the retry path: Execute stays, Cancel reads Close.
    check(
        'cancelled run keeps Execute for a retry',
        not cancel_dialog._execute_button.isHidden() and cancel_dialog._execute_button.isEnabled(),
    )
    check('cancelled run offers Close, not Done', cancel_dialog._cancel_button.text() == 'Close', cancel_dialog._cancel_button.text())
    check('cancelled run keeps the execution mode editable', cancel_dialog._local_radio.isEnabled())

    # A failed run likewise: the error report is captured instead of shown.
    reports = []
    ProcessDialog._show_error_report = lambda self, failed_tasks: reports.append(failed_tasks)

    def failing_body():
        raise RuntimeError('boom')

    failing = make_task('Export (chars)', 'render', failing_body)
    failed_dialog = run_dialog([failing])
    check('failed run shows the error report', len(reports) == 1 and reports[0] == [failing], repr(reports))
    check(
        'failed run keeps Execute for a retry',
        not failed_dialog._execute_button.isHidden() and failed_dialog._execute_button.isEnabled(),
    )
    check('failed run offers Close, not Done', failed_dialog._cancel_button.text() == 'Close', failed_dialog._cancel_button.text())
    failed_dialog._on_cancel_clicked()
    check('Close after a failure rejects the dialog', failed_dialog.result() == QtWidgets.QDialog.Rejected, str(failed_dialog.result()))

    # --- Case 8: what the marks mean (#36) --------------------------------
    from tumblepipe.farm import backend as farm_backend
    from tumblepipe.pipe.houdini.ui import farm_submit, process_task
    from tumblepipe.pipe.houdini.ui import validation_dialog

    model = clean._model
    check('the version column is headed Current',
          model.horizontalHeaderItem(model.COLUMN_VERSION).text() == 'Current'
          and bool(model.horizontalHeaderItem(model.COLUMN_VERSION).toolTip()))
    before = make_task('Export (chars)', 'model', lambda: None, current_version='v0004')
    group_before = make_task('Export (model)', 'model', None, children=[before], task_type='export_group')
    unrun = ProcessDialog('Publish', [group_before])
    check("a collapsed group row shows its children's version before the run",
          version_cell(unrun, group_before) == 'v0004', version_cell(unrun, group_before))

    def status_cell(dialog, task):
        item = dialog._model._task_items[task.id]
        return item.parent().child(item.row(), dialog._model.COLUMN_STATUS)

    saved = farm_submit.check_backend, farm_submit.remember_backend
    farm_submit.check_backend = lambda backend, title: True
    farm_submit.remember_backend = lambda backend: None
    try:
        validate = make_task('Validate', 'model', None, task_type='validate')
        validate.execute_farm = lambda: None
        publish = make_task('Export (model)', 'model', None)
        publish.execute_farm = lambda: None
        queued_dialog = ProcessDialog('Publish', [validate, publish])
        queued_dialog._machine_radio.setChecked(True)
        done3 = []
        queued_dialog._executor.all_completed.connect(lambda: done3.append(True))
        queued_dialog._on_execute_clicked()
        deadline = time.monotonic() + 10.0
        while not done3 and time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)
    finally:
        farm_submit.check_backend, farm_submit.remember_backend = saved
    check('the queued run finished', bool(done3) and queued_dialog._backend() == farm_backend.LOCAL)
    check('a publish queued on this machine gets the queued mark, with a tooltip',
          status_cell(queued_dialog, publish).text() == process_task.QUEUED_MARK
          and 'Farm Manager' in status_cell(queued_dialog, publish).toolTip(),
          status_cell(queued_dialog, publish).text())
    check('the validation, run here, keeps the tick',
          status_cell(queued_dialog, validate).text()
          == queued_dialog._model.STATUS_DISPLAY[TaskStatus.COMPLETED],
          status_cell(queued_dialog, validate).text())
    check('an in-session publish keeps the tick',
          status_cell(clean, clean._model.get_tasks()[0]).text()
          == clean._model.STATUS_DISPLAY[TaskStatus.COMPLETED])
    queued_dialog._model.reset_all_status()
    check('a reset clears the queued mark and its tooltip',
          status_cell(queued_dialog, publish).toolTip() == ''
          and status_cell(queued_dialog, publish).text()
          == queued_dialog._model.STATUS_DISPLAY[TaskStatus.PENDING])

    prompt = validation_dialog.ValidationConfirmDialog(
        validation_message='[ERROR] /x: bad', department='model', entity_name='hero')
    check('"Apply this choice to remaining validations" starts ticked',
          prompt._remember_checkbox.isChecked())
    prompt._on_cancel()
    check('and the choice is then remembered', prompt.remember_choice is True)

    check_up_front_validation()

    failed = [name for name, ok, _ in _checks if not ok]
    print()
    print(f"{len(_checks) - len(failed)}/{len(_checks)} checks passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
