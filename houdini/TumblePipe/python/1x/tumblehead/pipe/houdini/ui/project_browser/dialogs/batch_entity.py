from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal

from tumblehead.config.schema import Schema, apply_defaults
from tumblehead.util.uri import Uri
from tumblehead.pipe.houdini.ui.project_browser.models.batch_entity import BatchEntityTableModel


class BatchEntityDialog(QtWidgets.QDialog):
    """Dialog for creating multiple entities at once with schema-aware property editing"""

    entities_created = Signal(list)

    def __init__(self, api, parent_path: list[str], purpose: str = 'entity', parent=None):
        super().__init__(parent)
        self._api = api
        self._parent_path = parent_path
        self._purpose = purpose

        # Build parent entity URI
        self._parent_uri = Uri.parse_unsafe(f'{purpose}:/{"/".join(parent_path)}')

        # Get parent's schema and child schemas
        # For root entities (shots, assets), look up root schema directly
        # For child entities, get schema from entity's _schema property
        self._parent_schema = None
        self._child_schemas = []

        if len(parent_path) == 1:
            # Root entity (e.g., "shots" or "assets") - look up root schema
            root_schema_uri = Uri.parse_unsafe(f'schemas:/{purpose}/{parent_path[0]}')
            self._parent_schema = api.config.get_schema(root_schema_uri)
            if self._parent_schema is not None:
                self._child_schemas = api.config.get_child_schemas(root_schema_uri)
        else:
            # Child entity - get schema from entity properties
            try:
                self._parent_schema = api.config.get_entity_schema(self._parent_uri)
                if self._parent_schema is not None:
                    self._child_schemas = api.config.get_child_schemas(self._parent_schema.uri)
            except NotImplementedError:
                # Fallback: try to infer schema from path structure
                pass

        # Selected schema
        self._selected_schema = None
        if len(self._child_schemas) == 1:
            self._selected_schema = self._child_schemas[0]
        elif len(self._child_schemas) > 1:
            self._selected_schema = self._child_schemas[0]

        # Create model
        self._model = BatchEntityTableModel()
        if self._selected_schema is not None:
            self._model.set_schema(self._selected_schema)
        self._model.dataChanged.connect(lambda *_: self._update_status())

        # Setup UI
        self.setWindowTitle("Add Entities")
        self.resize(700, 500)
        self._create_ui()

        # Add initial row
        self._model.add_row()
        self._update_status()

    def _create_ui(self):
        """Create the dialog UI"""
        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)

        # Header section
        header = self._create_header()
        layout.addWidget(header)

        # Toolbar
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        # Table
        self._table_view = QtWidgets.QTableView()
        self._table_view.setModel(self._model)
        self._table_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table_view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._table_view.horizontalHeader().setStretchLastSection(True)
        self._table_view.verticalHeader().setVisible(False)

        # Resize columns to fit content
        self._table_view.resizeColumnsToContents()

        layout.addWidget(self._table_view)

        # Status label
        self._status_label = QtWidgets.QLabel("0 entities ready")
        self._status_label.setStyleSheet("color: gray;")
        layout.addWidget(self._status_label)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()

        self._create_button = QtWidgets.QPushButton("Create")
        self._create_button.clicked.connect(self._create_entities)
        self._create_button.setEnabled(False)
        button_layout.addWidget(self._create_button)

        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)

    def _create_header(self):
        """Create header section with parent URI and schema selector"""
        header = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout()
        layout.setContentsMargins(0, 0, 0, 10)
        header.setLayout(layout)

        # Parent URI display
        parent_label = QtWidgets.QLabel(str(self._parent_uri))
        parent_label.setStyleSheet("color: #919191;")
        layout.addRow("Parent:", parent_label)

        # Schema selector (only if multiple child schemas)
        if len(self._child_schemas) > 1:
            self._schema_combo = QtWidgets.QComboBox()
            for schema in self._child_schemas:
                self._schema_combo.addItem(schema.name, schema)
            self._schema_combo.currentIndexChanged.connect(self._on_schema_changed)
            layout.addRow("Entity Type:", self._schema_combo)
        elif len(self._child_schemas) == 1:
            schema_label = QtWidgets.QLabel(self._child_schemas[0].name)
            layout.addRow("Entity Type:", schema_label)
        else:
            schema_label = QtWidgets.QLabel("(no schema)")
            schema_label.setStyleSheet("color: #919191; font-style: italic;")
            layout.addRow("Entity Type:", schema_label)

        return header

    def _create_toolbar(self):
        """Create toolbar with add/remove buttons"""
        toolbar = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar.setLayout(layout)

        # Add Row button
        add_button = QtWidgets.QPushButton("+ Add Row")
        add_button.clicked.connect(self._add_row)
        layout.addWidget(add_button)

        # Remove Selected button
        remove_button = QtWidgets.QPushButton("- Remove Selected")
        remove_button.clicked.connect(self._remove_selected)
        layout.addWidget(remove_button)

        layout.addStretch()

        return toolbar

    def _on_schema_changed(self, index: int):
        """Handle schema selector change"""
        if index < 0 or index >= len(self._child_schemas):
            return

        # Confirm if there are existing rows with data
        if self._model.rowCount() > 0:
            result = QtWidgets.QMessageBox.question(
                self,
                "Change Entity Type",
                "Changing the entity type will clear all rows. Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes:
                # Revert combo box
                for i, schema in enumerate(self._child_schemas):
                    if schema == self._selected_schema:
                        self._schema_combo.blockSignals(True)
                        self._schema_combo.setCurrentIndex(i)
                        self._schema_combo.blockSignals(False)
                        break
                return

        self._selected_schema = self._child_schemas[index]
        self._model.set_schema(self._selected_schema)
        self._model.add_row()
        self._table_view.resizeColumnsToContents()
        self._update_status()

    def _add_row(self):
        """Add a new row to the table"""
        self._model.add_row()
        self._update_status()

    def _remove_selected(self):
        """Remove selected rows from the table"""
        selection_model = self._table_view.selectionModel()
        if not selection_model.hasSelection():
            return

        indices = sorted(set(index.row() for index in selection_model.selectedIndexes()), reverse=True)
        self._model.remove_rows(indices)
        self._update_status()

    def _update_status(self):
        """Update status label and Create button state"""
        if self._selected_schema is None:
            self._status_label.setText("No schema available - cannot create entities")
            self._status_label.setStyleSheet("color: red; font-weight: bold;")
            self._create_button.setEnabled(False)
            return

        total, valid, invalid = self._model.get_row_counts()

        if valid == 0:
            self._status_label.setText("Enter entity names to create")
            self._status_label.setStyleSheet("color: gray;")
            self._create_button.setEnabled(False)
        elif invalid > 0:
            self._status_label.setText(f"{valid} entities ready, {invalid} with errors")
            self._status_label.setStyleSheet("color: orange; font-weight: bold;")
            self._create_button.setEnabled(True)
        else:
            self._status_label.setText(f"{valid} entities ready to create")
            self._status_label.setStyleSheet("color: green; font-weight: bold;")
            self._create_button.setEnabled(True)

    def _create_entities(self):
        """Create all valid entities"""
        if self._selected_schema is None:
            QtWidgets.QMessageBox.warning(
                self,
                "No Schema Selected",
                "Cannot create entities without a schema."
            )
            return

        valid_rows = self._model.get_valid_rows()
        if not valid_rows:
            return

        created_uris = []
        errors = []

        for row in valid_rows:
            name = row['name']
            properties = row['properties']

            # Build entity URI
            entity_uri = self._parent_uri / name

            # Apply schema defaults
            properties = apply_defaults(self._selected_schema, properties)

            try:
                self._api.config.add_entity(entity_uri, properties, self._selected_schema.uri)
                created_uris.append(entity_uri)
            except Exception as e:
                errors.append(f"{name}: {str(e)}")

        if errors:
            QtWidgets.QMessageBox.warning(
                self,
                "Some Entities Failed",
                f"Created {len(created_uris)} entities.\n\nErrors:\n" + "\n".join(errors)
            )

        if created_uris:
            self.entities_created.emit(created_uris)

        self.accept()
