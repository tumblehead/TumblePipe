"""TumbleTrove tt_setup hook for TumblePipe.

Runs when the user clicks Configure on the TumblePipe package card. Presents
a Qt6 wizard with two flows:

  1. Use existing project — browse to a project root that already has a
     valid `_config/` directory. The wizard verifies it and emits the
     env vars needed to point TumblePipe at it.

  2. Create new project — collect a name + fps + parent directory, copy
     the bundled project_template/ into <parent>/<name>/, customize the
     JSON databases, and create the standard top-level subdirs.

On accept the wizard prints a single JSON object to stdout describing the
project-scope env var overrides:

    {"envVars": {"TH_PROJECT_PATH": "/abs/path/to/project"}}

On cancel the wizard exits non-zero with no stdout, which causes
TumbleTrove to surface the error and block the configure action — that's
intentional, the package can't function without TH_PROJECT_PATH.

Stdout is reserved for the JSON payload. All UI, progress, and diagnostics
go through Qt dialogs or stderr.
"""

from __future__ import annotations

import json
import shutil
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

# tt_setup runs outside Houdini, so qtpy's Houdini-bundled PySide6 isn't
# available. The hpm-managed venv (hpm.toml [scripts.tt_setup]) provides
# PySide6, but fall back to PyQt6 if someone is running this script
# outside hpm for development.
try:
    from PySide6 import QtWidgets  # type: ignore
except ImportError:
    try:
        from PyQt6 import QtWidgets  # type: ignore
    except ImportError:
        sys.stderr.write(
            'tt_setup: neither PySide6 nor PyQt6 is available. Run via '
            "'hpm run tt_setup' (which provisions the declared venv) or "
            "install PySide6 manually for development.\n"
        )
        sys.exit(2)


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR / 'project_template'
TOP_LEVEL_DIRS = ('assets', 'shots', 'groups', 'kits', 'export')


@dataclass
class WizardResult:
    project_path: Path | None = None
    env_vars: dict[str, str] = field(default_factory=dict)


# ---------- helpers --------------------------------------------------------


def _is_valid_project_name(name: str) -> bool:
    return bool(name) and name.isalnum()


def _looks_like_project(path: Path) -> bool:
    return (path / '_config' / 'db' / 'entity.json').is_file()


def _load_json(path: Path) -> dict:
    with path.open('r', encoding='utf-8') as fh:
        return json.load(fh)


