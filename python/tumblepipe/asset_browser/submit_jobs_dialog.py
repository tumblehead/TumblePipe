"""The Farm Submit dialog: tick publish, playblast and render steps per entity, submit.

Rows are the entities of one context (shots, or assets), grouped by sequence
or category; columns are pipeline steps — one publish column per department
in pool order, then Playblast (shots only) and Render. A ticked cell means
"do this step for this entity", and each cell shows how up to date that step
is: never done, stale, current, or nothing to do. Every row, column and
sequence carries a tri-state checkbox. The policy behind all of it — states,
ticks, warnings, what a row submits — is pure and lives in ``tumblepipe.farm.submit.grid``; the
status scan is ``tumblepipe.farm.submit.status``.

The settings stay the per-entity tri-state form it has been since the
multi-entity rework: left alone a field is *unpinned* and every entity uses
its own configured value (``⟨per entity⟩`` when they disagree); touching it
*pins* it for the whole submission. Samples, Motion blur and DOF are the
exception: they override the scene's render settings, so unpinned they show
``⟨from scene⟩`` and send nothing. The read-only Camera row is looked up by
``tumblepipe.farm.submit.camera``. Resolution order and the rules live in
``submit_settings``. The grid chooses the *steps*; the form never did.

Submit first shows a review over the window (``farm_submit_ui.ReviewSheet``:
a row per entity, its steps, what to check), then writes a plan and hands it
to a separate process (``tumblepipe.farm.submit.plan``) running Houdini's
bundled Python: Houdini is free in seconds however large the submission. The
progress follows on the same overlay (``farm_submit_ui.SubmissionView``);
the window stays open, so **Back to grid** submits more, and closing it does
not stop the submission.

Each step kind has a colour (``farm_submit_ui.KIND_COLOURS``) worn by its
columns' header strip, its ticked cells and its settings section's edge.
Columns with nothing to do on any row (a department nobody has a workfile
in) collapse to a sliver by themselves; any column collapses from its
header or the **Columns** menu. Settings sections collapse from their
headline. The pinned settings, the Render batch choices, collapsed columns
and sections, and the window's size are remembered per project and tab
(``tumblepipe.farm.submit.remembered``).

The grid always lists every entity of the context. Opened from the Render
quick action or a card's **Submit Jobs…**, it has those entities' Render
cells ticked; from the **Farm Submit** quick action, the open workfile's
entities have their department's publish cell ticked. With no pipeline
workfile open, both open with nothing ticked. The settings panel shows only
the sections of the step kinds that have a ticked cell.

**Assets** and **Shots** tabs switch the context. The dialog opens on the
context it was opened for (the open workfile's, or the card's), but an
artist in an asset workfile can still submit shots: each tab is its own
grid and settings form (``_ContextPane``), built the first time it is shown
and kept, ticks and pins included, while the dialog is open. Only the opened
tab is seeded with ticks and the workfile's department (``grid.tab_seed``);
Submit sends the tab in front and nothing from the other.

The **Edits** tab (``_EditsPane``) has no departments: it lists the
project's edits, each with its finished published versions, and one step,
**Render Edit**, which renders the chosen version into a movie on the farm
(``farm/pipelines/edit_render.py``).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QEvent, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPen, QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox,
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QStyledItemDelegate,
    QToolButton, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BORDER, FONT_BODY, FONT_FAMILY,
    TEXT_PRIMARY, TEXT_SECONDARY,
)

from tumblepipe.farm.submit import grid as grid
from tumblepipe.farm.submit import remembered
from tumblepipe.farm.submit import review as submit_review
from tumblepipe.farm.submit import status as farm_status
from tumblepipe.farm.submit import camera as render_camera
from tumblepipe.farm.submit import settings as resolve
from tumblepipe.pipe.houdini.ui.report import report_failure
from tumblepipe.pipe.usd.render_settings import PLAYBLAST_LIGHTS
from tumblepipe.farm.specs.playblast import PLAYBLAST_RENDERERS, PLAYBLAST_SHADINGS

from .farm_submit_ui import (
    EDIT_KIND, KIND_COLOURS, KIND_FILLS, FolderTabBar, NoticeCard, Overlay,
    ReviewSheet, Section, SubmissionView,
)
from tumblepipe.farm.submit.review import ReviewRow

log = logging.getLogger(__name__)

# Shown by an unpinned widget whose checked entities disagree. The angle
# brackets keep it from reading as a department or pool literally named
# "per entity".
PER_ENTITY_TEXT = "⟨per entity⟩"

# Shown by an unpinned override field (samples, motion blur, DOF): nothing is
# sent, so the render uses whatever the scene's render settings author.
FROM_SCENE_TEXT = "⟨from scene⟩"

# The render_mode the Range checkboxes send, keyed by (preview, full range)
# (batch_submit.RENDER_TASK_KEYS maps it to the render chains). Neither
# ticked has no mode: submit refuses it.
_RENDER_MODES = {
    (False, True): 'full',
    (True, False): 'first_middle_last',
    (True, True): 'preview_then_full',
}

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
ERROR_COLOUR = "#e05a4f"

# A collapsed column's width: room for its kind's strip, a ticked cell's
# colour and a stale dot, nothing else.
COLLAPSED_WIDTH = 16
# The header's kind strip, along its top edge.
KIND_STRIP = 3

# What each step column is, for its header's tooltip (the old legend's job).
_COLUMN_TIPS = {
    grid.PUBLISH: "Publish the {label} department from its latest workfile.",
    grid.PLAYBLAST: "A playblast on the farm, of every department up to the Playblast cut.",
    grid.RENDER: "A render on the farm, of every department up to the Render cut.",
}
_STATE_LINES = {
    grid.PUBLISH: (
        (grid.CURRENT, "exported since the workfile was last saved"),
        (grid.STALE, "workfile saved after its last export"),
        (grid.NEVER, "never exported"),
        (grid.UNCLAIMED, "(dim) only a Multi's workfile, which never exported it"),
        (grid.NONE, "no workfile: nothing to publish"),
    ),
    'preview': (
        (grid.CURRENT, "newer than every publish it shows"),
        (grid.STALE, "older than a publish it shows"),
        (grid.NEVER, "never made"),
        (grid.NONE, "no frame range: nothing to make"),
    ),
}

GLYPHS = {
    grid.NONE: "·", grid.NEVER: "○", grid.UNCLAIMED: "○", grid.STALE: "●",
    grid.CURRENT: "✓", grid.PENDING: "…",
}
GLYPH_COLOURS = {
    grid.NONE: DIM_COLOUR, grid.NEVER: NEVER_COLOUR, grid.UNCLAIMED: DIM_COLOUR,
    grid.STALE: STALE_COLOUR,
    grid.CURRENT: CURRENT_COLOUR, grid.PENDING: DIM_COLOUR,
}
TIPS = {
    grid.PUBLISH: {
        grid.NONE: "no workfile for this department",
        grid.NEVER: "never exported",
        grid.UNCLAIMED: (
            "no workfile of its own: its Multi's workfile has never exported "
            "it, so Select stale skips it (tick it to publish from the Multi)"
        ),
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
# list it. Owned by submit_settings so the pure resolution policy and
# the widget that drives it can't drift apart.
_DEFAULT_CHANNEL = resolve.DEFAULT_CHANNEL

# Rows whose folders are resolved per event-loop turn while the grid fills.
_PROBE_CHUNK = 12


# ── Theme ─────────────────────────────────────────────────

# Always-visible scrollbars. TumbleTrove's shared SCROLLBAR_STYLE draws the
# handle transparent, which hides that the grid and the settings panel
# scroll at all.
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
QFrame#farmSection QLineEdit, QFrame#farmSection QSpinBox, QFrame#farmSection QComboBox {{
    background-color: #1c1c1c;
    border-color: #3a3a3a;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QFrame#farmSection QLineEdit:focus, QFrame#farmSection QSpinBox:focus,
QFrame#farmSection QComboBox:focus {{
    border-color: {ACCENT};
}}
/* A field left to each entity shows their value in grey; one the artist
   set for the whole submission is white, with an accent edge. */
*[pinned="false"] {{ color: {TEXT_SECONDARY}; }}
*[pinned="true"] {{ color: {TEXT_PRIMARY}; }}
QLineEdit[pinned="true"], QSpinBox[pinned="true"], QComboBox[pinned="true"] {{
    border-left: 2px solid {ACCENT};
}}
QFrame#farmSection QLabel {{ background: transparent; }}
QToolButton {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 10px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
}}
QToolButton:hover {{ border-color: {ACCENT}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QMenu {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    padding: 4px 0;
}}
QMenu::item {{ padding: 5px 22px 5px 24px; }}
QMenu::item:selected {{ background-color: #2f3f55; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
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
    runs over every entity in the context, so the reads are batched.

    Importing ``tumblepipe.api`` here is a passive lookup: the catalog has
    already activated the project before showing the dialog.
    """
    from tumblepipe.api import default_client
    config = default_client().config
    with config.coherent():
        return [config.get_properties(uri) or {} for uri in entity_uris]


def _list_dept_names(context: str, *, only_publishable: bool, only_renderable: bool) -> list[str]:
    """Return department names for ``context`` ('shots' or 'assets').

    Filters by ``publishable`` / ``renderable`` flags. Excludes
    Python-generated departments (which can't be exported from Houdini)
    and disabled ones.
    """
    from tumblepipe.config.department import list_departments
    depts = list_departments(
        context, include_generated=False, include_disabled=False,
    )
    if only_publishable:
        depts = [d for d in depts if d.publishable]
    if only_renderable:
        depts = [d for d in depts if d.renderable]
    return [d.name for d in depts]


