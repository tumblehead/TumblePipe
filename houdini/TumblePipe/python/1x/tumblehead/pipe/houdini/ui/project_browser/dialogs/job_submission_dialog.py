"""Dialog for batch job submission to the render farm."""

import uuid

from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QStandardItemModel, QStandardItem

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.groups import list_groups, get_group
from tumblehead.pipe.paths import Context

from tumblehead.pipe.houdini.ui.project_browser.models.job_schemas import (
    create_publish_schema,
    create_render_schema,
    get_column_property_map,
    SubmissionConfigError,
)
from tumblehead.pipe.houdini.ui.project_browser.models.job_submission_table import (
    JobSubmissionTableModel,
)
from tumblehead.pipe.houdini.ui.project_browser.models.process_task import (
    ProcessTask,
)
from tumblehead.pipe.houdini.ui.project_browser.widgets import (
    CellSelectionTableView,
    JobSubmissionDelegate,
)
from tumblehead.pipe.houdini.ui.project_browser.widgets.job_section import (
    JobSectionWidget,
)

api = default_client()


def _get_nested_property(properties: dict, path: str):
    """Get a nested property value from a dict using dot notation.

    Args:
        properties: The properties dict
        path: Dot-separated path like 'render.pathtracedsamples'

    Returns:
        The value at the path, or None if not found
    """
    parts = path.split('.')
    value = properties
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _get_entity_properties_for_section(entity_uri: Uri, section: str) -> dict:
    """Get mapped entity properties for a section.

    Uses the schema-defined property_path mappings to load entity properties
    into column values.

    Args:
        entity_uri: The entity URI
        section: 'publish' or 'render'

    Returns:
        Dict mapping column keys to entity property values
    """
    try:
        properties = api.config.get_properties(entity_uri)
        if properties is None:
            return {}
    except Exception:
        return {}

    # Get the property mapping from the schema (column_key -> property_path)
    property_map = get_column_property_map(section)
    result = {}

    # Invert the mapping: we need property_path -> column_key, but get_column_property_map
    # gives us column_key -> property_path. So we iterate and populate from entity.
    for col_key, prop_path in property_map.items():
        value = _get_nested_property(properties, prop_path)
        if value is not None:
            result[col_key] = value

    # Add pool choices from entity farm.pools (for combo box filtering)
    pools = _get_nested_property(properties, 'farm.pools')
    if pools and isinstance(pools, list):
        result['_pool_choices'] = pools

    return result


