"""The Farm Submit dialog: tick publish, playblast and render steps per entity, submit.

Rows are the entities of one context (shots, or assets), grouped by sequence
or category; columns are pipeline steps — one publish column per department
in pool order, then Playblast (shots only) and Render. A ticked cell means
"do this step for this entity", and each cell shows how up to date that step
is: never done, stale, current, or nothing to do. Every row, column and
sequence carries a tri-state checkbox. The policy behind all of it — states,
ticks, warnings, what a row submits — is pure and lives in ``farm_grid``; the
status scan is ``farm_status``.

The settings stay the per-entity tri-state form it has been since the
multi-entity rework: left alone a field is *unpinned* and every entity uses
its own configured value (``⟨per entity⟩`` when they disagree); touching it
*pins* it for the whole submission. Resolution order and the rules live in
``submit_jobs_resolve``. The grid chooses the *steps*; the form never did.

Submit writes a plan and hands it to a separate process
(``tumblepipe.farm.submit_plan``) running Houdini's bundled Python, then
closes: Houdini is free in seconds however large the submission, and a
non-modal status window (``farm_submission_window``) follows the progress.

Opened from the Render quick action or a card's **Submit Jobs…**, the grid
has those entities' Render cells ticked; from the **Farm Submit** quick action it
opens with every entity and nothing ticked.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPen, QStandardItem, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox,
    QDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStyledItemDelegate, QToolTip,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BORDER, FONT_BODY, FONT_FAMILY,
    TEXT_PRIMARY, TEXT_SECONDARY,
)

from . import farm_grid as grid
from . import farm_status
from . import submit_jobs_resolve as resolve

log = logging.getLogger(__name__)

# Shown by an unpinned widget whose checked entities disagree. The angle
# brackets keep it from reading as a department or pool literally named
# "per entity".
PER_ENTITY_TEXT = "⟨per entity⟩"

# Cell colours. The theme's own tokens where it has one; amber and green for
# the two states that must be told apart at a glance (they also differ in
# lightness, not only hue).
STALE_COLOUR = "#f0a030"
CURRENT_COLOUR = "#2bae86"
NEVER_COLOUR = "#c8c8cc"
DIM_COLOUR = "#5c5c62"
TICK_OUTLINE = "#3fb58f"
RULE_COLOUR = "#3a3a3a"
ZEBRA_COLOUR = "#1f1f1f"
BAND_COLOUR = "#262626"
WARNING_COLOUR = STALE_COLOUR

GLYPHS = {
    grid.NONE: "·", grid.NEVER: "○", grid.STALE: "●",
    grid.CURRENT: "✓", grid.PENDING: "…",
}
GLYPH_COLOURS = {
    grid.NONE: DIM_COLOUR, grid.NEVER: NEVER_COLOUR, grid.STALE: STALE_COLOUR,
    grid.CURRENT: CURRENT_COLOUR, grid.PENDING: DIM_COLOUR,
}
TIPS = {
    grid.PUBLISH: {
        grid.NONE: "no workfile for this department",
        grid.NEVER: "never exported",
        grid.STALE: "the workfile was saved after the last export",
        grid.CURRENT: "exported since the last workfile save",
        grid.PENDING: "reading status…",
    },
    'preview': {
        grid.NONE: "not available: no frame range configured",
        grid.NEVER: "never made",
        grid.STALE: "older than the newest publish it composes",
        grid.CURRENT: "newer than every publish it composes",
        grid.PENDING: "reading status…",
    },
}

# Item data role carrying a shot row's URI string, or a sequence row's key.
_URI_ROLE = Qt.UserRole
_GROUP_ROLE = Qt.UserRole + 1

# Every entity has this channel implicitly, whether or not its properties
# list it. Owned by submit_jobs_resolve so the pure resolution policy and
# the widget that drives it can't drift apart.
_DEFAULT_CHANNEL = resolve.DEFAULT_CHANNEL

# Rows whose folders are resolved per event-loop turn while the grid fills.
_PROBE_CHUNK = 12


# ── Theme ─────────────────────────────────────────────────

# Always-visible scrollbars. TumbleTrove's shared SCROLLBAR_STYLE draws the
# handle transparent, which hides that the grid and the settings panel
# scroll at all. Shared with the submission window.
VISIBLE_SCROLLBARS = """
QScrollBar:vertical {
    background: #1f1f1f; width: 10px; margin: 0; border: none;
}
QScrollBar:horizontal {
    background: #1f1f1f; height: 10px; margin: 0; border: none;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #4a4a4a; border-radius: 4px; margin: 2px;
}
QScrollBar::handle:vertical { min-height: 30px; }
QScrollBar::handle:horizontal { min-width: 30px; }
QScrollBar::handle:hover { background: #6a6a6a; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""


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
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {TEXT_SECONDARY};
}}
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
    border-radius: 4px;
    padding: 5px 14px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:default {{
    background-color: {ACCENT};
    color: #ffffff;
    border-color: {ACCENT};
}}
QTreeWidget {{
    background-color: {BG_DARKEST};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    outline: none;
}}
QScrollArea {{ border: none; background: transparent; }}
""" + VISIBLE_SCROLLBARS


# ── Helpers ───────────────────────────────────────────────

def _properties_for(entity_uris: Sequence) -> list[dict]:
    """Resolved properties for each URI, read in one coherency scope.

    Bare ``get_properties`` re-stamps the config files per call, and this
    runs over every entity in the context, so the reads are batched. Empty
    list on failure.

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

    Empty list on failure — the grid then falls back to just the entities
    the dialog was opened for.

    ``list_entity_uris(closure=True)`` returns childless *category* nodes
    (e.g. an empty ``assets/CHAR``) alongside real entities, so each URI is
    vetted with ``is_terminal_entity`` (schema-keyed), all inside one
    ``coherent()`` scope.
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
        log.exception("Failed to list entities for the grid")
        return []


