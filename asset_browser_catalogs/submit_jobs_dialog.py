"""Slim submit-jobs dialog for the TumblePipe asset-browser catalog.

A compact dialog (this replaced the retired Project Browser's
``JobSubmissionDialog``).
One shared form drives publish + render submission for all selected entities;
defaults are seeded from the first entity's properties. Render starts enabled
and publish disabled — the dialog opens from the top-bar Render quick action
(current scene's entity) as well as the multi-select context menu, and
rendering is the common case for both.

Every open gets a checkable entity tree, scoped to the dialog's context and
seeded with the entities it was opened for. Any number of entities can be
checked, so a single-entity open (the Render quick action on the loaded
scene) can still fan out to a whole batch without going back to the browser
to multi-select first. Groups appear as a second root whose leaves mirror
the same entities.

The form is **not** a shared override. Each field is tri-state: left alone
it is *unpinned* and every entity follows its own configured value; touching
it *pins* it as a batch-wide choice. Unpinned fields render dimmed, with
``⟨per entity⟩`` standing in when the checked entities disagree — the
dimmed-default / bold-override grammar the retired Project Browser's
per-entity grid used, moved from cells to fields. The resolution order
(exception > pinned form > entity property > default) lives in
``submit_jobs_resolve``, which is pure and covered by
``tests/test_submit_jobs_resolve.py``.

That matters most for the frame range: the form used to seed from the first
checked entity and send those numbers for the whole batch, so submitting six
shots rendered all six at the first shot's length. With one entity checked
nothing can disagree, so the Render quick action's form is unchanged.

A field that offers a *choice* still spans the batch: the Render channel
menu lists the union of the checked entities' channels and submits exactly
what is checked. A channel a given entity does not define still reaches
``submit_entity_batch`` and still fails there — that visible failure is the
contract, and the pre-flight warnings surface it before the loop fires
rather than turning it into a silent skip.

Submission goes through ``tumblepipe.pipe.houdini.ui.process_dialog`` — one
farm-only ``ProcessTask`` per checked entity, each calling
``tumblepipe.farm.jobs.houdini.batch_submit.submit_entity_batch`` with its own
resolved settings. That buys a per-entity progress tree, a working Cancel and
a per-task error report, and it is safe on the GUI thread because
``ProcessExecutor`` has no worker thread: it sequences with
``QTimer.singleShot`` on the main thread, so the event loop keeps turning
between entities. A synchronous loop with one summary box at the end remains
as the fallback for when that UI is not importable (outside Houdini, or an
install missing hpm.toml's ``[python_dependencies]``).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BORDER, FONT_BODY, FONT_FAMILY,
    TEXT_PRIMARY, TEXT_SECONDARY,
)

# The catalog dir is not a package: tumbletrove loads pipeline.py by file
# path, and this dialog is loaded the same way. pipeline.py already puts
# this directory on sys.path for the underscore-prefixed modules, but the
# verify harness loads this file directly, so do it here too rather than
# depend on load order. Mirrors _pipeline_catalog's `from _pipeline_prefs
# import ...`.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import submit_jobs_resolve as resolve  # noqa: E402

log = logging.getLogger(__name__)

# Shown by an unpinned widget whose checked entities disagree. The angle
# brackets keep it from reading as a department or pool literally named
# "per entity".
PER_ENTITY_TEXT = "⟨per entity⟩"

# Pre-flight warning text. Not from the asset-browser theme: those tokens
# are background/foreground roles, and this needs to read as a caution
# against BG_DARK without being an error.
WARNING_COLOUR = "#d8a657"

# Item-data role carrying a leaf's entity URI string. Branch items have no
# value here, which is what ``_is_leaf`` keys off.
_URI_ROLE = Qt.UserRole

# Every entity has this channel implicitly, whether or not its properties
# list it. Owned by submit_jobs_resolve so the pure resolution policy and
# the widget that drives it can't drift apart.
_DEFAULT_CHANNEL = resolve.DEFAULT_CHANNEL


@dataclass
class _EntityLeaf:
    """One entity, and every tree item that stands for it.

    An entity appears once under the context root and once per group that
    contains it; ``items`` holds all of them so a check on any one can be
    mirrored to the rest.
    """
    uri: object
    name: str
    items: list = field(default_factory=list)


# ── Theme ─────────────────────────────────────────────────

_DIALOG_STYLE = f"""
QDialog {{ background-color: {BG_DARKEST}; }}
QLabel {{
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    background: transparent;
}}
QGroupBox {{
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {TEXT_PRIMARY};
}}
QGroupBox::indicator {{ width: 14px; height: 14px; }}
QLineEdit, QSpinBox, QComboBox {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 3px 5px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    min-height: 20px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    selection-background-color: {BG_DARKEST};
    selection-color: {TEXT_PRIMARY};
    outline: none;
}}
QCheckBox {{
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    spacing: 6px;
}}
QPushButton {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 5px 14px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:default {{
    background-color: {ACCENT};
    color: {BG_DARKEST};
    border-color: {ACCENT};
}}
QTreeWidget {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    outline: none;
}}
QTreeWidget::item {{ padding: 2px 0; }}
QTreeWidget::item:selected {{ background-color: {BG_DARKEST}; }}
/* The pre-flight table alternates rows; without an explicit colour Qt
   picks a light default that reads as white-on-white against this theme. */