class EntityTreeWidget(QtWidgets.QWidget):
    """Tree widget for selecting entities with checkboxes."""

    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._load_entities()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Label
        label = QtWidgets.QLabel("Entity Selection")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        # Tree view
        self._tree_view = QtWidgets.QTreeView()
        self._tree_view.setHeaderHidden(True)
        self._tree_view.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        layout.addWidget(self._tree_view)

    def _load_entities(self):
        """Load entities into the tree."""
        model = QStandardItemModel()
        self._tree_view.setModel(model)

        # Connect to item changed signal
        model.itemChanged.connect(self._on_item_changed)

        root = model.invisibleRootItem()

        # Add shots
        shots_item = QStandardItem("shots")
        shots_item.setEditable(False)
        shots_item.setCheckable(True)
        shots_item.setCheckState(Qt.Unchecked)
        shots_item.setData('shots', Qt.UserRole)
        shots_item.setData(None, Qt.UserRole + 1)  # No URI for category

        shot_entities = api.config.list_entities(Uri.parse_unsafe('entity:/shots'), closure=True)
        self._build_entity_tree(shots_item, shot_entities, 'shots')
        root.appendRow(shots_item)

        # Add assets
        assets_item = QStandardItem("assets")
        assets_item.setEditable(False)
        assets_item.setCheckable(True)
        assets_item.setCheckState(Qt.Unchecked)
        assets_item.setData('assets', Qt.UserRole)
        assets_item.setData(None, Qt.UserRole + 1)

        asset_entities = api.config.list_entities(Uri.parse_unsafe('entity:/assets'), closure=True)
        self._build_entity_tree(assets_item, asset_entities, 'assets')
        root.appendRow(assets_item)

        # Add groups
        groups_item = QStandardItem("groups")
        groups_item.setEditable(False)
        groups_item.setCheckable(True)
        groups_item.setCheckState(Qt.Unchecked)
        groups_item.setData('groups', Qt.UserRole)
        groups_item.setData(None, Qt.UserRole + 1)

        try:
            shot_groups = list_groups('shots')
            asset_groups = list_groups('assets')
            all_groups = shot_groups + asset_groups
            self._build_groups_tree(groups_item, all_groups)
        except Exception:
            pass

        root.appendRow(groups_item)

        # Expand top-level items
        for i in range(root.rowCount()):
            self._tree_view.expand(model.index(i, 0))

    def _build_entity_tree(self, parent_item, entities, context):
        """Build tree structure from entity list."""
        # Group entities by their parent path
        children = {}
        for entity in entities:
            segments = entity.uri.segments
            if len(segments) < 2:
                continue

            # Skip the context segment (shots/assets)
            path_segments = segments[1:]
            if len(path_segments) == 0:
                continue

            first_segment = path_segments[0]
            if first_segment not in children:
                children[first_segment] = []
            children[first_segment].append(entity)

        # Create items for each child
        for segment_name in sorted(children.keys()):
            child_entities = children[segment_name]

            # Check if this is a leaf (entity) or intermediate (category)
            is_leaf = all(len(e.uri.segments) == 2 for e in child_entities)

            item = QStandardItem(segment_name)
            item.setEditable(False)
            item.setCheckable(True)
            item.setCheckState(Qt.Unchecked)
            item.setData(context, Qt.UserRole)

            if is_leaf:
                # This is an entity
                entity = child_entities[0]
                item.setData(str(entity.uri), Qt.UserRole + 1)
            else:
                # This is a category - recurse
                item.setData(None, Qt.UserRole + 1)
                sub_entities = []
                for e in child_entities:
                    if len(e.uri.segments) > 2:
                        sub_entities.append(e)
                self._build_subcategory_tree(item, sub_entities, context, 2)

            parent_item.appendRow(item)

    def _build_subcategory_tree(self, parent_item, entities, context, depth):
        """Build subcategory tree recursively."""
        children = {}
        for entity in entities:
            segments = entity.uri.segments
            if len(segments) <= depth:
                continue

            segment_name = segments[depth]
            if segment_name not in children:
                children[segment_name] = []
            children[segment_name].append(entity)

        for segment_name in sorted(children.keys()):
            child_entities = children[segment_name]

            is_leaf = all(len(e.uri.segments) == depth + 1 for e in child_entities)

            item = QStandardItem(segment_name)
            item.setEditable(False)
            item.setCheckable(True)
            item.setCheckState(Qt.Unchecked)
            item.setData(context, Qt.UserRole)

            if is_leaf:
                entity = child_entities[0]
                item.setData(str(entity.uri), Qt.UserRole + 1)
            else:
                item.setData(None, Qt.UserRole + 1)
                sub_entities = [e for e in child_entities if len(e.uri.segments) > depth + 1]
                self._build_subcategory_tree(item, sub_entities, context, depth + 1)

            parent_item.appendRow(item)

    def _build_groups_tree(self, parent_item, groups):
        """Build tree from groups with members as children."""
        # Group by context
        by_context = {'shots': [], 'assets': []}
        for group in groups:
            ctx = group.uri.segments[0] if group.uri.segments else 'shots'
            if ctx in by_context:
                by_context[ctx].append(group)

        for context, context_groups in by_context.items():
            if not context_groups:
                continue

            context_item = QStandardItem(context)
            context_item.setEditable(False)
            context_item.setCheckable(True)
            context_item.setCheckState(Qt.Unchecked)
            context_item.setData(context, Qt.UserRole)
            context_item.setData(None, Qt.UserRole + 1)

            for group in sorted(context_groups, key=lambda g: g.name):
                group_item = QStandardItem(group.name)
                group_item.setEditable(False)
                group_item.setCheckable(True)
                group_item.setCheckState(Qt.Unchecked)
                group_item.setData(context, Qt.UserRole)
                group_item.setData(str(group.uri), Qt.UserRole + 1)
                group_item.setData('group', Qt.UserRole + 2)  # Mark as group

                # Add group members as children
                for member_uri in group.members:
                    member_name = str(member_uri)
                    member_context = member_uri.segments[0] if member_uri.segments else context
                    member_item = QStandardItem(member_name)
                    member_item.setEditable(False)
                    member_item.setCheckable(True)
                    member_item.setCheckState(Qt.Unchecked)
                    member_item.setData(member_context, Qt.UserRole)
                    member_item.setData(str(member_uri), Qt.UserRole + 1)
                    # No 'group' marker - these are regular entities
                    group_item.appendRow(member_item)

                context_item.appendRow(group_item)

            parent_item.appendRow(context_item)

    def _on_item_changed(self, item):
        """Handle item check state change."""
        # Block signals to prevent recursion
        model = self._tree_view.model()
        model.blockSignals(True)

        # Update children
        self._update_children_check_state(item, item.checkState())

        # Update parent
        self._update_parent_check_state(item)

        model.blockSignals(False)

        # Force repaint since signals were blocked
        self._tree_view.viewport().update()

        self.selection_changed.emit()

    def _update_children_check_state(self, item, state):
        """Update all children to match parent state."""
        for row in range(item.rowCount()):
            child = item.child(row)
            if child:
                child.setCheckState(state)
                self._update_children_check_state(child, state)

    def _update_parent_check_state(self, item):
        """Update parent check state based on children."""
        parent = item.parent()
        if parent is None:
            return

        checked = 0
        unchecked = 0
        for row in range(parent.rowCount()):
            child = parent.child(row)
            if child:
                if child.checkState() == Qt.Checked:
                    checked += 1
                elif child.checkState() == Qt.Unchecked:
                    unchecked += 1

        if checked == parent.rowCount():
            parent.setCheckState(Qt.Checked)
        elif unchecked == parent.rowCount():
            parent.setCheckState(Qt.Unchecked)
        else:
            parent.setCheckState(Qt.PartiallyChecked)

        self._update_parent_check_state(parent)

    def _select_all(self):
        """Select all entities."""
        model = self._tree_view.model()
        model.blockSignals(True)
        root = model.invisibleRootItem()
        for i in range(root.rowCount()):
            item = root.child(i)
            if item:
                item.setCheckState(Qt.Checked)
                self._update_children_check_state(item, Qt.Checked)
        model.blockSignals(False)
        self._tree_view.viewport().update()
        self.selection_changed.emit()

    def _clear_selection(self):
        """Clear all selections."""
        model = self._tree_view.model()
        model.blockSignals(True)
        root = model.invisibleRootItem()
        for i in range(root.rowCount()):
            item = root.child(i)
            if item:
                item.setCheckState(Qt.Unchecked)
                self._update_children_check_state(item, Qt.Unchecked)
        model.blockSignals(False)
        self._tree_view.viewport().update()
        self.selection_changed.emit()

    def get_selected_entities(self) -> list[tuple[str, str, str]]:
        """Get selected entities as (uri, name, context) tuples.

        Groups are expanded to their member entities.
        """
        model = self._tree_view.model()
        if model is None:
            return []

        selected = []
        self._collect_checked_entities(model.invisibleRootItem(), selected)
        return selected

    def _collect_checked_entities(self, item, selected):
        """Recursively collect checked leaf entities."""
        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            if child.checkState() == Qt.Checked:
                uri_str = child.data(Qt.UserRole + 1)
                context = child.data(Qt.UserRole)
                is_group = child.data(Qt.UserRole + 2) == 'group'

                if uri_str:
                    if is_group:
                        # Group with members as children - collect checked children
                        self._collect_checked_entities(child, selected)
                    else:
                        # Use full URI as the display name
                        selected.append((uri_str, uri_str, context))
                else:
                    # Category - collect children
                    self._collect_checked_entities(child, selected)
            elif child.checkState() == Qt.PartiallyChecked:
                # Partially checked - collect checked children
                self._collect_checked_entities(child, selected)

    def get_selected_uris(self) -> set[str]:
        """Get URIs of all checked items (entities and groups).

        Returns the URIs of items that are checked, including groups.
        This is used for persisting selection state.
        """
        model = self._tree_view.model()
        if model is None:
            return set()

        selected = set()
        self._collect_checked_uris(model.invisibleRootItem(), selected)
        return selected

    def _collect_checked_uris(self, item, selected: set[str]):
        """Recursively collect URIs of checked items."""
        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            if child.checkState() in (Qt.Checked, Qt.PartiallyChecked):
                uri_str = child.data(Qt.UserRole + 1)
                if uri_str:
                    selected.add(uri_str)
                # Always recurse to get child selections
                self._collect_checked_uris(child, selected)

    def select_by_uri(self, uri: Uri):
        """Select (check) the tree item matching the given URI.

        Args:
            uri: The URI to select (entity or group URI)
        """
        self.select_by_uris({str(uri)})

    def select_by_uris(self, uris: set[str]):
        """Select (check) tree items matching the given URIs.

        Args:
            uris: Set of URI strings to select
        """
        if not uris:
            return

        model = self._tree_view.model()
        if model is None:
            return

        model.blockSignals(True)
        self._check_matching_uris(model.invisibleRootItem(), uris)
        model.blockSignals(False)

        self._tree_view.viewport().update()
        self.selection_changed.emit()

    def _check_matching_uris(self, item, uris: set[str]):
        """Recursively check items matching the given URIs."""
        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            uri_str = child.data(Qt.UserRole + 1)
            if uri_str and uri_str in uris:
                # Check this item and update children
                child.setCheckState(Qt.Checked)
                self._update_children_check_state(child, Qt.Checked)
                # Update parent check states
                self._update_parent_check_state(child)
            else:
                # Recurse into children
                self._check_matching_uris(child, uris)

    def get_expanded_paths(self) -> set[str]:
        """Get paths of all expanded items for persistence.

        Returns paths (not URIs) since categories don't have URIs.
        Format: "shots", "shots/sq010", "groups/shots/mygroup", etc.
        """
        model = self._tree_view.model()
        if model is None:
            return set()

        expanded = set()
        self._collect_expanded_paths(model.invisibleRootItem(), "", expanded)
        return expanded

    def _collect_expanded_paths(self, item, parent_path: str, expanded: set[str]):
        """Recursively collect paths of expanded items."""
        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            # Build path for this item
            item_name = child.text()
            current_path = f"{parent_path}/{item_name}" if parent_path else item_name

            # Check if this item is expanded
            index = self._tree_view.model().indexFromItem(child)
            if self._tree_view.isExpanded(index):
                expanded.add(current_path)

            # Recurse into children
            self._collect_expanded_paths(child, current_path, expanded)

    def set_expanded_paths(self, paths: set[str]):
        """Restore expand state from saved paths.

        Args:
            paths: Set of path strings to expand
        """
        if not paths:
            return

        model = self._tree_view.model()
        if model is None:
            return

        # First collapse everything
        self._tree_view.collapseAll()

        # Then expand matching paths
        self._expand_matching_paths(model.invisibleRootItem(), "", paths)

    def _expand_matching_paths(self, item, parent_path: str, paths: set[str]):
        """Recursively expand items matching the given paths."""
        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            # Build path for this item
            item_name = child.text()
            current_path = f"{parent_path}/{item_name}" if parent_path else item_name

            # Check if this path should be expanded
            index = self._tree_view.model().indexFromItem(child)
            if current_path in paths:
                self._tree_view.expand(index)

            # Recurse into children
            self._expand_matching_paths(child, current_path, paths)

    def expand_to_selection(self):
        """Expand tree to reveal selected items, collapse non-selected branches.

        This is called on first dialog open to show the context selection.
        """
        model = self._tree_view.model()
        if model is None:
            return

        # Collapse everything first
        self._tree_view.collapseAll()

        # Expand paths that lead to checked items
        self._expand_to_checked(model.invisibleRootItem())

    def _expand_to_checked(self, item) -> bool:
        """Recursively expand branches that contain checked items.

        Args:
            item: The tree item to process

        Returns:
            True if this item or any descendant is checked
        """
        has_checked = False

        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            # Check if this child or its descendants are checked
            child_checked = child.checkState() in (Qt.Checked, Qt.PartiallyChecked)
            descendant_checked = self._expand_to_checked(child)

            if child_checked or descendant_checked:
                has_checked = True
                # Expand this item to reveal the checked child
                index = self._tree_view.model().indexFromItem(item)
                if index.isValid():
                    self._tree_view.expand(index)
                # Also expand the child if it has checked descendants
                if descendant_checked:
                    child_index = self._tree_view.model().indexFromItem(child)
                    self._tree_view.expand(child_index)

        return has_checked

    def get_content_width(self) -> int:
        """Calculate the width needed to display tree content without scrolling.

        Returns:
            Width in pixels needed for the tree content
        """
        # Get the width hint for column 0 (the only column)
        self._tree_view.resizeColumnToContents(0)
        column_width = self._tree_view.columnWidth(0)

        # Add padding for scrollbar and margins
        scrollbar_width = self._tree_view.verticalScrollBar().sizeHint().width()
        padding = 20  # Extra padding for comfort

        return column_width + scrollbar_width + padding


