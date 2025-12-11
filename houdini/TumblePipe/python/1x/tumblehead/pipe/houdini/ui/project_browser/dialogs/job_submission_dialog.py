"""Dialog for batch job submission to the render farm."""

import uuid

from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QStandardItemModel, QStandardItem

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.groups import list_groups, get_group

from tumblehead.pipe.houdini.ui.project_browser.models.job_schemas import (
    create_publish_schema,
    create_render_schema,
    get_column_property_map,
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

        # Publish section
        self._publish_section = JobSectionWidget("Publish", create_publish_schema())
        scroll_layout.addWidget(self._publish_section)

        # Render section
        self._render_section = JobSectionWidget("Render", create_render_schema())
        scroll_layout.addWidget(self._render_section)

        # Add stretch at bottom to push sections to top when collapsed
        scroll_layout.addStretch()
        scroll_area.setWidget(scroll_content)
        right_layout.addWidget(scroll_area, 1)

        splitter.addWidget(right_panel)

        # Set splitter sizes (30% / 70%)
        splitter.setSizes([300, 700])

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

        # Connect section enabled signals
        self._publish_section.enabled_changed.connect(self._update_status)
        self._render_section.enabled_changed.connect(self._update_status)

        # Connect model changes to status update
        self._publish_section.model.dataChanged.connect(self._update_status)
        self._render_section.model.dataChanged.connect(self._update_status)

    def _init_schema(self):
        """Initialize schemas (already done in setup_ui, just update status)."""
        self._update_status()

    def _on_entity_selection_changed(self):
        """Handle entity selection change."""
        selected = self._entity_tree.get_selected_entities()

        # Set entities on both sections (they stay synchronized)
        self._publish_section.set_entities(selected)
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
        self._apply_section_entity_properties(self._publish_section, 'publish')

        # Apply to render section
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
        # Get entity count from either section (they're synchronized)
        row_count = self._publish_section.model.rowCount()

        # Check for validation errors in both sections
        publish_errors = self._publish_section.model.validate_all() if self._publish_section.enabled else []
        render_errors = self._render_section.model.validate_all() if self._render_section.enabled else []
        errors = publish_errors + render_errors

        if row_count == 0:
            self._submit_btn.setEnabled(False)
            return

        # Check section enabled states
        publish_enabled = self._publish_section.enabled
        render_enabled = self._render_section.enabled
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
        publish_enabled = self._publish_section.enabled
        render_enabled = self._render_section.enabled

        # Get configs from each section
        publish_configs = self._publish_section.get_configs() if publish_enabled else []
        render_configs = self._render_section.get_configs() if render_enabled else []

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
        # Validate enabled sections
        publish_valid = self._publish_section.model.is_valid() if self._publish_section.enabled else True
        render_valid = self._render_section.model.is_valid() if self._render_section.enabled else True

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