QTreeWidget {{ alternate-background-color: {BG_DARKEST}; }}
QHeaderView::section {{
    background-color: {BG_DARKEST};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 3px 6px;
}}
"""


# ── Helpers ───────────────────────────────────────────────

def _properties_for(entity_uris: Sequence) -> list[dict]:
    """Resolved properties for each URI, read in one coherency scope.

    Bare ``get_properties`` re-stamps the config files per call, and this
    runs over the whole checked batch (which is every entity in the project
    after an "All"), so the reads are batched — same contract as the entity
    sweep. Empty list on failure.

    Importing ``tumblepipe.api`` here is a passive lookup: the catalog has
    already activated the project before showing the dialog.
    """
    uris = list(entity_uris)
    try:
        from tumblepipe.api import default_client
        config = default_client().config
        with config.coherent():
            return [config.get_properties(uri) or {} for uri in uris]
    except Exception:
        log.exception("Failed to read properties for %d entities", len(uris))
        return []


def _list_dept_names(context: str, *, only_publishable: bool, only_renderable: bool) -> list[str]:
    """Return department names for ``context`` ('shots' or 'assets').

    Filters by ``publishable`` / ``renderable`` flags. Excludes
    Python-generated departments (which can't be exported from Houdini)
    and disabled ones.
    """
    try:
        from tumblepipe.config.department import list_departments
        depts = list_departments(
            context, include_generated=False, include_disabled=False,
        )
        if only_publishable:
            depts = [d for d in depts if d.publishable]
        if only_renderable:
            depts = [d for d in depts if d.renderable]
        return [d.name for d in depts]
    except Exception:
        log.exception("Failed to list departments for context=%s", context)
        return []


def _list_selectable_entities(context: str) -> list[object]:
    """Return every terminal entity URI in ``context``, sorted by path.

    Empty list on failure — the tree then falls back to just the entities
    the dialog was opened for.

    ``list_entity_uris(closure=True)`` returns childless *category* nodes
    (e.g. an empty ``assets/CHAR``) alongside real entities, so each URI is
    vetted with ``is_terminal_entity`` (schema-keyed). That's a read per
    URI, so the whole sweep runs inside a ``coherent()`` scope — otherwise
    a project with hundreds of shots stamps the config file once per shot
    every time the dialog opens.
    """
    try:
        from tumblepipe.api import default_client
        from tumblepipe.config.entities import is_terminal_entity
        from tumblepipe.util.uri import Uri
        config = default_client().config
        root = Uri.parse_unsafe(f'entity:/{context}')
        with config.coherent():
            entities = [
                uri for uri in config.list_entity_uris(root, closure=True)
                if is_terminal_entity(config, uri)
            ]
        entities.sort(key=str)
        return entities
    except Exception:
        log.exception("Failed to list entities for the entity tree")
        return []


def _list_groups(context: str) -> list[tuple[str, list[object]]]:
    """Return ``(group_name, member_uris)`` for every group in ``context``.

    Groups are a config-authored convenience set (e.g. all the hero shots);
    they appear in the tree as a second root whose leaves *mirror* the same
    entities listed under the context root. Empty list on failure — the
    Groups root is simply not shown.
    """
    try:
        from tumblepipe.api import default_client
        from tumblepipe.config.groups import list_groups
        with default_client().config.coherent():
            groups = [
                (group.name, list(group.members))
                for group in list_groups(context)
            ]
        return sorted(groups, key=lambda item: item[0])
    except Exception:
        log.exception("Failed to list groups for context=%s", context)
        return []


# ── Widgets ───────────────────────────────────────────────

class _CheckableComboBox(QComboBox):
    """A combo box whose popup is a checkable list — pick several by mouse.

    Qt ships no multi-select combo, so this is the usual composition: a
    checkable item model behind a read-only line edit that shows the
    summary, plus an event filter that toggles the item under the cursor
    and keeps the popup open (a plain combo would close it and move the
    current index instead).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        empty_text: str = "(none)",
        hint: str = "",
    ) -> None:
        super().__init__(parent)
        self._empty_text = empty_text
        # Tooltips are rewritten on every check, so a caller's setToolTip
        # would not survive; the standing explanation comes in here instead.
        self._hint = hint
        self.setModel(QStandardItemModel(self))
        # Editable only to get a line edit to write the summary into; it is
        # read-only, and nothing the user does can insert an item.
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.lineEdit().setReadOnly(True)
        # Without an explicit delegate the check indicators stop being drawn
        # once a stylesheet is in play (the dialog's own).
        self.setItemDelegate(QStyledItemDelegate(self))
        self.view().viewport().installEventFilter(self)
        self.lineEdit().installEventFilter(self)
        self.model().dataChanged.connect(self._refresh_text)
        self.currentIndexChanged.connect(self._refresh_text)
        self._refresh_text()

    # ── API ───────────────────────────────────────────────

    def set_options(self, names: Sequence[str], checked: Sequence[str]) -> None:
        """Replace the menu with ``names``, checking those in ``checked``."""
        wanted = set(checked)
        model = self.model()
        model.clear()
        for name in names:
            item = QStandardItem(name)
            item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
            )
            item.setCheckState(Qt.Checked if name in wanted else Qt.Unchecked)
            model.appendRow(item)
        self._refresh_text()

    def options(self) -> list[str]:
        model = self.model()
        return [model.item(i).text() for i in range(model.rowCount())]

    def checked_items(self) -> list[str]:
        """Checked names, in menu order."""
        model = self.model()
        return [
            model.item(i).text()
            for i in range(model.rowCount())
            if model.item(i).checkState() == Qt.Checked
        ]

    def set_checked(self, checked: Sequence[str]) -> None:
        """Check exactly ``checked``, leaving the menu itself alone."""
        wanted = set(checked)
        model = self.model()
        for i in range(model.rowCount()):
            item = model.item(i)
            item.setCheckState(
                Qt.Checked if item.text() in wanted else Qt.Unchecked
            )

    # ── Internals ─────────────────────────────────────────

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self.view().viewport() and kind == QEvent.MouseButtonRelease:
            index = self.view().indexAt(event.position().toPoint())
            item = self.model().itemFromIndex(index) if index.isValid() else None
            if item is not None and item.isCheckable():
                item.setCheckState(
                    Qt.Unchecked
                    if item.checkState() == Qt.Checked else Qt.Checked
                )
            # Swallowed either way: a release would otherwise activate the
            # row and close the popup, and checking several channels in one
            # trip is the point.
            return True
        if obj is self.lineEdit() and kind == QEvent.MouseButtonPress:
            # The line edit is the widget most of this looks like, so a
            # click there opens the menu instead of doing nothing.
            self.showPopup()
            return True
        return super().eventFilter(obj, event)

    def _refresh_text(self, *_args) -> None:
        names = self.checked_items()
        text = ", ".join(names) if names else self._empty_text
        self.lineEdit().setText(text)
        self.lineEdit().setCursorPosition(0)
        # The summary elides in a narrow form; the tooltip spells it out.
        lines = names or [self._empty_text]
        if self._hint:
            lines = [self._hint, ""] + lines
        self.setToolTip("\n".join(lines))


# ── Tri-state form fields ────────────────────────────

class _PinnedField:
    """A form widget that can defer to each checked entity's own value.

    Three states, and the middle one is the reason this class exists:

    * **pinned** — the artist set it, so it applies to the whole batch and
      is drawn normally.
    * **unpinned, agreed** — every checked entity resolves to the same
      value, so the widget shows it, dimmed. Informative, but still the
      entity's value: check in a shot that disagrees and it turns into…
    * **unpinned, mixed** — the widget shows ``⟨per entity⟩`` and the
      submit sends no value for it at all, so each entity keeps its own.

    Pinning happens on *user* interaction only. Where Qt offers a
    user-only signal (``textEdited``, ``activated``, ``clicked``) that is
    what's connected; ``QSpinBox`` has none, so its ``valueChanged`` is
    gated on the dialog's ``_seeding`` flag.

    The unset representation is per widget type and deliberately native:
    a spin box parks on its ``minimum``, which is one step below the field's
    real lower bound and carries ``specialValueText``; a check box on
    ``PartiallyChecked`` (tri-state); a combo on a placeholder row; a line
    edit on empty-with-placeholder. Three of those are also a way *back* to
    unpinned without the revert button — which stays for the spin boxes,
    because spinning down past the field's minimum is not an affordance.
    """

    def __init__(self, key: str, widget: QWidget, kind: str) -> None:
        self.key = key
        self.widget = widget
        self.kind = kind  # 'spin' | 'check' | 'combo' | 'line'
        self.pinned = False

    # ── state ──────────────────────────────────────

    def pin(self) -> None:
        """Mark as an explicit batch-wide choice (a user edit happened)."""
        if not self.pinned:
            self.pinned = True
            self._restyle()

    def seed(self, value: Any) -> None:
        """Show ``value`` as the unpinned per-entity default.

        ``MIXED`` (the entities disagree) and ``REQUIRED`` (none of them
        configures it) both render as the unset representation — in both
        cases there is no single number to show, and in both cases the
        submit must not invent one.
        """
        self.pinned = False
        unset = value is resolve.MIXED or value is resolve.REQUIRED
        self._write(None if unset else value)
        self._restyle()

    def unpin(self, value: Any) -> None:
        """Revert to the per-entity default (the ↺ button)."""
        self.seed(value)

    def value(self) -> Any:
        """The widget's value, or ``MIXED`` when it sits on unset."""
        w = self.widget
        if self.kind == 'spin':
            # The minimum IS the sentinel (see _spin), so no separate state.
            raw = w.value()
            return resolve.MIXED if raw == w.minimum() else raw
        if self.kind == 'check':
            state = w.checkState()
            if state == Qt.PartiallyChecked:
                return resolve.MIXED
            return state == Qt.Checked
        if self.kind == 'combo':
            if w.currentIndex() <= 0:
                return resolve.MIXED
            return w.currentText()
        text = w.text().strip()
        return text or resolve.MIXED

    # ── widget plumbing ────────────────────────────

    def _write(self, value: Any) -> None:
        w = self.widget
        if self.kind == 'spin':
            w.setValue(w.minimum() if value is None else int(value))
        elif self.kind == 'check':
            if value is None:
                w.setCheckState(Qt.PartiallyChecked)
            else:
                w.setCheckState(Qt.Checked if value else Qt.Unchecked)
        elif self.kind == 'combo':
            if value is None:
                w.setCurrentIndex(0)
            else:
                index = w.findText(str(value))
                # A department the entity names but this context does not
                # offer would otherwise silently select the placeholder's
                # neighbour; park on the placeholder instead.
                w.setCurrentIndex(index if index > 0 else 0)
        else:
            w.setText('' if value is None else str(value))

    def _restyle(self) -> None:
        """Dim while unpinned — 'this follows the entity, not you'."""
        font = self.widget.font()
        font.setItalic(not self.pinned)
        self.widget.setFont(font)
        colour = TEXT_PRIMARY if self.pinned else TEXT_SECONDARY
        self.widget.setStyleSheet(f"color: {colour};")

