"""Pipeline catalog settings widget — multi-project management.

Lives in its own module so the import cost (PySide6 widgets) is paid
only when the user opens the gear-icon settings dialog.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from asset_browser.core.projects import ProjectConfig
from asset_browser.core.theme import (
    BORDER, BUTTON_GHOST_STYLE, BUTTON_PRIMARY_STYLE, FONT_FAMILY, FONT_SMALL,
    TEXT_DIM, scaled,
)


class PipelineSettingsWidget(QWidget):
    """Project list + edit form + Apply for the Pipeline catalog."""

    def __init__(self, catalog, parent=None) -> None:
        super().__init__(parent)
        self._catalog = catalog
        # Working copy of the registry — committed on Apply.
        self._working: list[ProjectConfig] = [
            ProjectConfig(
                name=p.name,
                project_path=p.project_path,
                pipeline_path="",
                config_path=p.config_path,
            )
            for p in catalog._registry.all()
        ]
        self._current_index: int | None = None
        self._build_ui()
        if self._working:
            self._list.setCurrentRow(0)

    # ── UI construction ───────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(scaled(6))

        hint = QLabel(
            "Registered Tumblehead projects. Add as many as you like — "
            "the asset browser merges all of them into one grid."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent;"
        )
        outer.addWidget(hint)

        # ── Project list + add/remove buttons row ──
        list_row = QHBoxLayout()
        list_row.setContentsMargins(0, 0, 0, 0)
        list_row.setSpacing(scaled(6))

        self._list = QListWidget(self)
        self._list.setStyleSheet(
            f"QListWidget {{ background-color: transparent; "
            f"border: 1px solid {BORDER}; "
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; }} '
            f"QListWidget::item {{ padding: 4px 8px; }} "
            f"QListWidget::item:selected {{ background-color: rgba(255,255,255,16); }}"
        )
        self._list.setMinimumHeight(scaled(120))
        self._list.currentRowChanged.connect(self._on_row_changed)
        list_row.addWidget(self._list, stretch=1)

        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(scaled(4))

        self._add_btn = QPushButton("Add")
        self._add_btn.setStyleSheet(BUTTON_GHOST_STYLE)
        self._add_btn.clicked.connect(self._on_add)
        btn_col.addWidget(self._add_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setStyleSheet(BUTTON_GHOST_STYLE)
        self._remove_btn.clicked.connect(self._on_remove)
        btn_col.addWidget(self._remove_btn)

        btn_col.addStretch()
        list_row.addLayout(btn_col)
        outer.addLayout(list_row)

        # ── Edit form for the selected project ──
        self._form_holder = QWidget(self)
        form = QFormLayout(self._form_holder)
        form.setContentsMargins(0, scaled(4), 0, scaled(4))

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. RND or growth")
        self._name_edit.editingFinished.connect(self._sync_from_form)
        form.addRow("Name", self._name_edit)

        # Project path with browse button
        proj_row = QHBoxLayout()
        proj_row.setContentsMargins(0, 0, 0, 0)
        proj_row.setSpacing(scaled(4))
        self._project_edit = QLineEdit()
        self._project_edit.setPlaceholderText("P:/RND")
        self._project_edit.editingFinished.connect(self._sync_from_form)
        proj_row.addWidget(self._project_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setStyleSheet(BUTTON_GHOST_STYLE)
        browse_btn.clicked.connect(self._on_browse)
        proj_row.addWidget(browse_btn)
        form.addRow("Project Path", proj_row)

        self._config_edit = QLineEdit()
        self._config_edit.setPlaceholderText("(optional)")
        self._config_edit.editingFinished.connect(self._sync_from_form)
        form.addRow("Config Path", self._config_edit)

        outer.addWidget(self._form_holder)

        # ── Apply button (commits to disk + reinits clients) ──
        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(0, 0, 0, 0)
        apply_row.addStretch()
        self._apply_btn = QPushButton("Apply Project Changes")
        self._apply_btn.setStyleSheet(BUTTON_PRIMARY_STYLE)
        self._apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(self._apply_btn)
        outer.addLayout(apply_row)

        self._refresh_list()

    # ── Helpers ───────────────────────────────────────

    def _refresh_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for proj in self._working:
            label = (
                f"{proj.name}    —    {proj.project_path}"
                if proj.project_path else proj.name
            )
            QListWidgetItem(label, self._list)
        self._list.blockSignals(False)
        self._update_form_enabled()

    def _update_form_enabled(self) -> None:
        has_sel = self._current_index is not None
        for w in (
            self._name_edit, self._project_edit, self._config_edit,
        ):
            w.setEnabled(has_sel)
        self._remove_btn.setEnabled(has_sel)

    def _on_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._working):
            self._current_index = None
            self._name_edit.setText("")
            self._project_edit.setText("")
            self._config_edit.setText("")
            self._update_form_enabled()
            return
        self._current_index = row
        proj = self._working[row]
        self._name_edit.setText(proj.name)
        self._project_edit.setText(proj.project_path)
        self._config_edit.setText(proj.config_path)
        self._update_form_enabled()

    def _sync_from_form(self) -> None:
        """Push the form fields back into the working copy."""
        if self._current_index is None:
            return
        proj = self._working[self._current_index]
        new_name = self._name_edit.text().strip() or proj.name
        proj_new = ProjectConfig(
            name=new_name,
            project_path=self._project_edit.text().strip(),
            pipeline_path="",
            config_path=self._config_edit.text().strip(),
        )
        self._working[self._current_index] = proj_new
        self._refresh_list()
        self._list.blockSignals(True)
        self._list.setCurrentRow(self._current_index)
        self._list.blockSignals(False)

    # ── Button handlers ───────────────────────────────

    def _on_add(self) -> None:
        # Append a fresh empty entry, select it, and let the user fill
        # the form.
        i = 1
        base = "new_project"
        existing = {p.name for p in self._working}
        name = base
        while name in existing:
            i += 1
            name = f"{base}_{i}"
        self._working.append(ProjectConfig(
            name=name, project_path="",
            pipeline_path="", config_path="",
        ))
        # pipeline_path is intentionally always "" — TH_PIPELINE_PATH is
        # set globally by hpm and is not a per-project concern.
        self._refresh_list()
        self._list.setCurrentRow(len(self._working) - 1)

    def _on_remove(self) -> None:
        if self._current_index is None:
            return
        del self._working[self._current_index]
        if self._working:
            self._current_index = min(
                self._current_index, len(self._working) - 1,
            )
        else:
            self._current_index = None
        self._refresh_list()
        if self._working:
            self._list.setCurrentRow(self._current_index or 0)
        else:
            self._on_row_changed(-1)

    def _on_browse(self) -> None:
        if self._current_index is None:
            return
        start = self._project_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "Select Project Folder", start,
        )
        if not path:
            return
        path = path.replace("\\", "/")
        self._project_edit.setText(path)
        # Auto-suggest sibling _config path if the user left it blank.
        proj_root = Path(path)
        if not self._config_edit.text().strip():
            cfg_in_proj = proj_root / "_config"
            if cfg_in_proj.exists():
                self._config_edit.setText(str(cfg_in_proj).replace("\\", "/"))
        # If the user hasn't typed a name yet, default to the leaf folder.
        if not self._name_edit.text().strip():
            self._name_edit.setText(proj_root.name)
        self._sync_from_form()

    def _on_apply(self) -> None:
        # Validate every working entry has at minimum a name + project path.
        bad: list[str] = []
        seen_names: set[str] = set()
        for proj in self._working:
            if not proj.name:
                bad.append("(unnamed entry)")
                continue
            if proj.name in seen_names:
                bad.append(f"duplicate name: {proj.name}")
            seen_names.add(proj.name)
            if not proj.project_path:
                bad.append(f"{proj.name}: missing Project Path")
        if bad:
            QMessageBox.warning(
                self, "Project Settings",
                "Fix these issues before applying:\n  - "
                + "\n  - ".join(bad),
            )
            return
        try:
            self._catalog._apply_project_changes(self._working)
        except Exception:
            QMessageBox.critical(
                self, "Project Settings",
                "Failed to apply project changes — see Houdini console.",
            )
            raise
        QMessageBox.information(
            self, "Project Settings",
            f"Saved {len(self._working)} project(s). "
            "The asset browser grid will repopulate on the next browse.",
        )
