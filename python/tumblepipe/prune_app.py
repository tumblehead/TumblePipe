"""The Prune Old Exports window, for TumbleTrove Desktop's Scripts panel.

    python -m tumblepipe.prune_app

Run by ``scripts/farm_launcher.py --app prune`` in Houdini's bundled
Python, which carries PySide6. It lists first and deletes only when asked:
**List** reads the project (``tumblepipe.prune_exports.make_plan``) and
shows what would go and what stays, and why; **Delete…** asks once more,
then deletes exactly that list and logs it. Changing the settings after a
listing needs a new listing before anything can be deleted. Reading and
deleting run on a worker thread.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tumblepipe import prune_exports
from tumblepipe.archive.roots import project_roots

_CONTEXTS = (
    ('Shots and assets', None),
    ('Shots', 'shots'),
    ('Assets', 'assets'),
)


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
        QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
        QPushButton, QSpinBox, QVBoxLayout, QWidget,
    )

    class Worker(QObject):
        finished = Signal(object)
        failed = Signal(str)

        def __init__(self, job):
            super().__init__()
            self._job = job

        def run(self):
            try:
                self.finished.emit(self._job())
            except Exception as error:  # surfaced to the user, not swallowed
                self.failed.emit(f'{type(error).__name__}: {error}')

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('TumblePipe — Prune Old Exports')
            self.resize(820, 620)
            self._thread = None
            self._worker = None
            self.plan = None
            layout = QVBoxLayout(self)
            intro = QLabel(
                'Deletes old export versions of shots and assets, keeping the newest '
                'finished ones of every export. Never deletes an unfinished version, '
                'one a current staged build uses, or one a latest workfile (.hip) pins.'
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)
            layout.addWidget(QLabel('Project folder'))
            self.project_edit = QLineEdit(os.environ.get('TH_PROJECT_PATH', ''))
            self.project_edit.textChanged.connect(self._stale)
            layout.addWidget(self.project_edit)
            row = QHBoxLayout()
            row.addWidget(QLabel('Keep the newest'))
            self.keep_spin = QSpinBox()
            self.keep_spin.setRange(1, 50)
            self.keep_spin.setValue(prune_exports.DEFAULT_KEEP)
            self.keep_spin.valueChanged.connect(self._stale)
            row.addWidget(self.keep_spin)
            row.addWidget(QLabel('finished versions of'))
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
            self.text.setPlaceholderText('List the project to see what would be deleted. Nothing is deleted until you confirm.')
            layout.addWidget(self.text, 1)
            bottom = QHBoxLayout()
            self.status = QLabel('')
            bottom.addWidget(self.status, 1)
            self.delete_button = QPushButton('Delete…')
            self.delete_button.setEnabled(False)
            self.delete_button.clicked.connect(self._delete)
            bottom.addWidget(self.delete_button)
            layout.addLayout(bottom)

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
            if not project.is_dir():
                self.status.setText('No such project folder.')
                return
            keep = self.keep_spin.value()
            context = _CONTEXTS[self.context_combo.currentIndex()][1]
            self.plan = None
            self.text.setPlainText('')
            self._busy(True, 'Reading the exports and the latest workfiles …')
            self._run(lambda: prune_exports.make_plan(project_roots(project), keep, context), self._listed)

        def _listed(self, plan):
            self.plan = plan
            self.text.setPlainText('\n'.join(prune_exports.summary(plan)))
            prunable = plan.prunable
            size = prune_exports.human(sum(v.version.size for v in prunable))
            self.delete_button.setText(f'Delete {len(prunable)} version(s), {size}…' if prunable else 'Delete…')
            self._busy(False, 'Nothing to delete.' if not prunable else 'Listed. Nothing is deleted until you confirm.')

        def _delete(self):
            plan = self.plan
            if plan is None or not plan.prunable:
                return
            prunable = plan.prunable
            size = prune_exports.human(sum(v.version.size for v in prunable))
            text = (
                f'Delete {len(prunable)} export version(s), {size}, from\n{plan.export_path}?\n\n'
                'This cannot be undone. Each folder removed is logged.'
            )
            if plan.hips_unreadable:
                text += (
                    f'\n\n{len(plan.hips_unreadable)} latest workfile(s) are .hiplc/.hipnc: '
                    'versions they pin are not protected.'
                )
            answer = QMessageBox.question(self, 'Prune Old Exports', text)
            if answer != QMessageBox.Yes:
                return
            log_path = prune_exports.log_path_for(plan)
            self._busy(True, 'Deleting …')
            self._run(lambda: (prune_exports.apply(plan, log_path), log_path), self._deleted)

        def _deleted(self, result):
            (removed, removed_bytes, errors), log_path = result
            self.plan = None
            lines = [f'Deleted {removed} version(s), {prune_exports.human(removed_bytes)}. Logged to {log_path}.']
            lines += [f'  {error}' for error in errors]
            self.text.appendPlainText('\n' + '\n'.join(lines))
            self.delete_button.setText('Delete…')
            self._busy(False, 'Done.' if not errors else f'Done, {len(errors)} folder(s) could not be deleted.')

    return Window()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog='python -m tumblepipe.prune_app').parse_args(argv)
    _add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = build_window()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