class JobSubmissionDialog(QtWidgets.QDialog):
    """Dialog for batch job submission to the render farm."""

    submission_started = Signal()
    submission_completed = Signal(list)  # List of submitted job IDs

    def __init__(
        self,
        context: Context | None = None,
        previous_selections: set[str] | None = None,
        previous_expanded: set[str] | None = None,
        previous_splitter_sizes: list[int] | None = None,
        parent=None
    ):
        super().__init__(parent)

        self._initial_context = context
        self._previous_selections = previous_selections
        self._previous_expanded = previous_expanded
        self._previous_splitter_sizes = previous_splitter_sizes
        self._splitter_sizes_applied = False

        self.setWindowTitle("Submit Jobs to Farm")
        self.resize(1200, 700)

        self._setup_ui()
        self._connect_signals()

        # Initialize with submission schema
        self._init_schema()

        # Apply initial selection and expand state
        # (splitter sizes are deferred to showEvent for proper layout)
        self._apply_initial_selection()

    def showEvent(self, event):
        """Handle dialog show event."""
        super().showEvent(event)

        # Apply splitter sizes on first show (after layout is ready)
        if not self._splitter_sizes_applied:
            self._splitter_sizes_applied = True
            self._apply_splitter_sizes()

    def _setup_ui(self):
        """Create the dialog UI."""
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        # Main splitter
        self._splitter = QtWidgets.QSplitter(Qt.Horizontal)
        layout.addWidget(self._splitter, 1)

        # Left panel - Entity tree
        self._entity_tree = EntityTreeWidget()
        self._splitter.addWidget(self._entity_tree)

        # Right panel - Vertical sections (Publish and Render)
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_panel.setLayout(right_layout)

        # Label
        table_label = QtWidgets.QLabel("Job Configuration")
        table_label.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(table_label)

        # Scroll area for sections
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(8)

        # Create sections - may fail if config is missing
        try:
            # Publish section
            self._publish_section = JobSectionWidget("Publish", create_publish_schema())
            scroll_layout.addWidget(self._publish_section)

            # Render section (enabled by default)
            self._render_section = JobSectionWidget("Render", create_render_schema())
            scroll_layout.addWidget(self._render_section)
            self._render_section.enabled = True

            self._config_error = None
        except SubmissionConfigError as e:
            self._config_error = str(e)
            # Create placeholder widgets
            self._publish_section = None
            self._render_section = None

            # Show error message in scroll area
            error_label = QtWidgets.QLabel(
                f"<b>Configuration Error</b><br><br>"
                f"{str(e).replace(chr(10), '<br>')}<br><br>"
                f"<i>Use the Column Editor to configure submission columns.</i>"
            )
            error_label.setWordWrap(True)
            error_label.setStyleSheet("color: #ff6666; padding: 20px;")
            scroll_layout.addWidget(error_label)

        # Add stretch at bottom to push sections to top when collapsed
        scroll_layout.addStretch()
        scroll_area.setWidget(scroll_content)
        right_layout.addWidget(scroll_area, 1)

        self._splitter.addWidget(right_panel)

        # Buttons
        bottom_layout = QtWidgets.QHBoxLayout()

        select_all_btn = QtWidgets.QPushButton("Select All")
        select_all_btn.clicked.connect(self._entity_tree._select_all)
        bottom_layout.addWidget(select_all_btn)

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self._entity_tree._clear_selection)
        bottom_layout.addWidget(clear_btn)

        bottom_layout.addStretch()

        self._submit_btn = QtWidgets.QPushButton("Submit to Farm")
        self._submit_btn.setEnabled(False)
        self._submit_btn.setMinimumWidth(120)
        bottom_layout.addWidget(self._submit_btn)

        cancel_btn = QtWidgets.QPushButton("Close")
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)

        layout.addLayout(bottom_layout)

    def _connect_signals(self):
        """Connect signal handlers."""
        self._entity_tree.selection_changed.connect(self._on_entity_selection_changed)
        self._submit_btn.clicked.connect(self._submit_jobs)

        # Connect section signals only if sections are available
        if self._publish_section is not None:
            self._publish_section.enabled_changed.connect(self._update_status)
            self._publish_section.model.dataChanged.connect(self._update_status)

        if self._render_section is not None:
            self._render_section.enabled_changed.connect(self._update_status)
            self._render_section.model.dataChanged.connect(self._update_status)

    def _init_schema(self):
        """Initialize schemas (already done in setup_ui, just update status)."""
        self._update_status()

    def _apply_initial_selection(self):
        """Apply initial selection and expand state.

        Selection priority:
        1. Previous selections (if provided) - restore user's last selection
        2. Context entity/group (if provided) - select based on current workfile
        3. No selection

        Expand priority:
        1. Previous expanded (if provided) - restore user's last expand state
        2. Auto-expand to show selected items (only if something was selected)
        3. Keep default expand state (top-level items expanded)
        """
        # Track if anything was selected
        has_selection = False

        # Apply selection
        if self._previous_selections is not None:
            # Restore previous selections
            self._entity_tree.select_by_uris(self._previous_selections)
            has_selection = len(self._previous_selections) > 0
        elif self._initial_context is not None:
            # Select entity/group from context
            self._entity_tree.select_by_uri(self._initial_context.entity_uri)
            has_selection = True

        # Apply expand state
        if self._previous_expanded is not None:
            # Restore previous expand state
            self._entity_tree.set_expanded_paths(self._previous_expanded)
        elif has_selection:
            # First open with selection - expand to show selected items
            self._entity_tree.expand_to_selection()
        # else: No context, no previous state - keep default expand (top-level only)

    def get_selected_uris(self) -> set[str]:
        """Get URIs of all selected items for persistence.

        Returns:
            Set of URI strings that are currently selected
        """
        return self._entity_tree.get_selected_uris()

    def get_expanded_paths(self) -> set[str]:
        """Get paths of all expanded items for persistence.

        Returns:
            Set of path strings that are currently expanded
        """
        return self._entity_tree.get_expanded_paths()

    def _apply_splitter_sizes(self):
        """Apply splitter sizes - restore previous or auto-fit to content.

        Size priority:
        1. Previous splitter sizes (if provided) - restore user's last setting
        2. Auto-fit to tree content width (first open behavior)
        """
        if self._previous_splitter_sizes is not None:
            # Restore previous splitter sizes
            self._splitter.setSizes(self._previous_splitter_sizes)
        else:
            # First open - auto-fit to tree content
            tree_width = self._entity_tree.get_content_width()
            # Ensure minimum width of 150px and maximum of 400px
            tree_width = max(150, min(400, tree_width))
            total_width = self._splitter.width()
            if total_width > 0:
                right_width = total_width - tree_width
                self._splitter.setSizes([tree_width, right_width])
            else:
                # Splitter not yet sized, use reasonable defaults
                self._splitter.setSizes([tree_width, 800])

    def get_splitter_sizes(self) -> list[int]:
        """Get current splitter sizes for persistence.

        Returns:
            List of widget sizes in the splitter
        """
        return self._splitter.sizes()

    def _on_entity_selection_changed(self):
        """Handle entity selection change."""
        # Skip if config error (no sections available)
        if self._config_error is not None:
            return

        selected = self._entity_tree.get_selected_entities()

        # Set entities on both sections (they stay synchronized)
        if self._publish_section is not None:
            self._publish_section.set_entities(selected)
        if self._render_section is not None:
            self._render_section.set_entities(selected)

        # Apply entity properties (frame ranges, farm settings, etc.) as defaults
        if selected:
            self._apply_entity_properties()

        self._update_status()

    def _apply_entity_properties(self):
        """Apply entity properties from config to both sections.

        This loads entity properties (farm settings, render settings, timeline)
        and applies them as defaults to the table cells. Values from config
        are shown dimmed; user edits become bold overrides.
        """
        # Apply to publish section
        if self._publish_section is not None:
            self._apply_section_entity_properties(self._publish_section, 'publish')

        # Apply to render section
        if self._render_section is not None:
            self._apply_section_entity_properties(self._render_section, 'render')

    def _apply_section_entity_properties(self, section: JobSectionWidget, section_name: str):
        """Apply entity properties to a specific section.

        Uses the property_path mappings from the column config to automatically
        load entity properties as default values. All mappings are now driven
        by config - no hardcoded property handling.

        Args:
            section: The JobSectionWidget to apply properties to
            section_name: 'publish' or 'render' for property mapping
        """
        model = section.model
        schema = section.schema
        configs = section.get_configs()

        for i, config in enumerate(configs):
            uri_str = config['entity']['uri']
            try:
                uri = Uri.parse_unsafe(uri_str)

                # Apply mapped entity properties (all driven by property_path in config)
                entity_props = _get_entity_properties_for_section(uri, section_name)
                for col_key, value in entity_props.items():
                    # Skip internal keys
                    if col_key.startswith('_'):
                        continue
                    # Only apply if column exists in schema
                    if schema.get_column_by_key(col_key) is not None:
                        model.set_cell_override(i, col_key, value, is_override=False)

            except Exception:
                pass

    def _update_status(self):
        """Update submit button state."""
        # Disable submit if config error
        if self._config_error is not None:
            self._submit_btn.setEnabled(False)
            return

        # Get entity count from either section (they're synchronized)
        row_count = 0
        if self._publish_section is not None:
            row_count = self._publish_section.model.rowCount()
        elif self._render_section is not None:
            row_count = self._render_section.model.rowCount()

        # Check for validation errors in both sections
        publish_errors = []
        render_errors = []
        if self._publish_section is not None and self._publish_section.enabled:
            publish_errors = self._publish_section.model.validate_all()
        if self._render_section is not None and self._render_section.enabled:
            render_errors = self._render_section.model.validate_all()
        errors = publish_errors + render_errors

        if row_count == 0:
            self._submit_btn.setEnabled(False)
            return

        # Check section enabled states
        publish_enabled = self._publish_section.enabled if self._publish_section is not None else False
        render_enabled = self._render_section.enabled if self._render_section is not None else False
        any_enabled = publish_enabled or render_enabled

        if errors or not any_enabled:
            self._submit_btn.setEnabled(False)
        else:
            self._submit_btn.setEnabled(True)

    def _get_merged_configs(self) -> list[dict]:
        """Merge configs from publish and render sections.

        Returns configs in format expected by batch_submit.py:
        {
            'entity': {'uri': ..., 'name': ..., 'context': ...},
            'settings': {
                'publish': True/False,
                'render': True/False,
                # Publish settings (prefixed with 'pub_')
                'pub_department': ...,
                'pub_pool': ...,
                'pub_priority': ...,
                # Render settings (prefixed with 'render_' for dept/pool/priority)
                'render_department': ...,
                'render_pool': ...,
                'render_priority': ...,
                'variants': [...],
                'tile_count': ...,
                'pre_roll': ...,
                'first_frame': ...,
                'last_frame': ...,
                'post_roll': ...,
                'batch_size': ...,
                'denoise': ...,
            }
        }
        """
        # Handle case where sections might be None (config error)
        publish_enabled = self._publish_section.enabled if self._publish_section is not None else False
        render_enabled = self._render_section.enabled if self._render_section is not None else False

        # Get configs from each section
        publish_configs = []
        render_configs = []
        if self._publish_section is not None and publish_enabled:
            publish_configs = self._publish_section.get_configs()
        if self._render_section is not None and render_enabled:
            render_configs = self._render_section.get_configs()

        # Build a map by URI for merging
        merged = {}

        for config in publish_configs:
            uri = config['entity']['uri']
            if uri not in merged:
                merged[uri] = {
                    'entity': config['entity'],
                    'settings': {
                        'publish': False,
                        'render': False,
                    }
                }
            merged[uri]['settings']['publish'] = True

            # Flatten publish settings with 'pub_' prefix
            pub_settings = config['settings']
            merged[uri]['settings']['pub_department'] = pub_settings.get('department')
            merged[uri]['settings']['pub_pool'] = pub_settings.get('pool')
            merged[uri]['settings']['pub_priority'] = pub_settings.get('priority')

        for config in render_configs:
            uri = config['entity']['uri']
            if uri not in merged:
                merged[uri] = {
                    'entity': config['entity'],
                    'settings': {
                        'publish': False,
                        'render': False,
                    }
                }
            merged[uri]['settings']['render'] = True

            # Flatten render settings with appropriate prefixes
            render_settings = config['settings']
            merged[uri]['settings']['render_department'] = render_settings.get('department')
            merged[uri]['settings']['render_pool'] = render_settings.get('pool')
            merged[uri]['settings']['render_priority'] = render_settings.get('priority')
            # These keep their original names
            merged[uri]['settings']['variants'] = render_settings.get('variants')
            merged[uri]['settings']['tile_count'] = render_settings.get('tile_count')
            merged[uri]['settings']['pre_roll'] = render_settings.get('pre_roll')
            merged[uri]['settings']['first_frame'] = render_settings.get('first_frame')
            merged[uri]['settings']['last_frame'] = render_settings.get('last_frame')
            merged[uri]['settings']['post_roll'] = render_settings.get('post_roll')
            merged[uri]['settings']['batch_size'] = render_settings.get('batch_size')
            merged[uri]['settings']['denoise'] = render_settings.get('denoise')

            # Pass through any additional settings (for config-driven columns)
            # This allows new columns added via config to automatically flow through
            for key, value in render_settings.items():
                if key not in merged[uri]['settings']:
                    merged[uri]['settings'][key] = value

        return list(merged.values())

    def _create_submission_tasks(self, configs: list[dict]) -> list[ProcessTask]:
        """Convert job configs to ProcessTask objects for the ProcessDialog.

        Args:
            configs: List of merged job configuration dicts

        Returns:
            List of ProcessTask objects ready for the ProcessDialog
        """
        from tumblehead.farm.jobs.houdini import batch_submit

        tasks = []
        for config in configs:
            entity = config['entity']
            settings = config['settings']
            uri = Uri.parse_unsafe(entity['uri'])

            # Build description
            job_types = []
            departments = []
            if settings.get('publish'):
                job_types.append('publish')
                if settings.get('pub_department'):
                    departments.append(settings['pub_department'])
            if settings.get('render'):
                job_types.append('render')
                if settings.get('render_department'):
                    departments.append(settings['render_department'])

            dept_str = ', '.join(set(departments)) if departments else 'N/A'
            description = f"{'+'.join(job_types)} [{dept_str}]"

            # Extract display values from settings
            variants = settings.get('variants', [])
            variant_str = ', '.join(variants) if variants else None
            first_frame = settings.get('first_frame')
            last_frame = settings.get('last_frame')

            # Create task with farm-only execution
            # Use default parameter binding to capture config in closure
            task = ProcessTask(
                id=str(uuid.uuid4()),
                uri=uri,
                department=dept_str,
                task_type='farm_submit',
                description=description,
                execute_local=None,  # No local execution for farm submission
                execute_farm=lambda c=config: batch_submit.submit_entity_batch(c),
                variant=variant_str,
                first_frame=first_frame,
                last_frame=last_frame,
            )
            tasks.append(task)

        return tasks

    def _on_submission_completed(self, results: dict):
        """Handle completion of farm submission.

        Args:
            results: Dict with 'completed', 'failed', and 'skipped' lists
        """
        completed = results.get('completed', [])

        # ProcessDialog already shows error report for failed tasks

        # Emit completion signal with task IDs (not job IDs since batch_submit
        # returns job IDs internally but we track by task)
        self.submission_completed.emit(completed)

    def _submit_jobs(self):
        """Submit jobs to the farm using the ProcessDialog."""
        # Check for config error
        if self._config_error is not None:
            QtWidgets.QMessageBox.warning(
                self, "Configuration Error",
                "Cannot submit jobs: submission columns not configured."
            )
            return

        # Validate enabled sections
        publish_valid = True
        render_valid = True
        if self._publish_section is not None and self._publish_section.enabled:
            publish_valid = self._publish_section.model.is_valid()
        if self._render_section is not None and self._render_section.enabled:
            render_valid = self._render_section.model.is_valid()

        if not (publish_valid and render_valid):
            QtWidgets.QMessageBox.warning(
                self, "Validation Error",
                "Please fix validation errors before submitting."
            )
            return

        # Get merged configs from both sections
        configs = self._get_merged_configs()

        if not configs:
            QtWidgets.QMessageBox.warning(
                self, "No Jobs Selected",
                "Please enable Publish and/or Render section and select entities."
            )
            return

        self.submission_started.emit()

        # Create ProcessTask objects for each entity
        tasks = self._create_submission_tasks(configs)

        if not tasks:
            QtWidgets.QMessageBox.warning(
                self, "No Jobs",
                "No jobs to submit."
            )
            return

        # Show ProcessDialog for farm submission
        # Import here to avoid circular imports
        from .process_dialog import ProcessDialog

        dialog = ProcessDialog(
            title="Submit to Farm",
            tasks=tasks,
            current_department=None,  # Disable mode filtering (farm-only)
            parent=self
        )
        dialog.process_completed.connect(self._on_submission_completed)
        dialog.exec_()
