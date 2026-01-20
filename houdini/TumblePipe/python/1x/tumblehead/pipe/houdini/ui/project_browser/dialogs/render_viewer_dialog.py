"""Dialog for viewing renders in DJV."""

from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QStandardItemModel, QStandardItem

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.pipe.paths import get_render, get_render_context

api = default_client()


class ShotTreeWidget(QtWidgets.QWidget):
    """Tree widget for selecting shots with checkboxes."""

    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._load_shots()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # Label
        label = QtWidgets.QLabel("Shot Selection")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        # Tree view
        self._tree_view = QtWidgets.QTreeView()
        self._tree_view.setHeaderHidden(True)
        self._tree_view.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        layout.addWidget(self._tree_view)

    def _load_shots(self):
        """Load shots into the tree."""
        model = QStandardItemModel()
        self._tree_view.setModel(model)

        # Connect to item changed signal
        model.itemChanged.connect(self._on_item_changed)

        root = model.invisibleRootItem()

        # Load shot entities
        try:
            shot_entities = api.config.list_entities(
                Uri.parse_unsafe('entity:/shots'), closure=True
            )
            self._build_shot_tree(root, shot_entities)
        except Exception as e:
            print(f"Error loading shots: {e}")

        # Expand first level
        for i in range(root.rowCount()):
            self._tree_view.expand(model.index(i, 0))

    def _build_shot_tree(self, parent_item, entities):
        """Build tree structure from shot entities."""
        # Group entities by sequence (first segment after 'shots')
        sequences = {}
        for entity in entities:
            segments = entity.uri.segments
            if len(segments) < 3:  # Need shots/sequence/shot
                continue

            sequence_name = segments[1]  # e.g., 'sq010'
            if sequence_name not in sequences:
                sequences[sequence_name] = []
            sequences[sequence_name].append(entity)

        # Create sequence items
        for sequence_name in sorted(sequences.keys()):
            seq_item = QStandardItem(sequence_name)
            seq_item.setEditable(False)
            seq_item.setCheckable(True)
            seq_item.setCheckState(Qt.Unchecked)
            seq_item.setData(None, Qt.UserRole + 1)  # No URI for sequence

            # Add shots under sequence
            for entity in sorted(sequences[sequence_name], key=lambda e: str(e.uri)):
                # Only include leaf entities (actual shots)
                if len(entity.uri.segments) == 3:
                    shot_name = entity.uri.segments[2]  # e.g., 'sh010'
                    shot_item = QStandardItem(shot_name)
                    shot_item.setEditable(False)
                    shot_item.setCheckable(True)
                    shot_item.setCheckState(Qt.Unchecked)
                    shot_item.setData(str(entity.uri), Qt.UserRole + 1)
                    seq_item.appendRow(shot_item)

            # Only add sequence if it has shots
            if seq_item.rowCount() > 0:
                parent_item.appendRow(seq_item)

    def _on_item_changed(self, item):
        """Handle item check state change."""
        model = self._tree_view.model()
        model.blockSignals(True)

        # Update children
        self._update_children_check_state(item, item.checkState())

        # Update parent
        self._update_parent_check_state(item)

        model.blockSignals(False)
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

    def get_selected_shots(self) -> list[tuple[Uri, str]]:
        """Get selected shots as (uri, shot_name) tuples.

        Returns:
            List of (Uri, shot_name) tuples for checked shots
        """
        model = self._tree_view.model()
        if model is None:
            return []

        selected = []
        self._collect_checked_shots(model.invisibleRootItem(), selected)
        return selected

    def _collect_checked_shots(self, item, selected):
        """Recursively collect checked leaf shots."""
        for row in range(item.rowCount()):
            child = item.child(row)
            if child is None:
                continue

            if child.checkState() == Qt.Checked:
                uri_str = child.data(Qt.UserRole + 1)
                if uri_str:
                    # This is a shot (has URI)
                    uri = Uri.parse_unsafe(uri_str)
                    shot_name = uri.segments[-1] if uri.segments else uri_str
                    selected.append((uri, shot_name))
                else:
                    # Category - collect children
                    self._collect_checked_shots(child, selected)
            elif child.checkState() == Qt.PartiallyChecked:
                # Partially checked - collect checked children
                self._collect_checked_shots(child, selected)

    def get_selected_uris(self) -> set[str]:
        """Get URIs of all checked items for persistence."""
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
                self._collect_checked_uris(child, selected)

    def select_by_uris(self, uris: set[str]):
        """Select (check) tree items matching the given URIs."""
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
                child.setCheckState(Qt.Checked)
                self._update_children_check_state(child, Qt.Checked)
                self._update_parent_check_state(child)
            else:
                self._check_matching_uris(child, uris)

    def select_all(self):
        """Select all shots."""
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

    def clear_selection(self):
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


