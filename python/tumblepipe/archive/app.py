"""The archive window, for TumbleTrove Desktop's Archive / Restore buttons.

    python -m tumblepipe.archive.app [--restore]

Run by ``scripts/farm_launcher.py --app archive`` (or ``--app restore``) in
Houdini's bundled Python, which carries PySide6. Two tabs over the same
library the command line uses (``python -m tumblepipe.archive``):

- **Archive** — pick a profile and options, read the project into a plan
  (sizes per kind, what is left out and why, the warnings), then write it;
- **Restore** — pick an archive, check it against its manifest, unpack it.

Reading, writing and checking run on a worker thread; Cancel stops between
files and removes anything half-written.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tumblepipe.archive import inventory as inventory_mod
from tumblepipe.archive import pack
from tumblepipe.archive.cli import human, parse_size
from tumblepipe.archive.plan import Options, Profile, make_plan
from tumblepipe.archive.roots import project_roots

_PROFILE_TEXT = {
    Profile.PROOF: 'Proof of work — every workfile version, and the latest version of every export',
    Profile.LATEST: 'Latest — the latest workfile per department, and the latest version of every export',
    Profile.FULL: 'Full — every workfile version and every finished export version',
}


def _add_houdini_dll_directory() -> None:
    """Let PySide6 load: Python 3.8+ on Windows ignores PATH."""
    hfs = os.environ.get('HFS')
    if hfs and hasattr(os, 'add_dll_directory'):
        bin_dir = Path(hfs) / 'bin'
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))


def build_window(restore: bool = False):
    from PySide6.QtCore import QObject, QThread, Signal
    from PySide6.QtWidgets import (
        QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
        QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
        QTabWidget, QVBoxLayout, QWidget,
    )

    class Worker(QObject):
        progress = Signal(int, int, str)        # permille, files done, current
        finished = Signal(object)
        failed = Signal(str)

        def __init__(self, job):
            super().__init__()
            self._job = job
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def is_cancelled(self):
            return self._cancel

        def report(self, state: pack.Progress):
            permille = int(1000 * state.bytes_done / state.bytes_total) if state.bytes_total else 1000
            self.progress.emit(permille, state.files_done, state.current)

        def run(self):
            try:
                self.finished.emit(self._job(self))
            except pack.Cancelled:
                self.failed.emit('Cancelled.')
            except Exception as error:  # surfaced to the user, not swallowed
                self.failed.emit(f'{type(error).__name__}: {error}')

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('TumblePipe — Archive')
            self.resize(820, 640)
            self._thread = None
            self._worker = None
            self._plan = None
            tabs = QTabWidget()
            tabs.addTab(self._archive_tab(), 'Archive')
            tabs.addTab(self._restore_tab(), 'Restore')
            if restore:
                tabs.setCurrentIndex(1)
            layout = QVBoxLayout(self)
            layout.addWidget(tabs)
            self.progress = QProgressBar()
            self.progress.setRange(0, 1000)
            self.status = QLabel('')
            self.cancel_button = QPushButton('Cancel')
            self.cancel_button.setEnabled(False)
            self.cancel_button.clicked.connect(self._cancel)
            row = QHBoxLayout()
            row.addWidget(self.progress, 1)
            row.addWidget(self.cancel_button)
            layout.addLayout(row)
            layout.addWidget(self.status)

        # Archive ------------------------------------------------------------

        def _archive_tab(self):
            tab = QWidget()
            layout = QVBoxLayout(tab)
            self.project_edit = QLineEdit(os.environ.get('TH_PROJECT_PATH', ''))
            layout.addWidget(QLabel('Project folder'))
            layout.addWidget(self.project_edit)
            self.profile_group = QButtonGroup(tab)
            for profile in Profile.ALL:
                button = QRadioButton(_PROFILE_TEXT[profile])
                button.setProperty('profile', profile)
                button.setChecked(profile == Profile.PROOF)
                self.profile_group.addButton(button)
                layout.addWidget(button)
            self.caches_box = QCheckBox('Include caches (latest finished version of each)')
            self.renders_box = QCheckBox('Include renders, playblasts and dailies (latest version of each)')
            layout.addWidget(self.caches_box)
            layout.addWidget(self.renders_box)
            self.plan_button = QPushButton('Read project')
            self.plan_button.clicked.connect(self._read_project)
            layout.addWidget(self.plan_button)
            self.plan_text = QPlainTextEdit()
            self.plan_text.setReadOnly(True)
            self.plan_text.setPlaceholderText('Read the project to see what the archive will hold.')
            layout.addWidget(self.plan_text, 1)
            out_row = QHBoxLayout()
            self.out_edit = QLineEdit()
            self.out_edit.setPlaceholderText('Where to write the archive (.zip), outside the project')
            browse = QPushButton('Browse…')
            browse.clicked.connect(self._browse_out)
            out_row.addWidget(self.out_edit, 1)
            out_row.addWidget(browse)
            layout.addLayout(out_row)
            split_row = QHBoxLayout()
            split_row.addWidget(QLabel('Split into parts of at most'))
            self.part_edit = QLineEdit()
            self.part_edit.setPlaceholderText('e.g. 50G — empty writes one file')
            split_row.addWidget(self.part_edit, 1)
            layout.addLayout(split_row)
            self.create_button = QPushButton('Write archive')
            self.create_button.setEnabled(False)
            self.create_button.clicked.connect(self._create)
            layout.addWidget(self.create_button)
            for button in self.profile_group.buttons():
                button.toggled.connect(self._options_changed)
            self.caches_box.toggled.connect(self._options_changed)
            self.renders_box.toggled.connect(self._options_changed)
            return tab

        def _options(self) -> Options:
            profile = self.profile_group.checkedButton().property('profile')
            return Options(profile, self.caches_box.isChecked(), self.renders_box.isChecked())

        def _options_changed(self, *_):
            # A plan is only good for the options it was made with.
            self._plan = None
            self.create_button.setEnabled(False)

        def _browse_out(self):
            name = Path(self.project_edit.text()).name or 'project'
            path, _ = QFileDialog.getSaveFileName(
                self, 'Write archive', f'{name}_{self._options().profile}.zip', 'Zip archive (*.zip)'
            )
            if path:
                self.out_edit.setText(path)

        def _read_project(self):
            project = Path(self.project_edit.text().strip())
            options = self._options()

            def job(worker):
                roots = project_roots(project)
                return make_plan(inventory_mod.scan(roots), options)

            self._run(job, self._show_plan, busy='Reading the project…')

        def _show_plan(self, plan):
            self._plan = plan
            lines = []
            total_in = total_out = 0
            for kind, row in sorted(plan.summary().items(), key=lambda kv: -(kv[1]['bytes_in'] + kv[1]['bytes_out'])):
                total_in += row['bytes_in']
                total_out += row['bytes_out']
                lines.append(
                    f'{kind:<16} archived {row["files_in"]:>7} / {human(row["bytes_in"]):>9}'
                    f'   left out {row["files_out"]:>7} / {human(row["bytes_out"]):>9}'
                )
            lines.insert(0, f'Archive: {human(total_in)}   (left out: {human(total_out)})\n')
            lines.append('\nLeft out, by reason:')
            for reason, row in sorted(plan.excluded_by_reason().items(), key=lambda kv: -kv[1]['bytes']):
                lines.append(f'  {human(row["bytes"]):>9}  {reason}')
            if plan.warnings:
                lines.append('\nCheck before sending:')
                lines.extend(f'  - {warning}' for warning in plan.warnings)
            self.plan_text.setPlainText('\n'.join(lines))
            self.create_button.setEnabled(True)
            self.status_text(f'{len(plan.included())} files to archive.')

        def _create(self):
            if self._plan is None:
                return
            out = self.out_edit.text().strip()
            if not out:
                self._browse_out()
                out = self.out_edit.text().strip()
                if not out:
                    return
            plan = self._plan
            part_text = self.part_edit.text().strip()
            try:
                part_size = parse_size(part_text) if part_text else None
            except argparse.ArgumentTypeError as error:
                QMessageBox.warning(self, 'Archive', str(error))
                return

            def job(worker):
                return pack.create(
                    plan, Path(out), part_size=part_size,
                    progress=worker.report, cancel=worker.is_cancelled,
                )

            def done(manifest):
                skipped = manifest['skipped']
                message = f'Wrote {len(manifest["files"])} files to {", ".join(manifest["parts"])}.'
                if skipped:
                    message += f'\n\n{len(skipped)} file(s) could not be read and were skipped:\n' + '\n'.join(skipped[:10])
                QMessageBox.information(self, 'Archive written', message)
                self.status_text(message.splitlines()[0])

            self._run(job, done, busy='Writing the archive…')

        # Restore ------------------------------------------------------------

        def _restore_tab(self):
            tab = QWidget()
            layout = QVBoxLayout(tab)
            row = QHBoxLayout()
            self.archive_edit = QLineEdit()
            self.archive_edit.setPlaceholderText('The archive (.zip, any part, or its .manifest.json)')
            browse = QPushButton('Browse…')
            browse.clicked.connect(self._browse_archive)
            row.addWidget(self.archive_edit, 1)
            row.addWidget(browse)
            layout.addWidget(QLabel('Archive'))
            layout.addLayout(row)
            self.archive_text = QPlainTextEdit()
            self.archive_text.setReadOnly(True)
            layout.addWidget(self.archive_text, 1)
            self.verify_button = QPushButton('Check archive')
            self.verify_button.clicked.connect(self._verify)
            layout.addWidget(self.verify_button)
            dest_row = QHBoxLayout()
            self.dest_edit = QLineEdit()
            self.dest_edit.setPlaceholderText('Folder to unpack into (the project folder is created inside it)')
            dest_browse = QPushButton('Browse…')
            dest_browse.clicked.connect(self._browse_dest)
            dest_row.addWidget(self.dest_edit, 1)
            dest_row.addWidget(dest_browse)
            layout.addWidget(QLabel('Restore into'))
            layout.addLayout(dest_row)
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText('Project name (leave empty to keep the archived name)')
            layout.addWidget(self.name_edit)
            self.restore_button = QPushButton('Restore')
            self.restore_button.clicked.connect(self._restore)
            layout.addWidget(self.restore_button)
            return tab

        def _browse_archive(self):
            path, _ = QFileDialog.getOpenFileName(
                self, 'Open archive', '', 'TumblePipe archive (*.zip *.manifest.json)'
            )
            if not path:
                return
            self.archive_edit.setText(path)
            try:
                manifest, parts = pack.load_manifest(Path(path))
            except Exception as error:
                self.archive_text.setPlainText(f'Not readable: {error}')
                return
            files = manifest['files']
            self.archive_text.setPlainText(
                f'Project:  {manifest["project"]["name"]}\n'
                f'Profile:  {manifest["profile"]}\n'
                f'Created:  {manifest["created"]} by {manifest.get("created_by") or "unknown"}\n'
                f'Files:    {len(files)} ({human(sum(f["size"] for f in files))})\n'
                f'Parts:    {", ".join(manifest["parts"])}\n'
                f'Made with TumblePipe {", ".join(manifest["tools"]["tumblepipe_versions"]) or "unknown"}, '
                f'Houdini {", ".join(manifest["tools"]["houdini_versions"]) or "unknown"}\n'
            )

        def _browse_dest(self):
            path = QFileDialog.getExistingDirectory(self, 'Restore into')
            if path:
                self.dest_edit.setText(path)

        def _verify(self):
            archive = Path(self.archive_edit.text().strip())

            def job(worker):
                return pack.verify(archive, progress=worker.report, cancel=worker.is_cancelled)

            def done(result):
                if result.ok:
                    self.status_text(f'OK: {result.checked} files match the manifest.')
                else:
                    QMessageBox.warning(
                        self, 'Archive check failed',
                        f'{len(result.problems)} problem(s):\n' + '\n'.join(result.problems[:20]),
                    )

            self._run(job, done, busy='Checking the archive…')

        def _restore(self):
            archive = Path(self.archive_edit.text().strip())
            dest = self.dest_edit.text().strip()
            if not dest:
                self._browse_dest()
                dest = self.dest_edit.text().strip()
                if not dest:
                    return
            name = self.name_edit.text().strip() or None

            def job(worker):
                return pack.restore(
                    archive, Path(dest), new_name=name,
                    progress=worker.report, cancel=worker.is_cancelled,
                )

            def done(manifest):
                project = name or manifest['project']['name']
                QMessageBox.information(
                    self, 'Restored',
                    f'Restored {len(manifest["files"])} files.\n\n'
                    f'Point a TumbleTrove project at {Path(dest) / project}.',
                )

            self._run(job, done, busy='Restoring…')

        # Worker plumbing ----------------------------------------------------

        def status_text(self, text):
            self.status.setText(text)

        def _set_busy(self, busy: bool):
            for button in (self.plan_button, self.verify_button, self.restore_button):
                button.setEnabled(not busy)
            self.create_button.setEnabled(not busy and self._plan is not None)
            self.cancel_button.setEnabled(busy)

        def _run(self, job, on_done, busy: str):
            self._set_busy(True)
            self.progress.setValue(0)
            self.status_text(busy)
            thread = QThread(self)
            worker = Worker(job)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            # Bound methods of this window, never lambdas: Qt runs a slot with
            # no receiver object on the emitting (worker) thread, which would
            # touch the UI off the main thread and race the queued progress.
            self._on_done = on_done
            worker.progress.connect(self._on_progress)
            worker.finished.connect(self._finish)
            worker.failed.connect(self._failed)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            self._thread, self._worker = thread, worker
            thread.start()

        def _on_progress(self, permille, files, current):
            self.progress.setValue(permille)
            self.status_text(f'{files} files — {current}')

        def _finish(self, result):
            self._set_busy(False)
            self.progress.setValue(1000)
            self._on_done(result)

        def _failed(self, message):
            self._set_busy(False)
            self.status_text(message)
            if message != 'Cancelled.':
                QMessageBox.critical(self, 'Archive', message)

        def _cancel(self):
            if self._worker is not None:
                self._worker.cancel()

        def closeEvent(self, event):
            if self._thread is not None and self._thread.isRunning():
                self._worker.cancel()
                self._thread.quit()
                self._thread.wait(5000)
            super().closeEvent(event)

    return Window()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='tumblepipe.archive.app')
    parser.add_argument('--restore', action='store_true', help='open on the Restore tab')
    args = parser.parse_args(argv)
    _add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('TumblePipe Archive')
    window = build_window(restore=args.restore)
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
