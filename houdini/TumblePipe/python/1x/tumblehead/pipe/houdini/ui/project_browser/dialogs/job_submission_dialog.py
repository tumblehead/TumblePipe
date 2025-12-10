"""Dialog for batch job submission to the render farm."""

from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QStandardItemModel, QStandardItem

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.groups import list_groups, get_group
from tumblehead.config.timeline import get_frame_range

from tumblehead.pipe.houdini.ui.project_browser.models.job_schemas import (
    get_submission_schema,
    ColumnType,
)
from tumblehead.pipe.houdini.ui.project_browser.models.job_submission_table import (
    JobSubmissionTableModel,
)
from tumblehead.pipe.houdini.ui.project_browser.widgets import (
    RowHoverTableView,
    JobSubmissionDelegate,
)

api = default_client()


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

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_selection)
        button_layout.addWidget(select_all_btn)
        button_layout.addWidget(clear_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

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
        """Build tree from groups."""
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
                item = QStandardItem(group.name)
                item.setEditable(False)
                item.setCheckable(True)
                item.setCheckState(Qt.Unchecked)
                item.setData(context, Qt.UserRole)
                item.setData(str(group.uri), Qt.UserRole + 1)
                item.setData('group', Qt.UserRole + 2)  # Mark as group
                context_item.appendRow(item)

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
                        # Expand group members
                        self._expand_group(uri_str, context, selected)
                    else:
                        name = child.text()
                        selected.append((uri_str, name, context))
                else:
                    # Category - collect children
                    self._collect_checked_entities(child, selected)
            elif child.checkState() == Qt.PartiallyChecked:
                # Partially checked - collect checked children
                self._collect_checked_entities(child, selected)

    def _expand_group(self, group_uri_str, context, selected):
        """Expand a group URI to its member entities."""
        try:
            uri = Uri.parse_unsafe(group_uri_str)
            group = get_group(uri)
            if group and group.members:
                for member_uri in group.members:
                    name = member_uri.segments[-1] if member_uri.segments else str(member_uri)
                    # Determine context from member URI
                    member_context = member_uri.segments[0] if member_uri.segments else context
                    selected.append((str(member_uri), name, member_context))
        except Exception:
            pass


class JobSubmissionDialog(QtWidgets.QDialog):
    """Dialog for batch job submission to the render farm."""

    submission_started = Signal()
    submission_completed = Signal(list)  # List of submitted job IDs

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Submit Jobs to Farm")
        self.resize(1200, 700)

        self._setup_ui()
        self._connect_signals()

        # Initialize with submission schema
        self._init_schema()

    def _setup_ui(self):
        """Create the dialog UI."""
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        # Main splitter
        splitter = QtWidgets.QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        # Left panel - Entity tree
        self._entity_tree = EntityTreeWidget()
        splitter.addWidget(self._entity_tree)

        # Right panel - Configuration table
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_panel.setLayout(right_layout)

        # Table label
        table_label = QtWidgets.QLabel("Job Configuration")
        table_label.setStyleSheet("font-weight: bold;")
        right_layout.addWidget(table_label)

        # Table model and view
        self._table_model = JobSubmissionTableModel()
        self._table_view = RowHoverTableView()
        self._table_view.setModel(self._table_model)
        self._table_view.setItemDelegate(JobSubmissionDelegate(self._table_view))
        self._table_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table_view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._table_view.horizontalHeader().setStretchLastSection(True)
        self._table_view.verticalHeader().setVisible(False)
        self._table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table_view.customContextMenuRequested.connect(self._on_table_context_menu)
        right_layout.addWidget(self._table_view, 1)

        splitter.addWidget(right_panel)

        # Set splitter sizes (30% / 70%)
        splitter.setSizes([300, 700])

        # Status and buttons
        bottom_layout = QtWidgets.QHBoxLayout()

        self._status_label = QtWidgets.QLabel("Select entities and configure job settings")
        self._status_label.setStyleSheet("color: #888;")
        bottom_layout.addWidget(self._status_label)

        bottom_layout.addStretch()

        self._submit_btn = QtWidgets.QPushButton("Submit to Farm")
        self._submit_btn.setEnabled(False)
        self._submit_btn.setMinimumWidth(120)
        bottom_layout.addWidget(self._submit_btn)

        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)

        layout.addLayout(bottom_layout)

    def _connect_signals(self):
        """Connect signal handlers."""
        self._entity_tree.selection_changed.connect(self._on_entity_selection_changed)
        self._submit_btn.clicked.connect(self._submit_jobs)
        self._table_model.dataChanged.connect(self._update_status)

    def _init_schema(self):
        """Initialize with submission schema."""
        schema = get_submission_schema()
        self._table_model.set_schema(schema)

        # Set column widths
        for i, col_def in enumerate(schema.columns):
            self._table_view.setColumnWidth(i + 1, col_def.width)

        self._update_status()

    def _on_entity_selection_changed(self):
        """Handle entity selection change."""
        selected = self._entity_tree.get_selected_entities()

        # Clear and repopulate table
        self._table_model.clear_rows()
        if selected:
            self._table_model.add_entities(selected)

            # Set frame ranges from entity configs
            self._apply_entity_frame_ranges()

        self._update_status()

    def _apply_entity_frame_ranges(self):
        """Apply frame ranges from entity timeline configs."""
        schema = self._table_model.get_schema()
        if schema is None:
            return

        # Check if schema has frame columns
        has_frame_cols = schema.get_column_by_key('first_frame') is not None
        if not has_frame_cols:
            return

        # Get configs and apply frame ranges
        configs = self._table_model.get_job_configs()
        for i, config in enumerate(configs):
            uri_str = config['entity']['uri']
            try:
                uri = Uri.parse_unsafe(uri_str)
                frame_range = get_frame_range(uri)
                if frame_range:
                    # Set individual frame columns (don't mark as override - it's from config)
                    self._table_model.set_cell_override(i, 'pre_roll', frame_range.start_roll, is_override=False)
                    self._table_model.set_cell_override(i, 'first_frame', frame_range.start_frame, is_override=False)
                    self._table_model.set_cell_override(i, 'last_frame', frame_range.end_frame, is_override=False)
                    self._table_model.set_cell_override(i, 'post_roll', frame_range.end_roll, is_override=False)
            except Exception:
                pass

    def _on_table_context_menu(self, pos):
        """Show context menu for table."""
        index = self._table_view.indexAt(pos)
        if not index.isValid():
            return

        col_def = index.data(JobSubmissionTableModel.ROLE_COLUMN_DEF)
        is_overridden = index.data(JobSubmissionTableModel.ROLE_IS_OVERRIDDEN)

        if col_def is None:
            return

        menu = QtWidgets.QMenu(self)

        # Reset single cell
        if is_overridden:
            reset_action = menu.addAction("Reset to Default")
            reset_action.triggered.connect(
                lambda: self._table_model.reset_cell_to_default(index.row(), col_def.key)
            )

        menu.addSeparator()

        # Bulk operations
        selected = self._table_view.selectionModel().selectedRows()
        current_value = index.data(JobSubmissionTableModel.ROLE_RAW_VALUE)

        if len(selected) > 1:
            apply_action = menu.addAction(f"Apply to Selected ({len(selected)} rows)")
            apply_action.triggered.connect(
                lambda: self._table_model.apply_to_selected(
                    [idx.row() for idx in selected], col_def.key, current_value
                )
            )

        apply_all_action = menu.addAction("Apply to All Rows")
        apply_all_action.triggered.connect(
            lambda: self._table_model.apply_to_all(col_def.key, current_value)
        )

        menu.addSeparator()

        reset_col_action = menu.addAction("Reset Column to Defaults")
        reset_col_action.triggered.connect(
            lambda: self._table_model.reset_column_to_default(col_def.key)
        )

        menu.exec_(self._table_view.mapToGlobal(pos))

    def _update_status(self):
        """Update status label and submit button state."""
        row_count = self._table_model.rowCount()
        errors = self._table_model.validate_all()

        if row_count == 0:
            self._status_label.setText("Select entities to submit")
            self._status_label.setStyleSheet("color: #888;")
            self._submit_btn.setEnabled(False)
            return

        # Count rows with publish or render checked
        configs = self._table_model.get_job_configs()
        publish_count = sum(1 for c in configs if c['settings'].get('publish', False))
        render_count = sum(1 for c in configs if c['settings'].get('render', False))
        active_count = sum(1 for c in configs if c['settings'].get('publish', False) or c['settings'].get('render', False))

        if errors:
            self._status_label.setText(f"{row_count} entities, {len(errors)} validation errors")
            self._status_label.setStyleSheet("color: #ff6b6b;")
            self._submit_btn.setEnabled(False)
        elif active_count == 0:
            self._status_label.setText(f"{row_count} entities - check Publish or Render to enable submission")
            self._status_label.setStyleSheet("color: #888;")
            self._submit_btn.setEnabled(False)
        else:
            parts = []
            if publish_count > 0:
                parts.append(f"{publish_count} publish")
            if render_count > 0:
                parts.append(f"{render_count} render")
            self._status_label.setText(f"Ready to submit: {', '.join(parts)}")
            self._status_label.setStyleSheet("color: #6bff6b;")
            self._submit_btn.setEnabled(True)

    def _submit_jobs(self):
        """Submit jobs to the farm."""
        if not self._table_model.is_valid():
            QtWidgets.QMessageBox.warning(
                self, "Validation Error",
                "Please fix validation errors before submitting."
            )
            return

        configs = self._table_model.get_job_configs()

        # Filter to only rows with publish or render checked
        active_configs = [
            c for c in configs
            if c['settings'].get('publish', False) or c['settings'].get('render', False)
        ]

        if not active_configs:
            QtWidgets.QMessageBox.warning(
                self, "No Jobs Selected",
                "Please check Publish and/or Render for at least one entity."
            )
            return

        # Summarize what will be submitted
        publish_count = sum(1 for c in active_configs if c['settings'].get('publish', False))
        render_count = sum(1 for c in active_configs if c['settings'].get('render', False))
        both_count = sum(1 for c in active_configs if c['settings'].get('publish', False) and c['settings'].get('render', False))

        summary_parts = []
        if publish_count > 0:
            summary_parts.append(f"{publish_count} publish job(s)")
        if render_count > 0:
            summary_parts.append(f"{render_count} render job(s)")
        if both_count > 0:
            summary_parts.append(f"({both_count} with both)")

        # Confirm submission
        reply = QtWidgets.QMessageBox.question(
            self, "Confirm Submission",
            f"Submit {', '.join(summary_parts)} to the farm?\n\n"
            f"One batch will be created per entity.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )

        if reply != QtWidgets.QMessageBox.Yes:
            return

        self.submission_started.emit()

        # TODO: Call batch_submit module to actually submit
        # For now, show what would be submitted
        job_ids = []
        details = []
        for config in active_configs:
            uri = config['entity']['uri']
            settings = config['settings']
            publish = settings.get('publish', False)
            render = settings.get('render', False)
            variants = settings.get('variants', [])
            dept = settings.get('department', 'N/A')

            job_type = []
            if publish:
                job_type.append('publish')
            if render:
                job_type.append('render')

            details.append(f"  - {uri}: {'+'.join(job_type)} [{dept}] variants={variants}")
            job_ids.append(f"batch_{len(job_ids) + 1}")

        QtWidgets.QMessageBox.information(
            self, "Jobs Submitted (Mock)",
            f"Would submit {len(active_configs)} batch(es):\n\n" +
            "\n".join(details[:10]) +
            ("\n  ..." if len(details) > 10 else "") +
            "\n\n(Actual farm submission to be implemented in batch_submit.py)"
        )

        self.submission_completed.emit(job_ids)
        self.accept()
