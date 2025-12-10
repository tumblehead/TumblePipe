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

        self._adapter.save_properties(self._uri, new_properties)

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
            EntityOpUpdate,
        )

        if not isinstance(change, UriChangeEntity):
            return

        entity_change = change.change
        parent_uri = entity_change.uri
        op = entity_change.op

        if isinstance(op, EntityOpInsert):
            new_uri = parent_uri / op.label
            self._adapter.save(new_uri, {'properties': {}, 'children': {}})
            self.data_changed.emit(new_uri)

        elif isinstance(op, EntityOpRemove):
            self._remove_entity_from_parent(parent_uri, op.label)
            self.data_changed.emit(parent_uri)

        elif isinstance(op, EntityOpUpdate):
            self._rename_entity(parent_uri, op.from_label, op.to_label)
            new_uri = parent_uri / op.to_label
            self.data_changed.emit(new_uri)

    def _remove_entity_from_parent(self, parent_uri: Uri, label: str):
        """Remove a child entity from its parent"""
        if parent_uri.segments:
            parent_data = self._adapter.lookup(parent_uri)
        else:
            parent_data = self._adapter.lookup_root(parent_uri.purpose)

        if parent_data and 'children' in parent_data:
            if label in parent_data['children']:
                del parent_data['children'][label]
                if parent_uri.segments:
                    self._adapter.save(parent_uri, parent_data)
                else:
                    self._adapter.save_root(parent_uri.purpose, parent_data)

    def _rename_entity(self, parent_uri: Uri, old_label: str, new_label: str):
        """Rename an entity by moving its data to a new key"""
        if parent_uri.segments:
            parent_data = self._adapter.lookup(parent_uri)
        else:
            parent_data = self._adapter.lookup_root(parent_uri.purpose)

        if parent_data and 'children' in parent_data:
            if old_label in parent_data['children']:
                parent_data['children'][new_label] = parent_data['children'].pop(old_label)
                if parent_uri.segments:
                    self._adapter.save(parent_uri, parent_data)
                else:
                    self._adapter.save_root(parent_uri.purpose, parent_data)
