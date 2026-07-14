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
the same entities. The form is then a shared override applied to every
checked entity; per-entity overrides remain out of scope (the retired
Project Browser's ``JobSubmissionDialog`` had a per-entity grid for that).

Submission is synchronous and per-entity — each call to
``tumblepipe.farm.jobs.houdini.batch_submit.submit_entity_batch`` runs on the
caller's thread, and the dialog reports a single success/failure summary at the
end. For a richer per-task progress UI, use the ProcessDialog flow in
``tumblepipe.pipe.houdini.ui.process_dialog``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BORDER, FONT_BODY, FONT_FAMILY,
    TEXT_PRIMARY, TEXT_SECONDARY,
)

log = logging.getLogger(__name__)

# Item-data role carrying a leaf's entity URI string. Branch items have no
# value here, which is what ``_is_leaf`` keys off.
_URI_ROLE = Qt.UserRole


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
"""


# ── Helpers ───────────────────────────────────────────────

def _safe_get_properties(entity_uri):
    """Return ``api.config.get_properties(entity_uri)`` or ``{}`` on failure.

    Importing ``tumblepipe.api`` here is a passive lookup: the catalog has
    already activated the project before showing the dialog.
    """
    try:
        from tumblepipe.api import default_client
        client = default_client()
        props = client.config.get_properties(entity_uri)
        return props or {}
    except Exception:
        log.exception("Failed to read properties for %s", entity_uri)
        return {}


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


def _nested(d: dict, dotted: str, default=None):
    cur = d
    for part in dotted.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


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
    """

    def __init__(
        self,
        entity_uris: Sequence,
        entity_names: Sequence[str],
        context: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not entity_uris:
            raise ValueError("entity_uris must be non-empty")
        if context not in ("shots", "assets"):
            raise ValueError(f"context must be 'shots' or 'assets', got {context!r}")
        self._entity_uris = list(entity_uris)
        self._entity_names = list(entity_names)
        self._context = context

        # Every entity leaf in the tree, keyed by URI string. One URI can own
        # several items (once under the context root, once per group that
        # contains it) — check state is mirrored across them.
        self._leaves: dict[str, _EntityLeaf] = {}
        # Guards the itemChanged handler against the writes it makes itself.
        self._syncing = False
        # URI the form was last seeded from; reseeding is skipped when the
        # primary entity hasn't actually changed.
        self._seeded_uri: str | None = None
        # Defaults from the primary (first checked) entity's properties.
        self._defaults = _safe_get_properties(self._entity_uris[0])

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
        root.addWidget(self._publish_box)

        self._render_box = self._build_render_section()
        root.addWidget(self._render_box)

        self._apply_entity_defaults()

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
        """Re-target the submission at whatever is checked now."""
        checked = self._checked_entities()
        self._entity_uris = [uri for uri, _ in checked]
        self._entity_names = [name for _, name in checked]
        self._header.setText(self._build_header_text())
        self._apply_entity_defaults()

    def _build_publish_section(self) -> QGroupBox:
        box = QGroupBox("Publish")
        box.setCheckable(True)
        box.setChecked(False)
        form = QFormLayout(box)
        form.setContentsMargins(10, 14, 10, 10)
        form.setSpacing(6)

        self._pub_dept = QComboBox()
        form.addRow("Department:", self._pub_dept)

        self._pub_pool = QLineEdit()
        self._pub_pool.setPlaceholderText("general")
        form.addRow("Pool:", self._pub_pool)

        self._pub_priority = QSpinBox()
        self._pub_priority.setRange(0, 100)
        form.addRow("Priority:", self._pub_priority)

        return box

    def _build_render_section(self) -> QGroupBox:
        box = QGroupBox("Render")
        box.setCheckable(True)
        # Render is the common case for this dialog (publish has its own
        # quick action), so it starts enabled.
        box.setChecked(True)
        form = QFormLayout(box)
        form.setContentsMargins(10, 14, 10, 10)
        form.setSpacing(6)

        # Department
        self._rnd_dept = QComboBox()
        form.addRow("Department:", self._rnd_dept)

        # Variants — comma-separated list
        self._rnd_variants = QLineEdit()
        self._rnd_variants.setPlaceholderText("default")
        form.addRow("Variants (csv):", self._rnd_variants)

        # Range mode — Full range submits the full_render chain (all
        # frames + slapcomp/mp4); First / Middle / Last submits the
        # partial_render chain (3 check frames + notify) for a quick look
        # before committing the farm to the whole range.
        self._rnd_mode = QComboBox()
        self._rnd_mode.addItems(["Full range", "First / Middle / Last"])
        form.addRow("Range:", self._rnd_mode)

        # Frame range row: first / last
        self._rnd_first = QSpinBox()
        self._rnd_first.setRange(-1_000_000, 1_000_000)
        self._rnd_last = QSpinBox()
        self._rnd_last.setRange(-1_000_000, 1_000_000)
        frame_row = QHBoxLayout()
        frame_row.setSpacing(6)
        frame_row.addWidget(self._rnd_first)
        frame_row.addWidget(QLabel("→"))
        frame_row.addWidget(self._rnd_last)
        frame_wrap = QWidget()
        frame_wrap.setLayout(frame_row)
        form.addRow("Frames:", frame_wrap)

        # Pre/Post roll
        self._rnd_pre = QSpinBox()
        self._rnd_pre.setRange(0, 1000)
        self._rnd_post = QSpinBox()
        self._rnd_post.setRange(0, 1000)
        roll_row = QHBoxLayout()
        roll_row.setSpacing(6)
        roll_row.addWidget(self._rnd_pre)
        roll_row.addWidget(QLabel("/"))
        roll_row.addWidget(self._rnd_post)
        roll_wrap = QWidget()
        roll_wrap.setLayout(roll_row)
        form.addRow("Pre / Post roll:", roll_wrap)

        # Pool / Priority / Tile / Batch — pack on two rows
        self._rnd_pool = QLineEdit()
        self._rnd_pool.setPlaceholderText("general")
        form.addRow("Pool:", self._rnd_pool)

        self._rnd_priority = QSpinBox()
        self._rnd_priority.setRange(0, 100)
        self._rnd_tile = QSpinBox()
        self._rnd_tile.setRange(1, 64)
        self._rnd_batch = QSpinBox()
        self._rnd_batch.setRange(1, 1000)
        ppb_row = QHBoxLayout()
        ppb_row.setSpacing(6)
        ppb_row.addWidget(QLabel("Pri:"))
        ppb_row.addWidget(self._rnd_priority)
        ppb_row.addWidget(QLabel("Tiles:"))
        ppb_row.addWidget(self._rnd_tile)
        ppb_row.addWidget(QLabel("Batch:"))
        ppb_row.addWidget(self._rnd_batch)
        ppb_wrap = QWidget()
        ppb_wrap.setLayout(ppb_row)
        form.addRow("", ppb_wrap)

        # Samples
        self._rnd_samples = QSpinBox()
        self._rnd_samples.setRange(1, 4096)
        form.addRow("Samples:", self._rnd_samples)

        # Boolean checkboxes
        self._rnd_denoise = QCheckBox("Denoise")
        self._rnd_mblur = QCheckBox("Motion blur")
        self._rnd_dof = QCheckBox("DOF")
        self._rnd_standalone = QCheckBox("Standalone")
        self._rnd_standalone.setChecked(False)
        self._rnd_copy_edit = QCheckBox("Copy to edit")
        self._rnd_copy_edit.setChecked(False)

        flags_row1 = QHBoxLayout()
        flags_row1.setSpacing(12)
        flags_row1.addWidget(self._rnd_denoise)
        flags_row1.addWidget(self._rnd_mblur)
        flags_row1.addWidget(self._rnd_dof)
        flags_row1.addStretch(1)
        flags_wrap1 = QWidget()
        flags_wrap1.setLayout(flags_row1)
        form.addRow("", flags_wrap1)

        flags_row2 = QHBoxLayout()
        flags_row2.setSpacing(12)
        flags_row2.addWidget(self._rnd_standalone)
        flags_row2.addWidget(self._rnd_copy_edit)
        flags_row2.addStretch(1)
        flags_wrap2 = QWidget()
        flags_wrap2.setLayout(flags_row2)
        form.addRow("", flags_wrap2)

        return box

    def _apply_entity_defaults(self) -> None:
        """Seed the form from the *primary* entity — the first checked one.

        Entity-derived fields (departments, frames, farm settings, render
        toggles) are re-seeded, deliberately overwriting user edits, since
        defaults follow the entity. Pure submission choices (range mode,
        Standalone, Copy to edit, section checkboxes) are left alone.

        Reseeding is keyed on the primary URI, so checking *more* entities
        into the batch doesn't clobber a form you've already tuned — only a
        change of primary does. With several entities checked, the form is a
        shared override applied to all of them; the per-entity override grid
        the retired Project Browser had is not (yet) back.
        """
        if not self._entity_uris:
            return  # nothing checked — leave the form as it stands
        primary = str(self._entity_uris[0])
        if primary == self._seeded_uri:
            return
        self._seeded_uri = primary
        self._defaults = _safe_get_properties(self._entity_uris[0])
        d = self._defaults

        pub_depts = _list_dept_names(
            self._context, only_publishable=True, only_renderable=False,
        )
        self._pub_dept.clear()
        self._pub_dept.addItems(pub_depts)
        # Seed: prefer entity property `submission.publish.department` if present.
        seed = _nested(d, 'submission.publish.department')
        if seed and seed in pub_depts:
            self._pub_dept.setCurrentText(seed)

        self._pub_pool.setText(str(_nested(d, 'farm.default_pool', '') or ''))
        self._pub_priority.setValue(int(_nested(d, 'farm.priority', 50)))

        rnd_depts = _list_dept_names(
            self._context, only_publishable=False, only_renderable=True,
        )
        self._rnd_dept.clear()
        self._rnd_dept.addItems(rnd_depts)
        seed = _nested(d, 'submission.render.department')
        if seed and seed in rnd_depts:
            self._rnd_dept.setCurrentText(seed)

        variants_default = _nested(d, 'variants') or ['default']
        if isinstance(variants_default, list):
            variants_text = ", ".join(str(v) for v in variants_default)
        else:
            variants_text = str(variants_default)
        self._rnd_variants.setText(variants_text)

        self._rnd_first.setValue(int(_nested(d, 'frame_start', 1001)))
        self._rnd_last.setValue(int(_nested(d, 'frame_end', 1100)))
        self._rnd_pre.setValue(int(_nested(d, 'roll_start', 0)))
        self._rnd_post.setValue(int(_nested(d, 'roll_end', 0)))
        self._rnd_pool.setText(str(_nested(d, 'farm.default_pool', '') or ''))
        self._rnd_priority.setValue(int(_nested(d, 'farm.priority', 50)))
        self._rnd_tile.setValue(int(_nested(d, 'farm.tile_count', 4)))
        self._rnd_batch.setValue(int(_nested(d, 'farm.batch_size', 10)))
        self._rnd_samples.setValue(
            int(_nested(d, 'render.pathtracedsamples', 64)),
        )
        self._rnd_denoise.setChecked(
            bool(_nested(d, 'render.enabledenoising', True)),
        )
        self._rnd_mblur.setChecked(
            bool(_nested(d, 'render.enablemblur', True)),
        )
        self._rnd_dof.setChecked(bool(_nested(d, 'render.enabledof', True)))

    # ── Submit ────────────────────────────────────────────

    def _on_submit(self) -> None:
        if not self._entity_uris:
            QMessageBox.warning(
                self, "Submit Jobs",
                f"Check at least one {self._context[:-1]} in the tree before "
                "submitting.",
            )
            return

        publish = self._publish_box.isChecked()
        render = self._render_box.isChecked()
        if not publish and not render:
            QMessageBox.warning(
                self, "Submit Jobs",
                "Enable at least one of Publish or Render before submitting.",
            )
            return

        # A wide fan-out is expensive and easy to trigger by mis-clicking a
        # branch, so make the user own it.
        if len(self._entity_uris) > 1:
            confirm = QMessageBox.question(
                self, "Submit Jobs",
                f"Submit {'publish + render' if publish and render else 'render' if render else 'publish'} "
                f"jobs for {len(self._entity_uris)} {self._context}?\n\n"
                "The same settings are applied to every one of them.",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if confirm != QMessageBox.Ok:
                return

        # Build the shared settings dict once — it's the same for every entity
        # in the slim flow. Per-entity overrides are out of scope.
        settings: dict = {'publish': publish, 'render': render}
        if publish:
            settings['pub_department'] = self._pub_dept.currentText()
            settings['pub_pool'] = self._pub_pool.text().strip() or 'general'
            settings['pub_priority'] = self._pub_priority.value()
        if render:
            variants = [
                v.strip() for v in self._rnd_variants.text().split(',')
                if v.strip()
            ] or ['default']
            settings.update({
                'render_department': self._rnd_dept.currentText(),
                'render_mode': (
                    'first_middle_last'
                    if self._rnd_mode.currentIndex() == 1 else 'full'
                ),
                'render_pool': self._rnd_pool.text().strip() or 'general',
                'render_priority': self._rnd_priority.value(),
                'variants': variants,
                'first_frame': self._rnd_first.value(),
                'last_frame': self._rnd_last.value(),
                'pre_roll': self._rnd_pre.value(),
                'post_roll': self._rnd_post.value(),
                'tile_count': self._rnd_tile.value(),
                'batch_size': self._rnd_batch.value(),
                'denoise': self._rnd_denoise.isChecked(),
                'mblur': self._rnd_mblur.isChecked(),
                'dof': self._rnd_dof.isChecked(),
                'standalone': self._rnd_standalone.isChecked(),
                'copy_to_edit': self._rnd_copy_edit.isChecked(),
                'samples': self._rnd_samples.value(),
            })

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
        for uri, name in zip(self._entity_uris, self._entity_names):
            config = {
                'entity': {
                    'uri': str(uri),
                    'name': name,
                    'context': self._context,
                },
                'settings': settings,
            }
            try:
                job_ids = submit_entity_batch(config)
                successes.append((name, list(job_ids or [])))
            except Exception as exc:
                log.exception("submit_entity_batch failed for %s", uri)
                failures.append((name, str(exc)))

        # Summary
        lines: list[str] = []
        if successes:
            lines.append(
                f"Submitted {len(successes)}/{len(self._entity_uris)} entities."
            )
            for name, ids in successes:
                lines.append(f"  • {name}: {', '.join(ids) if ids else '(no ids)'}")
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
