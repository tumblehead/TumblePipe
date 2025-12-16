from qtpy.QtCore import Qt, Signal, QEvent
from qtpy.QtGui import QBrush
from qtpy import QtWidgets

from ..helpers import entity_uri_from_path, has_staged_export
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
    edit_scene_for_entity = Signal(object)  # entity_uri - opens scene editor for entity's scene
    view_latest_export = Signal(list)  # name_path - view latest export for shot

    def __init__(self, api, parent=None):
        super().__init__(parent)

        # Members
        self._api = api
        self._selection = None      # Index path of SELECTED item (Qt handles orange highlight)
        self._open_context = None   # Index path of OPEN workfile (manual purple brush)

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
        self.tree_view.setHeaderHidden(False)
        self.tree_view.setMinimumHeight(0)
        self.tree_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self.tree_view)

        # Set up context menu for right-click
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._right_clicked)

        # Install event filter to handle mouse press on non-selectable items
        self.tree_view.viewport().installEventFilter(self)

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

        # Clear existing selection and select the entire row
        selection_model.select(model_index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)

        # Make sure the selected item is visible
        self.tree_view.scrollTo(model_index)

        # Ensure tree view has focus so selection shows with focused styling
        self.tree_view.setFocus()

        return True

    def _uri_to_path(self, uri):
        """Convert Uri to name_path list for tree navigation"""
        if uri is None:
            return None
        if uri.purpose == 'groups':
            return ['groups'] + list(uri.segments)
        else:
            return list(uri.segments)

    def _apply_context_styling(self, index_path):
        """Apply purple brush styling for open context indicator"""
        parent_brush = QBrush("#5e4a8a", Qt.Dense6Pattern)
        child_brush = QBrush("#5e4a8a", Qt.Dense4Pattern)
        self._apply_brush_styling(index_path, parent_brush, child_brush)

    def _clear_context_styling(self, index_path):
        """Clear brush styling from a path"""
        cleared_brush = QBrush(Qt.NoBrush)
        self._apply_brush_styling(index_path, cleared_brush, cleared_brush)

    def _apply_brush_styling(self, index_path, parent_brush, child_brush):
        """Apply brush styling to all columns along an index path"""
        model = self.tree_view.model()
        if model is None or not index_path:
            return

        try:
            column_count = model.columnCount()

            def set_row_background(parent, row, brush):
                """Set background for all columns in a row."""
                for col in range(column_count):
                    if parent is None:
                        # Top-level item
                        col_item = model.item(row, col)
                    else:
                        # Child item
                        col_item = parent.child(row, col)
                    if col_item is not None:
                        col_item.setBackground(brush)

            # Style first row (top-level)
            set_row_background(None, index_path[0], parent_brush)

            # Navigate to get the parent for subsequent rows
            parent_item = model.item(index_path[0])
            if parent_item is None:
                return

            # Style intermediate rows (parent color)
            for row_index in index_path[1:-1]:
                set_row_background(parent_item, row_index, parent_brush)
                parent_item = parent_item.child(row_index)
                if parent_item is None:
                    return

            # Style final row (child color)
            if len(index_path) > 1:
                set_row_background(parent_item, index_path[-1], child_brush)
            else:
                # Single-level path - re-style with child brush
                set_row_background(None, index_path[0], child_brush)
        except (IndexError, AttributeError):
            pass

    def set_open_context(self, context_uri):
        """Set the OPEN workfile context (purple highlight)

        This indicates which entity's workfile is currently loaded in Houdini.

        Args:
            context_uri: Uri of entity with open workfile, or None to clear
        """
        # Convert Uri to index path
        context_path = self._uri_to_path(context_uri)
        context_index = self._index_path(context_path) if context_path else None

        # Skip if unchanged
        if context_index == self._open_context:
            return

        # Clear old context styling
        if self._open_context is not None:
            try:
                self._clear_context_styling(self._open_context)
            except (IndexError, AttributeError):
                pass

        # Update state
        self._open_context = context_index

        # Apply new context styling
        if context_index is not None:
            self._apply_context_styling(context_index)

    def get_open_context(self):
        """Get the currently open context as Uri"""
        if self._open_context is None:
            return None
        try:
            name_path = self._name_path(self._open_context)
            if name_path is None:
                return None
            return entity_uri_from_path(name_path)
        except (IndexError, AttributeError, ValueError):
            self._open_context = None
            return None

    def select(self, selection):
        """Set the SELECTED entity (Qt handles orange highlight via Houdini theme)

        This is purely for UI navigation - doesn't affect which file is open.

        Args:
            selection: Uri of entity to select, or None to clear
        """
        # Convert selection Uri to path
        selection_path = self._uri_to_path(selection)
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
            # Always clear Qt selection when changing
            self._clear_qt_selection()

            # Set the new Qt selection (Houdini theme provides orange highlight)
            if selection_index is not None:
                self._set_qt_selection(selection_index)

            # Update the current selection state
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

            # With multiple columns, we get multiple indices per row
            # Filter to only column 0 (Name column) which has the entity data
            name_indices = [idx for idx in indices if idx.column() == 0]

            if len(name_indices) == 1:
                index = name_indices[0]
                if index.isValid():
                    model = self.tree_view.model()
                    if model is not None:
                        item = model.itemFromIndex(index)
                        if item is not None:
                            name_path = self._get_path(item)
                            # Update internal selection state for get_selection()
                            self._selection = self._index_path(name_path)
                        else:
                            name_path = None
                            self._selection = None
                    else:
                        name_path = None
                        self._selection = None
                else:
                    name_path = None
                    self._selection = None
            else:
                name_path = None
                self._selection = None

            # Convert path to Uri and emit
            entity_uri = entity_uri_from_path(name_path) if name_path else None
            self.selection_changed.emit(entity_uri)
        except Exception as e:
            raise RuntimeError(f"Error in workspace selection change: {e}")

    def eventFilter(self, obj, event):
        # Intercept mouse press on non-selectable items to prevent selection highlighting
        if obj == self.tree_view.viewport() and event.type() == QEvent.MouseButtonPress:
            index = self.tree_view.indexAt(event.pos())
            if index.isValid():
                model = self.tree_view.model()
                name_index = index.sibling(index.row(), 0)
                item = model.itemFromIndex(name_index)
                if item is not None and not item.isSelectable():
                    # Handle expand/collapse directly and consume the event
                    if self.tree_view.isExpanded(name_index):
                        self.tree_view.collapse(name_index)
                    else:
                        self.tree_view.expand(name_index)
                    return True  # Consume the event, preventing selection
        return super().eventFilter(obj, event)

    def _right_clicked(self, point):
        index = self.tree_view.indexAt(point)
        if not index.isValid():
            return
        model = self.tree_view.model()

        # Get the item from column 0 (Name column) to get the path
        name_index = model.index(index.row(), 0, index.parent())
        item = model.itemFromIndex(name_index)
        name_path = self._get_path(item)

        # Check which column was clicked
        column = index.column()

        # Scene column (1) - show scene assignment menu
        if column == 1:
            self._show_scene_context_menu(point, name_path)
            return

        # Group column (2) - show group membership menu
        if column == 2:
            self._show_group_context_menu(point, name_path)
            return

        # Name column (0) - show standard menu
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

        # Add "View Latest Export" for shots only (check if export exists)
        view_export_action = None
        if len(name_path) >= 2 and name_path[0] == 'shots':
            entity_uri = entity_uri_from_path(name_path)
            if has_staged_export(entity_uri):
                view_export_action = menu.addAction("View Latest Export")
            else:
                no_export_action = menu.addAction("No Published Export")
                no_export_action.setEnabled(False)

        create_batch_action = menu.addAction("Add Entity")

        remove_entry_action = None
        if len(name_path) > 1:
            remove_entry_action = menu.addAction("Remove Entity")

        selected_action = menu.exec_(self.tree_view.mapToGlobal(point))
        if selected_action is None:
            return
        if selected_action == open_location_action:
            return self.open_location.emit(name_path)
        if view_export_action and selected_action == view_export_action:
            return self.view_latest_export.emit(name_path)
        if selected_action == create_batch_action:
            return self.create_batch_entry.emit(name_path)
        if selected_action == remove_entry_action:
            return self.remove_entry.emit(name_path)

    def _show_scene_context_menu(self, point, name_path):
        """Show context menu for Scene column."""
        # Only show for shots (not assets, kits, or groups)
        if len(name_path) < 2 or name_path[0] != 'shots':
            return

        entity_uri = entity_uri_from_path(name_path)
        if entity_uri is None:
            return

        menu = QtWidgets.QMenu()
        edit_action = menu.addAction("Edit Scene")

        # Check if export exists
        view_export_action = None
        if has_staged_export(entity_uri):
            view_export_action = menu.addAction("View Latest Export")
        else:
            no_export_action = menu.addAction("No Published Export")
            no_export_action.setEnabled(False)

        selected_action = menu.exec_(self.tree_view.mapToGlobal(point))
        if selected_action is None:
            return
        if selected_action == edit_action:
            self.edit_scene_for_entity.emit(entity_uri)
        elif view_export_action and selected_action == view_export_action:
            self.view_latest_export.emit(name_path)

    def _show_group_context_menu(self, point, name_path):
        """Show context menu for Group column."""
        # Only show for entities (shots or assets), not categories or groups
        if len(name_path) < 2:
            return
        if name_path[0] == 'groups':
            return

        # Build group path for edit_group signal
        # Format: ["groups", context, group_name] for existing groups
        # Or just the entity name_path for creating/editing
        context = name_path[0]  # 'shots' or 'assets'
        group_path = ['groups', context] + name_path[1:]

        menu = QtWidgets.QMenu()
        edit_action = menu.addAction("Edit Group")

        # Add "View Latest Export" for shots only (check if export exists)
        view_export_action = None
        if name_path[0] == 'shots':
            entity_uri = entity_uri_from_path(name_path)
            if has_staged_export(entity_uri):
                view_export_action = menu.addAction("View Latest Export")
            else:
                no_export_action = menu.addAction("No Published Export")
                no_export_action.setEnabled(False)

        selected_action = menu.exec_(self.tree_view.mapToGlobal(point))
        if selected_action is None:
            return
        if selected_action == edit_action:
            self.edit_group.emit(group_path)
        elif view_export_action and selected_action == view_export_action:
            self.view_latest_export.emit(name_path)

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
        # Store BOTH states as entity-based URIs (robust to model recreation)
        preserved_selection = self.get_selection()
        preserved_context = self.get_open_context()
        preserved_state = self._get_tree_state()

        # Clear both states during model transition
        self._selection = None
        self._open_context = None
        self._clear_qt_selection()

        # Block signals during model transition to prevent stale signal handling
        self.tree_view.blockSignals(True)
        try:
            # Safely disconnect and clean up old model
            old_model = self.tree_view.model()
            if old_model is not None:
                selection_model = self.tree_view.selectionModel()
                if selection_model is not None:
                    try:
                        selection_model.selectionChanged.disconnect(self._selection_changed)
                    except (TypeError, RuntimeError):
                        # Signal wasn't connected or already disconnected
                        pass
                    selection_model.clear()  # Clear any pending selections
                old_model.deleteLater()
                # Process events to ensure old model is fully cleaned up before configuring new header
                QtWidgets.QApplication.processEvents()

            # Create and set the new model
            new_model = _create_workspace_model(self._api)
            self.tree_view.setModel(new_model)
            self.tree_view.setUniformRowHeights(True)

            # Configure column sizing - all columns auto-fit to content, horizontal scroll if needed
            header = self.tree_view.header()
            if header is not None and header.count() > 0:
                header.setStretchLastSection(False)
                header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)  # Name auto-fits
                header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)  # Scene auto-fits
                header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)  # Group fills remaining space

            # Restore tree expansion state first (before selection)
            if preserved_state:
                try:
                    self._set_tree_state(preserved_state)
                except (AttributeError, IndexError, TypeError):
                    # State restoration failed, continue without it
                    pass

            # Restore open context first (purple brush)
            if preserved_context is not None:
                try:
                    self.set_open_context(preserved_context)
                except (AttributeError, ValueError):
                    self._open_context = None

            # Restore selection second (Qt handles orange highlight)
            if preserved_selection is not None:
                try:
                    self.select(preserved_selection)
                except (AttributeError, ValueError):
                    self._selection = None

            # Connect signals for new model
            new_selection_model = self.tree_view.selectionModel()
            if new_selection_model is not None:
                new_selection_model.selectionChanged.connect(self._selection_changed)
        finally:
            self.tree_view.blockSignals(False)