# ── Dialog ────────────────────────────────────────────────

class SubmitJobsDialog(QDialog):
    """Compact submit dialog for one or more pipeline entities.

    Args:
        entity_uris: List of ``tumblepipe.util.uri.Uri`` (or any object with
            ``str(uri)``) — one per selected entity. All must share the
            same ``context`` (i.e. all shots OR all assets).
        entity_names: Display names parallel to ``entity_uris``.
        context: ``'shots'`` or ``'assets'`` — drives department filtering.
        parent: Parent widget. Pass ``hou.qt.mainWindow()`` from Houdini.
        department: Department the dialog was opened *from* — the loaded
            workfile's department when the dialog comes from a scene quick
            action. Seeds the Render (and Playblast) department combos, so
            submitting from a lighting workfile defaults to lighting instead
            of whichever department config happens to list first. ``None``
            (the asset-browser path, which has no "opened from") falls back
            to the entity's ``submission.*.department`` property.
    """

    def __init__(
        self,
        entity_uris: Sequence,
        entity_names: Sequence[str],
        context: str,
        parent: QWidget | None = None,
        department: str | None = None,
    ) -> None:
        super().__init__(parent)
        if not entity_uris:
            raise ValueError("entity_uris must be non-empty")
        if context not in ("shots", "assets"):
            raise ValueError(f"context must be 'shots' or 'assets', got {context!r}")
        self._entity_uris = list(entity_uris)
        self._entity_names = list(entity_names)
        self._context = context
        # Department the dialog was opened from (a loaded workfile), or None.
        self._open_department = department or None

        # Every entity leaf in the tree, keyed by URI string. One URI can own
        # several items (once under the context root, once per group that
        # contains it) — check state is mirrored across them.
        self._leaves: dict[str, _EntityLeaf] = {}
        # Guards the itemChanged handler against the writes it makes itself.
        self._syncing = False
        # Guards the spin boxes' valueChanged against programmatic seeding
        # (Qt gives QSpinBox no user-only edit signal).
        self._seeding = False
        # Every tri-state form field, keyed by its settings key.
        self._fields: dict[str, _PinnedField] = {}
        # Resolved properties for the checked batch, refreshed on every
        # selection change inside one coherency scope.
        self._properties: list[dict] = []
        # Context-derived defaults the pure resolver can't look up itself
        # (the first publishable / renderable department). Filled once the
        # department combos are populated.
        self._fallbacks: dict = {}

        self.setWindowTitle("Submit Jobs")
        self.setMinimumWidth(520)
        self.setStyleSheet(_DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Header — entity count and a truncated name list.
        self._header = QLabel(self._build_header_text())
        self._header.setWordWrap(True)
        self._header.setStyleSheet(f"color: {TEXT_SECONDARY};")
        root.addWidget(self._header)

        # Entity tree — check any number of entities in this context. The
        # entities the dialog was opened for start checked; everything else
        # in the project is one click away, so a single-entity open (the
        # Render quick action) can still fan out to a whole batch.
        root.addWidget(self._build_entity_tree())

        self._publish_box = self._build_publish_section()
        self._publish_box.toggled.connect(
            lambda *_a: self._refresh_preflight()
        )
        root.addWidget(self._publish_box)

        self._render_box = self._build_render_section()
        self._render_box.toggled.connect(
            lambda *_a: self._refresh_preflight()
        )
        root.addWidget(self._render_box)

        # Playblast is a shots-only GL preview; the section is absent entirely
        # for the assets context.
        self._playblast_box = None
        if self._context == "shots":
            self._playblast_box = self._build_playblast_section()
            self._playblast_box.toggled.connect(
                lambda *_a: self._refresh_preflight()
            )
            root.addWidget(self._playblast_box)

        # Pre-flight sits below the job sections: it reports on them, so it
        # reads top-to-bottom as "who, what, then what that actually means".
        self._preflight_box = self._build_preflight()
        root.addWidget(self._preflight_box)

        self._apply_open_department()
        self._reseed_form(initial=True)

        # Submit / Cancel row.
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self,
        )
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText("Submit")
        ok_btn.setDefault(True)
        buttons.accepted.connect(self._on_submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── Tri-state field factories ────────────────────────

    def _register(self, key: str, widget: QWidget, kind: str) -> QWidget:
        """Wrap ``widget`` as a tri-state field and wire its pin signal.

        A batch field (one with no per-entity source, e.g. Range mode) is
        not registered: it has no unpinned state, so it is read straight off
        its widget at submit time.
        """
        entry = _PinnedField(key, widget, kind)
        self._fields[key] = entry
        # Look the signal up by name: a dict of the four bound signals would
        # evaluate every branch, and only one of them exists on any given
        # widget class.
        signal_name = {
            'spin': 'valueChanged', 'check': 'clicked',
            'combo': 'activated', 'line': 'textEdited',
        }[kind]
        getattr(widget, signal_name).connect(
            lambda *_a: self._refresh_preflight()
        )
        if kind == 'spin':
            # No user-only signal exists; the dialog's _seeding flag is
            # what separates a user edit from a reseed.
            widget.valueChanged.connect(
                lambda *_a, e=entry: None if self._seeding else e.pin()
            )
        elif kind == 'check':
            widget.clicked.connect(lambda *_a, e=entry: e.pin())
        elif kind == 'combo':
            # activated fires only on user interaction, unlike
            # currentIndexChanged. Index 0 is the placeholder, so picking it
            # is an explicit "go back to per entity".
            widget.activated.connect(
                lambda index, e=entry: e.pin() if index > 0
                else e.seed(resolve.MIXED)
            )
        else:
            widget.textEdited.connect(lambda *_a, e=entry: e.pin())
        return widget

    def _spin(self, key: str, low: int, high: int) -> QSpinBox:
        """An integer field that can park on ``⟨per entity⟩``.

        The range is widened by exactly one step below ``low`` and that step
        carries ``specialValueText`` — Qt's own idiom for "no value". One
        step, not a far-away sentinel: the minimum is reachable by spinning,
        so anything further would let a bounded field (pre-roll, tiles) be
        typed below its real lower bound.
        """
        box = QSpinBox()
        box.setRange(low - 1, high)
        box.setSpecialValueText(PER_ENTITY_TEXT)
        box.setToolTip(
            "Leave on ⟨per entity⟩ to let each checked entity use its "
            "own configured value."
        )
        self._register(key, box, 'spin')
        return box

    def _check(self, key: str, label: str) -> QCheckBox:
        box = QCheckBox(label)
        box.setTristate(True)
        box.setToolTip(
            "Partially checked = each entity keeps its own configured value."
        )
        self._register(key, box, 'check')
        return box

    def _combo(self, key: str, names: Sequence[str]) -> QComboBox:
        """A department combo with a leading per-entity placeholder."""
        box = QComboBox()
        box.addItem(PER_ENTITY_TEXT)
        box.addItems(list(names))
        self._register(key, box, 'combo')
        return box

    def _line(self, key: str, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(f"{PER_ENTITY_TEXT} — e.g. {placeholder}")
        self._register(key, edit, 'line')
        return edit

    def _revert(self, *keys: str) -> QPushButton:
        """A ↺ that returns its fields to the per-entity default.

        One per *row* rather than per widget: a frame row is two spin boxes
        only meaningful together, and a button apiece would double the
        form's visual weight for a control most submits never touch.
        """
        button = QPushButton("↺")
        button.setFixedWidth(24)
        button.setToolTip("Use each entity's own configured value")
        button.clicked.connect(lambda: self._unpin(keys))
        return button

    def _unpin(self, keys: Sequence[str]) -> None:
        seeded = self._seed_values()
        self._seeding = True
        try:
            for key in keys:
                entry = self._fields.get(key)
                if entry is not None:
                    entry.unpin(seeded.get(key, resolve.MIXED))
        finally:
            self._seeding = False

    # ── UI construction ───────────────────────────────────

    def _build_header_text(self) -> str:
        n = len(self._entity_uris)
        if n == 0:
            return f"No {self._context} checked — check at least one to submit."
        names = ", ".join(self._entity_names[:8])
        if len(self._entity_names) > 8:
            names += f", +{len(self._entity_names) - 8} more"
        suffix = "entity" if n == 1 else "entities"
        return f"Submit jobs for {n} {suffix} ({self._context}): {names}"

    def _build_entity_tree(self) -> QWidget:
        """Build the checkable entity tree, scoped to the dialog's context.

        Two roots: the context itself (``Shots`` / ``Assets``), nested by
        category, and ``Groups`` (when the project has any), whose leaves
        mirror the same entities. The tree is scoped to one context because
        the department lists and the group listing both are — batching shots
        and assets together would need two department combos.

        Entities the dialog was opened for start checked. If one of them
        isn't in the config listing (an off-config scene), it still gets a
        leaf, so the dialog never silently drops its own target.
        """
        wrap = QWidget()
        column = QVBoxLayout(wrap)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        # Filter + bulk-check row.
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(f"Filter {self._context}…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        all_btn = QPushButton("All")
        all_btn.setToolTip("Check every visible entity")
        all_btn.clicked.connect(lambda: self._set_all_checked(True))
        none_btn = QPushButton("None")
        none_btn.setToolTip("Uncheck every visible entity")
        none_btn.clicked.connect(lambda: self._set_all_checked(False))
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.addWidget(self._filter, 1)
        top_row.addWidget(all_btn)
        top_row.addWidget(none_btn)
        top_wrap = QWidget()
        top_wrap.setLayout(top_row)
        column.addWidget(top_wrap)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumHeight(160)
        self._tree.setMaximumHeight(260)
        column.addWidget(self._tree)

        opened = {str(uri): uri for uri in self._entity_uris}
        listed = _list_selectable_entities(self._context)
        # Union, so an off-config opened entity survives; sorted for a stable
        # tree order (which is also the submission order).
        by_uri = {str(uri): uri for uri in listed}
        by_uri.update(opened)
        all_uris = [by_uri[key] for key in sorted(by_uri)]

        context_root = QTreeWidgetItem(self._tree, [self._context.capitalize()])
        context_root.setFlags(context_root.flags() | Qt.ItemIsUserCheckable)
        context_root.setCheckState(0, Qt.Unchecked)
        for uri in all_uris:
            # segments == [context, *categories, name]
            parent = self._branch(context_root, uri.segments[1:-1])
            self._add_leaf(parent, uri)

        for name, members in _list_groups(self._context):
            group_root = None
            for member in members:
                key = str(member)
                if key not in by_uri:
                    continue  # stale member — not a live entity any more
                if group_root is None:
                    groups_root = self._groups_root()
                    group_root = QTreeWidgetItem(groups_root, [name])
                    group_root.setFlags(group_root.flags() | Qt.ItemIsUserCheckable)
                    group_root.setCheckState(0, Qt.Unchecked)
                self._add_leaf(group_root, by_uri[key])

        self._tree.expandItem(context_root)
        # Seed the check state from the entities the dialog was opened for.
        self._syncing = True
        for key in opened:
            for item in self._leaves[key].items:
                item.setCheckState(0, Qt.Checked)
        self._syncing = False
        self._refresh_branch_states()
        self._scroll_to_first_checked()
        self._tree.itemChanged.connect(self._on_item_changed)
        return wrap

    def _groups_root(self) -> QTreeWidgetItem:
        """The lazily-created ``Groups`` top-level item."""
        existing = getattr(self, "_groups_item", None)
        if existing is None:
            existing = QTreeWidgetItem(self._tree, ["Groups"])
            existing.setFlags(existing.flags() | Qt.ItemIsUserCheckable)
            existing.setCheckState(0, Qt.Unchecked)
            self._groups_item = existing
        return existing

    def _branch(self, root: QTreeWidgetItem, path: list[str]) -> QTreeWidgetItem:
        """Return (creating as needed) the branch item at ``path`` under
        ``root`` — e.g. ``['000']`` for ``entity:/shots/000/sh020``."""
        node = root
        for segment in path:
            child = None
            for i in range(node.childCount()):
                candidate = node.child(i)
                if candidate.text(0) == segment and not self._is_leaf(candidate):
                    child = candidate
                    break
            if child is None:
                child = QTreeWidgetItem(node, [segment])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
            node = child
        return node

    def _add_leaf(self, parent: QTreeWidgetItem, uri) -> None:
        """Add a checkable entity leaf under ``parent`` and register it as a
        mirror of every other leaf carrying the same URI."""
        key = str(uri)
        name = uri.segments[-1]
        item = QTreeWidgetItem(parent, [name])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Unchecked)
        item.setData(0, _URI_ROLE, key)
        leaf = self._leaves.get(key)
        if leaf is None:
            self._leaves[key] = _EntityLeaf(uri=uri, name=name, items=[item])
        else:
            leaf.items.append(item)

    @staticmethod
    def _is_leaf(item: QTreeWidgetItem) -> bool:
        return item.data(0, _URI_ROLE) is not None

    # ── Tree check state ──────────────────────────────────

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            state = item.checkState(0)
            if self._is_leaf(item):
                self._mirror_leaf(item, state)
            else:
                # A branch check cascades to its (visible) leaves. Filtered-out
                # leaves are left alone — checking "Shots" while a filter is
                # active shouldn't quietly submit the shots you can't see.
                for leaf_item in self._descendant_leaves(item):
                    if not leaf_item.isHidden():
                        self._mirror_leaf(leaf_item, state)
            self._refresh_branch_states()
        finally:
            self._syncing = False
        self._on_selection_changed()

    def _mirror_leaf(self, item: QTreeWidgetItem, state) -> None:
        """Apply ``state`` to every leaf sharing this item's URI (the same
        entity can sit under the context root and under N groups)."""
        key = item.data(0, _URI_ROLE)
        leaf = self._leaves.get(key)
        if leaf is None:
            return
        for mirror in leaf.items:
            if mirror.checkState(0) != state:
                mirror.setCheckState(0, state)

    def _descendant_leaves(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        if self._is_leaf(item):
            return [item]
        found: list[QTreeWidgetItem] = []
        for i in range(item.childCount()):
            found.extend(self._descendant_leaves(item.child(i)))
        return found

    def _refresh_branch_states(self) -> None:
        """Roll leaf check states up into partially-checked branches."""
        for i in range(self._tree.topLevelItemCount()):
            self._roll_up(self._tree.topLevelItem(i))

    def _roll_up(self, item: QTreeWidgetItem) -> None:
        if self._is_leaf(item):
            return
        leaves = self._descendant_leaves(item)
        if not leaves:
            return
        for i in range(item.childCount()):
            self._roll_up(item.child(i))
        checked = sum(1 for leaf in leaves if leaf.checkState(0) == Qt.Checked)
        if checked == 0:
            state = Qt.Unchecked
        elif checked == len(leaves):
            state = Qt.Checked
        else:
            state = Qt.PartiallyChecked
        if item.checkState(0) != state:
            item.setCheckState(0, state)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        self._syncing = True
        try:
            for leaf in self._leaves.values():
                if all(item.isHidden() for item in leaf.items):
                    continue  # filtered out — leave it as it is
                for item in leaf.items:
                    item.setCheckState(0, state)
            self._refresh_branch_states()
        finally:
            self._syncing = False
        self._on_selection_changed()

    def _apply_filter(self, text: str) -> None:
        """Hide leaves whose name doesn't match, then hide emptied branches.

        Check state is untouched — filtering narrows the view, never the
        submission.
        """
        needle = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            self._filter_item(self._tree.topLevelItem(i), needle)
        if needle:
            self._tree.expandAll()

    def _filter_item(self, item: QTreeWidgetItem, needle: str) -> bool:
        """Hide ``item`` unless it (or a descendant) matches. Returns
        whether it stayed visible."""
        if self._is_leaf(item):
            visible = not needle or needle in item.text(0).lower()
            item.setHidden(not visible)
            return visible
        any_visible = False
        for i in range(item.childCount()):
            if self._filter_item(item.child(i), needle):
                any_visible = True
        item.setHidden(not any_visible)
        return any_visible

    def _scroll_to_first_checked(self) -> None:
        for i in range(self._tree.topLevelItemCount()):
            for item in self._descendant_leaves(self._tree.topLevelItem(i)):
                if item.checkState(0) == Qt.Checked:
                    self._tree.scrollToItem(item)
                    parent = item.parent()
                    while parent is not None:
                        self._tree.expandItem(parent)
                        parent = parent.parent()
                    return

    def _checked_entities(self) -> list[tuple[object, str]]:
        """``(uri, name)`` for every checked entity, in tree order."""
        return [
            (leaf.uri, leaf.name)
            for key, leaf in sorted(self._leaves.items())
            if leaf.items[0].checkState(0) == Qt.Checked
        ]

    def _on_selection_changed(self) -> None:
        """Re-target the submission at whatever is checked now.

        Reseeding only touches *unpinned* fields, so growing the batch
        re-derives the per-entity defaults without discarding anything the
        artist deliberately set for the whole batch.
        """
        checked = self._checked_entities()
        self._entity_uris = [uri for uri, _ in checked]
        self._entity_names = [name for _, name in checked]
        self._header.setText(self._build_header_text())
        self._reseed_form()

    def _build_publish_section(self) -> QGroupBox:
        box = QGroupBox("Publish")
        box.setCheckable(True)
        box.setChecked(False)
        form = QFormLayout(box)
        form.setContentsMargins(10, 14, 10, 10)
        form.setSpacing(6)

        pub_depts = _list_dept_names(
            self._context, only_publishable=True, only_renderable=False,
        )
        # The department lists are context-scoped and the context is fixed
        # for the dialog's life, so they are built once here rather than
        # refilled on every selection change.
        self._fallbacks['pub_department'] = pub_depts[0] if pub_depts else None
        self._pub_dept = self._combo('pub_department', pub_depts)
        self._pub_dept.setToolTip(
            "Publishes every department up to and including this one, in "
            "pipeline order."
        )
        form.addRow("Department:", self._pub_dept)

        self._pub_pool = self._line('pub_pool', 'general')
        form.addRow("Pool:", self._pub_pool)

        self._pub_priority = self._spin('pub_priority', 0, 100)
        form.addRow("Priority:", self._row(
            self._pub_priority, revert=('pub_priority',),
        ))

        return box

    def _row(self, *widgets, revert: Sequence[str] = ()) -> QWidget:
        """Pack widgets onto one form row, with an optional ↺ at the end.

        A plain string becomes an inline label, so a row can read
        ``Pri: [ ] Tiles: [ ] Batch: [ ] ↺`` instead of three form rows.
        """
        layout = QHBoxLayout()
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            layout.addWidget(
                QLabel(widget) if isinstance(widget, str) else widget
            )
        if revert:
            layout.addStretch(1)
            layout.addWidget(self._revert(*revert))
        wrap = QWidget()
        wrap.setLayout(layout)
        return wrap

    def _build_render_section(self) -> QGroupBox:
        box = QGroupBox("Render")
        box.setCheckable(True)
        # Render is the common case for this dialog (publish has its own
        # quick action), so it starts enabled.
        box.setChecked(True)
        form = QFormLayout(box)
        form.setContentsMargins(10, 14, 10, 10)
        form.setSpacing(6)

        rnd_depts = _list_dept_names(
            self._context, only_publishable=False, only_renderable=True,
        )
        self._renderable_departments = rnd_depts
        self._fallbacks['render_department'] = (
            rnd_depts[0] if rnd_depts else None
        )
        self._fallbacks['pb_department'] = self._fallbacks['render_department']

        self._rnd_dept = self._combo('render_department', rnd_depts)
        self._rnd_dept.setToolTip(
            "Renders every department up to and including this one, in "
            "pipeline order — departments after it are left out of the "
            "composed stage. Also names the render output."
        )
        form.addRow("Department:", self._rnd_dept)

        # Channels — a checkable menu of the channels the checked entities
        # actually define. A batch field by contract: the menu spans the
        # batch and submits exactly what is checked, so a channel a given
        # entity lacks still fails visibly on the farm rather than being
        # quietly dropped. The pre-flight table warns about it up front.
        self._rnd_channels = _CheckableComboBox(
            empty_text="(none — check at least one)",
            hint="Channels to render — one render per checked channel.",
        )
        all_channels = QPushButton("All")
        all_channels.setToolTip("Check every channel")
        all_channels.clicked.connect(
            lambda: self._rnd_channels.set_checked(
                self._rnd_channels.options()
            )
        )
        no_channels = QPushButton("None")
        no_channels.setToolTip("Uncheck every channel")
        no_channels.clicked.connect(lambda: self._rnd_channels.set_checked([]))
        self._rnd_channels.model().dataChanged.connect(
            lambda *_a: self._refresh_preflight()
        )
        form.addRow("Channels:", self._row(
            self._rnd_channels, all_channels, no_channels,
        ))

        # Range mode — a batch field: a choice about the submission, not a
        # property of the entity, so it has no per-entity state. Full range
        # submits the full_render chain (all frames + slapcomp/mp4); First /
        # Middle / Last submits partial_render (3 check frames + notify) for
        # a look before committing the farm to the whole range.
        self._rnd_mode = QComboBox()
        self._rnd_mode.addItems(["Full range", "First / Middle / Last"])
        form.addRow("Range:", self._rnd_mode)

        self._rnd_first = self._spin('first_frame', -1_000_000, 1_000_000)
        self._rnd_last = self._spin('last_frame', -1_000_000, 1_000_000)
        form.addRow("Frames:", self._row(
            self._rnd_first, "→", self._rnd_last,
            revert=('first_frame', 'last_frame'),
        ))

        self._rnd_pre = self._spin('pre_roll', 0, 1000)
        self._rnd_post = self._spin('post_roll', 0, 1000)
        form.addRow("Pre / Post roll:", self._row(
            self._rnd_pre, "/", self._rnd_post,
            revert=('pre_roll', 'post_roll'),
        ))

        self._rnd_pool = self._line('render_pool', 'general')
        form.addRow("Pool:", self._rnd_pool)

        self._rnd_priority = self._spin('render_priority', 0, 100)
        self._rnd_tile = self._spin('tile_count', 1, 64)
        self._rnd_batch = self._spin('batch_size', 1, 1000)
        form.addRow("", self._row(
            "Pri:", self._rnd_priority, "Tiles:", self._rnd_tile,
            "Batch:", self._rnd_batch,
            revert=('render_priority', 'tile_count', 'batch_size'),
        ))

        self._rnd_samples = self._spin('samples', 1, 4096)
        form.addRow("Samples:", self._row(
            self._rnd_samples, revert=('samples',),
        ))

        # Tri-state: PartiallyChecked means "each entity keeps its own",
        # and is also the way back after clicking one by accident.
        self._rnd_denoise = self._check('denoise', "Denoise")
        self._rnd_mblur = self._check('mblur', "Motion blur")
        self._rnd_dof = self._check('dof', "DOF")
        form.addRow("", self._row(
            self._rnd_denoise, self._rnd_mblur, self._rnd_dof,
            revert=('denoise', 'mblur', 'dof'),
        ))

        # Batch fields: plain two-state checkboxes, no per-entity source.
        self._rnd_standalone = QCheckBox("Standalone")
        self._rnd_standalone.setChecked(False)
        self._rnd_copy_edit = QCheckBox("Copy to edit")
        self._rnd_copy_edit.setChecked(False)
        form.addRow("", self._row(
            self._rnd_standalone, self._rnd_copy_edit,
        ))

        return box

    def _build_playblast_section(self) -> QGroupBox:
        """A GL (Storm) preview on the farm — shots only.

        The department is just the output label (which
        ``playblast/<shot>/<dept>/`` and daily the mp4 lands under); the
        input is always the shot's staged 'default' stage, and the frame
        range is derived per-shot from config at submit time — the pattern
        the render half now follows too — so there is no frame or channel
        field here. Starts unchecked: an opt-in extra alongside render.
        """
        box = QGroupBox("Playblast")
        box.setCheckable(True)
        box.setChecked(False)
        form = QFormLayout(box)
        form.setContentsMargins(10, 14, 10, 10)
        form.setSpacing(6)

        self._pb_dept = self._combo(
            'pb_department', self._renderable_departments,
        )
        self._pb_dept.setToolTip(
            "Playblasts every department up to and including this one, in "
            "pipeline order — the same cut the render uses."
        )
        form.addRow("Department:", self._pb_dept)

        self._pb_width = self._spin('pb_res_x', 16, 8192)
        self._pb_height = self._spin('pb_res_y', 16, 8192)
        form.addRow("Resolution:", self._row(
            self._pb_width, "×", self._pb_height,
            revert=('pb_res_x', 'pb_res_y'),
        ))

        self._pb_pool = self._line('pb_pool', 'general')
        form.addRow("Pool:", self._pb_pool)

        self._pb_priority = self._spin('pb_priority', 0, 100)
        form.addRow("Priority:", self._row(
            self._pb_priority, revert=('pb_priority',),
        ))

        return box

    # ── Seeding ───────────────────────────────────────────

    def _apply_open_department(self) -> None:
        """Pin the department the dialog was opened from.

        Submitting from a lighting workfile almost always means "render what
        I am looking at", for every shot in the batch — an explicit intent,
        so it pins rather than merely seeding. Skipped when the department
        is not renderable, or when the dialog came from the browser (which
        has no opened-from department); those combos then resolve per entity
        from ``submission.render.department``.
        """
        name = self._open_department
        if not name or name not in self._renderable_departments:
            return
        for key in ('render_department', 'pb_department'):
            entry = self._fields.get(key)
            if entry is None:
                continue
            index = entry.widget.findText(name)
            if index > 0:
                entry.widget.setCurrentIndex(index)
                entry.pin()

    def _properties_aligned(self) -> list[dict]:
        """Resolved properties, guaranteed parallel to ``_entity_uris``.

        ``_properties_for`` returns ``[]`` on a config failure, and a short
        list would silently drop the tail of the batch out of a zip — so pad
        rather than truncate.
        """
        properties = list(self._properties)
        if len(properties) != len(self._entity_uris):
            return [{} for _ in self._entity_uris]
        return properties

    def _seed_values(self) -> dict:
        """Per-field agreement across the checked batch (or ``MIXED``)."""
        return resolve.seed_form(
            self._properties_aligned(),
            sections=resolve.SECTIONS,
            fallbacks=self._fallbacks,
        )

    def _reseed_form(self, *, initial: bool = False) -> None:
        """Re-derive every *unpinned* field from the checked entities.

        Pinned fields are the artist's explicit batch-wide choice and
        survive a change of selection untouched. This replaces the old
        "reseed only when the primary entity changes" rule: there is no
        primary any more, and growing the batch can no longer clobber a
        tuned form because it only ever re-derives what the artist did not
        set.
        """
        self._properties = _properties_for(self._entity_uris)
        seeded = self._seed_values()
        self._seeding = True
        try:
            for key, entry in self._fields.items():
                if not entry.pinned:
                    entry.seed(seeded.get(key, resolve.MIXED))
        finally:
            self._seeding = False
        self._refresh_channel_options(initial=initial)
        self._refresh_preflight()

    def _refresh_channel_options(self, *, initial: bool = False) -> None:
        """Repopulate the channel menu from the checked entities.

        The menu lists the *union* over the batch, because a channel only
        the second checked shot defines still has to be selectable — which
        is what typing into the old csv field allowed.

        On the first build the *intersection* starts checked — the channels
        every opened entity actually defines. For one entity that is its own
        list, exactly what the csv field pre-filled; for a batch it is the
        largest set that renders on all of them. Seeding the union instead
        would check every channel any shot defines and warn on all of them
        (paleindia shots carry 6-13 channels and barely overlap).

        After that, picks carry over by name, so a channel arriving with an
        entity checked *into* the batch starts unchecked: widening the batch
        must never widen the render.
        """
        properties = self._properties_aligned()
        names = resolve.channel_union(properties)
        if initial:
            checked = resolve.channel_intersection(properties)
        else:
            if names == self._rnd_channels.options():
                return  # nothing new in the batch — leave the picks alone
            checked = self._rnd_channels.checked_items()
        self._rnd_channels.set_options(names, checked)

    # ── Pre-flight ────────────────────────────────────────

    def _build_preflight(self) -> QGroupBox:
        """A table of what each checked entity will actually be submitted with.

        The form is no longer one shared override, so "what am I about to
        send?" stopped being answerable by reading the form. This answers it:
        one row per checked entity, one column per setting the batch does
        *not* agree on, plus whatever warnings that entity would hit.

        Every warning here used to surface only as a ``BatchSubmitError`` in
        the summary box — after the loop had already submitted every entity
        ahead of it.
        """
        box = QGroupBox("Pre-flight")
        box.setCheckable(True)
        # Collapsed by default: a homogeneous batch has nothing to say, and
        # a single-entity submit is the common case.
        box.setChecked(False)
        column = QVBoxLayout(box)
        column.setContentsMargins(10, 14, 10, 10)
        column.setSpacing(6)

        self._preflight = QTreeWidget()
        self._preflight.setRootIsDecorated(False)
        self._preflight.setAlternatingRowColors(True)
        self._preflight.setMinimumHeight(120)
        self._preflight.setMaximumHeight(220)
        column.addWidget(self._preflight)

        self._preflight_note = QLabel()
        self._preflight_note.setWordWrap(True)
        self._preflight_note.setStyleSheet(f"color: {TEXT_SECONDARY};")
        column.addWidget(self._preflight_note)

        box.toggled.connect(lambda *_a: self._refresh_preflight())
        return box

    def _refresh_preflight(self) -> None:
        """Rebuild the pre-flight rows from the current form and selection.

        Cheap enough to run on every keystroke: the batch's properties are
        already resolved and cached, so this is pure dict work — no config
        reads, and therefore none of the stat-storm risk that the entity
        sweep had to be scoped against.
        """
        table = getattr(self, "_preflight", None)
        if table is None:
            return  # called during construction, before the table exists
        table.clear()
        if not self._preflight_box.isChecked():
            self._preflight_note.setText("")
            return

        rows = self._resolved_batch()
        sections = self._active_sections()
        columns = resolve.varying_fields(
            self._properties_aligned(),
            sections=sections,
            pinned=self._pinned_keys(),
            fallbacks=self._fallbacks,
        )
        table.setColumnCount(2 + len(columns))
        table.setHeaderLabels(
            [self._context[:-1].capitalize()]
            + [column.label for column in columns]
            + ["Warnings"]
        )

        warned = 0
        for name, settings, warnings in rows:
            cells = [name]
            for column in columns:
                value = settings.get(column.key)
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                cells.append("—" if value is None else str(value))
            cells.append("; ".join(warnings))
            item = QTreeWidgetItem(table, cells)
            if warnings:
                warned += 1
                item.setForeground(len(cells) - 1, QColor(WARNING_COLOUR))
        for index in range(table.columnCount()):
            table.resizeColumnToContents(index)

        if not rows:
            note = f"Nothing checked — check at least one {self._context[:-1]}."
        elif not columns:
            note = (
                f"All {len(rows)} agree on every setting shown here."
                if len(rows) > 1 else ""
            )
        else:
            varying = ", ".join(c.label.lower() for c in columns)
            note = f"Varies across the batch: {varying}."
        if warned:
            note += (
                f" {warned} of {len(rows)} would be submitted with a warning."
            )
        self._preflight_note.setText(note.strip())

    # ── Resolution ────────────────────────────────────────

    def _active_sections(self) -> list[str]:
        """The enabled job sections, in submission order."""
        boxes = (
            ('publish', self._publish_box),
            ('render', self._render_box),
            ('playblast', self._playblast_box),
        )
        return [
            name for name, box in boxes
            if box is not None and box.isChecked()
        ]

    def _form_values(self) -> dict:
        """Every current form value, keyed by settings key.

        Tri-state fields report ``MIXED`` when they sit on their unset
        representation; the resolver reads that as "fall through to the
        entity". The batch fields are read straight off their widgets —
        they have no per-entity source to fall through to.
        """
        values = {key: entry.value() for key, entry in self._fields.items()}
        values.update({
            'variants': self._rnd_channels.checked_items(),
            'render_mode': (
                'first_middle_last'
                if self._rnd_mode.currentIndex() == 1 else 'full'
            ),
            'standalone': self._rnd_standalone.isChecked(),
            'copy_to_edit': self._rnd_copy_edit.isChecked(),
        })
        return values

    def _pinned_keys(self) -> list[str]:
        """Fields the artist explicitly set — these apply to the whole batch."""
        return [key for key, entry in self._fields.items() if entry.pinned]

    def _resolved_batch(self) -> list[tuple[str, dict, list[str]]]:
        """``(name, settings, warnings)`` for every checked entity.

        The single source of truth for both the pre-flight table and the
        submit loop, so what the table shows is by construction what gets
        submitted.
        """
        sections = self._active_sections()
        form = self._form_values()
        pinned = self._pinned_keys()
        rows: list[tuple[str, dict, list[str]]] = []
        # strict: _properties_aligned() guarantees the pairing, and a
        # silent truncation here would drop the tail of the batch out of
        # the pre-flight AND the submit — the bug class that alignment
        # helper exists to prevent, so fail loudly if it ever regresses.
        for name, properties in zip(
            self._entity_names, self._properties_aligned(), strict=True,
        ):
            settings = resolve.resolve_settings(
                properties,
                sections=sections,
                form=form,
                pinned=pinned,
                fallbacks=self._fallbacks,
            )
            # An empty assignment means "inherit the whole pool", so there is
            # nothing to warn about. Read off the properties dict we already
            # hold rather than calling get_entity_departments, which would be
            # one more config read per entity for the same value.
            assigned = list(properties.get('departments') or []) or None
            rows.append((
                name, settings,
                resolve.entity_warnings(
                    properties, settings, departments=assigned,
                ),
            ))
        return rows

    # ── Submit ────────────────────────────────────────────

    def _on_submit(self) -> None:
        if not self._entity_uris:
            QMessageBox.warning(
                self, "Submit Jobs",
                f"Check at least one {self._context[:-1]} in the tree before "
                "submitting.",
            )
            return

        sections = self._active_sections()
        if not sections:
            QMessageBox.warning(
                self, "Submit Jobs",
                "Enable at least one of Publish, Render or Playblast before "
                "submitting.",
            )
            return

        if 'render' in sections and not self._rnd_channels.checked_items():
            # The csv field this replaced fell back to 'default' when it was
            # emptied, which rendered something nobody asked for.
            QMessageBox.warning(
                self, "Submit Jobs",
                "Check at least one channel in the Render section before "
                "submitting.",
            )
            return

        rows = self._resolved_batch()

        # A wide fan-out is expensive and easy to trigger by mis-clicking a
        # branch, so make the user own it. The warning count is the part
        # worth reading: it is the difference between a batch that renders
        # and one that half-fails an hour from now.
        if len(rows) > 1 or any(warnings for _n, _s, warnings in rows):
            pinned = len(self._pinned_keys())
            lines = [
                f"Submit {' + '.join(sections)} jobs for "
                f"{len(rows)} {self._context}?",
                "",
                f"{pinned} setting{'' if pinned == 1 else 's'} pinned to the "
                f"whole batch; the rest follow each entity's own config.",
            ]
            warned = [
                f"  • {name}: {'; '.join(warnings)}"
                for name, _s, warnings in rows if warnings
            ]
            if warned:
                lines += ["", f"{len(warned)} with warnings:"] + warned[:10]
                if len(warned) > 10:
                    lines.append(f"  … and {len(warned) - 10} more")
            confirm = QMessageBox.question(
                self, "Submit Jobs", "\n".join(lines),
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if confirm != QMessageBox.Ok:
                return

        # ProcessDialog gives the batch a per-entity progress tree, a
        # working Cancel and its own error report. The inline loop is the
        # fallback for when the pipeline UI isn't importable.
        if not self._submit_via_process_dialog(rows):
            self._submit_inline(rows)

    def _submit_tasks(self, rows: Sequence[tuple]) -> list:
        """One ``ProcessTask`` per checked entity, or ``[]`` if unavailable.

        ``ProcessDialog`` and its executor survived the Project Browser's
        retirement (they moved up to ``pipe/houdini/ui/``) and are what the
        export/publish flows use, so this is reuse rather than a rebuild —
        the retired ``JobSubmissionDialog`` submitted exactly this way.

        Safe on the GUI thread: ``ProcessExecutor`` has no worker thread. It
        sequences tasks with ``QTimer.singleShot(0, ...)`` on the main
        thread, so the event loop keeps turning between entities (progress
        paints, Cancel responds) without any of the affinity hazards that
        come with moving pipeline work off it.

        Returns an empty list when the pipeline import fails, which is the
        signal to fall back to the inline loop.
        """
        try:
            from tumblepipe.pipe.houdini.ui.process_task import ProcessTask
            from tumblepipe.farm.jobs.houdini.batch_submit import (
                submit_entity_batch,
            )
        except Exception:
            log.exception("ProcessTask/batch_submit unavailable")
            return []

        import uuid

        tasks = []
        for uri, (name, settings, warnings) in zip(
            self._entity_uris, rows, strict=True,
        ):
            kinds = [k for k in resolve.SECTIONS if settings.get(k)]
            departments = [
                settings[key] for key in
                ('pub_department', 'render_department', 'pb_department')
                if settings.get(key)
            ]
            # dict.fromkeys, not set(): the order is the pipeline's, and a
            # summary that reshuffles it reads as a different cut.
            label = ', '.join(dict.fromkeys(departments)) or 'N/A'
            description = f"{'+'.join(kinds)} [{label}]"
            if warnings:
                description += f"  ⚠ {'; '.join(warnings)}"
            config = {
                'entity': {
                    'uri': str(uri), 'name': name, 'context': self._context,
                },
                'settings': settings,
            }
            tasks.append(ProcessTask(
                id=str(uuid.uuid4()),
                uri=uri,
                department=label,
                task_type='farm_submit',
                description=description,
                # Farm-only: no local execution for a farm submission, which
                # is also what pins ProcessDialog to its farm mode.
                execute_local=None,
                execute_farm=lambda c=config: submit_entity_batch(c),
                first_frame=settings.get('first_frame'),
                last_frame=settings.get('last_frame'),
            ))
        return tasks

    def _submit_via_process_dialog(self, rows: Sequence[tuple]) -> bool:
        """Run the batch through ProcessDialog. False if it isn't available.

        Worth the indirection for anything past one entity: the inline loop
        blocks the GUI for the whole batch with no progress and no way out,
        and reports which entity failed only once every entity has been
        tried.
        """
        tasks = self._submit_tasks(rows)
        if not tasks:
            return False
        try:
            from tumblepipe.pipe.houdini.ui.process_dialog import ProcessDialog
        except Exception:
            log.exception("ProcessDialog unavailable — falling back inline")
            return False

        dialog = ProcessDialog(
            title="Submit to Farm",
            tasks=tasks,
            # None disables the local/farm mode filtering: every task here is
            # farm-only, so there is nothing to filter by department.
            current_department=None,
            parent=self,
        )
        dialog.process_completed.connect(self._on_submission_completed)
        dialog.exec()
        return True

    def _on_submission_completed(self, results: dict) -> None:
        """Close on a clean run; stay open so a failure can be retried.

        ProcessDialog reports the per-task errors itself, so this deliberately
        adds no second summary box on top of it.
        """
        if results.get('failed') or results.get('skipped'):
            return
        if results.get('completed'):
            self.accept()

    def _submit_inline(self, rows: Sequence[tuple]) -> None:
        """Blocking per-entity loop with a single summary at the end.

        The fallback for when ``ProcessDialog`` can't be imported (outside
        Houdini, or a partial install). Identical submission, worse feedback.
        """
        try:
            from tumblepipe.farm.jobs.houdini.batch_submit import (
                submit_entity_batch,
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Submit Jobs",
                f"tumblepipe.farm.jobs.houdini.batch_submit unavailable:\n{exc}",
            )
            return

        successes: list[tuple[str, list[str]]] = []
        failures: list[tuple[str, str]] = []
        for uri, (name, settings, _warnings) in zip(
            self._entity_uris, rows, strict=True,
        ):
            config = {
                'entity': {
                    'uri': str(uri), 'name': name, 'context': self._context,
                },
                'settings': settings,
            }
            try:
                job_ids = submit_entity_batch(config)
                successes.append((name, list(job_ids or [])))
            except Exception as exc:
                log.exception("submit_entity_batch failed for %s", uri)
                failures.append((name, str(exc)))

        lines: list[str] = []
        if successes:
            lines.append(
                f"Submitted {len(successes)}/{len(self._entity_uris)} entities."
            )
            for name, ids in successes:
                lines.append(
                    f"  • {name}: {', '.join(ids) if ids else '(no ids)'}"
                )
        if failures:
            lines.append("")
            lines.append(f"{len(failures)} failed:")
            for name, err in failures:
                lines.append(f"  • {name}: {err}")
        msg_text = "\n".join(lines) if lines else "Nothing was submitted."

        if failures and not successes:
            QMessageBox.critical(self, "Submit Jobs", msg_text)
        elif failures:
            QMessageBox.warning(self, "Submit Jobs", msg_text)
        else:
            QMessageBox.information(self, "Submit Jobs", msg_text)
            self.accept()
