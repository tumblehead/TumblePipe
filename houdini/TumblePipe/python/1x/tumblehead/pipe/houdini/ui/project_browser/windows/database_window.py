from copy import deepcopy

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from tumblehead.util.uri import Uri

from ..utils.database_adapter import DatabaseAdapter
from ..views.json_editor import JsonView
from ..views.database_uri_view import DatabaseUriView


class DatabaseWindow(QMainWindow):
    """Non-modal database editor window for project_browser integration"""

    data_changed = Signal(object)
    window_closed = Signal()

    def __init__(self, api, parent=None):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.Window |
            Qt.WindowCloseButtonHint |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint
        )

        self.setWindowTitle("Database Editor")
        self.resize(1000, 700)

        self._api = api
        self._adapter = DatabaseAdapter(api)
        self._uri = None
        self._original_properties = None
        self._inherited_properties = {}

        self._build_ui()

    def _build_ui(self):
        central_widget = QSplitter(Qt.Horizontal, self)
        self.setCentralWidget(central_widget)

        self._uri_view = DatabaseUriView(self._adapter, self)
        self._uri_view.selected.connect(self._on_entity_selected)
        self._uri_view.change.connect(self._on_uri_change)
        central_widget.addWidget(self._uri_view)

        json_panel = QWidget(self)
        json_layout = QVBoxLayout(json_panel)
        json_layout.setContentsMargins(0, 0, 0, 0)
        json_layout.setSpacing(0)
        central_widget.addWidget(json_panel)

        self._json_view = JsonView(parent=self)
        self._json_view.setEnabled(False)
        self._json_view.change.connect(self._on_json_change)
        json_layout.addWidget(self._json_view)

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(5, 5, 5, 5)

        self._discard_button = QPushButton("Discard Changes")
        self._discard_button.clicked.connect(self._on_discard_changes)
        self._discard_button.setEnabled(False)
        button_layout.addWidget(self._discard_button)

        button_layout.addStretch(1)

        self._save_button = QPushButton("Save Changes")
        self._save_button.clicked.connect(self._on_save_changes)
        self._save_button.setEnabled(False)
        button_layout.addWidget(self._save_button)

        json_layout.addLayout(button_layout)

        central_widget.setSizes([300, 700])

    def _on_entity_selected(self, uri: Uri | None):
        if self._uri is not None and self._json_view.has_change():
            result = QMessageBox.question(
                self, "Unsaved Changes",
                f"You have unsaved changes for:\n    - {self._uri}\n\nDo you want to save them?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )
            if result == QMessageBox.Cancel:
                self._uri_view.set_selected(self._uri)
                return
            elif result == QMessageBox.Save:
                self._on_save_changes()
            else:
                self._json_view.discard_changes()

        if uri is None:
            self._json_view.set_value(dict())
            self._json_view.setEnabled(False)
            self._original_properties = None
            self._inherited_properties = {}
        else:
            if uri.segments:
                value = self._adapter.lookup_properties(uri)
                inherited = self._adapter.get_inherited_properties(uri)
            else:
                value = self._adapter.lookup_root_properties(uri.purpose)
                inherited = self._adapter.get_root_inherited_properties(uri.purpose)
            self._original_properties = deepcopy(value)
            self._inherited_properties = inherited
            self._json_view.set_value(value, inherited_data=inherited)
            self._json_view.setEnabled(True)

        self._uri = uri
        self._update_buttons()

    def _on_json_change(self, _):
        self._update_buttons()

    def _update_buttons(self):
        has_change = self._json_view.has_change()
        self._discard_button.setEnabled(has_change)
        self._save_button.setEnabled(has_change)
        # Update window title with dirty indicator
        title = "Database Editor *" if has_change else "Database Editor"
        self.setWindowTitle(title)

    def _on_discard_changes(self):
        if self._uri is None:
            return
        result = QMessageBox.question(
            self, "Discard Changes",
            f'Are you sure you want to discard all changes to "{self._uri}"?'
        )
        if result != QMessageBox.Yes:
            return
        self._json_view.discard_changes()
        self._update_buttons()

    def _on_save_changes(self):
        if self._uri is None:
            return

        new_properties = self._json_view.to_json()

        if self._uri.purpose == 'schemas' and self._uri.segments:
            from ..utils.schema_migration import build_migration, apply_migration
            from ..dialogs.schema_migration_dialog import SchemaMigrationDialog

            old_properties = self._original_properties or {}

            migration = build_migration(
                self._adapter._config.cache,
                self._uri,
                old_properties,
                new_properties
            )

            if migration and (migration.additions or migration.removals):
                dialog = SchemaMigrationDialog(migration, self)
                if dialog.exec_() != SchemaMigrationDialog.Accepted:
                    return

                if dialog.should_migrate():
                    apply_migration(self._adapter, migration)

        # Try to save with merge conflict detection
        success, msg = self._adapter.save_properties_with_merge(self._uri, new_properties)
        if not success:
            if msg == "conflict":
                if not self._handle_properties_merge_conflict(new_properties):
                    return
            else:
                QMessageBox.warning(self, "Save Error", f"Failed to save: {msg}")
                return

        if self._uri.segments:
            value = self._adapter.lookup_properties(self._uri)
            inherited = self._adapter.get_inherited_properties(self._uri)
        else:
            value = self._adapter.lookup_root_properties(self._uri.purpose)
            inherited = self._adapter.get_root_inherited_properties(self._uri.purpose)
        self._original_properties = deepcopy(value)
        self._inherited_properties = inherited
        self._json_view.set_value(value, inherited_data=inherited, preserve_state=True)
        self._update_buttons()

        # Store URI before refresh (refresh may trigger selection changes that set self._uri to None)
        saved_uri = self._uri

        self._uri_view.refresh()

        self.data_changed.emit(saved_uri)

    def _handle_properties_merge_conflict(self, new_properties: dict) -> bool:
        """Handle merge conflict for properties save. Returns True if saved."""
        result = QMessageBox.question(
            self, "Merge Conflict",
            "The database was modified externally and conflicts with your changes.\n\n"
            "• 'Save' - Keep your changes, discard external changes\n"
            "• 'Discard' - Reload from disk, lose your changes",
            QMessageBox.Save | QMessageBox.Discard,
            QMessageBox.Discard
        )
        if result == QMessageBox.Save:
            self._adapter.force_save_properties(self._uri, new_properties)
            return True
        else:
            self._reload_from_disk()
            return False

    def closeEvent(self, event):
        if self._uri is not None and self._json_view.has_change():
            result = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Do you want to save them before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )
            if result == QMessageBox.Cancel:
                event.ignore()
                return
            elif result == QMessageBox.Save:
                self._on_save_changes()

        self.window_closed.emit()
        event.accept()

    def _on_uri_change(self, change):
        """Handle entity add/remove/rename in URI tree"""
        from ..views.database_uri_view import (
            UriChangeEntity,
            EntityOpInsert,
            EntityOpRemove,
            EntityOpReorder,
            EntityOpUpdate,
        )

        if not isinstance(change, UriChangeEntity):
            return

        entity_change = change.change
        parent_uri = entity_change.uri
        op = entity_change.op

        if isinstance(op, EntityOpInsert):
            new_uri = parent_uri / op.label
            new_data = {'properties': {}, 'children': {}}
            if self._save_with_conflict_handling(new_uri, new_data):
                self.data_changed.emit(new_uri)

        elif isinstance(op, EntityOpRemove):
            if self._remove_entity_from_parent(parent_uri, op.label):
                self.data_changed.emit(parent_uri)

        elif isinstance(op, EntityOpUpdate):
            if self._rename_entity(parent_uri, op.from_label, op.to_label):
                new_uri = parent_uri / op.to_label
                self.data_changed.emit(new_uri)

        elif isinstance(op, EntityOpReorder):
            if self._reorder_entity_in_parent(parent_uri, op.from_label, op.to_label):
                self.data_changed.emit(parent_uri)

    def _remove_entity_from_parent(self, parent_uri: Uri, label: str) -> bool:
        """Remove a child entity from its parent. Returns True if saved."""
        if parent_uri.segments:
            parent_data = self._adapter.lookup(parent_uri)
        else:
            parent_data = self._adapter.lookup_root(parent_uri.purpose)

        if parent_data and 'children' in parent_data:
            if label in parent_data['children']:
                del parent_data['children'][label]
                return self._save_with_conflict_handling(parent_uri, parent_data)
        return False

    def _rename_entity(self, parent_uri: Uri, old_label: str, new_label: str) -> bool:
        """Rename an entity by moving its data to a new key. Returns True if saved."""
        if parent_uri.segments:
            parent_data = self._adapter.lookup(parent_uri)
        else:
            parent_data = self._adapter.lookup_root(parent_uri.purpose)

        if parent_data and 'children' in parent_data:
            if old_label in parent_data['children']:
                parent_data['children'][new_label] = parent_data['children'].pop(old_label)
                return self._save_with_conflict_handling(parent_uri, parent_data)
        return False

    def _reorder_entity_in_parent(self, parent_uri: Uri, from_label: str, to_label: str) -> bool:
        """Reorder entity from_label to the position of to_label in parent's children. Returns True if saved."""
        if parent_uri.segments:
            parent_data = self._adapter.lookup(parent_uri)
        else:
            parent_data = self._adapter.lookup_root(parent_uri.purpose)

        if parent_data and 'children' in parent_data:
            children = parent_data['children']
            if from_label in children and to_label in children:
                # Get list of keys in current order
                keys = list(children.keys())
                from_idx = keys.index(from_label)
                to_idx = keys.index(to_label)

                # Remove from_label and insert at to_label position
                keys.pop(from_idx)
                keys.insert(to_idx, from_label)

                # Rebuild dict in new order
                parent_data['children'] = {k: children[k] for k in keys}

                return self._save_with_conflict_handling(parent_uri, parent_data)
        return False

    def _save_with_conflict_handling(self, uri: Uri, data: dict) -> bool:
        """Save data with merge conflict handling. Returns True if saved."""
        if uri.segments:
            success, msg = self._adapter.save_with_merge(uri, data)
        else:
            success, msg = self._adapter.save_root_with_merge(uri.purpose, data)

        if success:
            return True

        if msg == "conflict":
            return self._handle_merge_conflict(uri, data)

        # Other error
        QMessageBox.warning(self, "Save Error", f"Failed to save: {msg}")
        return False

    def _handle_merge_conflict(self, uri: Uri, data: dict) -> bool:
        """Handle merge conflict with user dialog. Returns True if saved."""
        result = QMessageBox.question(
            self, "Merge Conflict",
            "The database was modified externally and conflicts with your changes.\n\n"
            "• 'Save' - Keep your changes, discard external changes\n"
            "• 'Discard' - Reload from disk, lose your changes",
            QMessageBox.Save | QMessageBox.Discard,
            QMessageBox.Discard
        )
        if result == QMessageBox.Save:
            if uri.segments:
                self._adapter.force_save(uri, data)
            else:
                self._adapter.force_save_root(uri.purpose, data)
            return True
        else:
            self._reload_from_disk()
            return False

    def _reload_from_disk(self):
        """Reload all data from disk, discarding local changes."""
        self._adapter.reload_cache()
        self._uri_view.refresh()
        # Re-select current entity to refresh JSON view
        if self._uri is not None:
            self._on_entity_selected(self._uri)