def _list_groups(context: str) -> list[tuple[str, list[object]]]:
    """Return ``(group_name, member_uris)`` for every group in ``context``.

    Offered as a filter above the grid: picking a Multi narrows the rows to
    its members. Empty list on failure.
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


def _group_members(group_uri) -> list[object]:
    """Member URIs of the Multi at ``group_uri``; empty list on failure."""
    try:
        from tumblepipe.config.groups import get_group
        group = get_group(group_uri)
        return list(group.members) if group is not None else []
    except Exception:
        log.exception("Failed to read the members of %s", group_uri)
        return []


def _box(painter: QPainter, rect: QRect, state: str) -> None:
    """Draw a tri-state checkbox (``on`` / ``mixed`` / ``off``) in ``rect``."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    if state == 'off':
        painter.setPen(QPen(QColor("#6a6a70"), 1))
        painter.setBrush(QColor(BG_DARKEST))
    else:
        painter.setPen(QPen(QColor(TICK_OUTLINE), 1))
        painter.setBrush(QColor(ACCENT))
    painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 3, 3)
    if state != 'off':
        painter.setPen(QPen(QColor("#ffffff"), 2))
        r = rect.adjusted(3, 3, -3, -3)
        if state == 'on':
            painter.drawLine(r.left(), r.center().y(), r.left() + r.width() // 3, r.bottom())
            painter.drawLine(r.left() + r.width() // 3, r.bottom(), r.right(), r.top())
        else:
            painter.drawLine(r.left(), r.center().y(), r.right(), r.center().y())
    painter.restore()


BOX = 14
# The first column's left gutter: a sequence row's expand arrow sits in it,
# and every row's checkbox starts right after it, so they line up.
_ARROW_WIDTH = 20


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
    """A form widget that can defer to each entity's own value.

    Three states, and the middle one is the reason this class exists:

    * **pinned** — the artist set it, so it applies to the whole submission
      and is drawn normally.
    * **unpinned, agreed** — every entity in the submission resolves to the
      same value, so the widget shows it, dimmed. Informative, but still the
      entity's value: tick a shot that disagrees and it turns into…
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
        """Mark as an explicit submission-wide choice (a user edit happened)."""
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


# ── Grid painting ─────────────────────────────────────────

class _GridDelegate(QStyledItemDelegate):
    """Paints every grid cell from the dialog's state; the items hold no data
    beyond which row they are, so a tick is a repaint, not a model write."""

    def __init__(self, dialog: "SubmitJobsDialog") -> None:
        super().__init__(dialog)
        self._dialog = dialog

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), 28)

    def paint(self, painter: QPainter, option, index) -> None:
        dialog = self._dialog
        item = dialog._tree.itemFromIndex(index)
        if item is None:
            return
        rect = option.rect
        column = index.column()
        painter.save()
        is_group = item.data(0, _GROUP_ROLE) is not None
        if is_group:
            painter.fillRect(rect, QColor(BAND_COLOUR))
        elif item.data(0, _URI_ROLE) in dialog._zebra:
            painter.fillRect(rect, QColor(ZEBRA_COLOUR))
        dialog._paint_cell(painter, rect, item, column, is_group)
        # Grid rules: a right rule per column, a bottom rule per row, and a
        # heavier one ahead of the preview columns.
        painter.setPen(QPen(QColor(RULE_COLOUR), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if column == dialog._first_preview_column:
            painter.setPen(QPen(QColor("#5a5a5a"), 2))
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        painter.restore()


class _CheckHeader(QHeaderView):
    """Column headers with a tri-state checkbox under each step's label."""

    def __init__(self, dialog: "SubmitJobsDialog") -> None:
        super().__init__(Qt.Horizontal, dialog)
        self._dialog = dialog
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setDefaultAlignment(Qt.AlignCenter)

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(size.width(), 50)

    def paintSection(self, painter: QPainter, rect: QRect, logical: int) -> None:
        painter.save()
        painter.fillRect(rect, QColor(BAND_COLOUR))
        painter.setPen(QPen(QColor(RULE_COLOUR), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.setPen(QPen(QColor("#4a4a4a"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if logical == self._dialog._first_preview_column:
            painter.setPen(QPen(QColor("#5a5a5a"), 2))
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        self._dialog._paint_header(painter, rect, logical)
        painter.restore()


# ── Dialog ────────────────────────────────────────────────

class SubmitJobsDialog(QDialog):
    """The Farm Submit grid for one context's entities.

    Args:
        entity_uris: Entities to start with ticked (``tick_kinds`` on each).
            May be empty — the Farm Submit quick action opens with nothing ticked.
            A Multi (``groups:`` URI) stands for its members.
        entity_names: Display names parallel to ``entity_uris``.
        context: ``'shots'`` or ``'assets'``.
        parent: Parent widget. Pass ``hou.qt.mainWindow()`` from Houdini.
        department: Department the dialog was opened *from* — the loaded
            workfile's. Pins the Render (and Playblast) cut, so submitting
            from a lighting workfile previews up to lighting. ``None`` falls
            back to each entity's ``submission.render.department``.
        tick_kinds: Step kinds to tick for ``entity_uris`` —
            ``grid.RENDER`` by default, what the Render quick action and
            **Submit Jobs…** have always meant.
    """

    def __init__(
        self,
        entity_uris: Sequence,
        entity_names: Sequence[str],
        context: str,
        parent: QWidget | None = None,
        department: str | None = None,
        tick_kinds: Sequence[str] = (grid.RENDER,),
    ) -> None:
        super().__init__(parent)
        if context not in ("shots", "assets"):
            raise ValueError(f"context must be 'shots' or 'assets', got {context!r}")
        # A Multi is never submitted itself: its member entities are.
        if any(resolve.is_group_target(uri) for uri in entity_uris):
            entity_uris = resolve.expand_targets(entity_uris, _group_members)
            if not entity_uris:
                raise ValueError(
                    "This Multi has no member entities to submit. Add "
                    "members to it in the browser first."
                )
        self._context = context
        self._noun = ('shot', 'shots') if context == 'shots' else ('asset', 'assets')
        # Department the dialog was opened from (a loaded workfile), or None.
        self._open_department = department or None

        # Guards the spin boxes' valueChanged against programmatic seeding
        # (Qt gives QSpinBox no user-only edit signal).
        self._seeding = False
        # Every tri-state form field, keyed by its settings key.
        self._fields: dict[str, _PinnedField] = {}
        # Context-derived defaults the pure resolver can't look up itself
        # (the last renderable department, for the preview cuts).
        self._fallbacks: dict = {}

        # Rows: every entity in the context, plus any opened entity the
        # listing does not know (an off-config scene keeps its row).
        opened = {str(uri): uri for uri in entity_uris}
        by_uri = {str(uri): uri for uri in _list_selectable_entities(context)}
        by_uri.update(opened)
        self._uris: list[str] = sorted(by_uri)
        self._uri_objects = by_uri
        props = _properties_for([by_uri[key] for key in self._uris])
        if len(props) != len(self._uris):
            props = [{} for _ in self._uris]
        self._props: dict[str, dict] = dict(zip(self._uris, props))

        self._publish_departments = _list_dept_names(
            context, only_publishable=True, only_renderable=False,
        )
        self._renderable_departments = _list_dept_names(
            context, only_publishable=False, only_renderable=True,
        )
        self._fallbacks['render_department'] = grid.default_preview_department(
            self._renderable_departments
        )
        self._fallbacks['pb_department'] = self._fallbacks['render_department']
        self._columns = grid.columns_for(context, self._publish_departments)
        self._first_preview_column = next(
            (i + 1 for i, c in enumerate(self._columns) if c.kind != grid.PUBLISH),
            -1,
        )
        self._warnings_column = len(self._columns) + 1

        # Grid state: ticks and per-cell states, keyed (uri, column key).
        self._ticks: set = set()
        self._states: dict = {}
        self._statuses: dict[str, farm_status.RowStatus] = {}
        self._warnings: dict[str, list[str]] = {}
        self._zebra: set = set()
        self._hidden: set = set()
        self._items: dict[str, QTreeWidgetItem] = {}
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._group_uris: dict[str, list[str]] = {}

        # Background status scan.
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="farm-status")
        self._pending: dict = {}
        self._probe_queue: list[str] = []
        self._preview_only = False
        self._poll = QTimer(self)
        self._poll.setInterval(120)
        self._poll.timeout.connect(self._drain_scans)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh)

        self.setWindowTitle("Farm Submit")
        self.setMinimumSize(1100, 640)
        # Opens wide enough for every column and the settings beside them;
        # the grid scrolls sideways below that.
        self.resize(1560, 820)
        self.setStyleSheet(_DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addLayout(self._build_toolbar())

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)
        body.addWidget(self._build_grid(), 1)
        body.addWidget(self._build_forms())

        footer = QHBoxLayout()
        self._summary = QLabel()
        self._summary.setStyleSheet(f"color: {TEXT_PRIMARY};")
        footer.addWidget(self._summary, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        self._submit_btn = QPushButton("Submit")
        self._submit_btn.setDefault(True)
        self._submit_btn.clicked.connect(self._on_submit)
        footer.addWidget(close_btn)
        footer.addWidget(self._submit_btn)
        root.addLayout(footer)

        self._apply_open_department()
        # Seed the tick state from the entities the dialog was opened for.
        for key in opened:
            for column in self._columns:
                if column.kind in tick_kinds:
                    self._ticks.add((key, column.key))
        self._scroll_to_first_ticked(opened)
        self._reseed_form(initial=True)
        self._start_scan()

    # ── Toolbar ───────────────────────────────────────────

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(f"Filter {self._context}…")
        self._filter.setClearButtonEnabled(True)
        self._filter.setFixedWidth(200)
        self._filter.textChanged.connect(lambda *_a: self._apply_filter())
        row.addWidget(self._filter)

        self._group_filter = QComboBox()
        self._group_filter.addItem(f"All {self._context}", None)
        for name, members in _list_groups(self._context):
            self._group_filter.addItem(name, [str(m) for m in members])
        self._group_filter.setToolTip("Show only the members of one Multi")
        self._group_filter.currentIndexChanged.connect(lambda *_a: self._apply_filter())
        if self._group_filter.count() == 1:
            self._group_filter.hide()
        row.addWidget(self._group_filter)

        stale_btn = QPushButton("Select stale")
        stale_btn.setToolTip(
            "Tick every visible cell that is stale or never done"
        )
        stale_btn.clicked.connect(self._select_stale)
        row.addWidget(stale_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.setToolTip("Untick every cell")
        clear_btn.clicked.connect(self._clear_ticks)
        row.addWidget(clear_btn)

        row.addStretch(1)
        legend = QLabel(
            f"<span style='color:{CURRENT_COLOUR}'>✓</span> current &nbsp; "
            f"<span style='color:{STALE_COLOUR}'>●</span> stale &nbsp; "
            f"<span style='color:{NEVER_COLOUR}'>○</span> never &nbsp; "
            f"<span style='color:{DIM_COLOUR}'>·</span> nothing to do"
        )
        legend.setStyleSheet(f"color: {TEXT_SECONDARY};")
        row.addWidget(legend)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        row.addWidget(self._status_label)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setToolTip("Read every cell's status from disk again")
        refresh_btn.clicked.connect(self._start_scan)
        row.addWidget(refresh_btn)
        return row

    # ── Grid ──────────────────────────────────────────────

    def _build_grid(self) -> QWidget:
        tree = QTreeWidget()
        self._tree = tree
        tree.setColumnCount(len(self._columns) + 2)
        tree.setHeader(_CheckHeader(self))
        tree.setHeaderLabels(
            [self._noun[0].capitalize()]
            + [c.label for c in self._columns] + ["Warnings"]
        )
        tree.setItemDelegate(_GridDelegate(self))
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        tree.setFocusPolicy(Qt.NoFocus)
        tree.setExpandsOnDoubleClick(False)
        tree.setUniformRowHeights(True)
        # No indentation and no Qt branch arrows: the sequence row draws its
        # own arrow, so every row's first column starts at the same x and the
        # checkboxes down it form one straight line.
        tree.setRootIsDecorated(False)
        tree.setIndentation(0)
        tree.setMouseTracking(True)
        header = tree.header()
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(40)
        header.resizeSection(0, 170)
        # Wide enough for the header label ('environment', 'animation'),
        # which _paint_header draws at 12px; never narrower than a cell needs.
        label_font = QFont(FONT_FAMILY)
        label_font.setPixelSize(12)
        label_font.setBold(True)
        metrics = QFontMetrics(label_font)
        for index, column in enumerate(self._columns, start=1):
            floor = 56 if column.kind == grid.PUBLISH else 82
            header.resizeSection(
                index, max(floor, metrics.horizontalAdvance(column.label) + 16),
            )
            header.setSectionResizeMode(index, QHeaderView.Fixed)
        header.sectionClicked.connect(self._on_header_clicked)

        for uri in self._uris:
            group = self._group_key(uri)
            group_item = self._group_items.get(group)
            if group_item is None:
                group_item = QTreeWidgetItem(tree, [group])
                group_item.setData(0, _GROUP_ROLE, group)
                self._group_items[group] = group_item
                self._group_uris[group] = []
            item = QTreeWidgetItem(group_item, [self._uri_objects[uri].segments[-1]])
            item.setData(0, _URI_ROLE, uri)
            self._items[uri] = item
            self._group_uris[group].append(uri)
            for column in self._columns:
                self._states[(uri, column.key)] = grid.PENDING
        tree.expandAll()
        self._restripe()
        tree.viewport().installEventFilter(self)
        return tree

    def _group_key(self, uri: str) -> str:
        """The sequence (or category path) a row sits under."""
        segments = self._uri_objects[uri].segments
        return '/'.join(segments[1:-1]) or self._context

    def _restripe(self) -> None:
        """Alternate row shading over the *visible* rows of each group."""
        self._zebra = set()
        for uris in self._group_uris.values():
            visible = [u for u in uris if u not in self._hidden]
            self._zebra.update(visible[1::2])

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.Wheel
            and isinstance(obj, (QAbstractSpinBox, QComboBox))
            and not obj.hasFocus()
        ):
            QApplication.sendEvent(self._forms_scroll.verticalScrollBar(), event)
            return True
        if obj is self._tree.viewport():
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._on_grid_click(event.position().toPoint())
                return False
            if event.type() == QEvent.ToolTip:
                text = self._tooltip_at(event.pos())
                if text:
                    QToolTip.showText(event.globalPos(), text, self._tree)
                else:
                    QToolTip.hideText()
                return True
        return super().eventFilter(obj, event)

    def _tooltip_at(self, pos) -> str:
        index = self._tree.indexAt(pos)
        if not index.isValid():
            return ""
        item = self._tree.itemFromIndex(index)
        uri = item.data(0, _URI_ROLE)
        column = index.column()
        if uri is None or column == 0:
            return ""
        if column == self._warnings_column:
            return "\n".join(self._warnings.get(uri, []))
        step = self._columns[column - 1]
        state = self._states.get((uri, step.key), grid.PENDING)
        table = TIPS[grid.PUBLISH if step.kind == grid.PUBLISH else 'preview']
        ticked = " — will be submitted" if (uri, step.key) in self._ticks else ""
        return f"{item.text(0)} · {step.label}: {table[state]}{ticked}"

    def _on_grid_click(self, pos) -> None:
        index = self._tree.indexAt(pos)
        if not index.isValid():
            return
        item = self._tree.itemFromIndex(index)
        column = index.column()
        uri = item.data(0, _URI_ROLE)
        group = item.data(0, _GROUP_ROLE)
        if column == 0 and group is not None:
            left = self._tree.visualRect(index).left()
            if pos.x() < left + _ARROW_WIDTH:
                item.setExpanded(not item.isExpanded())
                return
        if column == self._warnings_column:
            return
        if column == 0:
            keys = self._row_keys(uri) if uri else self._group_keys(group)
        else:
            step = self._columns[column - 1]
            if uri:
                keys = [(uri, step.key)] if self._tickable(uri, step.key) else []
            else:
                keys = self._group_keys(group, step.key)
        self._toggle(keys)

    def _on_header_clicked(self, logical: int) -> None:
        if logical == 0:
            self._toggle(self._visible_keys())
        elif 1 <= logical <= len(self._columns):
            self._toggle(self._visible_keys(self._columns[logical - 1].key))

    # ── Keys ──────────────────────────────────────────────

    def _tickable(self, uri: str, column_key: str) -> bool:
        return self._states.get((uri, column_key), grid.PENDING) in grid.TICKABLE

    def _row_keys(self, uri: str) -> list:
        return [
            (uri, c.key) for c in self._columns if self._tickable(uri, c.key)
        ]

    def _group_keys(self, group: str, column_key: str | None = None) -> list:
        keys = []
        for uri in self._group_uris.get(group, []):
            if uri in self._hidden:
                continue
            columns = [column_key] if column_key else [c.key for c in self._columns]
            keys.extend((uri, key) for key in columns if self._tickable(uri, key))
        return keys

    def _visible_keys(self, column_key: str | None = None) -> list:
        keys = []
        for group in self._group_uris:
            keys.extend(self._group_keys(group, column_key))
        return keys

    def _toggle(self, keys: list) -> None:
        if not keys:
            return
        self._ticks = grid.toggle(keys, self._ticks)
        self._changed()

    def _select_stale(self) -> None:
        visible = {
            key: state for key, state in self._states.items()
            if key[0] not in self._hidden
        }
        self._ticks |= set(grid.stale_keys(visible))
        self._changed()

    def _clear_ticks(self) -> None:
        self._ticks = set()
        self._changed()

    def _changed(self) -> None:
        """Ticks changed: repaint now, recompute warnings and the form soon."""
        self._tree.viewport().update()
        self._tree.header().viewport().update()
        self._refresh_timer.start()

    def _refresh(self) -> None:
        self._reseed_form()
        self._recompute_warnings()
        self._summary.setText(
            grid.summary(self._uris, self._columns, self._ticks, self._noun)
        )
        self._tree.viewport().update()

    # ── Painting ──────────────────────────────────────────

    def _paint_header(self, painter: QPainter, rect: QRect, logical: int) -> None:
        painter.setPen(QColor(TEXT_SECONDARY))
        font = QFont(FONT_FAMILY)
        font.setPixelSize(12)
        if logical == 0:
            box = QRect(rect.left() + _ARROW_WIDTH, rect.center().y() - BOX // 2, BOX, BOX)
            _box(painter, box, grid.tri_state(self._visible_keys(), self._ticks))
            painter.setFont(font)
            painter.drawText(
                rect.adjusted(_ARROW_WIDTH + BOX + 8, 0, 0, 0),
                Qt.AlignVCenter | Qt.AlignLeft, "All",
            )
            return
        if logical == self._warnings_column:
            painter.setFont(font)
            painter.drawText(rect.adjusted(10, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, "Warnings")
            return
        column = self._columns[logical - 1]
        if column.kind != grid.PUBLISH:
            font.setBold(True)
            painter.setPen(QColor(TEXT_PRIMARY))
        painter.setFont(font)
        painter.drawText(
            QRect(rect.left(), rect.top() + 6, rect.width(), 18), Qt.AlignCenter, column.label,
        )
        box = QRect(rect.center().x() - BOX // 2, rect.bottom() - BOX - 8, BOX, BOX)
        _box(painter, box, grid.tri_state(self._visible_keys(column.key), self._ticks))

    def _paint_cell(self, painter, rect, item, column: int, is_group: bool) -> None:
        font = QFont(FONT_FAMILY)
        font.setPixelSize(13)
        if is_group:
            group = item.data(0, _GROUP_ROLE)
            if column == 0:
                painter.setPen(QColor(TEXT_SECONDARY))
                small = QFont(FONT_FAMILY)
                small.setPixelSize(10)
                painter.setFont(small)
                painter.drawText(
                    QRect(rect.left(), rect.top(), _ARROW_WIDTH, rect.height()),
                    Qt.AlignCenter, "▼" if item.isExpanded() else "▶",
                )
                box = QRect(rect.left() + _ARROW_WIDTH, rect.center().y() - BOX // 2, BOX, BOX)
                _box(painter, box, grid.tri_state(self._group_keys(group), self._ticks))
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(QColor(TEXT_PRIMARY))
                painter.drawText(
                    rect.adjusted(_ARROW_WIDTH + BOX + 8, 0, 0, 0), Qt.AlignVCenter, group,
                )
            elif column == self._warnings_column:
                font.setPixelSize(11)
                painter.setFont(font)
                painter.setPen(QColor(TEXT_SECONDARY))
                count = len([u for u in self._group_uris[group] if u not in self._hidden])
                painter.drawText(
                    rect.adjusted(10, 0, 0, 0), Qt.AlignVCenter,
                    f"{count} {self._noun[count != 1]}",
                )
            else:
                step = self._columns[column - 1]
                keys = self._group_keys(group, step.key)
                if keys:
                    box = QRect(rect.center().x() - BOX // 2, rect.center().y() - BOX // 2, BOX, BOX)
                    _box(painter, box, grid.tri_state(keys, self._ticks))
                stale = sum(
                    1 for uri in self._group_uris[group]
                    if uri not in self._hidden
                    and self._states.get((uri, step.key)) in grid.NEEDS_WORK
                )
                if stale:
                    # In the corner, so the checkbox stays centred under the
                    # header's.
                    small = QFont(FONT_FAMILY)
                    small.setPixelSize(10)
                    painter.setFont(small)
                    painter.setPen(QColor(STALE_COLOUR))
                    painter.drawText(rect.adjusted(0, 2, -4, 0), Qt.AlignTop | Qt.AlignRight, str(stale))
            return

        uri = item.data(0, _URI_ROLE)
        if column == 0:
            box = QRect(rect.left() + _ARROW_WIDTH, rect.center().y() - BOX // 2, BOX, BOX)
            _box(painter, box, grid.tri_state(self._row_keys(uri), self._ticks))
            painter.setFont(font)
            painter.setPen(QColor(TEXT_PRIMARY))
            painter.drawText(
                rect.adjusted(_ARROW_WIDTH + BOX + 8, 0, 0, 0), Qt.AlignVCenter, item.text(0),
            )
            return
        if column == self._warnings_column:
            warnings = self._warnings.get(uri)
            if warnings:
                font.setPixelSize(12)
                painter.setFont(font)
                painter.setPen(QColor(WARNING_COLOUR))
                painter.drawText(
                    rect.adjusted(10, 0, -4, 0), Qt.AlignVCenter,
                    painter.fontMetrics().elidedText("; ".join(warnings), Qt.ElideRight, rect.width() - 14),
                )
            return
        step = self._columns[column - 1]
        key = (uri, step.key)
        state = self._states.get(key, grid.PENDING)
        ticked = key in self._ticks
        if ticked:
            painter.fillRect(rect.adjusted(1, 1, -1, -1), QColor(ACCENT))
            painter.setPen(QPen(QColor(TICK_OUTLINE), 1))
            painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.setFont(font)
        painter.setPen(QColor("#ffffff" if ticked else GLYPH_COLOURS[state]))
        painter.drawText(rect, Qt.AlignCenter, GLYPHS[state])

    # ── Filtering ─────────────────────────────────────────

    def _apply_filter(self) -> None:
        """Hide rows that don't match; ticks are untouched.

        Filtering narrows the view, never the submission — but every tick
        operation (headers, sequences, Select stale) only reaches visible
        rows, so checking a column under a filter cannot quietly submit what
        you cannot see.
        """
        needle = self._filter.text().strip().lower()
        members = self._group_filter.currentData()
        wanted = set(members) if members else None
        self._hidden = set()
        for uri, item in self._items.items():
            visible = (not needle or needle in uri.lower()) and (
                wanted is None or uri in wanted
            )
            item.setHidden(not visible)
            if not visible:
                self._hidden.add(uri)
        for group, item in self._group_items.items():
            item.setHidden(all(u in self._hidden for u in self._group_uris[group]))
        self._restripe()
        self._changed()

    def _scroll_to_first_ticked(self, opened: dict) -> None:
        for uri in self._uris:
            if uri in opened:
                self._tree.scrollToItem(self._items[uri])
                return

    # ── Status scan ───────────────────────────────────────

    def _start_scan(self, *, preview_only: bool = False) -> None:
        """(Re)read every row's status in the background, visible rows first."""
        self._preview_only = preview_only
        visible = [u for u in self._uris if u not in self._hidden]
        rest = [u for u in self._uris if u in self._hidden]
        self._probe_queue = visible + rest
        # Scans already in flight answer an older question; drop them (the
        # workers finish on their own and their results are ignored).
        self._pending = {}
        if not preview_only:
            for key in list(self._states):
                self._states[key] = grid.PENDING
        self._status_label.setText("Reading status…")
        self._poll.start()
        self._tree.viewport().update()

    def _cut(self, uri: str, key: str) -> str | None:
        """This row's preview cut: the pinned department, else the entity's."""
        entry = self._fields.get(key)
        if entry is not None and entry.pinned:
            value = entry.value()
            if value is not resolve.MIXED:
                return value
        value = resolve.entity_value(
            resolve.FIELDS_BY_KEY[key], self._props.get(uri, {}), self._fallbacks,
        )
        return None if value is resolve.REQUIRED else value

    def _drain_scans(self) -> None:
        """Main-thread poll: resolve a chunk of probes, apply finished scans."""
        if self._probe_queue:
            chunk = self._probe_queue[:_PROBE_CHUNK]
            self._probe_queue = self._probe_queue[_PROBE_CHUNK:]
            try:
                from tumblepipe.api import default_client
                with default_client().config.coherent():
                    for uri in chunk:
                        probe = farm_status.plan_probe(
                            self._uri_objects[uri],
                            [] if self._preview_only else self._publish_departments,
                            playblast_department=(
                                self._cut(uri, 'pb_department')
                                if self._context == 'shots' else None
                            ),
                            render_department=self._cut(uri, 'render_department'),
                        )
                        self._pending[self._executor.submit(farm_status.scan, probe)] = (
                            uri, self._preview_only,
                        )
            except Exception:
                log.exception("Could not resolve status folders")
                self._probe_queue = []

        finished = [future for future in self._pending if future.done()]
        for future in finished:
            uri, preview_only = self._pending.pop(future)
            try:
                status = future.result()
            except Exception as error:
                status = farm_status.RowStatus(uri=uri, error=str(error))
            if preview_only:
                # Only the preview folders were read; keep the publish half.
                old = self._statuses.get(uri)
                if old is None:
                    continue
                old.playblast_mtime = status.playblast_mtime
                old.render_mtime = status.render_mtime
            else:
                self._statuses[uri] = status
            self._apply_status(uri)
        if finished:
            self._ticks = grid.drop_untickable(self._ticks, self._states)
            self._changed()
        if not self._probe_queue and not self._pending:
            self._poll.stop()
            self._status_label.setText("")
        else:
            done = len(self._uris) - len(self._probe_queue) - len(self._pending)
            self._status_label.setText(f"Reading status… {done}/{len(self._uris)}")

    def _apply_status(self, uri: str) -> None:
        status = self._statuses.get(uri)
        if status is None:
            return
        props = self._props.get(uri, {})
        has_range = (
            resolve.nested(props, 'frame_start') is not None
            and resolve.nested(props, 'frame_end') is not None
        )
        departments = self._publish_departments
        for column in self._columns:
            key = (uri, column.key)
            if status.error:
                self._states[key] = grid.PENDING
                continue
            if column.kind == grid.PUBLISH:
                self._states[key] = grid.publish_state(
                    status.hip_mtimes.get(column.department),
                    status.export_mtimes.get(column.department),
                )
                continue
            field_key = 'pb_department' if column.kind == grid.PLAYBLAST else 'render_department'
            cut = grid.preview_cut(departments, self._cut(uri, field_key))
            preview = (
                status.playblast_mtime if column.kind == grid.PLAYBLAST
                else status.render_mtime
            )
            self._states[key] = grid.preview_state(
                preview,
                [status.export_mtimes.get(name) for name in cut],
                available=has_range,
            )

    # ── Forms ─────────────────────────────────────────────

    def _build_forms(self) -> QWidget:
        column = QWidget()
        # A scroll area's viewport keeps the platform palette (light grey)
        # unless its contents paint their own background.
        column.setObjectName("farmForms")
        column.setStyleSheet(f"QWidget#farmForms {{ background-color: {BG_DARKEST}; }}")
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_publish_section())
        if self._context == "shots":
            layout.addWidget(self._build_playblast_section())
        else:
            self._pb_dept = None
        layout.addWidget(self._build_render_section())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(column)
        scroll.viewport().setStyleSheet(f"background-color: {BG_DARKEST};")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The widest rows are two spin boxes sized for ⟨per entity⟩ plus a
        # label and a ↺ (about 450px); narrower clips the ↺ buttons.
        scroll.setFixedWidth(max(480, column.minimumSizeHint().width() + 24))
        # Scrolling the panel with the wheel must not edit what passes under
        # the pointer: a spin box or combo takes the wheel even unfocused,
        # and every settings edit pins that field for the whole submission,
        # so a scroll silently set e.g. 63 samples on every shot. They take
        # the wheel only once clicked into; otherwise it scrolls the panel.
        self._forms_scroll = scroll
        # One findChildren per type: PySide6 takes no tuple of types.
        guarded = column.findChildren(QAbstractSpinBox) + column.findChildren(QComboBox)
        for widget in guarded:
            widget.setFocusPolicy(Qt.StrongFocus)
            widget.installEventFilter(self)
        return scroll

    def _register(self, key: str, widget: QWidget, kind: str) -> QWidget:
        """Wrap ``widget`` as a tri-state field and wire its pin signal.

        A batch field (one with no per-entity source, e.g. Range mode) is
        not registered: it has no unpinned state, so it is read straight off
        its widget at submit time.
        """
        entry = _PinnedField(key, widget, kind)
        self._fields[key] = entry
        signal_name = {
            'spin': 'valueChanged', 'check': 'clicked',
            'combo': 'activated', 'line': 'textEdited',
        }[kind]
        getattr(widget, signal_name).connect(lambda *_a: self._changed())
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
            "Leave on ⟨per entity⟩ to let each entity use its own "
            "configured value."
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
        """A ↺ that returns its fields to the per-entity default."""
        button = QPushButton("↺")
        button.setFixedWidth(24)
        button.setStyleSheet("padding: 2px;")
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
        self._changed()

    def _row(self, *widgets, revert: Sequence[str] = ()) -> QWidget:
        """Pack widgets onto one form row, with an optional ↺ at the end."""
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

    def _section(self, title: str) -> tuple[QGroupBox, QFormLayout]:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(10, 14, 10, 10)
        form.setSpacing(6)
        return box, form

    def _build_publish_section(self) -> QGroupBox:
        box, form = self._section("Publish")
        self._pub_pool = self._line('pub_pool', 'general')
        form.addRow("Pool:", self._pub_pool)
        self._pub_priority = self._spin('pub_priority', 0, 100)
        form.addRow("Priority:", self._row(self._pub_priority, revert=('pub_priority',)))
        return box

    def _build_playblast_section(self) -> QGroupBox:
        """A GL (Storm) preview on the farm — shots only.

        The input is the shot's staged 'default' stage cut at this
        department, and the frame range is the shot's own, so there is no
        frame or channel field here.
        """
        box, form = self._section("Playblast")
        self._pb_dept = self._combo('pb_department', self._renderable_departments)
        self._pb_dept.setToolTip(
            "Playblasts every department up to and including this one, in "
            "pipeline order — the same cut the render uses."
        )
        self._pb_dept.activated.connect(lambda *_a: self._on_cut_changed())
        form.addRow("Up to:", self._pb_dept)
        self._pb_width = self._spin('pb_res_x', 16, 8192)
        self._pb_height = self._spin('pb_res_y', 16, 8192)
        form.addRow("Resolution:", self._row(
            self._pb_width, "×", self._pb_height, revert=('pb_res_x', 'pb_res_y'),
        ))
        self._pb_pool = self._line('pb_pool', 'general')
        form.addRow("Pool:", self._pb_pool)
        self._pb_priority = self._spin('pb_priority', 0, 100)
        form.addRow("Priority:", self._row(self._pb_priority, revert=('pb_priority',)))
        return box

    def _build_render_section(self) -> QGroupBox:
        box, form = self._section("Render")
        self._rnd_dept = self._combo('render_department', self._renderable_departments)
        self._rnd_dept.setToolTip(
            "Renders every department up to and including this one, in "
            "pipeline order — departments after it are left out of the "
            "composed stage. Also names the render output."
        )
        self._rnd_dept.activated.connect(lambda *_a: self._on_cut_changed())
        form.addRow("Up to:", self._rnd_dept)

        # Channels — a checkable menu of the channels the ticked entities
        # actually define. A batch field by contract: the menu spans the
        # submission and submits exactly what is checked, so a channel a
        # given entity lacks still fails visibly on the farm rather than
        # being quietly dropped. The Warnings column says so up front.
        self._rnd_channels = _CheckableComboBox(
            empty_text="(none — check at least one)",
            hint="Channels to render — one render per checked channel.",
        )
        all_channels = QPushButton("All")
        all_channels.setStyleSheet("padding: 3px 8px;")
        all_channels.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._rnd_channels.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        all_channels.clicked.connect(
            lambda: self._rnd_channels.set_checked(self._rnd_channels.options())
        )
        self._rnd_channels.model().dataChanged.connect(lambda *_a: self._changed())
        form.addRow("Channels:", self._row(self._rnd_channels, all_channels))

        # Range mode — a batch field: Full range submits the full_render chain
        # (all frames + slapcomp/mp4); First / Middle / Last submits
        # partial_render (3 check frames + notify).
        self._rnd_mode = QComboBox()
        self._rnd_mode.addItems(["Full range", "First / Middle / Last"])
        form.addRow("Range:", self._rnd_mode)

        self._rnd_first = self._spin('first_frame', -1_000_000, 1_000_000)
        self._rnd_last = self._spin('last_frame', -1_000_000, 1_000_000)
        form.addRow("Frames:", self._row(
            self._rnd_first, "→", self._rnd_last, revert=('first_frame', 'last_frame'),
        ))
        self._rnd_pre = self._spin('pre_roll', 0, 1000)
        self._rnd_post = self._spin('post_roll', 0, 1000)
        form.addRow("Pre / Post roll:", self._row(
            self._rnd_pre, "/", self._rnd_post, revert=('pre_roll', 'post_roll'),
        ))
        self._rnd_pool = self._line('render_pool', 'general')
        form.addRow("Pool:", self._rnd_pool)
        self._rnd_priority = self._spin('render_priority', 0, 100)
        self._rnd_tile = self._spin('tile_count', 1, 64)
        self._rnd_batch = self._spin('batch_size', 1, 1000)
        form.addRow("Pri / Tiles:", self._row(
            self._rnd_priority, self._rnd_tile,
            revert=('render_priority', 'tile_count'),
        ))
        form.addRow("Batch:", self._row(self._rnd_batch, revert=('batch_size',)))
        self._rnd_samples = self._spin('samples', 1, 4096)
        form.addRow("Samples:", self._row(self._rnd_samples, revert=('samples',)))
        self._rnd_denoise = self._check('denoise', "Denoise")
        self._rnd_mblur = self._check('mblur', "Motion blur")
        self._rnd_dof = self._check('dof', "DOF")
        form.addRow("", self._row(
            self._rnd_denoise, self._rnd_mblur, self._rnd_dof,
            revert=('denoise', 'mblur', 'dof'),
        ))
        self._rnd_standalone = QCheckBox("Standalone")
        self._rnd_copy_edit = QCheckBox("Copy to edit")
        form.addRow("", self._row(self._rnd_standalone, self._rnd_copy_edit))
        return box

    def _on_cut_changed(self) -> None:
        """A preview cut changed: its cells' staleness depends on it.

        The publish columns are unaffected, so only the preview folders are
        read again — unless the first full scan is still running, which
        reads the new cut's folders anyway once restarted.
        """
        for uri in self._uris:
            self._apply_status(uri)
        scanning = bool(self._probe_queue or self._pending)
        self._start_scan(preview_only=not scanning)
        self._changed()

    # ── Seeding ───────────────────────────────────────────

    def _apply_open_department(self) -> None:
        """Pin the department the dialog was opened from.

        Submitting from a lighting workfile almost always means "preview what
        I am looking at", for every entity — an explicit intent, so it pins
        rather than merely seeding. Skipped when the department is not
        renderable, or when there is no opened-from department.
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

    def _batch(self) -> list[str]:
        """Rows the form speaks for: every ticked row, else every visible row."""
        ticked = {uri for uri, _key in self._ticks}
        rows = [u for u in self._uris if u in ticked]
        return rows or [u for u in self._uris if u not in self._hidden]

    def _seed_values(self) -> dict:
        """Per-field agreement across the batch (or ``MIXED``)."""
        return resolve.seed_form(
            [self._props.get(u, {}) for u in self._batch()],
            sections=resolve.SECTIONS,
            fallbacks=self._fallbacks,
        )

    def _reseed_form(self, *, initial: bool = False) -> None:
        """Re-derive every *unpinned* field from the entities being submitted.

        Pinned fields are the artist's explicit choice and survive a change
        of ticks untouched; growing the submission only re-derives what the
        artist did not set.
        """
        seeded = self._seed_values()
        self._seeding = True
        try:
            for key, entry in self._fields.items():
                if not entry.pinned:
                    entry.seed(seeded.get(key, resolve.MIXED))
        finally:
            self._seeding = False
        self._refresh_channel_options(initial=initial)

    def _refresh_channel_options(self, *, initial: bool = False) -> None:
        """Repopulate the channel menu from the rows being rendered.

        The menu lists the *union*, because a channel only one shot defines
        still has to be selectable. The first build checks the
        *intersection* — for one entity exactly its own list; for a batch
        the largest set that renders on all of them. After that, picks carry
        over by name, so a channel arriving with a newly ticked entity
        starts unchecked: widening the submission must never widen the
        render.
        """
        render_rows = [
            uri for uri in self._uris if (uri, grid.RENDER) in self._ticks
        ] or self._batch()
        properties = [self._props.get(u, {}) for u in render_rows]
        names = resolve.channel_union(properties)
        if initial:
            checked = resolve.channel_intersection(properties)
        else:
            if names == self._rnd_channels.options():
                return
            checked = self._rnd_channels.checked_items()
        self._rnd_channels.set_options(names, checked)

    # ── Resolution ────────────────────────────────────────

    def _form_values(self) -> dict:
        """Every current form value, keyed by settings key.

        Tri-state fields report ``MIXED`` when they sit on their unset
        representation; the resolver reads that as "fall through to the
        entity". The batch fields are read straight off their widgets.
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
        return [key for key, entry in self._fields.items() if entry.pinned]

    def _resolved_rows(self) -> list[tuple[str, dict, list[str]]]:
        """``(uri, settings, warnings)`` for every ticked row, in grid order.

        The single source of truth for the Warnings column and the submit,
        so what the grid warns about is by construction what gets
        submitted.
        """
        form = self._form_values()
        pinned = self._pinned_keys()
        rows = []
        for uri in self._uris:
            kinds = grid.row_kinds(uri, self._columns, self._ticks)
            if not kinds:
                continue
            properties = self._props.get(uri, {})
            settings = grid.finish_row_settings(
                resolve.resolve_settings(
                    properties, sections=kinds, form=form, pinned=pinned,
                    fallbacks=self._fallbacks,
                ),
                grid.row_publish_departments(uri, self._columns, self._ticks),
            )
            assigned = list(properties.get('departments') or []) or None
            warnings = resolve.entity_warnings(properties, settings, departments=assigned)
            missing = grid.unpublished_upstream(
                uri, self._columns, self._ticks, self._states,
                cuts={
                    grid.PLAYBLAST: settings.get('pb_department'),
                    grid.RENDER: settings.get('render_department'),
                },
            )
            if missing:
                warnings.insert(0, f"{', '.join(missing)} stale and not ticked")
            rows.append((uri, settings, warnings))
        return rows

    def _recompute_warnings(self) -> None:
        self._warnings = {uri: w for uri, _s, w in self._resolved_rows() if w}

    # ── Submit ────────────────────────────────────────────

    def _on_submit(self) -> None:
        rows = self._resolved_rows()
        if not rows:
            QMessageBox.warning(
                self, "Farm Submit", "Tick at least one cell before submitting.",
            )
            return
        if any(s.get('render') for _u, s, _w in rows) and not self._rnd_channels.checked_items():
            QMessageBox.warning(
                self, "Farm Submit",
                "Check at least one channel in the Render settings before "
                "submitting.",
            )
            return

        warned = [
            f"  • {self._uri_objects[uri].segments[-1]}: {'; '.join(w)}"
            for uri, _s, w in rows if w
        ]
        if len(rows) > 1 or warned:
            lines = [
                f"Submit {grid.summary(self._uris, self._columns, self._ticks, self._noun)}?",
            ]
            if warned:
                lines += ["", f"{len(warned)} with warnings:"] + warned[:10]
                if len(warned) > 10:
                    lines.append(f"  … and {len(warned) - 10} more")
            confirm = QMessageBox.question(
                self, "Farm Submit", "\n".join(lines),
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if confirm != QMessageBox.Ok:
                return

        configs = [
            {
                'entity': {
                    'uri': uri,
                    'name': self._uri_objects[uri].segments[-1],
                    'context': self._context,
                },
                'settings': settings,
            }
            for uri, settings, _w in rows
        ]
        try:
            window = start_submission(configs, parent=self.parentWidget())
        except Exception as error:
            log.exception("Could not start the background submission")
            QMessageBox.critical(
                self, "Farm Submit",
                "Could not start the submission in the background:\n\n"
                f"{error}",
            )
            return
        window.show()
        self.accept()

    def done(self, result: int) -> None:
        self._poll.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().done(result)


def start_submission(configs: list[dict], parent: QWidget | None = None):
    """Write ``configs`` as a plan, launch the runner, return its status window.

    Shared by the dialog and the status window's **Retry failed**.
    """
    import datetime as dt
    import os
    import uuid

    from tumblepipe.api import api, local_path
    from tumblepipe.util.uri import Uri
    from tumblepipe.farm import submit_plan

    from .farm_submission_window import FarmSubmissionWindow

    houdini_version = os.environ.get('TH_HOUDINI_VERSION')
    try:
        import hou
        houdini_version = hou.applicationVersionString()
    except Exception:
        pass

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    directory = Path(local_path(api.storage.resolve(
        Uri.parse_unsafe('temp:/farm_submissions')
    ))) / f'{stamp}_{uuid.uuid4().hex[:6]}'
    plan = submit_plan.make_plan(configs, env=dict(os.environ), houdini_version=houdini_version)
    plan_path = submit_plan.write_plan(directory, plan)
    process = submit_plan.launch(plan_path)
    window = FarmSubmissionWindow(plan_path, process, configs, parent=parent)
    # A window with no parent (the Farm Submit app outside Houdini) would be
    # collected the moment the caller returns; hold it until it closes.
    _OPEN_WINDOWS.append(window)
    window.destroyed.connect(lambda *_a, w=window: _forget_window(w))
    return window


_OPEN_WINDOWS: list = []


def _forget_window(window) -> None:
    try:
        _OPEN_WINDOWS.remove(window)
    except ValueError:
        pass