def _list_selectable_entities(context: str) -> list[object]:
    """Return every terminal entity URI in ``context``, sorted by path.

    ``list_entity_uris(closure=True)`` returns childless *category* nodes
    (e.g. an empty ``assets/CHAR``) alongside real entities, so each URI is
    vetted with ``is_terminal_entity`` (schema-keyed), all inside one
    ``coherent()`` scope.
    """
    from tumblepipe.api import default_client
    from tumblepipe.config.entities import is_terminal_entity
    from tumblepipe.util.uri import Uri
    config = default_client().config
    root = Uri.parse(f'entity:/{context}')
    with config.coherent():
        entities = [
            uri for uri in config.list_entity_uris(root, closure=True)
            if is_terminal_entity(config, uri)
        ]
    entities.sort(key=str)
    return entities


def _list_groups(context: str) -> list[tuple[str, list[object]]]:
    """Return ``(group_name, member_uris)`` for every group in ``context``.

    Offered as a filter above the grid: picking a Multi narrows the rows to
    its members.
    """
    from tumblepipe.api import default_client
    from tumblepipe.config.groups import list_groups
    with default_client().config.coherent():
        groups = [
            (group.name, list(group.members))
            for group in list_groups(context)
        ]
    return sorted(groups, key=lambda item: item[0])


def _group_members(group_uri) -> list[object]:
    """Member URIs of the Multi at ``group_uri``; empty when it is gone."""
    from tumblepipe.config.groups import get_group
    group = get_group(group_uri)
    return list(group.members) if group is not None else []


def _box(painter: QPainter, rect: QRect, state: str, kind: str | None = None) -> None:
    """Draw a tri-state checkbox (``on`` / ``mixed`` / ``off``) in ``rect``,
    filled in ``kind``'s colour when it ticks one kind's cells, else the accent."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    if state == 'off':
        painter.setPen(QPen(QColor("#6a6a70"), 1))
        painter.setBrush(QColor(BG_DARKEST))
    elif kind is not None:
        painter.setPen(QPen(QColor(KIND_COLOURS[kind]), 1))
        painter.setBrush(QColor(KIND_FILLS[kind]))
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
    ``PartiallyChecked`` (tri-state); a combo on a placeholder row; a toggle
    (a yes/no field shown as a three-row combo, where a partially checked
    box would read as checked) on its placeholder row; a line edit on
    empty-with-placeholder. Four of those are also a way *back* to unpinned
    without the revert button — which stays for the spin boxes, because
    spinning down past the field's minimum is not an affordance.
    """

    def __init__(self, key: str, widget: QWidget, kind: str) -> None:
        self.key = key
        self.widget = widget
        self.kind = kind  # 'spin' | 'check' | 'combo' | 'toggle' | 'line'
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
        if self.kind == 'toggle':
            # Rows: placeholder, yes, no.
            index = w.currentIndex()
            return resolve.MIXED if index <= 0 else index == 1
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
        elif self.kind == 'toggle':
            w.setCurrentIndex(0 if value is None else (1 if value else 2))
        else:
            w.setText('' if value is None else str(value))

    def _restyle(self) -> None:
        """Grey while unpinned ('this follows the entity, not you'), white
        with an accent edge once pinned (the dialog's ``pinned`` rules).

        Not italic: italic grey read as a disabled field, and artists took
        the Playblast settings for locked (2026-11).
        """
        self.widget.setProperty("pinned", self.pinned)
        style = self.widget.style()
        style.unpolish(self.widget)
        style.polish(self.widget)
        self.widget.update()


# ── Grid painting ─────────────────────────────────────────