class RenderViewerDialog(QtWidgets.QDialog):
    """Dialog for selecting shots and render settings for DJV viewing."""

    def __init__(
        self,
        previous_selections: set[str] | None = None,
        parent=None
    ):
        super().__init__(parent)

        self._settings = QtWidgets.QApplication.instance().settings() if hasattr(
            QtWidgets.QApplication.instance(), 'settings'
        ) else None

        self._setup_ui()
        self._load_settings()

        # Restore previous selections
        if previous_selections:
            self._shot_tree.select_by_uris(previous_selections)

        # Connect signals
        self._shot_tree.selection_changed.connect(self._update_view_button)
        self._department_combo.currentIndexChanged.connect(self._on_department_changed)
        self._layer_combo.currentIndexChanged.connect(self._on_layer_changed)

        # Initial state
        self._update_view_button()

    def _setup_ui(self):
        self.setWindowTitle("View Renders in DJV")
        self.setMinimumSize(400, 500)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Shot tree
        self._shot_tree = ShotTreeWidget()
        layout.addWidget(self._shot_tree, 1)

        # Selection buttons
        selection_layout = QtWidgets.QHBoxLayout()
        selection_layout.setSpacing(5)

        select_all_btn = QtWidgets.QPushButton("Select All")
        select_all_btn.clicked.connect(self._shot_tree.select_all)
        selection_layout.addWidget(select_all_btn)

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self._shot_tree.clear_selection)
        selection_layout.addWidget(clear_btn)

        selection_layout.addStretch()
        layout.addLayout(selection_layout)

        # Separator
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(line)

        # Render settings
        settings_layout = QtWidgets.QFormLayout()
        settings_layout.setSpacing(8)

        # Render department
        self._department_combo = QtWidgets.QComboBox()
        self._populate_departments()
        settings_layout.addRow("Department:", self._department_combo)

        # Render layer
        self._layer_combo = QtWidgets.QComboBox()
        self._layer_combo.addItems(['slapcomp', 'beauty', 'all'])
        self._layer_combo.setCurrentText('slapcomp')
        settings_layout.addRow("Layer:", self._layer_combo)

        # AOV
        self._aov_combo = QtWidgets.QComboBox()
        self._aov_combo.addItems(['beauty', 'rgba', 'normal', 'albedo', 'all'])
        self._aov_combo.setCurrentText('beauty')
        settings_layout.addRow("AOV:", self._aov_combo)

        layout.addLayout(settings_layout)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(8)

        button_layout.addStretch()

        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        self._view_btn = QtWidgets.QPushButton("View in DJV")
        self._view_btn.setDefault(True)
        self._view_btn.clicked.connect(self.accept)
        button_layout.addWidget(self._view_btn)

        layout.addLayout(button_layout)

    def _populate_departments(self):
        """Populate the render department dropdown."""
        self._department_combo.clear()
        try:
            departments = list_departments('render')
            for dept in departments:
                self._department_combo.addItem(dept.name)
            # Default to 'render' if available
            idx = self._department_combo.findText('render')
            if idx >= 0:
                self._department_combo.setCurrentIndex(idx)
        except Exception as e:
            print(f"Error loading departments: {e}")
            self._department_combo.addItem('render')

    def _on_department_changed(self, index):
        """Handle department selection change."""
        # Could update layer/AOV options based on department
        pass

    def _on_layer_changed(self, index):
        """Handle layer selection change."""
        # Could update AOV options based on layer
        pass

    def _update_view_button(self):
        """Enable/disable view button based on selection."""
        selected = self._shot_tree.get_selected_shots()
        self._view_btn.setEnabled(len(selected) > 0)

    def _load_settings(self):
        """Load saved settings."""
        settings = QtWidgets.QSettings('Tumblehead', 'ProjectBrowser')

        # Restore dropdown selections
        dept = settings.value('render_viewer/department', 'render')
        idx = self._department_combo.findText(dept)
        if idx >= 0:
            self._department_combo.setCurrentIndex(idx)

        layer = settings.value('render_viewer/layer', 'slapcomp')
        idx = self._layer_combo.findText(layer)
        if idx >= 0:
            self._layer_combo.setCurrentIndex(idx)

        aov = settings.value('render_viewer/aov', 'beauty')
        idx = self._aov_combo.findText(aov)
        if idx >= 0:
            self._aov_combo.setCurrentIndex(idx)

    def _save_settings(self):
        """Save current settings."""
        settings = QtWidgets.QSettings('Tumblehead', 'ProjectBrowser')
        settings.setValue('render_viewer/department', self._department_combo.currentText())
        settings.setValue('render_viewer/layer', self._layer_combo.currentText())
        settings.setValue('render_viewer/aov', self._aov_combo.currentText())

    def accept(self):
        """Save settings and accept dialog."""
        self._save_settings()
        super().accept()

    def get_selected_shots(self) -> list[tuple[Uri, str]]:
        """Get selected shots as (uri, shot_name) tuples."""
        return self._shot_tree.get_selected_shots()

    def get_selected_uris(self) -> set[str]:
        """Get URIs of selected shots for persistence."""
        return self._shot_tree.get_selected_uris()

    def get_selected_department(self) -> str:
        """Get selected render department."""
        return self._department_combo.currentText()

    def get_selected_layer(self) -> str:
        """Get selected render layer."""
        return self._layer_combo.currentText()

    def get_selected_aov(self) -> str:
        """Get selected AOV."""
        return self._aov_combo.currentText()
