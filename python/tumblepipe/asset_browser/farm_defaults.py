"""Farm pools and priority presets for one project (#34).

``tumblepipe.config.farm`` had add/remove pool, add/remove priority preset
and set default preset, but nothing in the UI called them. A TD sending a
new project to an existing Deadline pool had to hand-edit ``entity:/ .farm``
in the Database Editor, and Farm Submit offers only the pools the project
lists.

This sits under Settings > Project defaults, beside the department pool.
Unlike the pool, each change is written as it is made: they are single
edits with nothing to warn about, like the Layout in Animate toggle. The
writers pin only the farm keys they edit.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from tumblepipe.pipe.houdini.ui.report import report_failure

from tumbletrove.asset_browser.core.theme import (
    ACCENT, FONT_FAMILY, FONT_SMALL, TEXT_DIM, scaled,
)

from .departments import GHOST_STYLE, LIST_STYLE

DEFAULT_MARK = "  (default)"


def pool_label(name: str, default: str | None) -> str:
    return name + (DEFAULT_MARK if name == default else "")


def preset_label(name: str, value: int, default: str | None) -> str:
    return f"{name}: {value}" + (DEFAULT_MARK if name == default else "")


class FarmDefaultsEditor(QWidget):
    """A project's farm pools and priority presets, each with a default."""

    def __init__(self, *, activator, parent=None) -> None:
        super().__init__(parent)
        self._activator = activator
        self._project = None
        self._editable = False
        self._pools: list[str] = []
        self._presets: list[tuple[str, int]] = []
        self._build_ui()
        self._refresh()

    # ── UI ────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(scaled(6))

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {ACCENT}; border: none; background: transparent;"
        )
        self._note.hide()
        outer.addWidget(self._note)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self._pool_list, pool_buttons = self._list_with_buttons((
            ("Add…", self._on_add_pool),
            ("Remove", self._on_remove_pool),
            ("Set Default", self._on_default_pool),
        ))
        self._pool_buttons = pool_buttons
        form.addRow("Pools", self._row(self._pool_list, pool_buttons))
        self._preset_list, preset_buttons = self._list_with_buttons((
            ("Add…", self._on_add_preset),
            ("Remove", self._on_remove_preset),
            ("Set Default", self._on_default_preset),
        ))
        self._preset_buttons = preset_buttons
        form.addRow("Priorities", self._row(self._preset_list, preset_buttons))
        outer.addLayout(form)

        hint = QLabel(
            "The Deadline pools Farm Submit offers for this project, and the "
            "named priorities it offers; the defaults are preselected. A pool "
            "must already exist on the farm."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent;"
        )
        outer.addWidget(hint)

    def _list_with_buttons(self, buttons):
        listing = QListWidget(self)
        listing.setStyleSheet(LIST_STYLE)
        listing.setMinimumHeight(scaled(70))
        listing.currentRowChanged.connect(lambda _row: self._update_enabled())
        made = []
        for label, slot in buttons:
            button = QPushButton(label)
            button.setStyleSheet(GHOST_STYLE)
            button.clicked.connect(slot)
            made.append(button)
        return listing, made

    def _row(self, listing, buttons):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(scaled(6))
        row.addWidget(listing, stretch=1)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(scaled(4))
        for button in buttons:
            column.addWidget(button)
        column.addStretch()
        row.addLayout(column)
        holder = QWidget(self)
        holder.setLayout(row)
        return holder

    # ── Project ───────────────────────────────────────

    def set_project(self, project, *, editable: bool, note: str = "") -> None:
        """Show *project*'s farm settings (None: nothing, *note* says why)."""
        self._project = project
        self._editable = editable and project is not None
        self._note.setText(note)
        self._note.setVisible(bool(note))
        self._refresh()

    def _refresh(self) -> None:
        """Re-read the project's farm settings and redraw both lists."""
        pool_row, preset_row = self._pool_list.currentRow(), self._preset_list.currentRow()
        default_pool = default_preset = None
        self._pools, self._presets = [], []
        if self._project is not None:
            from tumblepipe.config import farm
            from tumblepipe.api import api
            try:
                self._activator.activate(self._project)
                self._pools = [pool.name for pool in farm.list_pools()]
                self._presets = [(p.name, p.value) for p in farm.list_priority_presets()]
                default_pool = farm.get_default_pool()
                root = api.config.get_properties(farm.ENTITY_ROOT_URI) or {}
                default_preset = (root.get('farm') or {}).get('default_priority') or None
            except Exception as exc:
                report_failure("Reading the project's farm settings", exc)
        self._fill(self._pool_list, [pool_label(n, default_pool) for n in self._pools], pool_row)
        self._fill(self._preset_list,
                   [preset_label(n, v, default_preset) for n, v in self._presets], preset_row)
        self._update_enabled()

    @staticmethod
    def _fill(listing, labels, row) -> None:
        listing.blockSignals(True)
        listing.clear()
        for label in labels:
            item = QListWidgetItem(label, listing)
            if label.endswith(DEFAULT_MARK):
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        listing.blockSignals(False)
        if 0 <= row < listing.count():
            listing.setCurrentRow(row)

    def _update_enabled(self) -> None:
        has_project = self._project is not None
        for listing, buttons in ((self._pool_list, self._pool_buttons),
                                 (self._preset_list, self._preset_buttons)):
            listing.setEnabled(has_project)
            selected = listing.currentRow() >= 0
            add, remove, default = buttons
            add.setEnabled(self._editable)
            remove.setEnabled(self._editable and selected)
            default.setEnabled(self._editable and selected)

    def _edit(self, what: str, change) -> None:
        """Run one farm write against this project, then redraw."""
        try:
            self._activator.activate(self._project)
            change()
        except Exception as exc:
            report_failure(what, exc)
        self._refresh()

    def _selected(self, listing, names: list[str]) -> str | None:
        row = listing.currentRow()
        return names[row] if 0 <= row < len(names) else None

    # ── Pools ─────────────────────────────────────────

    def _on_add_pool(self) -> None:
        from tumblepipe.config import farm
        name, ok = QInputDialog.getText(
            self, "Add Farm Pool", "Deadline pool name (it must exist on the farm):")
        if not ok:
            return
        name = name.strip()
        problem = farm.pool_name_problem(name)
        if problem is None and name in self._pools:
            problem = f"'{name}' is already one of this project's pools."
        if problem:
            QMessageBox.warning(self, "Add Farm Pool", problem)
            return
        self._edit("Adding the farm pool", lambda: farm.add_pool(name))
        self._pool_list.setCurrentRow(self._pools.index(name) if name in self._pools else -1)

    def _on_remove_pool(self) -> None:
        from tumblepipe.config import farm
        name = self._selected(self._pool_list, self._pools)
        if name is None:
            return
        answer = QMessageBox.question(
            self, "Remove Farm Pool",
            f"Remove '{name}' from this project's farm pools?\n\n"
            "Farm Submit stops offering it. The pool itself stays on the farm.",
        )
        if answer == QMessageBox.Yes:
            self._edit("Removing the farm pool", lambda: farm.remove_pool(name))

    def _on_default_pool(self) -> None:
        from tumblepipe.config import farm
        name = self._selected(self._pool_list, self._pools)
        if name is not None:
            self._edit("Setting the default farm pool", lambda: farm.set_default_pool(name))

    # ── Priority presets ──────────────────────────────

    def _on_add_preset(self) -> None:
        from tumblepipe.config import farm
        name, ok = QInputDialog.getText(self, "Add Priority", "Priority name (e.g. urgent):")
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "Add Priority", "A priority needs a name.")
            return
        if name in [n for n, _ in self._presets]:
            QMessageBox.warning(self, "Add Priority", f"'{name}' is already a priority.")
            return
        value, ok = QInputDialog.getInt(
            self, "Add Priority", f"Deadline priority for '{name}' (0-100):", 50, 0, 100)
        if ok:
            self._edit("Adding the priority", lambda: farm.add_priority_preset(name, value))

    def _on_remove_preset(self) -> None:
        from tumblepipe.config import farm
        name = self._selected(self._preset_list, [n for n, _ in self._presets])
        if name is None:
            return
        answer = QMessageBox.question(
            self, "Remove Priority", f"Remove the priority '{name}'?")
        if answer == QMessageBox.Yes:
            self._edit("Removing the priority", lambda: farm.remove_priority_preset(name))

    def _on_default_preset(self) -> None:
        from tumblepipe.config import farm
        name = self._selected(self._preset_list, [n for n, _ in self._presets])
        if name is not None:
            self._edit("Setting the default priority",
                       lambda: farm.set_default_priority_preset(name))