class _GridDelegate(QStyledItemDelegate):
    """Paints every grid cell from the dialog's state; the items hold no data
    beyond which row they are, so a tick is a repaint, not a model write."""

    def __init__(self, dialog: "_ContextPane") -> None:
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
        # heavier one where one step kind gives way to the next.
        painter.setPen(QPen(QColor(RULE_COLOUR), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if column in dialog._kind_starts:
            painter.setPen(QPen(QColor("#5a5a5a"), 2))
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        painter.restore()


class _CheckHeader(QHeaderView):
    """Column headers: the step's kind strip, its label, a tri-state checkbox,
    and — under the pointer — a chevron that collapses the column."""

    HEIGHT = 56

    def __init__(self, dialog: "_ContextPane") -> None:
        super().__init__(Qt.Horizontal, dialog)
        self._dialog = dialog
        self.hovered = -1
        # Clickable is set by _build_grid: QTreeView.setHeader() resets it.
        self.setHighlightSections(False)
        self.setDefaultAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(size.width(), self.HEIGHT)

    def mouseMoveEvent(self, event) -> None:
        logical = self.logicalIndexAt(event.position().toPoint())
        if logical != self.hovered:
            self.hovered = logical
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovered = -1
        self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        # The collapse chevron is handled on press and the event goes no
        # further, so the header never registers a click there and the
        # column's checkbox is left alone.
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            logical = self.logicalIndexAt(pos)
            rect = QRect(self.sectionViewportPosition(logical), 0,
                         self.sectionSize(logical), self.height())
            if self._dialog._chevron_hit(logical, rect, pos):
                self._dialog._set_collapsed(logical, True)
                return
        super().mousePressEvent(event)

    def paintSection(self, painter: QPainter, rect: QRect, logical: int) -> None:
        painter.save()
        painter.fillRect(rect, QColor(BAND_COLOUR))
        painter.setPen(QPen(QColor(RULE_COLOUR), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.setPen(QPen(QColor("#4a4a4a"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if logical in self._dialog._kind_starts:
            painter.setPen(QPen(QColor("#5a5a5a"), 2))
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        self._dialog._paint_header(painter, rect, logical, hovered=logical == self.hovered)
        painter.restore()


# ── Context pane ──────────────────────────────────────────

class _ContextPane(QWidget):
    """One context's grid and settings form: a tab of :class:`SubmitJobsDialog`.

    Everything that depends on the context lives here — rows, department
    columns, the Playblast section (shots only), fallbacks, pins, ticks and
    the status scan — so the Assets and Shots tabs cannot leak into each
    other: each is its own pane, and Submit sends only the one in front.

    Args:
        dialog: The owning dialog; the footer and accept/close are its.
        entity_uris: Entities to start with ticked, Multis already expanded.
        context: ``'shots'`` or ``'assets'``.
        department: Department to pin the preview cuts to, or ``None``.
        tick_kinds: Step kinds to tick for ``entity_uris``.
    """

    def __init__(
        self,
        dialog: "SubmitJobsDialog",
        entity_uris: Sequence,
        context: str,
        department: str | None = None,
        tick_kinds: Sequence[str] = (grid.RENDER,),
    ) -> None:
        super().__init__(dialog)
        self._dialog = dialog
        self._context = context
        self._noun = grid.context_noun(context)
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
        # Logical header indices where one step kind gives way to the next:
        # a heavier rule is drawn ahead of each.
        self._kind_starts = {
            i + 1 for i, c in enumerate(self._columns)
            if i > 0 and self._columns[i - 1].kind != c.kind
        }
        self._warnings_column = len(self._columns) + 1

        # Collapsed columns (by key): the artist's, and those collapsed by
        # themselves for having nothing to do (``_auto_collapsed``, expanded
        # again once they do). A column the artist expanded never collapses
        # by itself again while the window is open.
        self._remembered = dialog.remembered_tab(context)
        self._collapsed: set[str] = remembered.restore_collapsed(
            self._remembered, [c.key for c in self._columns],
        )
        self._auto_collapsed: set[str] = set()
        self._user_expanded: set[str] = set()
        self._widths: dict[str, int] = {}
        # Each ↺ button and the keys it reverts: shown only while one is pinned.
        self._reverts: list[tuple[QPushButton, tuple[str, ...]]] = []
        # Pins the open workfile made (``_apply_open_department``), not the
        # artist's: never remembered.
        self._context_pins: dict[str, str] = {}

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
        # Render camera lookups, keyed (uri, channel, department).
        self._cameras: dict[tuple, render_camera.RenderCamera] = {}
        self._camera_pending: dict = {}
        self._camera_poll = QTimer(self)
        self._camera_poll.setInterval(120)
        self._camera_poll.timeout.connect(self._drain_cameras)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self._refresh)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addLayout(self._build_toolbar())

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)
        body.addWidget(self._build_grid(), 1)
        body.addWidget(self._build_forms())

        # The last opening's pins first, then the open workfile's department:
        # what the artist is looking at now wins over what they set last time.
        self._restore_remembered()
        self._apply_open_department()
        # Seed the tick state from the entities the dialog was opened for;
        # the open workfile's department narrows their publish cells to its.
        self._ticks = grid.opened_ticks(
            opened, self._columns, tick_kinds, self._open_department,
        )
        self._scroll_to_first_ticked(opened)
        self._show_active_sections()
        self._reseed_form(initial=True)
        self._start_scan()

    @property
    def context(self) -> str:
        return self._context

    def summary_text(self) -> str:
        """The footer line for this tab: what Submit would send."""
        return grid.summary(self._uris, self._columns, self._ticks, self._noun)

    def ticked_row_count(self) -> int:
        return grid.ticked_rows(self._ticks)

    def shutdown(self) -> None:
        """Stop the status scan; the dialog is closing."""
        self._poll.stop()
        self._camera_poll.stop()
        self._refresh_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ── Remembered settings ───────────────────────────────

    def remembered_record(self) -> dict:
        """What this tab keeps for the next opening (``farm.submit.remembered``).

        A pin the open workfile made (its department as the cut) is the
        workfile's, not the artist's: it is not carried to an opening from
        another workfile, or from none, unless the artist changed it.
        """
        pinned = remembered.artist_pins(
            {key: entry.value() for key, entry in self._fields.items() if entry.pinned},
            self._context_pins,
        )
        return remembered.capture(
            pinned, self._batch_choices(), sorted(self._collapsed - self._auto_collapsed),
        )

    def _batch_choices(self) -> dict[str, bool]:
        return {
            'render_preview': self._rnd_preview.isChecked(),
            'render_full': self._rnd_full.isChecked(),
            'standalone': self._rnd_standalone.isChecked(),
            'copy_to_edit': self._rnd_copy_edit.isChecked(),
        }

    def _accepts(self, key: str, value: Any) -> bool:
        """Whether field ``key`` can show ``value`` as a pinned choice."""
        entry = self._fields.get(key)
        if entry is None:
            return False
        widget = entry.widget
        if entry.kind == 'spin':
            return (
                isinstance(value, int) and not isinstance(value, bool)
                and widget.minimum() < value <= widget.maximum()
            )
        if entry.kind in ('check', 'toggle'):
            return isinstance(value, bool)
        if entry.kind == 'combo':
            return isinstance(value, str) and widget.findText(value) > 0
        return isinstance(value, str) and bool(value.strip())

    def _restore_remembered(self) -> None:
        """Pin what was pinned last time, and set the batch choices back."""
        record = self._remembered
        self._seeding = True
        try:
            for key, value in remembered.restore_pinned(record, self._accepts).items():
                entry = self._fields[key]
                entry._write(value)
                entry.pin()
        finally:
            self._seeding = False
        boxes = {
            'render_preview': self._rnd_preview, 'render_full': self._rnd_full,
            'standalone': self._rnd_standalone, 'copy_to_edit': self._rnd_copy_edit,
        }
        for key, checked in remembered.restore_batch(record).items():
            boxes[key].setChecked(checked)

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

        # Which columns show. Empty ones collapse by themselves; any one
        # collapses from here, its header's ‹ or its header's right-click.
        self._columns_button = QToolButton()
        self._columns_button.setText("Columns ▾")
        self._columns_button.setToolTip("Show, collapse or auto-collapse the step columns")
        self._columns_button.setPopupMode(QToolButton.InstantPopup)
        self._columns_menu = QMenu(self._columns_button)
        self._columns_menu.aboutToShow.connect(self._fill_columns_menu)
        self._columns_button.setMenu(self._columns_menu)
        row.addWidget(self._columns_button)

        row.addStretch(1)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        row.addWidget(self._status_label)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setToolTip("Read every cell's status from disk again")
        refresh_btn.clicked.connect(self._start_scan)
        refresh_btn.clicked.connect(self._refresh_cameras)
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
        # setHeader() makes the header clickable only if the view sorts, and
        # this one does not: without this the header checkboxes never emit
        # sectionClicked and a click on them does nothing.
        header.setSectionsClickable(True)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(COLLAPSED_WIDTH)
        header.resizeSection(0, 170)
        # Wide enough for the header label ('environment', 'animation'),
        # which _paint_header draws at 12px, and the collapse chevron beside
        # it on hover; never narrower than a cell needs.
        label_font = QFont(FONT_FAMILY)
        label_font.setPixelSize(12)
        label_font.setBold(True)
        metrics = QFontMetrics(label_font)
        for index, column in enumerate(self._columns, start=1):
            floor = 60 if column.kind == grid.PUBLISH else 86
            self._widths[column.key] = max(
                floor, metrics.horizontalAdvance(column.label) + 30,
            )
            header.setSectionResizeMode(index, QHeaderView.Fixed)
            header.resizeSection(
                index,
                COLLAPSED_WIDTH if column.key in self._collapsed else self._widths[column.key],
            )
        header.sectionClicked.connect(self._on_header_clicked)
        header.customContextMenuRequested.connect(self._header_menu)
        header.viewport().installEventFilter(self)

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
                self._show_tip(event, self._tooltip_at(event.pos()), self._tree)
                return True
        if obj is self._tree.header().viewport() and event.type() == QEvent.ToolTip:
            logical = self._tree.header().logicalIndexAt(event.pos())
            self._show_tip(event, self._header_tooltip(logical), self._tree.header())
            return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _show_tip(event, text: str, widget: QWidget) -> None:
        if text:
            QToolTip.showText(event.globalPos(), text, widget)
        else:
            QToolTip.hideText()

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
        ticked = " (will be submitted)" if (uri, step.key) in self._ticks else ""
        text = f"{item.text(0)}, {step.label}: {GLYPHS[state]} {table[state]}{ticked}"
        if step.key in self._collapsed:
            text += "\nThe column is collapsed: click its header to show it."
        return text

    def _header_tooltip(self, logical: int) -> str:
        """What a column is and what its cells' marks mean: the old legend's job."""
        if logical == 0:
            return (
                f"Tick or untick every visible {self._noun[0]}'s steps. "
                "Each row's and sequence's box does the same for its own."
            )
        if not 1 <= logical <= len(self._columns):
            return ""
        column = self._columns[logical - 1]
        colour = KIND_COLOURS[column.kind]
        lines = [
            f"<b style='color:{colour}'>{column.label}</b>",
            _COLUMN_TIPS[column.kind].format(label=column.label),
            "",
        ]
        table = _STATE_LINES[grid.PUBLISH if column.kind == grid.PUBLISH else 'preview']
        for state, meaning in table:
            lines.append(
                f"<span style='color:{GLYPH_COLOURS[state]}'>{GLYPHS[state]}</span>"
                f"&nbsp;&nbsp;{meaning}"
            )
        lines.append("")
        if column.key in self._collapsed:
            why = " (nothing to do on any row)" if column.key in self._auto_collapsed else ""
            lines.append(f"<i>Collapsed{why}. Click to show it.</i>")
        else:
            lines.append(
                "<i>Click to tick every visible row. ‹ collapses the column; "
                "right-click for more.</i>"
            )
        return "<br>".join(lines)

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
            if step.key in self._collapsed:
                return  # a sliver is not a target; its header shows it again
            if uri:
                keys = [(uri, step.key)] if self._tickable(uri, step.key) else []
            else:
                keys = self._group_keys(group, step.key)
        self._toggle(keys)

    def _on_header_clicked(self, logical: int) -> None:
        if logical == 0:
            self._toggle(self._visible_keys())
        elif 1 <= logical <= len(self._columns):
            column = self._columns[logical - 1]
            if column.key in self._collapsed:
                self._set_collapsed(logical, False)
            else:
                self._toggle(self._visible_keys(column.key))

    # ── Collapsed columns ─────────────────────────────────

    def _chevron_rect(self, rect: QRect) -> QRect:
        """The collapse chevron's spot in a header section: bottom right,
        level with the checkbox and clear of the label."""
        return QRect(rect.right() - 16, rect.bottom() - BOX - 10, 14, BOX + 4)

    def _chevron_hit(self, logical: int, rect: QRect, pos) -> bool:
        if not 1 <= logical <= len(self._columns):
            return False
        if self._columns[logical - 1].key in self._collapsed:
            return False
        return self._chevron_rect(rect).contains(pos)

    def _set_collapsed(self, logical: int, collapsed: bool, *, auto: bool = False) -> None:
        """Collapse column ``logical`` to a sliver, or show it again.

        Collapsing is a view, like the row filter: the column's ticks stay
        (a ticked sliver shows its kind's colour), but bulk ticks — the All
        box, a row's or sequence's box, Select stale — no longer reach it.
        """
        key = self._columns[logical - 1].key
        if collapsed:
            self._collapsed.add(key)
            if auto:
                self._auto_collapsed.add(key)
        else:
            self._collapsed.discard(key)
            self._auto_collapsed.discard(key)
            if not auto:
                self._user_expanded.add(key)
        if not auto and collapsed:
            # Collapsed on purpose: no longer the auto rule's to reopen.
            self._auto_collapsed.discard(key)
        self._tree.header().resizeSection(
            logical, COLLAPSED_WIDTH if collapsed else self._widths[key],
        )
        self._changed()

    def _auto_collapse(self) -> None:
        """Collapse the columns with nothing to do on any row; reopen those
        that have work again. Never a column with a tick, or one the artist
        opened."""
        if not self._dialog.ui['collapse_empty']:
            return
        empty = grid.empty_columns(self._uris, self._columns, self._states)
        ticked = {key for _uri, key in self._ticks}
        for logical, column in enumerate(self._columns, start=1):
            key = column.key
            if key in empty and key not in self._collapsed:
                if key not in ticked and key not in self._user_expanded:
                    self._set_collapsed(logical, True, auto=True)
            elif key not in empty and key in self._auto_collapsed:
                self._set_collapsed(logical, False, auto=True)

    def _set_collapse_empty(self, enabled: bool) -> None:
        self._dialog.ui['collapse_empty'] = enabled
        if enabled:
            self._user_expanded.clear()
            self._auto_collapse()
        else:
            for logical, column in enumerate(self._columns, start=1):
                if column.key in self._auto_collapsed:
                    self._set_collapsed(logical, False, auto=True)

    def _show_all_columns(self) -> None:
        for logical, column in enumerate(self._columns, start=1):
            if column.key in self._collapsed:
                self._set_collapsed(logical, False)

    def _fill_columns_menu(self, menu: QMenu | None = None) -> None:
        menu = menu or self._columns_menu
        menu.clear()
        previous = None
        for logical, column in enumerate(self._columns, start=1):
            if previous is not None and column.kind != previous:
                menu.addSeparator()
            previous = column.kind
            action = menu.addAction(column.label)
            action.setCheckable(True)
            action.setChecked(column.key not in self._collapsed)
            action.toggled.connect(
                lambda shown, n=logical: self._set_collapsed(n, not shown)
            )
        menu.addSeparator()
        auto = menu.addAction("Collapse empty columns by themselves")
        auto.setCheckable(True)
        auto.setChecked(self._dialog.ui['collapse_empty'])
        auto.setToolTip("A column with nothing to do on any row collapses once the status is read")
        auto.toggled.connect(self._set_collapse_empty)
        show_all = menu.addAction("Show every column")
        show_all.setEnabled(bool(self._collapsed))
        show_all.triggered.connect(self._show_all_columns)

    def _header_menu(self, pos) -> None:
        header = self._tree.header()
        logical = header.logicalIndexAt(pos)
        menu = QMenu(self)
        if 1 <= logical <= len(self._columns):
            column = self._columns[logical - 1]
            collapsed = column.key in self._collapsed
            action = menu.addAction(f"{'Show' if collapsed else 'Collapse'} {column.label}")
            action.triggered.connect(
                lambda _c=False, n=logical, c=collapsed: self._set_collapsed(n, not c)
            )
            menu.addSeparator()
        sub = menu.addMenu("Columns")
        self._fill_columns_menu(sub)
        menu.exec(header.mapToGlobal(pos))

    # ── Keys ──────────────────────────────────────────────

    def _tickable(self, uri: str, column_key: str) -> bool:
        return self._states.get((uri, column_key), grid.PENDING) in grid.TICKABLE

    def _shown_columns(self) -> list[str]:
        """Keys of the columns not collapsed: what bulk ticks reach."""
        return [c.key for c in self._columns if c.key not in self._collapsed]

    def _row_keys(self, uri: str) -> list:
        return [(uri, key) for key in self._shown_columns() if self._tickable(uri, key)]

    def _group_keys(self, group: str, column_key: str | None = None) -> list:
        keys = []
        columns = [column_key] if column_key else self._shown_columns()
        for uri in self._group_uris.get(group, []):
            if uri in self._hidden:
                continue
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
            if key[0] not in self._hidden and key[1] not in self._collapsed
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

    def _show_active_sections(self) -> None:
        """Show the settings sections of the step kinds that have a tick,
        each headed by what it will submit."""
        active = grid.ticked_kinds(self._columns, self._ticks)
        for kind, section in self._sections.items():
            section.setVisible(kind in active)
            section.set_summary(grid.kind_summary(
                kind, self._uris, self._columns, self._ticks, self._noun,
            ))
        self._no_settings.setVisible(not active)

    def _scope_playblast_look(self) -> None:
        """Show only the settings the chosen renderer reads.

        They used to be greyed out instead, which artists took for locked
        fields, and Shading (viewport) beside Materials (Karma) — both
        offering clay — read as one setting twice (2026-11). With the
        renderer left per entity, either may apply: both sets show, each
        label naming its renderer.
        """
        rows_by_renderer = getattr(self, '_pb_rows', None)
        entry = self._fields.get('pb_renderer')
        if not rows_by_renderer or entry is None:
            return
        renderer = entry.value()
        mixed = renderer is resolve.MIXED
        form = self._pb_form
        for name, rows in rows_by_renderer.items():
            shown = mixed or renderer == name
            for label, row, text in rows:
                # The whole row, spacing included (hiding the widgets alone
                # leaves a gap per row).
                form.setRowVisible(row, shown)
                if label is not None:
                    label.setText(f"{text} ({'Karma' if name == 'karma' else name}):" if mixed else f"{text}:")
        form.setRowVisible(self._pb_hint, mixed)

    def _show_reverts(self) -> None:
        """A ↺ shows only beside a field the artist pinned: nothing to revert otherwise."""
        for button, keys in self._reverts:
            button.setVisible(any(
                self._fields[key].pinned for key in keys if key in self._fields
            ))

    def _refresh(self) -> None:
        self._show_active_sections()
        self._reseed_form()
        self._scope_playblast_look()
        self._show_reverts()
        self._update_camera()
        self._recompute_warnings()
        self._dialog._pane_changed(self)
        self._tree.viewport().update()

    # ── Painting ──────────────────────────────────────────

    def _paint_header(
        self, painter: QPainter, rect: QRect, logical: int, *, hovered: bool = False,
    ) -> None:
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
        colour = QColor(KIND_COLOURS[column.kind])
        # The kind's strip: what ties the column to its settings section.
        painter.fillRect(QRect(rect.left(), rect.top(), rect.width(), KIND_STRIP), colour)
        if column.key in self._collapsed:
            painter.setPen(colour if hovered else QColor(TEXT_SECONDARY))
            painter.setFont(font)
            painter.drawText(rect.adjusted(0, KIND_STRIP, 0, 0), Qt.AlignCenter, "›")
            return
        if column.kind != grid.PUBLISH:
            font.setBold(True)
            painter.setPen(QColor(TEXT_PRIMARY))
        painter.setFont(font)
        painter.drawText(
            QRect(rect.left(), rect.top() + KIND_STRIP + 6, rect.width(), 18),
            Qt.AlignCenter, column.label,
        )
        box = QRect(rect.center().x() - BOX // 2, rect.bottom() - BOX - 8, BOX, BOX)
        _box(painter, box, grid.tri_state(self._visible_keys(column.key), self._ticks), column.kind)
        if hovered:
            chevron = self._chevron_rect(rect)
            painter.fillRect(chevron, QColor(BG_DARKEST))
            painter.setPen(QColor(TEXT_PRIMARY))
            painter.drawText(chevron, Qt.AlignCenter, "‹")

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
                if step.key in self._collapsed:
                    return
                keys = self._group_keys(group, step.key)
                if keys:
                    box = QRect(rect.center().x() - BOX // 2, rect.center().y() - BOX // 2, BOX, BOX)
                    _box(painter, box, grid.tri_state(keys, self._ticks), step.kind)
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
            # The kind's own colour, so a ticked cell says which settings
            # section speaks for it.
            painter.fillRect(rect.adjusted(1, 1, -1, -1), QColor(KIND_FILLS[step.kind]))
            painter.setPen(QPen(QColor(KIND_COLOURS[step.kind]), 1))
            painter.drawRect(rect.adjusted(1, 1, -2, -2))
        if step.key in self._collapsed:
            # A sliver: its tick's colour, and a dot where there is work.
            if state in grid.NEEDS_WORK:
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#ffffff" if ticked else GLYPH_COLOURS[state]))
                painter.drawEllipse(QPointF(rect.center()) + QPointF(0.5, 0.5), 2.5, 2.5)
            return
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
            except Exception as error:
                # The rows whose folders could not be resolved show the
                # error in their cells, like a scan that failed.
                log.exception("Could not resolve status folders")
                for uri in chunk:
                    self._statuses[uri] = farm_status.RowStatus(
                        uri=uri, error=str(error),
                    )
                    self._apply_status(uri)

        finished = [future for future in self._pending if future.done()]
        for future in finished:
            uri, preview_only = self._pending.pop(future)
            try:
                status = future.result()
            except Exception as error:
                # A share that will not answer: the row's cells show it.
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
            # Every cell's state is known: the empty columns can tell.
            self._auto_collapse()
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
                    shared=column.department in status.shared,
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
        # One section per step kind, shown only while a cell of that kind is
        # ticked (``_show_active_sections``): settings for a step that will
        # not run only read as if it will. Each sits on a lighter frame,
        # edged in its kind's colour, and collapses from its headline.
        self._sections: dict[str, Section] = {}
        self._sections[grid.PUBLISH] = self._build_publish_section()
        if self._context == "shots":
            self._sections[grid.PLAYBLAST] = self._build_playblast_section()
        else:
            self._pb_dept = None
        self._sections[grid.RENDER] = self._build_render_section()
        self._no_settings = QLabel(
            "Tick a cell to see its settings.\n\nHover a column's header for "
            "what it does and what its marks mean."
        )
        self._no_settings.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self._no_settings.setAlignment(Qt.AlignCenter)
        self._no_settings.setWordWrap(True)
        layout.addWidget(self._no_settings)
        for section in self._sections.values():
            layout.addWidget(section)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(column)
        # By name: a stylesheet with no selector reaches every widget under
        # the viewport, and painted each form row's holder dark on the
        # lighter sections.
        scroll.viewport().setObjectName("farmFormsViewport")
        scroll.viewport().setStyleSheet(
            f"QWidget#farmFormsViewport {{ background-color: {BG_DARKEST}; }}"
        )
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The widest rows are two spin boxes sized for ⟨per entity⟩ plus a
        # label and a ↺ (about 450px); narrower clips the ↺ buttons.
        # Measured with every section showing and open, so the panel keeps
        # one width as sections come, go and collapse.
        scroll.setFixedWidth(max(500, column.minimumSizeHint().width() + 24))
        collapsed = set(self._dialog.ui['collapsed_sections'])
        for kind, section in self._sections.items():
            section.set_expanded(kind not in collapsed)
            section.toggled.connect(
                lambda expanded, k=kind: self._dialog.section_toggled(k, expanded)
            )
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
            'combo': 'activated', 'toggle': 'activated', 'line': 'textEdited',
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
        elif kind in ('combo', 'toggle'):
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

    def _spin(
        self, key: str, low: int, high: int, *, override: bool = False,
    ) -> QSpinBox:
        """An integer field that can park on ``⟨per entity⟩``.

        The range is widened by exactly one step below ``low`` and that step
        carries ``specialValueText`` — Qt's own idiom for "no value". One
        step, not a far-away sentinel: the minimum is reachable by spinning,
        so anything further would let a bounded field (pre-roll, tiles) be
        typed below its real lower bound.
        """
        box = QSpinBox()
        # No arrows: the stylesheet left them as two dots that read as a
        # glitch. Typing, the arrow keys and the wheel (once focused) step it.
        box.setButtonSymbols(QAbstractSpinBox.NoButtons)
        box.setRange(low - 1, high)
        if override:
            box.setSpecialValueText(FROM_SCENE_TEXT)
            box.setToolTip(
                "Leave on ⟨from scene⟩ to render with the scene's own render "
                "settings; a value here overrides them on the farm."
            )
        else:
            box.setSpecialValueText(PER_ENTITY_TEXT)
            box.setToolTip(
                "Leave on ⟨per entity⟩ to let each entity use its own "
                "configured value."
            )
        self._register(key, box, 'spin')
        return box

    def _check(
        self, key: str, label: str, *, override: bool = False,
    ) -> QCheckBox:
        box = QCheckBox(label)
        box.setTristate(True)
        box.setToolTip(
            "Partially checked = use the scene's own render settings; "
            "checked or unchecked overrides them on the farm."
            if override else
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

    def _choice(self, key: str, yes: str, no: str) -> QComboBox:
        """A yes/no field as ``⟨per entity⟩`` / ``yes`` / ``no``.

        For a setting whose per-entity state must be unmistakable: a
        partially checked box looks checked at a glance, and two submissions
        that looked identical rendered differently (wifi_retina, Scene
        materials).
        """
        box = QComboBox()
        box.addItems([PER_ENTITY_TEXT, yes, no])
        self._register(key, box, 'toggle')
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
        # Hidden until a field it reverts is pinned (_show_reverts); keeps
        # its room meanwhile, so the row does not shift when it appears.
        policy = button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        button.setSizePolicy(policy)
        button.hide()
        self._reverts.append((button, tuple(keys)))
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

    def _section(self, kind: str, title: str):
        """A settings section for step ``kind``, and its form layout."""
        section = Section(title, KIND_COLOURS[kind])
        return section, section.form()

    def _build_publish_section(self) -> Section:
        box, form = self._section(grid.PUBLISH, "Publish")
        self._pub_pool = self._line('pub_pool', 'general')
        form.addRow("Pool:", self._pub_pool)
        self._pub_priority = self._spin('pub_priority', 0, 100)
        form.addRow("Priority:", self._row(self._pub_priority, revert=('pub_priority',)))
        return box

    def _build_playblast_section(self) -> Section:
        """A preview on the farm, viewport or Karma XPU — shots only.

        The input is the shot's staged 'default' stage cut at this
        department, and the frame range is the shot's own, so there is no
        frame or channel field here. Only the chosen renderer's settings
        show (``_scope_playblast_look``).
        """
        box, form = self._section(grid.PLAYBLAST, "Playblast")
        self._pb_dept = self._combo('pb_department', self._renderable_departments)
        self._pb_dept.setToolTip(
            "Playblasts every department up to and including this one, in "
            "pipeline order — the same cut the render uses."
        )
        self._pb_dept.activated.connect(lambda *_a: self._on_cut_changed())
        form.addRow("Up to:", self._pb_dept)
        self._pb_width = self._spin('pb_res_x', 16, 8192)
        self._pb_height = self._spin('pb_res_y', 16, 8192)
        self._pb_width.setToolTip(
            "Each shot plays at its own render resolution unless set here."
        )
        form.addRow("Resolution:", self._row(
            self._pb_width, "×", self._pb_height, revert=('pb_res_x', 'pb_res_y'),
        ))
        self._pb_renderer = self._combo('pb_renderer', PLAYBLAST_RENDERERS)
        self._pb_renderer.setToolTip(
            "viewport: the viewport's look — display colours and textures, "
            "shaded as set below. Fast, on the CPU, takes no licence.\n"
            "karma: Karma XPU, lit and sampled as set below. Takes a Karma "
            "licence and an NVIDIA GPU."
        )
        form.addRow("Renderer:", self._row(self._pb_renderer, revert=('pb_renderer',)))
        self._pb_shading = self._combo('pb_shading', PLAYBLAST_SHADINGS)
        self._pb_shading.setToolTip(
            "Viewport only. Every option has ambient occlusion.\n"
            "lit: the scene's colours under lights that follow the camera.\n"
            "clay: the same lights on plain grey.\n"
            "albedo: the scene's colours, unlit.\n"
            "occlusion: white, with only the ambient occlusion."
        )
        shading_row = self._row(self._pb_shading, revert=('pb_shading',))
        form.addRow("Shading:", shading_row)
        self._pb_motion_blur = self._check('pb_motion_blur', "Motion blur")
        self._pb_motion_blur.setToolTip(
            "Viewport only. Blur what moves while the shot camera's shutter "
            "is open. Partially checked = each entity keeps its own "
            "configured value."
        )
        self._pb_dof = self._check('pb_dof', "Depth of field")
        self._pb_dof.setToolTip(
            "Viewport only. Defocus by the shot camera's lens: its f-stop "
            "and focus distance (an f-stop of 0 stays sharp). Partially "
            "checked = each entity keeps its own configured value."
        )
        self._pb_blur_samples = self._spin('pb_blur_samples', 2, 64)
        self._pb_blur_samples.setToolTip(
            "Viewport only. Images averaged per frame for the blurs: more is "
            "smoother and slower."
        )
        blur_row = self._row(
            self._pb_motion_blur, self._pb_dof, self._pb_blur_samples,
            revert=('pb_motion_blur', 'pb_dof', 'pb_blur_samples'),
        )
        form.addRow("Blur:", blur_row)
        self._pb_light = self._combo('pb_light', PLAYBLAST_LIGHTS)
        self._pb_light.setToolTip(
            "Karma only.\n"
            "dome: the scene's lights off, one white dome light — soft clay "
            "shading.\nheadlight: a light on the camera with ambient "
            "occlusion.\nscene: the scene's own lights."
        )
        self._pb_samples = self._spin('pb_samples', 1, 4096)
        self._pb_materials = self._choice('pb_materials', "Scene materials", "Clay")
        self._pb_materials.setToolTip(
            "Karma only. Scene materials: shade with the scene's materials. "
            "Clay: plain grey. ⟨per entity⟩: each shot keeps its own "
            "configured value."
        )
        light_row = self._row(self._pb_light, revert=('pb_light',))
        samples_row = self._row(self._pb_samples, revert=('pb_samples',))
        materials_row = self._row(self._pb_materials, revert=('pb_materials',))
        form.addRow("Light:", light_row)
        form.addRow("Samples:", samples_row)
        form.addRow("Materials:", materials_row)
        # Each renderer's own rows, label and its text included:
        # _scope_playblast_look shows only the chosen renderer's.
        self._pb_rows = {
            renderer: [
                (form.labelForField(row), row, text) for row, text in rows
            ]
            for renderer, rows in (
                ('viewport', ((shading_row, "Shading"), (blur_row, "Blur"))),
                ('karma', (
                    (light_row, "Light"), (samples_row, "Samples"),
                    (materials_row, "Materials"),
                )),
            )
        }
        self._pb_hint = QLabel(
            "The renderer differs between shots: each uses the settings "
            "named for its renderer. Set a renderer to see only its settings."
        )
        self._pb_hint.setWordWrap(True)
        self._pb_hint.setStyleSheet(f"color: {TEXT_SECONDARY};")
        form.insertRow(form.getWidgetPosition(shading_row)[0], self._pb_hint)
        self._pb_form = form
        self._pb_pool = self._line('pb_pool', 'general')
        form.addRow("Pool:", self._pb_pool)
        self._pb_priority = self._spin('pb_priority', 0, 100)
        form.addRow("Priority:", self._row(self._pb_priority, revert=('pb_priority',)))
        return box

    def _build_render_section(self) -> Section:
        box, form = self._section(grid.RENDER, "Render")
        self._rnd_dept = self._combo('render_department', self._renderable_departments)
        self._rnd_dept.setToolTip(
            "Renders every department up to and including this one, in "
            "pipeline order — departments after it are left out of the "
            "composed stage. Also names the render output."
        )
        self._rnd_dept.activated.connect(lambda *_a: self._on_cut_changed())
        form.addRow("Up to:", self._rnd_dept)

        # Read-only: the camera the published layers in the cut name.
        self._rnd_camera = QLabel("")
        self._rnd_camera.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # A long path must not widen the fixed-width panel; the tooltip
        # carries it in full.
        self._rnd_camera.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._rnd_camera.setStyleSheet(f"color: {TEXT_SECONDARY};")
        form.addRow("Camera:", self._rnd_camera)

        # Channels — a checkable menu of the channels the ticked entities
        # actually define. The menu spans the submission and the checks are
        # a batch choice, but each entity submits only the checked channels
        # it defines (resolve.entity_channels). The Warnings column names
        # what each entity skips, and one left with none blocks the submit.
        self._rnd_channels = _CheckableComboBox(
            empty_text="(none — check at least one)",
            hint="Channels to render — one render per checked channel.",
        )
        all_channels = QPushButton("All")
        all_channels.setStyleSheet("padding: 3px 8px;")
        all_channels.setToolTip(
            "Check every channel in the batch. Each entity still renders "
            "only the checked channels it defines."
        )
        all_channels.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._rnd_channels.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        all_channels.clicked.connect(
            lambda: self._rnd_channels.set_checked(self._rnd_channels.options())
        )
        self._rnd_channels.model().dataChanged.connect(lambda *_a: self._changed())
        form.addRow("Channels:", self._row(self._rnd_channels, all_channels))

        # Range — a batch field: Full range submits the full_render chain
        # (all frames + slapcomp/mp4); Preview submits partial_render (first,
        # middle and last frame, posted to Discord). Both queue the full range
        # behind the preview frames.
        self._rnd_preview = QCheckBox("Preview (first / middle / last)")
        self._rnd_preview.setToolTip(
            "Render the first, middle and last frame first and post them to "
            "Discord. With Full range also ticked, the full render waits for "
            "the preview frames."
        )
        self._rnd_full = QCheckBox("Full range")
        self._rnd_full.setChecked(True)
        form.addRow("Range:", self._row(self._rnd_preview, self._rnd_full))

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
        self._rnd_samples = self._spin('samples', 1, 4096, override=True)
        form.addRow("Samples:", self._row(self._rnd_samples, revert=('samples',)))
        self._rnd_denoise = self._check('denoise', "Denoise")
        self._rnd_mblur = self._check('mblur', "Motion blur", override=True)
        self._rnd_dof = self._check('dof', "DOF", override=True)
        form.addRow("", self._row(
            self._rnd_denoise, self._rnd_mblur, self._rnd_dof,
            revert=('denoise', 'mblur', 'dof'),
        ))
        self._rnd_standalone = QCheckBox("Standalone")
        self._rnd_copy_edit = QCheckBox("Copy to edit")
        form.addRow("", self._row(self._rnd_standalone, self._rnd_copy_edit))
        return box

    # ── Render camera ─────────────────────────────────────

    def _camera_keys(self) -> list[tuple[str, str, str]]:
        """``(uri, channel, department)`` for every row the render speaks for.

        The channel is the first checked one the entity defines; the camera
        is read from that channel's exports.
        """
        rows = [
            uri for uri in self._uris if (uri, grid.RENDER) in self._ticks
        ] or self._batch()
        checked = self._rnd_channels.checked_items()
        keys = []
        for uri in rows:
            department = self._cut(uri, 'render_department')
            if not department:
                continue
            channels = resolve.entity_channels(checked, self._props.get(uri, {}))
            keys.append((uri, channels[0] if channels else resolve.DEFAULT_CHANNEL, department))
        return keys

    def _refresh_cameras(self) -> None:
        """Refresh: forget every looked-up camera and read them again."""
        self._cameras = {}
        self._camera_pending = {}
        self._update_camera()

    def _update_camera(self) -> None:
        """Start lookups for unseen rows and show what is known."""
        if grid.RENDER not in grid.ticked_kinds(self._columns, self._ticks):
            return  # the Render section is hidden; nothing to show
        keys = self._camera_keys()
        waiting = set(self._camera_pending.values())
        missing = [k for k in keys if k not in self._cameras and k not in waiting]
        if missing:
            try:
                from tumblepipe.api import default_client
                with default_client().config.coherent():
                    for key in missing:
                        uri, channel, department = key
                        probe = render_camera.plan_probe(
                            self._uri_objects[uri], channel, department,
                        )
                        future = self._executor.submit(render_camera.read_camera, probe)
                        self._camera_pending[future] = key
            except Exception as error:
                # Shown in the Camera row, which is where the artist looks.
                log.exception("Could not resolve render camera folders")
                for key in missing:
                    self._cameras[key] = render_camera.RenderCamera(
                        key[0], None, error=str(error),
                    )
            if self._camera_pending:
                self._camera_poll.start()
        self._show_camera(keys)

    def _drain_cameras(self) -> None:
        finished = [f for f in self._camera_pending if f.done()]
        for future in finished:
            key = self._camera_pending.pop(future)
            try:
                self._cameras[key] = future.result()
            except Exception as error:
                # Shown in the Camera row, which is where the artist looks.
                self._cameras[key] = render_camera.RenderCamera(
                    key[0], None, error=str(error),
                )
        if not self._camera_pending:
            self._camera_poll.stop()
        if finished:
            self._show_camera(self._camera_keys())

    def _show_camera(self, keys: list[tuple[str, str, str]]) -> None:
        label = self._rnd_camera
        if not keys:
            label.setText("—")
            label.setToolTip("")
            return
        known = [self._cameras[k] for k in keys if k in self._cameras]
        if len(known) < len(keys):
            label.setText("Reading…")
            label.setToolTip("")
            return
        lines = []
        for key, found in zip(keys, known):
            name = self._uri_objects[key[0]].segments[-1]
            if found.camera:
                lines.append(f"{name}: {found.camera}  ({found.source}, {key[1]})")
            else:
                lines.append(f"{name}: not found  {found.error or ''}".rstrip())
        cameras = {found.camera for found in known}
        if len(cameras) == 1 and None not in cameras:
            label.setText(known[0].camera)
            label.setStyleSheet(f"color: {TEXT_PRIMARY};")
        elif cameras == {None}:
            label.setText("not found")
            label.setStyleSheet(f"color: {STALE_COLOUR};")
        else:
            label.setText(PER_ENTITY_TEXT)
            label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        label.setToolTip(
            "The RenderSettings camera the latest published layers in the "
            "render cut name (strongest department first, then the project "
            "default). The farm renders through the composed stage's "
            "camera.\n\n" + "\n".join(lines)
        )

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
                self._context_pins[key] = name

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
            resolve.CHANNEL_LIST_KEY: self._rnd_channels.checked_items(),
            'render_mode': self._render_mode() or 'full',
            'standalone': self._rnd_standalone.isChecked(),
            'copy_to_edit': self._rnd_copy_edit.isChecked(),
        })
        return values

    def _render_mode(self) -> str | None:
        """The render_mode the Range checkboxes ask for; None with neither ticked."""
        return _RENDER_MODES.get(
            (self._rnd_preview.isChecked(), self._rnd_full.isChecked())
        )

    def _pinned_keys(self) -> list[str]:
        return [key for key, entry in self._fields.items() if entry.pinned]

    def _resolved_rows(self) -> list[tuple[str, dict, list[str], list[str]]]:
        """``(uri, settings, warnings, errors)`` for every ticked row, in grid order.

        The single source of truth for the Warnings column and the submit,
        so what the grid warns about is by construction what gets
        submitted. ``errors`` are the blocking ones (``entity_errors``).
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
            requested = form[resolve.CHANNEL_LIST_KEY]
            warnings = resolve.entity_warnings(
                properties, settings, departments=assigned, requested=requested,
            )
            errors = resolve.entity_errors(properties, settings, requested=requested)
            missing = self._stale_upstream(uri, settings)
            if missing:
                warnings.insert(0, f"{', '.join(missing)} stale and not ticked")
            rows.append((uri, settings, warnings, errors))
        return rows

    def _stale_upstream(self, uri: str, settings: dict) -> list[str]:
        """Stale, unticked departments a ticked preview on ``uri`` composes."""
        return grid.unpublished_upstream(
            uri, self._columns, self._ticks, self._states,
            cuts={
                grid.PLAYBLAST: settings.get('pb_department'),
                grid.RENDER: settings.get('render_department'),
            },
        )

    def _recompute_warnings(self) -> None:
        # Blocking errors first, so an elided cell still shows them.
        self._warnings = {
            uri: e + w for uri, _s, w, e in self._resolved_rows() if e or w
        }

    # ── Submit ────────────────────────────────────────────

    def submit(self) -> None:
        """Review this tab's ticked cells over the window, then submit them.

        A single row with nothing to check goes straight out; anything more
        shows the review first (``farm_submit_ui.ReviewSheet``). Either way
        the progress follows on the same overlay, and the window stays.
        """
        rows = self._resolved_rows()
        if not rows:
            self._dialog.notice("Nothing ticked", "Tick at least one cell before submitting.")
            return
        renders = any(s.get('render') for _u, s, _w, _e in rows)
        if renders and self._render_mode() is None:
            self._dialog.notice(
                "No render range",
                "Tick Preview, Full range or both in the Render settings "
                "before submitting.",
            )
            return
        if renders and not self._rnd_channels.checked_items():
            self._dialog.notice(
                "No channel to render",
                "Check at least one channel in the Render settings before "
                "submitting.",
            )
            return
        # Every job family ends in a Deadline submission, unless it runs on
        # this machine; say so before the artist reviews anything, not as
        # a per-row failure afterwards.
        from tumblepipe.farm.backend import setup_problem
        problem = setup_problem(self._dialog.backend())
        if problem is not None:
            self._dialog.notice("Can't submit yet", problem)
            return

        steps = {uri: grid.row_steps(uri, self._columns, self._ticks) for uri, *_rest in rows}
        stale = {uri: self._stale_upstream(uri, s) for uri, s, _w, _e in rows}
        review = [
            ReviewRow(
                name=self._uri_objects[uri].segments[-1],
                steps=steps[uri],
                errors=list(errors),
                stale=stale[uri],
                # _resolved_rows puts the stale-upstream warning first, when it has one.
                warnings=list(warnings[1:] if stale[uri] else warnings),
            )
            for uri, _s, warnings, errors in rows
        ]
        configs = [
            {
                'entity': {
                    'uri': uri,
                    'name': self._uri_objects[uri].segments[-1],
                    'context': self._context,
                },
                'settings': settings,
            }
            for uri, settings, _w, _e in rows
        ]
        if submit_review.skips_review(review):
            self._dialog.start(configs, steps, self)
            return
        sheet = ReviewSheet(review, self._noun, self._dialog.where_text())
        sheet.back_requested.connect(self._dialog.close_overlay)
        sheet.tick_stale_requested.connect(lambda: self._tick_stale(stale))
        sheet.submit_requested.connect(lambda: self._dialog.start(configs, steps, self))
        self._dialog.show_overlay(sheet)

    def _tick_stale(self, stale: dict[str, list[str]]) -> None:
        """Tick what the previews would otherwise show stale, and go back to
        the grid so the artist sees what was added."""
        self._ticks |= grid.stale_ticks(stale)
        self._dialog.close_overlay()
        self._changed()

    def after_submission(self, sent: set[str]) -> None:
        """Back from a submission: untick the rows that went out, read the
        status again (their cells are pending now, not stale)."""
        self._ticks = grid.untick_sent(self._ticks, sent)
        self._start_scan()
        self._changed()


# ── Edits tab ─────────────────────────────────────────────

class _EditsPane(QWidget):
    """The Edits tab: one row per edit, and its one step, Render Edit.

    A row offers the edit's finished published versions, the latest first
    selected (``grid.default_edit_version``); an edit never published has
    nothing to render and cannot be ticked. Ticked rows render on the farm
    into ``render:/edits/<name>/<name>_edit_vNNNN.mp4``
    (``farm/pipelines/edit_render.py``), with the Pool and Priority beside
    the list.

    Args:
        dialog: The owning dialog; the footer and accept/close are its.
        entity_uris: Edits to start with ticked.
    """

    _NAME, _VERSION, _RENDER = range(3)

    def __init__(self, dialog: "SubmitJobsDialog", entity_uris: Sequence) -> None:
        super().__init__(dialog)
        self._dialog = dialog
        from tumblepipe.pipe import edits as pipe_edits
        from tumblepipe.config import farm as farm_config

        opened = {str(uri) for uri in entity_uris}
        self._names: dict[str, str] = {}
        self._versions: dict[str, QComboBox] = {}
        self._items: dict[str, QTreeWidgetItem] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Edit", "Version", "Render Edit"])
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QAbstractItemView.NoSelection)
        self._tree.header().setSectionResizeMode(self._NAME, QHeaderView.Stretch)
        self._tree.header().setStretchLastSection(False)
        root.addWidget(self._tree, 1)

        for name in pipe_edits.list_edits():
            uri = str(pipe_edits.edit_uri(name))
            published = pipe_edits.published_versions(name)
            item = QTreeWidgetItem([name, "", ""])
            self._tree.addTopLevelItem(item)
            self._names[uri] = name
            self._items[uri] = item
            latest = grid.default_edit_version(published)
            if latest is None:
                item.setText(self._VERSION, "not published")
                item.setForeground(self._VERSION, QColor(DIM_COLOUR))
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                item.setToolTip(
                    self._RENDER,
                    "Nothing to render: publish this edit from the editor first.",
                )
                continue
            combo = QComboBox()
            for version in reversed(published):
                combo.addItem(version)
            combo.setCurrentText(latest)
            combo.setToolTip("The published version to render.")
            self._tree.setItemWidget(item, self._VERSION, combo)
            self._versions[uri] = combo
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                self._RENDER, Qt.Checked if uri in opened else Qt.Unchecked,
            )
            item.setToolTip(
                self._RENDER,
                f"Render this version into render:/edits/{name}/"
                f"{name}_edit_vNNNN.mp4 on the farm.",
            )
        if not self._items:
            empty = QTreeWidgetItem(["This project has no edits.", "", ""])
            empty.setFlags(Qt.NoItemFlags)
            self._tree.addTopLevelItem(empty)
        self._tree.resizeColumnToContents(self._VERSION)
        self._tree.resizeColumnToContents(self._RENDER)
        self._tree.itemChanged.connect(lambda *_a: self._dialog._pane_changed(self))

        box = Section("Render Edit", KIND_COLOURS[EDIT_KIND])
        form = box.form()
        self._pool = QLineEdit(farm_config.get_default_pool() or 'general')
        form.addRow("Pool:", self._pool)
        self._priority = QSpinBox()
        self._priority.setRange(0, 100)
        self._priority.setValue(farm_config.get_default_priority())
        self._priority.setButtonSymbols(QAbstractSpinBox.NoButtons)
        form.addRow("Priority:", self._priority)
        note = QLabel(
            "Renders the published version with the editor, sound included, "
            "into one movie: one task, on the CPU."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_SECONDARY};")
        form.addRow(note)
        side = QVBoxLayout()
        side.addWidget(box)
        side.addStretch(1)
        holder = QWidget()
        holder.setLayout(side)
        holder.setFixedWidth(320)
        root.addWidget(holder)

    @property
    def context(self) -> str:
        return grid.EDITS

    def _ticked(self) -> dict[str, str]:
        """``{edit URI: version}`` of the ticked rows."""
        return {
            uri: self._versions[uri].currentText()
            for uri, item in self._items.items()
            if uri in self._versions and item.checkState(self._RENDER) == Qt.Checked
        }

    def summary_text(self) -> str:
        return grid.edit_summary(len(self._ticked()))

    def ticked_row_count(self) -> int:
        return len(self._ticked())

    def shutdown(self) -> None:
        """Nothing runs in the background here."""

    def submit(self) -> None:
        """Submit a Render Edit per ticked edit; the progress shows over the window."""
        ticked = self._ticked()
        if not ticked:
            self._dialog.notice("Nothing ticked", "Tick at least one edit before submitting.")
            return
        pool = self._pool.text().strip()
        if not pool:
            self._dialog.notice("No pool", "Name the pool to render in.")
            return
        from tumblepipe.farm.backend import setup_problem
        problem = setup_problem(self._dialog.backend())
        if problem is not None:
            self._dialog.notice("Can't submit yet", problem)
            return
        configs = grid.edit_rows(ticked, self._names, pool, self._priority.value())
        steps = {uri: [(EDIT_KIND, "Render Edit")] for uri in ticked}
        self._dialog.start(configs, steps, self)

    def after_submission(self, sent: set[str]) -> None:
        """Back from a submission: untick the edits that went out."""
        for uri in sent:
            item = self._items.get(uri)
            if item is not None and uri in self._versions:
                item.setCheckState(self._RENDER, Qt.Unchecked)


# ── Dialog ────────────────────────────────────────────────

class SubmitJobsDialog(QDialog):
    """The Farm Submit grid, one tab per context (Assets, Shots, Edits).

    The review before submitting, the submission's progress and any reason
    Submit cannot go ahead show on an overlay over this window
    (``farm_submit_ui.Overlay``), never in windows of their own, and the
    window stays open after a submission: **Back to grid** submits more.

    Args:
        entity_uris: Entities to start with ticked (``tick_kinds`` on each).
            May be empty — the Farm Submit quick action opens with nothing ticked.
            A Multi (``groups:`` URI) stands for its members.
        entity_names: Display names parallel to ``entity_uris``.
        context: ``'shots'``, ``'assets'`` or ``'edits'`` — the tab the
            dialog opens on, and the only one ``entity_uris``, ``department``
            and ``tick_kinds`` seed. The other tabs list every entity of
            their context with nothing ticked.
        parent: Parent widget. Pass ``hou.qt.mainWindow()`` from Houdini.
        department: Department the dialog was opened *from* — the loaded
            workfile's. Pins the Render (and Playblast) cut, so submitting
            from a lighting workfile previews up to lighting. ``None`` falls
            back to each entity's ``submission.render.department``.
        tick_kinds: Step kinds to tick for ``entity_uris`` —
            ``grid.RENDER`` by default, what the Render quick action and
            **Submit Jobs…** have always meant; ``grid.PUBLISH`` from the
            Farm Submit quick action, narrowed to ``department``'s column.
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
        if context not in grid.CONTEXTS:
            raise ValueError(f"context must be one of {grid.CONTEXTS}, got {context!r}")
        # A Multi is never submitted itself: its member entities are.
        if any(resolve.is_group_target(uri) for uri in entity_uris):
            entity_uris = resolve.expand_targets(entity_uris, _group_members)
            if not entity_uris:
                raise ValueError(
                    "This Multi has no member entities to submit. Add "
                    "members to it in the browser first."
                )
        self._opened = (context, list(entity_uris), department, tuple(tick_kinds))
        # One pane per context, built the first time its tab is shown and
        # kept until the dialog closes, so switching back finds its ticks.
        self._panes: dict[str, _ContextPane] = {}

        # What the last opening left (``farm.submit.remembered``): per tab,
        # and for the window as a whole (``ui``, which the panes read).
        self._stored = _load_remembered()
        self.ui = remembered.ui_settings(self._stored.get('ui'))
        self._project = _project_key()

        # Parented to Houdini's main window, a closed dialog would otherwise
        # live on hidden, rows and all, until Houdini exits: one per open.
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Farm Submit")
        self.setMinimumSize(1100, 640)
        # Opens wide enough for every column and the settings beside them,
        # or at the size it was left; the grid scrolls sideways below that.
        size = self.ui['size'] or [1600, 860]
        self.resize(*size)
        self.setStyleSheet(_DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(12)

        self._tabs = FolderTabBar()
        for name in grid.CONTEXTS:
            index = self._tabs.addTab(grid.tab_label(name, 0))
            self._tabs.setTabData(index, name)
            self._tabs.setTabToolTip(
                index,
                f"Submit {name}. Each tab keeps its own ticks and settings; "
                f"Submit sends only the tab in front.",
            )
        root.addWidget(self._tabs)
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._summary = QLabel()
        self._summary.setStyleSheet(f"color: {TEXT_PRIMARY};")
        footer.addWidget(self._summary, 1)
        self._cleanup = QCheckBox("Clean up old jobs")
        self._cleanup.setChecked(self.ui['cleanup'])
        self._cleanup.setToolTip(
            "Before submitting an entity, remove its finished and failed jobs "
            "(everyone's, every step) from the farm, so the Farm Manager shows "
            "only what is current. Queued, running and suspended jobs are "
            "never touched."
        )
        self._cleanup.toggled.connect(lambda on: self.ui.__setitem__('cleanup', on))
        footer.addWidget(self._cleanup)
        footer.addSpacing(12)
        run_on = QLabel("Run on")
        run_on.setStyleSheet(f"color: {TEXT_SECONDARY};")
        footer.addWidget(run_on)
        self._backend = QComboBox()
        self._backend.addItem("Render farm", "farm")
        self._backend.addItem("This machine", "local")
        self._backend.setToolTip(
            "Render farm: the jobs go to Deadline. "
            "This machine: the same jobs run here, in the background, one "
            "task at a time — as a render node would run them. Follow them "
            "in the Farm Manager."
        )
        self._backend.setCurrentIndex(max(0, self._backend.findData(_saved_backend())))
        footer.addWidget(self._backend)
        footer.addSpacing(12)
        close_btn = QPushButton("Close")
        close_btn.setAutoDefault(False)
        close_btn.clicked.connect(self.reject)
        self._submit_btn = QPushButton("Submit…")
        self._submit_btn.setDefault(True)
        self._submit_btn.setToolTip("Review what will be sent, then send it")
        self._submit_btn.clicked.connect(self._on_submit)
        footer.addWidget(close_btn)
        footer.addWidget(self._submit_btn)
        root.addLayout(footer)

        self._overlay = Overlay(self)
        self._overlay.escape.connect(self._on_overlay_escape)

        # The opened context's pane is built here, so a failure to build it
        # still reaches the caller as an exception, as it always has.
        self._show_context(context)
        self._tabs.setCurrentIndex(grid.CONTEXTS.index(context))
        self._tabs.currentChanged.connect(self._on_tab_changed)

    # ── Tabs ──────────────────────────────────────────────

    def current_context(self) -> str:
        return self._tabs.tabData(self._tabs.currentIndex())

    def _pane(self, context: str) -> _ContextPane:
        pane = self._panes.get(context)
        if pane is None:
            opened_context, uris, department, kinds = self._opened
            uris, department, kinds = grid.tab_seed(
                context, opened_context, uris, department, kinds,
            )
            if context == grid.EDITS:
                pane = _EditsPane(self, uris)
            else:
                pane = _ContextPane(
                    self, uris, context, department=department, tick_kinds=kinds,
                )
            self._panes[context] = pane
            self._stack.addWidget(pane)
        return pane

    def _show_context(self, context: str) -> None:
        pane = self._pane(context)
        self._stack.setCurrentWidget(pane)
        self._pane_changed(pane)

    def _on_tab_changed(self, index: int) -> None:
        context = self._tabs.tabData(index)
        try:
            self._show_context(context)
        except Exception as error:
            # Back to the tab still on screen, so the bar and grid agree.
            shown = self._stack.currentWidget()
            if isinstance(shown, (_ContextPane, _EditsPane)):
                self._tabs.blockSignals(True)
                self._tabs.setCurrentIndex(grid.CONTEXTS.index(shown.context))
                self._tabs.blockSignals(False)
            report_failure(f"Listing the {context}", error)

    def _pane_changed(self, pane: _ContextPane) -> None:
        """A pane's ticks changed: relabel its tab, and the footer if it is in front."""
        index = grid.CONTEXTS.index(pane.context)
        self._tabs.setTabText(
            index, grid.tab_label(pane.context, pane.ticked_row_count()),
        )
        if self._stack.currentWidget() is pane:
            self._summary.setText(pane.summary_text())

    # ── Remembered ────────────────────────────────────────

    def remembered_tab(self, context: str) -> dict:
        """The record the last opening left for ``context`` in this project."""
        tabs = self._stored.get('tabs')
        record = tabs.get(remembered.scope(self._project, context)) if isinstance(tabs, dict) else None
        return record if isinstance(record, dict) else {}

    def section_toggled(self, kind: str, expanded: bool) -> None:
        collapsed = [k for k in self.ui['collapsed_sections'] if k != kind]
        if not expanded:
            collapsed.append(kind)
        self.ui['collapsed_sections'] = collapsed

    def _save_remembered(self) -> None:
        """Keep this opening's choices for the next; a prefs file we cannot
        write only loses them."""
        tabs = dict(self._stored.get('tabs') or {})
        for context, pane in self._panes.items():
            if isinstance(pane, _ContextPane):
                tabs[remembered.scope(self._project, context)] = pane.remembered_record()
        ui = dict(self.ui)
        ui['size'] = [self.width(), self.height()]
        try:
            from .prefs import update_prefs
            update_prefs(farm_submit={'tabs': tabs, 'ui': ui})
        except Exception:
            log.warning("Could not remember the Farm Submit settings", exc_info=True)

    # ── Overlay ───────────────────────────────────────────

    def show_overlay(self, widget: QWidget, max_width: int = 860) -> None:
        self._overlay.show_content(widget, max_width=max_width)

    def close_overlay(self) -> None:
        self._overlay.close_content()

    def notice(self, title: str, text: str) -> None:
        """Why Submit cannot go ahead, over the window."""
        card = NoticeCard(title, text)
        card.dismissed.connect(self.close_overlay)
        self.show_overlay(card, max_width=520)
        card.ok_button.setFocus()

    def _on_overlay_escape(self) -> None:
        content = self._overlay.content()
        if isinstance(content, SubmissionView):
            if content.is_finished:
                content.back_requested.emit()
            return  # still sending: Cancel or Close window say what they do
        self.close_overlay()

    def keyPressEvent(self, event) -> None:
        if not self._overlay.isHidden():
            # The overlay answers its own keys; nothing reaches the grid
            # or the window's default button under it.
            if event.key() == Qt.Key_Escape:
                self._on_overlay_escape()
            return
        super().keyPressEvent(event)

    # ── Submit ────────────────────────────────────────────

    def backend(self) -> str:
        """Where Submit sends the work: ``farm`` or ``local`` (``farm.backend``)."""
        return self._backend.currentData()

    def where_text(self) -> str:
        """Where the review says the steps go."""
        where = "on the render farm" if self.backend() == "farm" else "on this machine"
        if self._cleanup.isChecked():
            where += ", after cleaning up their old finished and failed jobs"
        return where

    def remember_backend(self) -> None:
        """Open on the same choice next time; a prefs file we cannot write only loses that."""
        try:
            from .prefs import update_prefs
            update_prefs(farm_backend=self.backend())
        except Exception:
            log.warning("Could not remember the Farm Submit 'Run on' choice", exc_info=True)

    def _on_submit(self) -> None:
        # isHidden, not isVisible: the overlay counts while it is up even
        # when the window itself is not shown (a test, a minimised window).
        if not self._overlay.isHidden():
            return
        pane = self._stack.currentWidget()
        if isinstance(pane, (_ContextPane, _EditsPane)):
            pane.submit()

    def start(
        self,
        configs: list[dict],
        steps: dict[str, list[tuple[str, str]]],
        pane: _ContextPane | _EditsPane,
    ) -> None:
        """Send ``configs`` and follow them on the overlay.

        ``steps`` (``{uri: [(kind, label)]}``) labels each row with what it
        submits. Retry submits the failed rows the same way.
        """
        try:
            plan_path, process = start_submission(
                configs, backend=self.backend(), cleanup=self._cleanup.isChecked(),
            )
        except Exception as error:
            self.close_overlay()
            report_failure("Starting the farm submission", error)
            return
        self.remember_backend()
        view = SubmissionView(plan_path, process, configs, steps)
        view.back_requested.connect(lambda: self._back_to_grid(pane, view))
        view.close_requested.connect(self.accept)
        view.retry_requested.connect(lambda retry: self.start(retry, steps, pane))
        self.show_overlay(view, max_width=900)

    def _back_to_grid(self, pane, view: SubmissionView) -> None:
        sent = view.sent_uris()
        self.close_overlay()
        pane.after_submission(sent)

    def done(self, result: int) -> None:
        content = self._overlay.content()
        if isinstance(content, SubmissionView):
            content.stop()  # the submission itself runs on in its process
        self._save_remembered()
        for pane in self._panes.values():
            pane.shutdown()
        super().done(result)


def _project_key() -> str:
    """The project the remembered settings belong to: its root path."""
    try:
        from tumblepipe.api import default_client
        return str(default_client().PROJECT_PATH)
    except Exception:
        log.debug("No project path for the Farm Submit settings", exc_info=True)
        return ""


def _load_remembered() -> dict:
    try:
        from .prefs import load_prefs
        stored = load_prefs().farm_submit
    except Exception:
        log.warning("Could not read the Farm Submit settings", exc_info=True)
        return {}
    return stored if isinstance(stored, dict) else {}


def _saved_backend() -> str:
    """The 'Run on' choice of the last submission; the farm when unknown."""
    try:
        from .prefs import load_prefs
        return load_prefs().farm_backend
    except Exception:
        log.warning("Could not read the Farm Submit 'Run on' choice", exc_info=True)
        return "farm"


def _houdini_version() -> str | None:
    """The Houdini the farm jobs should run: this session's, else the
    environment's (the Farm Submit app outside Houdini), else none — the
    runner then picks the farm's default."""
    import sys
    hou = sys.modules.get('hou')
    if hou is not None:
        return hou.applicationVersionString()
    from tumblepipe.apps.houdini import HoudiniVersionError, resolve_houdini_version
    try:
        return resolve_houdini_version()
    except HoudiniVersionError:
        return None


def start_submission(
    configs: list[dict], backend: str = "farm", cleanup: bool = False,
    ) -> tuple[Path, Any]:
    """Write ``configs`` as a plan and launch its runner; return
    ``(plan_path, process)`` for a ``farm_submit_ui.SubmissionView`` to follow.

    ``backend`` is where the batches go: the Deadline farm or this machine
    (``farm.backend``). ``cleanup`` has the runner remove each entity's
    finished and failed jobs before submitting it.
    """
    import datetime as dt
    import os
    import uuid

    from tumblepipe.api import api
    from tumblepipe.util.uri import Uri
    from tumblepipe.farm.submit import plan as submit_plan

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    directory = Path(api.storage.resolve(
        Uri.parse('temp:/farm_submissions')
    )) / f'{stamp}_{uuid.uuid4().hex[:6]}'
    plan = submit_plan.make_plan(
        configs, env=dict(os.environ), houdini_version=_houdini_version(),
        backend=backend, cleanup=cleanup,
    )
    plan_path = submit_plan.write_plan(directory, plan)
    process = submit_plan.launch(plan_path)
    return plan_path, process
