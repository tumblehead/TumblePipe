from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QBrush
from qtpy import QtWidgets

from ..helpers import entity_uri_from_path
from ..models import _create_workspace_model


class WorkspaceBrowser(QtWidgets.QWidget):
    selection_changed = Signal(object)
    open_location = Signal(object)
    create_entry = Signal(object)
    create_batch_entry = Signal(object)
    remove_entry = Signal(object)
    create_group = Signal(object)
    edit_group = Signal(object)
    delete_group = Signal(object)

    def __init__(self, api, parent=None):
        super().__init__(parent)

        # Members
        self._api = api
        self._selection = None

        # Settings
        self.setObjectName("WorkspaceBrowser")
        self.setMinimumHeight(0)

        # Set the layout
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        # Create the tree view navigation
        self.tree_view = QtWidgets.QTreeView()
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setMinimumHeight(0)
        layout.addWidget(self.tree_view)

        # Emit clicked signal
        self.tree_view.clicked.connect(self._left_clicked)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._right_clicked)

        # Initial update
        self.refresh()

    def _index_path(self, name_path):
        if name_path is None:
            return None

        model = self.tree_view.model()
        if model is None:
            return None

        try:
            # Find top level item (Assets, Shots, Kits)
            names = [model.item(index).text() for index in range(model.rowCount())]
            if name_path[0] not in names:
                return None
            index = names.index(name_path[0])
            item = model.item(index)
            index_path = [index]

            # Navigate through the path
            for name in name_path[1:]:
                if item.rowCount() == 0:
                    return None
                names = [item.child(index).text() for index in range(item.rowCount())]
                if name not in names:
                    return None
                index = names.index(name)
                index_path.append(index)
                item = item.child(index)
            return index_path
        except (ValueError, AttributeError, IndexError):
            # Path doesn't exist in current model
            return None

    def _name_path(self, index_path):
        if index_path is None:
            return None

        model = self.tree_view.model()
        if model is None:
            return None

        try:
            # Validate index path is within bounds
            if not index_path or index_path[0] >= model.rowCount():
                return None

            item = model.item(index_path[0])
            if item is None:
                return None

            name_path = [item.text()]

            # Navigate through the index path
            for index in index_path[1:]:
                if index >= item.rowCount():
                    return None
                item = item.child(index)
                if item is None:
                    return None
                name_path.append(item.text())
            return name_path
        except (AttributeError, IndexError):
            # Invalid index path for current model
            return None

    def _index_path_to_model_index(self, index_path):
        """Convert internal index_path to Qt QModelIndex"""
        if not index_path:
            return None

        model = self.tree_view.model()
        if model is None:
            return None

        try:
            # Start with top-level index
            if index_path[0] >= model.rowCount():
                return None

            model_index = model.index(index_path[0], 0)
            if not model_index.isValid():
                return None

            # Navigate through the path
            for child_index in index_path[1:]:
                if child_index >= model.rowCount(model_index):
                    return None
                model_index = model.index(child_index, 0, model_index)
                if not model_index.isValid():
                    return None

            return model_index
        except (AttributeError, IndexError):
            return None

    def _clear_qt_selection(self):
        """Clear Qt selection state safely"""
        selection_model = self.tree_view.selectionModel()
        if selection_model is not None:
            selection_model.clearSelection()

    def _set_qt_selection(self, index_path):
        """Set Qt selection state for given index_path"""
        if not index_path:
            return False

        model_index = self._index_path_to_model_index(index_path)
        if model_index is None or not model_index.isValid():
            return False

        selection_model = self.tree_view.selectionModel()
        if selection_model is None:
            return False

        # Import QItemSelectionModel flags
        from qtpy.QtCore import QItemSelectionModel

        # Clear existing selection and select the new item
        selection_model.select(model_index, QItemSelectionModel.ClearAndSelect)

        # Make sure the selected item is visible
        self.tree_view.scrollTo(model_index)

        return True

    def select(self, selection):
        cleared_brush = QBrush(Qt.NoBrush)
        parent_brush = QBrush("#5e4a8a", Qt.Dense6Pattern)
        child_brush = QBrush("#5e4a8a", Qt.Dense4Pattern)

        def _set_style(index_path, parent_brush, child_brush):
            model = self.tree_view.model()
            if model is None or not index_path:
                return

            try:
                item = model.item(index_path[0])
                if item is None:
                    return

                item.setBackground(parent_brush)
                for row_index in index_path[1:-1]:
                    item = item.child(row_index)
                    if item is None:
                        return
                    item.setBackground(parent_brush)

                if len(index_path) > 1:
                    item = item.child(index_path[-1])
                    if item is not None:
                        item.setBackground(child_brush)
                else:
                    item.setBackground(child_brush)
            except (IndexError, AttributeError):
                # Invalid path, skip styling
                pass

        # Convert selection Uri to path
        if selection is not None:
            if selection.purpose == 'groups':
                selection_path = ['groups'] + list(selection.segments)
            else:
                selection_path = list(selection.segments)
        else:
            selection_path = None
        selection_index = self._index_path(selection_path) if selection_path else None

        # Check if the index path is the same (avoid unnecessary work)
        if selection_index == self._selection:
            return

        # Block signals during programmatic selection change to prevent
        # _selection_changed from firing and emitting None to main.py
        selection_model = self.tree_view.selectionModel()
        if selection_model is not None:
            selection_model.blockSignals(True)

        try:
            # Clear the old selection (both visual and Qt selection)
            if self._selection is not None:
                try:
                    _set_style(self._selection, cleared_brush, cleared_brush)
                except (IndexError, AttributeError):
                    # Old selection is invalid, just clear the reference
                    pass

            # Always clear Qt selection when changing
            self._clear_qt_selection()

            # Set the new selection (both visual styling and Qt selection)
            if selection_index is not None:
                # Set visual styling
                _set_style(selection_index, parent_brush, child_brush)

                # Set Qt selection state
                qt_selection_success = self._set_qt_selection(selection_index)
                if not qt_selection_success:
                    # Qt selection failed, but we can still keep the visual styling
                    pass
            else:
                # selection_index is None, ensure Qt selection is cleared
                # (already cleared above, but this makes intent explicit)
                pass

            # Update the current selection
            self._selection = selection_index
        finally:
            # Re-enable signals
            if selection_model is not None:
                selection_model.blockSignals(False)

    def get_selection(self):
        if self._selection is None:
            return None
        try:
            name_path = self._name_path(self._selection)
            if name_path is None:
                return None
            return entity_uri_from_path(name_path)
        except (IndexError, AttributeError, ValueError):
            # Selection index is invalid (likely due to model recreation)
            # Clear invalid selection and return None
            self._selection = None
            return None

    def _get_path(self, item):
        name_path = []
        while item is not None:
            name_path.insert(0, item.text())
            item = item.parent()
        return name_path

    def _selection_changed(self, item_selection):
        """Handle selection changes with validation"""
        try:
            # Get the selected item
            indices = item_selection.indexes()
            if len(indices) == 1:
                index = indices[0]
                if index.isValid():
                    model = self.tree_view.model()
                    if model is not None:
                        item = model.itemFromIndex(index)
                        if item is not None:
                            name_path = self._get_path(item)
                        else:
                            name_path = None
                    else:
                        name_path = None
                else:
                    name_path = None
            else:
                name_path = None

            # Convert path to Uri and emit
            entity_uri = entity_uri_from_path(name_path) if name_path else None
            self.selection_changed.emit(entity_uri)
        except Exception as e:
            raise RuntimeError(f"Error in workspace selection change: {e}")

    def _left_clicked(self, index):
        model = self.tree_view.model()
        item = model.itemFromIndex(index)
        if item.isSelectable():
            return
        if self.tree_view.isExpanded(index):
            self.tree_view.collapse(index)
        else:
            self.tree_view.expand(index)

    def _right_clicked(self, point):
        index = self.tree_view.indexAt(point)
        if not index.isValid():
            return
        model = self.tree_view.model()
        item = model.itemFromIndex(index)
        name_path = self._get_path(item)

        menu = QtWidgets.QMenu()

        if len(name_path) >= 1 and name_path[0] == "groups":
            if len(name_path) <= 2:
                new_group_action = menu.addAction("New Group")
                selected_action = menu.exec_(self.tree_view.mapToGlobal(point))
                if selected_action == new_group_action:
                    self.create_group.emit(name_path)
            else:
                edit_group_action = menu.addAction("Edit Group")
                delete_group_action = menu.addAction("Delete Group")
                selected_action = menu.exec_(self.tree_view.mapToGlobal(point))
                if selected_action is None:
                    return
                if selected_action == edit_group_action:
                    self.edit_group.emit(name_path)
                elif selected_action == delete_group_action:
                    self.delete_group.emit(name_path)
            return

        open_location_action = menu.addAction("Open Location")
        create_batch_action = menu.addAction("Add Entity")

        remove_entry_action = None
        if len(name_path) > 1:
            remove_entry_action = menu.addAction("Remove Entity")

        selected_action = menu.exec_(self.tree_view.mapToGlobal(point))
        if selected_action is None:
            return
        if selected_action == open_location_action:
            return self.open_location.emit(name_path)
        if selected_action == create_batch_action:
            return self.create_batch_entry.emit(name_path)
        if selected_action == remove_entry_action:
            return self.remove_entry.emit(name_path)

    def _get_tree_state(self):
        # Recursive visit function
        def _visit(item):
            name = item.text()
            expanded = self.tree_view.isExpanded(item.index())
            children = dict(
                _visit(item.child(row_index)) for row_index in range(item.rowCount())
            )
            return name, dict(expanded=expanded, children=children)

        # Get the tree state
        model = self.tree_view.model()
        if model is None:
            return dict()
        return dict(
            _visit(model.item(row_index)) for row_index in range(model.rowCount())
        )

    def _set_tree_state(self, state):
        # Recursive visit function
        def _visit(item, data):
            self.tree_view.setExpanded(item.index(), data["expanded"])
            for row_index in range(item.rowCount()):
                child_item = item.child(row_index)
                child_name = child_item.text()
                child_data = data["children"].get(child_name)
                if child_data is None:
                    continue
                _visit(child_item, child_data)

        # Set the tree state
        model = self.tree_view.model()
        for row_index in range(model.rowCount()):
            item = model.item(row_index)
            item_name = item.text()
            item_data = state.get(item_name)
            if item_data is None:
                continue
            _visit(item, item_data)

    def refresh(self):
        # Store the current selection (entity-based, more robust than index-based)
        preserved_selection = self.get_selection()
        preserved_state = self._get_tree_state()

        # Clear current selection to prevent issues during model transition
        self._selection = None
        self._clear_qt_selection()

        # Safely disconnect old model
        old_model = self.tree_view.model()
        if old_model is not None:
            selection_model = self.tree_view.selectionModel()
            if selection_model is not None:
                try:
                    selection_model.selectionChanged.disconnect(self._selection_changed)
                except (TypeError, RuntimeError):
                    # Signal wasn't connected or already disconnected
                    pass
            old_model.deleteLater()

        # Create and set the new model
        new_model = _create_workspace_model(self._api)
        self.tree_view.setModel(new_model)
        self.tree_view.setUniformRowHeights(True)

        # Restore tree expansion state first (before selection)
        if preserved_state:
            try:
                self._set_tree_state(preserved_state)
            except (AttributeError, IndexError, TypeError):
                # State restoration failed, continue without it
                pass

        # Restore selection (entity-based restoration is more reliable)
        if preserved_selection is not None:
            try:
                self.select(preserved_selection)
            except (AttributeError, ValueError):
                # Selection restoration failed, clear selection
                self._selection = None

        # Connect signals for new model
        new_selection_model = self.tree_view.selectionModel()
        if new_selection_model is not None:
            new_selection_model.selectionChanged.connect(self._selection_changed)