def _store_json(path: Path, data: dict) -> None:
    with path.open('w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=4)
        fh.write('\n')


def _customize_template(project_path: Path, project_name: str, fps: int) -> None:
    entity_path = project_path / '_config' / 'db' / 'entity.json'
    entity = _load_json(entity_path)
    entity.setdefault('properties', {}).setdefault('farm', {})
    entity['properties']['farm']['pools'] = [project_name]
    entity['properties']['farm']['default_pool'] = project_name
    _store_json(entity_path, entity)

    config_path = project_path / '_config' / 'db' / 'config.json'
    config = _load_json(config_path)
    config.setdefault('children', {}).setdefault('project', {}).setdefault('properties', {})
    config['children']['project']['properties']['fps'] = fps
    _store_json(config_path, config)

    schemas_path = project_path / '_config' / 'db' / 'schemas.json'
    schemas = _load_json(schemas_path)
    entity_props = (
        schemas.setdefault('children', {})
        .setdefault('entity', {})
        .setdefault('properties', {})
    )
    entity_props['fps'] = fps
    config_project_props = (
        schemas['children']
        .setdefault('config', {})
        .setdefault('children', {})
        .setdefault('project', {})
        .setdefault('properties', {})
    )
    config_project_props['fps'] = fps
    _store_json(schemas_path, schemas)


def _copy_template(target: Path) -> None:
    if not TEMPLATE_DIR.is_dir():
        raise RuntimeError(
            f'Bundled project template not found at {TEMPLATE_DIR}. '
            'The TumblePipe package is incomplete.'
        )
    shutil.copytree(
        TEMPLATE_DIR,
        target,
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.bak'),
    )
    for sub in TOP_LEVEL_DIRS:
        (target / sub).mkdir(exist_ok=True)


# ---------- wizard pages ---------------------------------------------------


class ModePage(QtWidgets.QWizardPage):
    """Pick between existing and new project flows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle('TumblePipe Project')
        self.setSubTitle(
            'Point TumblePipe at an existing project on disk, or create a new one '
            'from the bundled template.'
        )

        self._existing = QtWidgets.QRadioButton('Use an existing project')
        self._existing.setChecked(True)
        self._new = QtWidgets.QRadioButton('Create a new project')

        existing_help = QtWidgets.QLabel(
            "Choose this if you already have a project folder containing a "
            "<code>_config/</code> directory."
        )
        existing_help.setWordWrap(True)
        existing_help.setStyleSheet('color: gray;')
        existing_help.setIndent(24)

        new_help = QtWidgets.QLabel(
            'Choose this to scaffold a new project from the TumblePipe template '
            '(config databases, conventions, USD context).'
        )
        new_help.setWordWrap(True)
        new_help.setStyleSheet('color: gray;')
        new_help.setIndent(24)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._existing)
        layout.addWidget(existing_help)
        layout.addSpacing(8)
        layout.addWidget(self._new)
        layout.addWidget(new_help)
        layout.addStretch()

    def selected_mode(self) -> str:
        return 'existing' if self._existing.isChecked() else 'new'

    def nextId(self) -> int:
        return SetupWizard.PAGE_EXISTING if self._existing.isChecked() else SetupWizard.PAGE_NEW


class ExistingProjectPage(QtWidgets.QWizardPage):
    """Browse to an existing project root and verify its layout."""

    FIELD = 'existing_path*'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle('Select Existing Project')
        self.setSubTitle(
            'Browse to the root of a project that already has a '
            "<code>_config/</code> directory."
        )

        self._path_edit = QtWidgets.QLineEdit()
        self._path_edit.setPlaceholderText('Path to project root…')
        self._path_edit.textChanged.connect(self._on_path_changed)

        browse_button = QtWidgets.QPushButton('Browse…')
        browse_button.clicked.connect(self._on_browse)

        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self._path_edit, stretch=1)
        path_row.addWidget(browse_button)

        self._status = QtWidgets.QLabel('')
        self._status.setWordWrap(True)

        form = QtWidgets.QFormLayout(self)
        form.addRow('Project root', path_row)
        form.addRow('', self._status)

        self.registerField(self.FIELD, self._path_edit)

    def _on_browse(self) -> None:
        start = self._path_edit.text().strip() or str(Path.home())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Select Project Root', start
        )
        if chosen:
            self._path_edit.setText(chosen)

    def _on_path_changed(self, _text: str) -> None:
        self._refresh_status()
        self.completeChanged.emit()

    def _refresh_status(self) -> None:
        text = self._path_edit.text().strip()
        if not text:
            self._status.setText('')
            return
        path = Path(text).expanduser()
        if not path.exists():
            self._status.setText("Path doesn't exist.")
            self._status.setStyleSheet('color: #c44;')
            return
        if not path.is_dir():
            self._status.setText('Path is not a directory.')
            self._status.setStyleSheet('color: #c44;')
            return
        if not _looks_like_project(path):
            self._status.setText(
                "Couldn't find <code>_config/db/entity.json</code> inside this folder. "
                'Pick the project root, not a sub-folder.'
            )
            self._status.setStyleSheet('color: #c44;')
            return
        self._status.setText(f'Looks valid — will set TH_PROJECT_PATH to <code>{path}</code>.')
        self._status.setStyleSheet('color: #2a7;')

    def isComplete(self) -> bool:
        text = self._path_edit.text().strip()
        if not text:
            return False
        path = Path(text).expanduser()
        return path.is_dir() and _looks_like_project(path)

    def nextId(self) -> int:
        return -1


class NewProjectPage(QtWidgets.QWizardPage):
    """Collect details for a new project to scaffold."""

    F_NAME = 'new_name*'
    F_PARENT = 'new_parent*'
    F_FPS = 'new_fps'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle('Create New Project')
        self.setSubTitle(
            "Choose where the project goes and a few defaults. "
            "TumblePipe's project template will be copied to "
            '<code>&lt;parent&gt;/&lt;name&gt;/</code>.'
        )

        self._name_edit = QtWidgets.QLineEdit()
        self._name_edit.setPlaceholderText('alphanumeric, e.g. myfilm')
        self._name_edit.textChanged.connect(self._on_changed)

        self._parent_edit = QtWidgets.QLineEdit()
        self._parent_edit.setPlaceholderText('Parent directory (project will be created inside)…')
        self._parent_edit.textChanged.connect(self._on_changed)

        browse_button = QtWidgets.QPushButton('Browse…')
        browse_button.clicked.connect(self._on_browse)

        parent_row = QtWidgets.QHBoxLayout()
        parent_row.addWidget(self._parent_edit, stretch=1)
        parent_row.addWidget(browse_button)

        self._fps_spin = QtWidgets.QSpinBox()
        self._fps_spin.setRange(1, 240)
        self._fps_spin.setValue(24)

        self._target_label = QtWidgets.QLabel('')
        self._target_label.setWordWrap(True)
        self._target_label.setStyleSheet('color: gray;')

        form = QtWidgets.QFormLayout(self)
        form.addRow('Project name', self._name_edit)
        form.addRow('Parent directory', parent_row)
        form.addRow('FPS', self._fps_spin)
        form.addRow('', self._target_label)

        self.registerField(self.F_NAME, self._name_edit)
        self.registerField(self.F_PARENT, self._parent_edit)
        self.registerField(self.F_FPS, self._fps_spin)

    def _on_browse(self) -> None:
        start = self._parent_edit.text().strip() or str(Path.home())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Select Parent Directory', start
        )
        if chosen:
            self._parent_edit.setText(chosen)

    def _on_changed(self, _text: str = '') -> None:
        self._refresh_target_label()
        self.completeChanged.emit()

    def _target_path(self) -> Path | None:
        name = self._name_edit.text().strip()
        parent_text = self._parent_edit.text().strip()
        if not name or not parent_text:
            return None
        return Path(parent_text).expanduser() / name

    def _refresh_target_label(self) -> None:
        name = self._name_edit.text().strip()
        parent_text = self._parent_edit.text().strip()
        target = self._target_path()

        if name and not _is_valid_project_name(name):
            self._target_label.setText('Project name must be alphanumeric (no spaces or dashes).')
            self._target_label.setStyleSheet('color: #c44;')
            return
        if parent_text:
            parent = Path(parent_text).expanduser()
            if not parent.exists():
                self._target_label.setText("Parent directory doesn't exist.")
                self._target_label.setStyleSheet('color: #c44;')
                return
            if not parent.is_dir():
                self._target_label.setText('Parent path is not a directory.')
                self._target_label.setStyleSheet('color: #c44;')
                return
        if target and target.exists():
            self._target_label.setText(f'<code>{target}</code> already exists — pick a different name or parent.')
            self._target_label.setStyleSheet('color: #c44;')
            return
        if target:
            self._target_label.setText(f'Will create <code>{target}</code>')
            self._target_label.setStyleSheet('color: #2a7;')
        else:
            self._target_label.setText('')

    def isComplete(self) -> bool:
        name = self._name_edit.text().strip()
        parent_text = self._parent_edit.text().strip()
        if not _is_valid_project_name(name) or not parent_text:
            return False
        parent = Path(parent_text).expanduser()
        if not parent.is_dir():
            return False
        target = parent / name
        return not target.exists()

    def validatePage(self) -> bool:
        target = self._target_path()
        if target is None:
            return False
        try:
            _copy_template(target)
            _customize_template(target, self._name_edit.text().strip(), self._fps_spin.value())
        except Exception as exc:
            sys.stderr.write(traceback.format_exc())
            shutil.rmtree(target, ignore_errors=True)
            QtWidgets.QMessageBox.critical(
                self, 'Project creation failed',
                f'Could not scaffold project:\n\n{exc}',
            )
            return False
        return True

    def nextId(self) -> int:
        return -1


class SetupWizard(QtWidgets.QWizard):
    PAGE_MODE = 0
    PAGE_EXISTING = 1
    PAGE_NEW = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('TumblePipe — Project Setup')
        self.setOption(QtWidgets.QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QtWidgets.QWizard.WizardOption.NoCancelButtonOnLastPage, False)
        self.setMinimumWidth(620)

        self.setPage(self.PAGE_MODE, ModePage())
        self.setPage(self.PAGE_EXISTING, ExistingProjectPage())
        self.setPage(self.PAGE_NEW, NewProjectPage())
        self.setStartId(self.PAGE_MODE)

        self._result = WizardResult()

    def collected_result(self) -> WizardResult:
        return self._result

    def accept(self) -> None:
        mode_page = self.page(self.PAGE_MODE)
        assert isinstance(mode_page, ModePage)
        if mode_page.selected_mode() == 'existing':
            text = (self.field(ExistingProjectPage.FIELD) or '').strip()
            self._result.project_path = Path(text).expanduser()
        else:
            name = (self.field(NewProjectPage.F_NAME) or '').strip()
            parent_text = (self.field(NewProjectPage.F_PARENT) or '').strip()
            self._result.project_path = Path(parent_text).expanduser() / name
        self._result.env_vars = {'TH_PROJECT_PATH': str(self._result.project_path)}
        super().accept()


# ---------- entry ----------------------------------------------------------


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    wizard = SetupWizard()
    code = wizard.exec()
    accepted = code == QtWidgets.QDialog.DialogCode.Accepted if hasattr(QtWidgets.QDialog, 'DialogCode') else bool(code)
    if not accepted:
        sys.stderr.write('tt_setup: cancelled by user.\n')
        return 1

    result = wizard.collected_result()
    if not result.env_vars or not result.project_path:
        sys.stderr.write('tt_setup: wizard accepted but produced no env vars.\n')
        return 1

    payload = {'envVars': result.env_vars}
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
