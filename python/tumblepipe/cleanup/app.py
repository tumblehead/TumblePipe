"""The Clean Up Project window, for TumbleTrove Desktop's Scripts panel.

    python -m tumblepipe.cleanup.app

Run by ``scripts/farm_launcher.py --app cleanup`` in Houdini's bundled
Python, which carries PySide6. One row per category (``tumblepipe.cleanup``):
tick what to look at and set what it keeps. It lists first and deletes only
when asked: **List** reads the project and shows, per category, what would
go and what stays, and why; **Delete…** asks once more, then deletes exactly
that list and logs it. Changing anything after a listing needs a new listing
before anything can be deleted. Reading and deleting run on a worker thread.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tumblepipe.archive.roots import project_roots
from tumblepipe.cleanup import core

_CONTEXTS = (
    ('Shots and assets', None),
    ('Shots', 'shots'),
    ('Assets', 'assets'),
)

#: Per category: the settings its row shows, as (Settings field, text
#: before, text after, minimum, maximum).
_FIELDS = {
    'exports': (('keep_exports', 'keep the newest', 'finished versions', 1, 50),),
    'renders': (
        ('keep_renders', 'keep the newest', 'complete versions;', 1, 50),
        ('check_render_days', 'check renders older than', 'days', 0, 3650),
    ),
    'caches': (('keep_caches', 'keep the newest', 'finished versions', 1, 50),),
    'backups': (
        ('keep_backups', 'keep the newest', "of the latest version's;", 0, 100),
        ('backup_days', 'none newer than', 'days', 0, 3650),
    ),
    'jobs': (
        ('job_days', 'not named by Deadline for', 'days;', 0, 3650),
        ('local_job_days', 'local farm:', 'days', 0, 3650),
    ),
    'deleted': (('deleted_days', 'deleted more than', 'days ago', 0, 3650),),
}


def _add_houdini_dll_directory() -> None:
    """Let PySide6 load: Python 3.8+ on Windows ignores PATH."""
    hfs = os.environ.get('HFS')
    if hfs and hasattr(os, 'add_dll_directory'):
        bin_dir = Path(hfs) / 'bin'
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))


def build_window():
    from PySide6.QtCore import QObject, QThread, Signal
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
        QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
    )
    from tumblepipe.util import value_ladder_qt

    class Worker(QObject):
        finished = Signal(object)
        failed = Signal(str)
        progress = Signal(str)

        def __init__(self, job):
            super().__init__()
            self._job = job

        def run(self):
            try:
                self.finished.emit(self._job(self.progress.emit))
            except Exception as error:  # surfaced to the user, not swallowed
                self.failed.emit(f'{type(error).__name__}: {error}')

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('TumblePipe — Clean Up Project')
            self.resize(900, 720)
            self._thread = None
            self._worker = None
            self.plan = None
            defaults = core.Settings()
            layout = QVBoxLayout(self)
            intro = QLabel(
                'Deletes what the project no longer needs. Tick what to look at, then List: '
                'nothing is deleted until you have seen the list and confirmed.'
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)
            layout.addWidget(QLabel('Project folder'))
            self.project_edit = QLineEdit(os.environ.get('TH_PROJECT_PATH', ''))
            self.project_edit.textChanged.connect(self._stale)
            layout.addWidget(self.project_edit)

            grid = QGridLayout()
            self.checks: dict[str, QCheckBox] = {}
            self.spins: dict[str, QSpinBox] = {}
            for row, category in enumerate(core.categories()):
                check = QCheckBox(category.title)
                check.setChecked(True)
                check.setToolTip(category.about[:1].upper() + category.about[1:] + '.')
                check.toggled.connect(self._stale)
                self.checks[category.key] = check
                grid.addWidget(check, row, 0)
                fields = QHBoxLayout()
                for name, before, after, low, high in _FIELDS[category.key]:
                    fields.addWidget(QLabel(before))
                    spin = QSpinBox()
                    spin.setRange(low, high)
                    default = int(getattr(defaults, name))
                    spin.setValue(default)
                    spin.valueChanged.connect(self._stale)
                    value_ladder_qt.install(spin, default=default)
                    self.spins[name] = spin
                    fields.addWidget(spin)
                    fields.addWidget(QLabel(after))
                fields.addStretch()
                grid.addLayout(fields, row, 1)
            grid.setColumnStretch(1, 1)
            layout.addLayout(grid)

            row = QHBoxLayout()
            row.addWidget(QLabel('Exports, renders, caches and backups of'))
            self.context_combo = QComboBox()
            for label, _value in _CONTEXTS:
                self.context_combo.addItem(label)
            self.context_combo.currentIndexChanged.connect(self._stale)
            row.addWidget(self.context_combo)
            row.addStretch()
            self.list_button = QPushButton('List')
            self.list_button.clicked.connect(self._list)
            row.addWidget(self.list_button)
            layout.addLayout(row)

            self.text = QPlainTextEdit()
            self.text.setReadOnly(True)
            self.text.setPlaceholderText(
                'List the project to see what would be deleted. Nothing is deleted until you confirm.'
            )
            layout.addWidget(self.text, 1)
            bottom = QHBoxLayout()
            self.status = QLabel('')
            bottom.addWidget(self.status, 1)
            self.delete_button = QPushButton('Delete…')
            self.delete_button.setEnabled(False)
            self.delete_button.clicked.connect(self._delete)
            bottom.addWidget(self.delete_button)
            layout.addLayout(bottom)

        def settings(self) -> core.Settings:
            values = {name: spin.value() for name, spin in self.spins.items()}
            return core.Settings(context=_CONTEXTS[self.context_combo.currentIndex()][1], **values)

        def picked(self) -> tuple[str, ...]:
            return tuple(key for key, check in self.checks.items() if check.isChecked())

        def _stale(self, *_args):
            if self.plan is not None:
                self.plan = None
                self.delete_button.setEnabled(False)
                self.delete_button.setText('Delete…')
                self.status.setText('Settings changed: List again before deleting.')

        def _busy(self, busy: bool, message: str = ''):
            self.list_button.setEnabled(not busy)
            self.delete_button.setEnabled(not busy and bool(self.plan and self.plan.prunable))
            self.status.setText(message)

        def _run(self, job, done):
            self._thread = QThread(self)
            self._worker = Worker(job)
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.progress.connect(self.status.setText)
            self._worker.finished.connect(done)
            self._worker.failed.connect(self._failed)
            self._worker.finished.connect(self._thread.quit)
            self._worker.failed.connect(self._thread.quit)
            self._thread.start()

        def _failed(self, message: str):
            self.plan = None
            self._busy(False, message)

        def _list(self):
            project = Path(self.project_edit.text().strip())
            if not self.project_edit.text().strip() or not project.is_dir():
                self.status.setText('No such project folder.')
                return
            picked = self.picked()
            if not picked:
                self.status.setText('Tick at least one thing to look at.')
                return
            settings = self.settings()
            self.plan = None
            self.text.setPlainText('')
            self._busy(True, 'Reading the project …')
            self._run(
                lambda progress: core.make_plan(project_roots(project), settings, picked, progress=progress),
                self._listed,
            )

        def _listed(self, plan):
            self.plan = plan
            self.text.setPlainText('\n'.join(core.summary(plan)))
            prunable = plan.prunable
            size = core.human(plan.prunable_size)
            self.delete_button.setText(f'Delete {len(prunable)} item(s), {size}…' if prunable else 'Delete…')
            self._busy(False, 'Nothing to delete.' if not prunable else 'Listed. Nothing is deleted until you confirm.')

        def _delete(self):
            plan = self.plan
            if plan is None or not plan.prunable:
                return
            lines = [
                f'{category.title}: {len(category.prunable)} {category.noun}(s), {core.human(category.prunable_size)}'
                for category in plan.categories if category.prunable
            ]
            text = (
                f'Delete from {plan.roots.project_path.name}?\n\n' + '\n'.join(lines)
                + f'\n\nIn all {core.human(plan.prunable_size)}. This cannot be undone. '
                'Everything removed is logged.'
            )
            answer = QMessageBox.question(self, 'Clean Up Project', text)
            if answer != QMessageBox.Yes:
                return
            log_path = core.log_path_for(plan.roots)
            self._busy(True, 'Deleting …')
            self._run(lambda _progress: (core.apply(plan, log_path), plan, log_path), self._deleted)

        def _deleted(self, result):
            results, plan, log_path = result
            self.plan = None
            lines = []
            failed = 0
            for category in plan.categories:
                removed, removed_bytes, errors = results[category.key]
                if removed or errors:
                    lines.append(f'{category.title}: deleted {removed}, {core.human(removed_bytes)}.')
                lines += [f'  {error}' for error in errors]
                failed += len(errors)
            lines.append(f'Logged to {log_path}.')
            self.text.appendPlainText('\n' + '\n'.join(lines))
            self.delete_button.setText('Delete…')
            self._busy(False, 'Done.' if not failed else f'Done, {failed} item(s) could not be deleted.')

    return Window()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog='python -m tumblepipe.cleanup.app').parse_args(argv)
    _add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = build_window()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
