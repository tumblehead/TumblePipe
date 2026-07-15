"""Department pool editor for one project.

The pool (``departments:/<context>/<name>``) has always been editable —
``tumblepipe.config.department`` has had add/remove/enable/flag setters for
as long as it has existed — but nothing in the UI called them, so adding a
department meant hand-editing JSON in the Database Editor. This is that
front-end.

The one thing this dialog must get right is **order**. A department's
position in the pool is the pipeline order:

* the staged build sublayers departments in ``reversed()`` pool order, so
  later in the pool = stronger USD layer;
* "downstream" is ``names[index + 1:]`` — the Downstream Exports menu,
  import_shot's exclusion, the publish task graph, the propagate/update farm
  jobs;
* AOV precedence ranks by pool index.

So reordering an established pool restages composition for every existing
entity, and the dialog says so before it commits.

Lives in its own module so its PySide6 import cost is paid only when the
user actually opens it.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    BORDER, BUTTON_GHOST_STYLE, BUTTON_PRIMARY_STYLE, FONT_FAMILY, FONT_SMALL,
    TEXT_DIM, scaled,
)

CONTEXTS = ("shots", "assets", "render")


@dataclass
class _Dept:
    """A working-copy department — committed on Apply, not on edit."""
    name: str
    independent: bool
    publishable: bool
    renderable: bool
    generated: bool
    enabled: bool
    short: str
    is_new: bool = False


class DepartmentPoolDialog(QDialog):
    """Edit one project's department pool: order, membership, flags."""

    def __init__(self, catalog, project, parent=None) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._project = project
        self.setWindowTitle(f"Departments — {project.name}")
        self.setMinimumWidth(scaled(520))

        # Every config read and write below has to resolve against this
        # project, not whichever one happens to be active.
        self._catalog._activate_project(project)

        self._working: dict[str, list[_Dept]] = {
            context: self._load(context) for context in CONTEXTS
        }
        self._original: dict[str, list[str]] = {
            context: [d.name for d in depts]
            for context, depts in self._working.items()
        }
        self._context = "shots"
        self._current: int | None = None
        self._build_ui()
        self._refresh_list()

    # ── Load ──────────────────────────────────────────

    def _load(self, context: str) -> list[_Dept]:
        from tumblepipe.config.department import list_departments
        return [
            _Dept(
                name=d.name,
                independent=d.independent,
                publishable=d.publishable,
                renderable=d.renderable,
                generated=d.generated,
                enabled=d.enabled,
                short=d.short or "",
            )
            # Disabled departments are exactly what this dialog exists to
            # bring back, so it is the one reader that must see them.
            for d in list_departments(context, include_disabled=True)
        ]

    # ── UI ────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(scaled(6))

        hint = QLabel(
            "The department pool for this project. <b>Order is the pipeline "
            "order</b>: later departments layer over earlier ones in the "
            "staged build, and everything below a department counts as "
            "downstream of it."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.RichText)
        hint.setStyleSheet(
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent;"
        )
        outer.addWidget(hint)

        self._context_combo = QComboBox()
        self._context_combo.addItems(CONTEXTS)
        self._context_combo.currentTextChanged.connect(self._on_context_changed)
        outer.addWidget(self._context_combo)

        row = QHBoxLayout()
        row.setSpacing(scaled(6))

        self._list = QListWidget(self)
        self._list.setStyleSheet(
            f"QListWidget {{ background-color: transparent; "
            f"border: 1px solid {BORDER}; "
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; }} '
            f"QListWidget::item {{ padding: 4px 8px; }} "
            f"QListWidget::item:selected {{ background-color: rgba(255,255,255,16); }}"
        )
        self._list.setMinimumHeight(scaled(200))
        self._list.currentRowChanged.connect(self._on_row_changed)
        row.addWidget(self._list, stretch=1)

        buttons = QVBoxLayout()
        buttons.setSpacing(scaled(4))
        for label, slot in (
            ("Add…", self._on_add),
            ("Remove", self._on_remove),
            ("Move Up", self._on_move_up),
            ("Move Down", self._on_move_down),
        ):
            btn = QPushButton(label)
            btn.setStyleSheet(BUTTON_GHOST_STYLE)
            btn.clicked.connect(slot)
            buttons.addWidget(btn)
        buttons.addStretch()
        row.addLayout(buttons)
        outer.addLayout(row)

        # ── Flags for the selected department ──
        self._form_holder = QWidget(self)
        form = QFormLayout(self._form_holder)

        self._short_edit = QLineEdit()
        self._short_edit.setPlaceholderText("e.g. mdl (optional)")
        self._short_edit.editingFinished.connect(self._sync_from_form)
        form.addRow("Short label", self._short_edit)

        self._enabled_cb = QCheckBox("Enabled")
        self._enabled_cb.setToolTip(
            "Off retires the department: it disappears from every menu, deck "
            "and job graph, but its workfiles and exports stay on disk. This "
            "is the safe way to drop a department you no longer use."
        )
        self._publishable_cb = QCheckBox("Publishable")
        self._publishable_cb.setToolTip(
            "The department exports a layer (it appears in export/import "
            "department menus)."
        )
        self._renderable_cb = QCheckBox("Renderable")
        self._renderable_cb.setToolTip(
            "The department's layer composes into the render stage. Leave "
            "this off for departments that are not render layers (tracking, "
            "notes, references) — the update job treats the LAST renderable "
            "shot department as the final layer to re-import."
        )
        self._independent_cb = QCheckBox("Independent")
        self._independent_cb.setToolTip(
            "The department does not depend on upstream publishes, so a "
            "publish upstream of it does not propagate into it."
        )
        self._generated_cb = QCheckBox("Generated")
        self._generated_cb.setToolTip(
            "Produced by Python rather than a Houdini workfile — hidden from "
            "the Houdini export menus."
        )
        for cb in (
            self._enabled_cb, self._publishable_cb, self._renderable_cb,
            self._independent_cb, self._generated_cb,
        ):
            cb.toggled.connect(self._sync_from_form)
            form.addRow("", cb)

        outer.addWidget(self._form_holder)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(BUTTON_GHOST_STYLE)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet(BUTTON_PRIMARY_STYLE)
        apply_btn.clicked.connect(self._on_apply)
        footer.addWidget(apply_btn)
        outer.addLayout(footer)

    # ── List state ────────────────────────────────────

    def _depts(self) -> list[_Dept]:
        return self._working[self._context]

    def _refresh_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for dept in self._depts():
            label = dept.name if dept.enabled else f"{dept.name}  (disabled)"
            item = QListWidgetItem(label, self._list)
            if not dept.enabled:
                item.setForeground(Qt.gray)
        self._list.blockSignals(False)
        if self._current is not None and 0 <= self._current < len(self._depts()):
            self._list.setCurrentRow(self._current)
        else:
            self._current = None
            self._on_row_changed(-1)

    def _on_context_changed(self, context: str) -> None:
        self._context = context
        self._current = None
        self._refresh_list()

    def _on_row_changed(self, row: int) -> None:
        depts = self._depts()
        if row < 0 or row >= len(depts):
            self._current = None
            self._form_holder.setEnabled(False)
            return
        self._current = row
        dept = depts[row]
        self._form_holder.setEnabled(True)
        for widget, value in (
            (self._short_edit, dept.short),
            (self._enabled_cb, dept.enabled),
            (self._publishable_cb, dept.publishable),
            (self._renderable_cb, dept.renderable),
            (self._independent_cb, dept.independent),
            (self._generated_cb, dept.generated),
        ):
            widget.blockSignals(True)
            if isinstance(widget, QLineEdit):
                widget.setText(value)
            else:
                widget.setChecked(value)
            widget.blockSignals(False)

    def _sync_from_form(self) -> None:
        if self._current is None:
            return
        dept = self._depts()[self._current]
        dept.short = self._short_edit.text().strip()
        dept.enabled = self._enabled_cb.isChecked()
        dept.publishable = self._publishable_cb.isChecked()
        dept.renderable = self._renderable_cb.isChecked()
        dept.independent = self._independent_cb.isChecked()
        dept.generated = self._generated_cb.isChecked()
        self._refresh_list()

    # ── Buttons ───────────────────────────────────────

    def _on_add(self) -> None:
        from tumblepipe.config.department import validate_name
        name, ok = QInputDialog.getText(
            self, "Add Department",
            f"New {self._context} department name:",
        )
        if not ok:
            return
        name = name.strip()
        try:
            validate_name(name)
        except ValueError as exc:
            QMessageBox.warning(self, "Add Department", str(exc))
            return
        if any(d.name == name for d in self._depts()):
            QMessageBox.warning(
                self, "Add Department",
                f"'{name}' is already in the {self._context} pool.",
            )
            return
        # Insert after the selection, so placing a department in the middle
        # of the pipeline is the obvious gesture rather than a hidden one.
        index = len(self._depts()) if self._current is None else self._current + 1
        self._depts().insert(index, _Dept(
            name=name,
            independent=False,
            publishable=True,
            renderable=False,
            generated=False,
            enabled=True,
            short="",
            is_new=True,
        ))
        self._current = index
        self._refresh_list()

    def _on_remove(self) -> None:
        if self._current is None:
            return
        dept = self._depts()[self._current]
        answer = QMessageBox.question(
            self, "Remove Department",
            f"Remove '{dept.name}' from the {self._context} pool?\n\n"
            "Its workfiles and exports stay on disk — this only takes it out "
            "of the pool. Entities scoped to it will quietly drop it.\n\n"
            "To retire a department without losing it from the pool, untick "
            "Enabled instead.",
        )
        if answer != QMessageBox.Yes:
            return
        del self._depts()[self._current]
        self._current = None
        self._refresh_list()

    def _move(self, delta: int) -> None:
        if self._current is None:
            return
        depts = self._depts()
        target = self._current + delta
        if target < 0 or target >= len(depts):
            return
        depts[self._current], depts[target] = depts[target], depts[self._current]
        self._current = target
        self._refresh_list()

    def _on_move_up(self) -> None:
        self._move(-1)

    def _on_move_down(self) -> None:
        self._move(1)

    # ── Commit ────────────────────────────────────────

    def _order_changed(self) -> bool:
        """Did an *existing* department move relative to the others?

        Appending a new department at the end is not a reorder — nothing that
        already exists changed position — so it must not raise the warning.
        """
        for context in CONTEXTS:
            before = self._original[context]
            after = [d.name for d in self._working[context] if not d.is_new]
            if [n for n in after if n in before] != [n for n in before if n in after]:
                return True
        return False

    def _trailing_renderable_warning(self) -> str | None:
        """A new renderable department appended last to the shots pool.

        The update job re-imports the LAST renderable shot department as "the"
        final layer, so a tracking-style department tacked on the end with
        renderable ticked silently hijacks it.
        """
        depts = [d for d in self._working["shots"] if d.enabled]
        renderable = [d for d in depts if d.renderable]
        if not renderable:
            return None
        last = renderable[-1]
        if not last.is_new:
            return None
        return (
            f"'{last.name}' is the last renderable shot department, which "
            "makes it the final layer the update job re-imports. If it is not "
            "actually a render layer, untick Renderable."
        )

    def _on_apply(self) -> None:
        warnings: list[str] = []
        if self._order_changed():
            warnings.append(
                "You reordered the pool. Order is the pipeline order: this "
                "changes USD sublayer strength and what counts as downstream "
                "for every existing shot and asset in this project."
            )
        trailing = self._trailing_renderable_warning()
        if trailing:
            warnings.append(trailing)

        if warnings:
            answer = QMessageBox.warning(
                self, "Apply Department Changes",
                "\n\n".join(warnings) + "\n\nApply anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        try:
            self._commit()
        except Exception as exc:
            QMessageBox.critical(
                self, "Apply Department Changes",
                f"Failed to apply: {exc}\n\nSee the Houdini console.",
            )
            raise

        self._catalog.invalidate_cache()
        self._catalog._request_global_detail_refresh()
        self.accept()

    def _commit(self) -> None:
        from tumblepipe.config import department as dept_mod

        self._catalog._activate_project(self._project)
        for context in CONTEXTS:
            working = self._working[context]
            live = {
                d.name for d in
                dept_mod.list_departments(context, include_disabled=True)
            }
            wanted = {d.name for d in working}

            for name in live - wanted:
                dept_mod.remove_department(context, name)

            for dept in working:
                if dept.name not in live:
                    dept_mod.add_department(
                        context, dept.name,
                        independent=dept.independent,
                        publishable=dept.publishable,
                        renderable=dept.renderable,
                        generated=dept.generated,
                        enabled=dept.enabled,
                        short=dept.short or None,
                    )
                    continue
                dept_mod.set_independent(context, dept.name, dept.independent)
                dept_mod.set_publishable(context, dept.name, dept.publishable)
                dept_mod.set_renderable(context, dept.name, dept.renderable)
                dept_mod.set_generated(context, dept.name, dept.generated)
                dept_mod.set_enabled(context, dept.name, dept.enabled)
                dept_mod.set_short(context, dept.name, dept.short or None)

            # Order last: the adds above appended, so this is what actually
            # places them.
            dept_mod.reorder_departments(context, [d.name for d in working])


class EntityDepartmentsDialog(QDialog):
    """Choose which departments one shot or asset uses.

    An entity with everything ticked stores no assignment at all — it simply
    inherits the pool, so a department added to the pool later reaches it
    automatically. Unticking is a scoping decision and nothing more: the
    workfiles and exports of an unticked department stay on disk and keep
    composing into the staged build, and the browser goes on showing them,
    flagged. Nothing here can change a render.
    """

    def __init__(self, catalog, asset_id: str, parent=None) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._asset_id = asset_id

        project = catalog._resolver.project_for(asset_id)
        self._entity_uri = catalog._resolver.uri_for(asset_id)
        if project is None or self._entity_uri is None:
            raise ValueError(f"Not a pipeline entity: {asset_id}")
        catalog._activate_project(project)
        self._project = project

        from tumblepipe.config.department import (
            get_entity_departments, list_departments,
        )
        self._context = self._entity_uri.segments[0]
        self._pool = [
            d.name for d in
            list_departments(self._context, include_generated=False)
        ]
        assigned = get_entity_departments(self._entity_uri)
        # No assignment means "inherit the pool" — show that as everything
        # ticked, which is also how the user turns it back off.
        self._checked = set(assigned) if assigned else set(self._pool)
        self._with_work = set(
            catalog._get_department_workfile_info(asset_id).keys()
        )

        self.setWindowTitle(f"Departments — {asset_id.split('/')[-1]}")
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setSpacing(scaled(6))

        hint = QLabel(
            "The departments this entity uses. Unticking one only hides it "
            "from menus and task lists — any workfile or export it already "
            "has stays on disk, keeps rendering, and keeps showing here."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f'font-family: "{FONT_FAMILY}"; font-size: {FONT_SMALL}px; '
            f"color: {TEXT_DIM}; border: none; background: transparent;"
        )
        outer.addWidget(hint)

        self._boxes: list[QCheckBox] = []
        for name in self._pool:
            cb = QCheckBox(name)
            cb.setChecked(name in self._checked)
            if name in self._with_work:
                cb.setToolTip(
                    f"{name} has a workfile. Unticking it will not touch it."
                )
            outer.addWidget(cb)
            self._boxes.append(cb)

        footer = QHBoxLayout()
        all_btn = QPushButton("Use all (inherit)")
        all_btn.setStyleSheet(BUTTON_GHOST_STYLE)
        all_btn.setToolTip(
            "Drop the scoping: the entity follows the project's pool, "
            "including departments added to it later."
        )
        all_btn.clicked.connect(self._on_use_all)
        footer.addWidget(all_btn)
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(BUTTON_GHOST_STYLE)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet(BUTTON_PRIMARY_STYLE)
        apply_btn.clicked.connect(self._on_apply)
        footer.addWidget(apply_btn)
        outer.addLayout(footer)

    def _on_use_all(self) -> None:
        for cb in self._boxes:
            cb.setChecked(True)

    def _on_apply(self) -> None:
        selected = [cb.text() for cb in self._boxes if cb.isChecked()]
        if not selected:
            QMessageBox.warning(
                self, "Departments",
                "An entity needs at least one department. To hide a "
                "department from every entity, retire it in the project's "
                "department pool instead.",
            )
            return

        from tumblepipe.config.department import set_entity_departments
        # Everything ticked == inherit the pool: store nothing, so the entity
        # picks up departments added to the pool later.
        names = [] if set(selected) == set(self._pool) else selected
        try:
            self._catalog._activate_project(self._project)
            set_entity_departments(self._entity_uri, names)
        except Exception as exc:
            QMessageBox.critical(
                self, "Departments",
                f"Failed to apply: {exc}\n\nSee the Houdini console.",
            )
            raise

        self._catalog._request_card_refresh_for_id(self._asset_id)
        self._catalog._request_global_detail_refresh()
        self.accept()
