"""Dialog for editing submission column configuration."""

from typing import Optional
import copy

from qtpy import QtWidgets
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QWidget,
    QLabel,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
    QTabWidget,
    QMessageBox,
    QSplitter,
    QFrame,
    QScrollArea,
    QSizePolicy,
)

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

from tumblehead.pipe.houdini.ui.project_browser.models.job_schemas import (
    get_available_choice_functions,
    get_available_default_functions,
)


# Column type options
COLUMN_TYPES = ['integer', 'float', 'combo', 'boolean', 'string', 'multi_select']


class ColumnListWidget(QWidget):
    """Widget for displaying and managing a list of columns with reorder/delete."""

    selection_changed = Signal(int)  # Emits selected index, -1 if none
    columns_changed = Signal()  # Emits when columns are added/removed/reordered

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Column list
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

        # Buttons row
        btn_layout = QHBoxLayout()

        self._up_btn = QPushButton("Up")
        self._up_btn.setFixedWidth(50)
        self._up_btn.clicked.connect(self._move_up)
        btn_layout.addWidget(self._up_btn)

        self._down_btn = QPushButton("Down")
        self._down_btn.setFixedWidth(50)
        self._down_btn.clicked.connect(self._move_down)
        btn_layout.addWidget(self._down_btn)

        btn_layout.addStretch()

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setFixedWidth(60)
        self._delete_btn.clicked.connect(self._delete_column)
        btn_layout.addWidget(self._delete_btn)

        layout.addLayout(btn_layout)

        self._update_buttons()

    def _on_selection_changed(self, row: int):
        self._update_buttons()
        self.selection_changed.emit(row)

    def _update_buttons(self):
        row = self._list.currentRow()
        count = self._list.count()
        has_selection = row >= 0

        self._up_btn.setEnabled(has_selection and row > 0)
        self._down_btn.setEnabled(has_selection and row < count - 1)
        self._delete_btn.setEnabled(has_selection)

    def _move_up(self):
        row = self._list.currentRow()
        if row > 0:
            # Swap in data
            self._columns[row], self._columns[row - 1] = self._columns[row - 1], self._columns[row]
            # Refresh list
            self._refresh_list()
            self._list.setCurrentRow(row - 1)
            self.columns_changed.emit()

    def _move_down(self):
        row = self._list.currentRow()
        if row < len(self._columns) - 1:
            # Swap in data
            self._columns[row], self._columns[row + 1] = self._columns[row + 1], self._columns[row]
            # Refresh list
            self._refresh_list()
            self._list.setCurrentRow(row + 1)
            self.columns_changed.emit()

    def _delete_column(self):
        row = self._list.currentRow()
        if row >= 0:
            column = self._columns[row]
            reply = QMessageBox.question(
                self,
                "Delete Column",
                f"Delete column '{column.get('label', column.get('key', 'Unknown'))}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                del self._columns[row]
                self._refresh_list()
                # Select next item or previous if at end
                if self._columns:
                    new_row = min(row, len(self._columns) - 1)
                    self._list.setCurrentRow(new_row)
                self.columns_changed.emit()

    def _refresh_list(self):
        """Refresh the list widget from column data."""
        current_row = self._list.currentRow()
        self._list.clear()
        for col in self._columns:
            label = col.get('label', col.get('key', 'Unknown'))
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, col.get('key'))
            self._list.addItem(item)
        # Restore selection
        if current_row >= 0 and current_row < self._list.count():
            self._list.setCurrentRow(current_row)
        self._update_buttons()

    def set_columns(self, columns: list):
        """Set the columns to display."""
        self._columns = columns
        self._refresh_list()
        if self._columns:
            self._list.setCurrentRow(0)

    def get_columns(self) -> list:
        """Get the current columns."""
        return self._columns

    def get_selected_index(self) -> int:
        """Get currently selected index."""
        return self._list.currentRow()

    def get_selected_column(self) -> Optional[dict]:
        """Get currently selected column dict."""
        row = self._list.currentRow()
        if row >= 0 and row < len(self._columns):
            return self._columns[row]
        return None

    def update_selected_label(self, label: str):
        """Update the label of the selected item in the list."""
        row = self._list.currentRow()
        if row >= 0:
            item = self._list.item(row)
            if item:
                item.setText(label)

    def add_column(self, column: dict):
        """Add a new column."""
        self._columns.append(column)
        self._refresh_list()
        self._list.setCurrentRow(len(self._columns) - 1)
        self.columns_changed.emit()


class ColumnPropertyPanel(QWidget):
    """Panel for editing properties of a single column."""

    property_changed = Signal()  # Emits when any property changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._column = None
        self._updating = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area for properties
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_content = QWidget()
        form = QFormLayout(scroll_content)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        # Basic properties
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("unique_identifier")
        self._key_edit.textChanged.connect(self._on_property_changed)
        form.addRow("Key:", self._key_edit)

        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("Display Name")
        self._label_edit.textChanged.connect(self._on_label_changed)
        form.addRow("Label:", self._label_edit)

        self._type_combo = QComboBox()
        self._type_combo.addItems(COLUMN_TYPES)
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        form.addRow("Type:", self._type_combo)

        self._width_spin = QSpinBox()
        self._width_spin.setRange(30, 300)
        self._width_spin.setValue(100)
        self._width_spin.valueChanged.connect(self._on_property_changed)
        form.addRow("Width:", self._width_spin)

        self._tooltip_edit = QLineEdit()
        self._tooltip_edit.setPlaceholderText("Tooltip text")
        self._tooltip_edit.textChanged.connect(self._on_property_changed)
        form.addRow("Tooltip:", self._tooltip_edit)

        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        form.addRow(sep1)

        # Entity mapping
        mapping_label = QLabel("Entity Mapping")
        mapping_label.setStyleSheet("font-weight: bold;")
        form.addRow(mapping_label)

        self._property_path_edit = QLineEdit()
        self._property_path_edit.setPlaceholderText("e.g., farm.priority")
        self._property_path_edit.textChanged.connect(self._on_property_changed)
        form.addRow("Property Path:", self._property_path_edit)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        form.addRow(sep2)

        # Type-specific options group
        type_label = QLabel("Type Options")
        type_label.setStyleSheet("font-weight: bold;")
        form.addRow(type_label)

        # Numeric options
        self._min_spin = QSpinBox()
        self._min_spin.setRange(-999999, 999999)
        self._min_spin.setValue(0)
        self._min_spin.valueChanged.connect(self._on_property_changed)
        self._min_row = form.addRow("Min:", self._min_spin)

        self._max_spin = QSpinBox()
        self._max_spin.setRange(-999999, 999999)
        self._max_spin.setValue(100)
        self._max_spin.valueChanged.connect(self._on_property_changed)
        self._max_row = form.addRow("Max:", self._max_spin)

        self._step_spin = QSpinBox()
        self._step_spin.setRange(1, 100)
        self._step_spin.setValue(1)
        self._step_spin.valueChanged.connect(self._on_property_changed)
        self._step_row = form.addRow("Step:", self._step_spin)

        # Combo/choices options
        self._choices_func_combo = QComboBox()
        self._choices_func_combo.addItem("")  # Empty option
        self._choices_func_combo.addItems(get_available_choice_functions())
        self._choices_func_combo.currentTextChanged.connect(self._on_property_changed)
        self._choices_func_row = form.addRow("Choices Func:", self._choices_func_combo)

        self._default_func_combo = QComboBox()
        self._default_func_combo.addItem("")  # Empty option
        self._default_func_combo.addItems(get_available_default_functions())
        self._default_func_combo.currentTextChanged.connect(self._on_property_changed)
        self._default_func_row = form.addRow("Default Func:", self._default_func_combo)

        self._choices_edit = QLineEdit()
        self._choices_edit.setPlaceholderText("option1, option2, option3")
        self._choices_edit.textChanged.connect(self._on_property_changed)
        self._choices_row = form.addRow("Static Choices:", self._choices_edit)

        # Multi-select option
        self._per_entity_check = QCheckBox()
        self._per_entity_check.stateChanged.connect(self._on_property_changed)
        self._per_entity_row = form.addRow("Per-Entity Choices:", self._per_entity_check)

        # Boolean default
        self._default_bool_check = QCheckBox()
        self._default_bool_check.stateChanged.connect(self._on_property_changed)
        self._default_bool_row = form.addRow("Default:", self._default_bool_check)

        # Integer/float default
        self._default_num_spin = QSpinBox()
        self._default_num_spin.setRange(-999999, 999999)
        self._default_num_spin.valueChanged.connect(self._on_property_changed)
        self._default_num_row = form.addRow("Default:", self._default_num_spin)

        # Separator
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        form.addRow(sep3)

        # Validator
        val_label = QLabel("Validation")
        val_label.setStyleSheet("font-weight: bold;")
        form.addRow(val_label)

        self._validator_combo = QComboBox()
        self._validator_combo.addItem("")
        self._validator_combo.addItem("priority")
        self._validator_combo.currentTextChanged.connect(self._on_property_changed)
        form.addRow("Validator:", self._validator_combo)

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # Store form layout for showing/hiding rows
        self._form = form

        # Initially hide all type-specific options
        self._update_type_options('string')

    def _on_property_changed(self):
        if self._updating:
            return
        self._sync_to_column()
        self.property_changed.emit()

    def _on_label_changed(self, text: str):
        if self._updating:
            return
        self._sync_to_column()
        self.property_changed.emit()

    def _on_type_changed(self, type_str: str):
        if self._updating:
            return
        self._update_type_options(type_str)
        self._sync_to_column()
        self.property_changed.emit()

    def _update_type_options(self, type_str: str):
        """Show/hide type-specific options based on selected type."""
        is_numeric = type_str in ('integer', 'float')
        is_combo = type_str == 'combo'
        is_boolean = type_str == 'boolean'
        is_multi = type_str == 'multi_select'

        # Show/hide numeric options
        self._min_spin.setVisible(is_numeric)
        self._form.labelForField(self._min_spin).setVisible(is_numeric)
        self._max_spin.setVisible(is_numeric)
        self._form.labelForField(self._max_spin).setVisible(is_numeric)
        self._step_spin.setVisible(is_numeric)
        self._form.labelForField(self._step_spin).setVisible(is_numeric)

        # Show/hide combo options
        show_choices = is_combo or is_multi
        self._choices_func_combo.setVisible(show_choices)
        self._form.labelForField(self._choices_func_combo).setVisible(show_choices)
        self._default_func_combo.setVisible(show_choices)
        self._form.labelForField(self._default_func_combo).setVisible(show_choices)
        self._choices_edit.setVisible(show_choices)
        self._form.labelForField(self._choices_edit).setVisible(show_choices)

        # Multi-select specific
        self._per_entity_check.setVisible(is_multi)
        self._form.labelForField(self._per_entity_check).setVisible(is_multi)

        # Boolean default
        self._default_bool_check.setVisible(is_boolean)
        self._form.labelForField(self._default_bool_check).setVisible(is_boolean)

        # Numeric default
        self._default_num_spin.setVisible(is_numeric)
        # Hide duplicate default label for numeric
        num_label = self._form.labelForField(self._default_num_spin)
        if num_label:
            num_label.setVisible(is_numeric)

    def set_column(self, column: Optional[dict]):
        """Set the column to edit."""
        self._column = column
        self._updating = True
        try:
            if column is None:
                self.setEnabled(False)
                self._clear_fields()
            else:
                self.setEnabled(True)
                self._load_from_column(column)
        finally:
            self._updating = False

    def _clear_fields(self):
        """Clear all fields."""
        self._key_edit.clear()
        self._label_edit.clear()
        self._type_combo.setCurrentIndex(0)
        self._width_spin.setValue(100)
        self._tooltip_edit.clear()
        self._property_path_edit.clear()
        self._min_spin.setValue(0)
        self._max_spin.setValue(100)
        self._step_spin.setValue(1)
        self._choices_func_combo.setCurrentIndex(0)
        self._default_func_combo.setCurrentIndex(0)
        self._choices_edit.clear()
        self._per_entity_check.setChecked(False)
        self._default_bool_check.setChecked(False)
        self._default_num_spin.setValue(0)
        self._validator_combo.setCurrentIndex(0)

    def _load_from_column(self, col: dict):
        """Load field values from column dict."""
        self._key_edit.setText(col.get('key', ''))
        self._label_edit.setText(col.get('label', ''))

        type_str = col.get('type', 'string').lower()
        idx = self._type_combo.findText(type_str)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        self._update_type_options(type_str)

        self._width_spin.setValue(col.get('width', 100))
        self._tooltip_edit.setText(col.get('tooltip', ''))
        self._property_path_edit.setText(col.get('property_path', ''))

        self._min_spin.setValue(col.get('min', 0))
        self._max_spin.setValue(col.get('max', 100))
        self._step_spin.setValue(col.get('step', 1))

        choices_func = col.get('choices_func', '')
        idx = self._choices_func_combo.findText(choices_func)
        self._choices_func_combo.setCurrentIndex(idx if idx >= 0 else 0)

        default_func = col.get('default_func', '')
        idx = self._default_func_combo.findText(default_func)
        self._default_func_combo.setCurrentIndex(idx if idx >= 0 else 0)

        choices = col.get('choices', [])
        if isinstance(choices, list):
            self._choices_edit.setText(', '.join(choices))
        else:
            self._choices_edit.setText('')

        self._per_entity_check.setChecked(col.get('per_entity_choices', False))

        default = col.get('default')
        if type_str == 'boolean':
            self._default_bool_check.setChecked(bool(default))
        elif type_str in ('integer', 'float'):
            self._default_num_spin.setValue(default if isinstance(default, (int, float)) else 0)

        validator = col.get('validator', '')
        idx = self._validator_combo.findText(validator)
        self._validator_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _sync_to_column(self):
        """Sync field values back to column dict."""
        if self._column is None:
            return

        self._column['key'] = self._key_edit.text().strip()
        self._column['label'] = self._label_edit.text().strip()
        self._column['type'] = self._type_combo.currentText()
        self._column['width'] = self._width_spin.value()

        tooltip = self._tooltip_edit.text().strip()
        if tooltip:
            self._column['tooltip'] = tooltip
        elif 'tooltip' in self._column:
            del self._column['tooltip']

        prop_path = self._property_path_edit.text().strip()
        if prop_path:
            self._column['property_path'] = prop_path
        elif 'property_path' in self._column:
            del self._column['property_path']

        type_str = self._type_combo.currentText()

        # Numeric options
        if type_str in ('integer', 'float'):
            self._column['min'] = self._min_spin.value()
            self._column['max'] = self._max_spin.value()
            self._column['step'] = self._step_spin.value()
            self._column['default'] = self._default_num_spin.value()
        else:
            for key in ['min', 'max', 'step']:
                if key in self._column:
                    del self._column[key]

        # Combo/choices options
        if type_str in ('combo', 'multi_select'):
            choices_func = self._choices_func_combo.currentText()
            if choices_func:
                self._column['choices_func'] = choices_func
            elif 'choices_func' in self._column:
                del self._column['choices_func']

            default_func = self._default_func_combo.currentText()
            if default_func:
                self._column['default_func'] = default_func
            elif 'default_func' in self._column:
                del self._column['default_func']

            choices_str = self._choices_edit.text().strip()
            if choices_str and not choices_func:
                self._column['choices'] = [c.strip() for c in choices_str.split(',') if c.strip()]
            elif 'choices' in self._column:
                del self._column['choices']

            if type_str == 'multi_select':
                self._column['per_entity_choices'] = self._per_entity_check.isChecked()
            elif 'per_entity_choices' in self._column:
                del self._column['per_entity_choices']
        else:
            for key in ['choices_func', 'default_func', 'choices', 'per_entity_choices']:
                if key in self._column:
                    del self._column[key]

        # Boolean default
        if type_str == 'boolean':
            self._column['default'] = self._default_bool_check.isChecked()

        # Validator
        validator = self._validator_combo.currentText()
        if validator:
            self._column['validator'] = validator
        elif 'validator' in self._column:
            del self._column['validator']

    def get_label(self) -> str:
        """Get the current label value."""
        return self._label_edit.text().strip()


class DefaultHiddenPanel(QWidget):
    """Panel for managing default hidden columns."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns = []
        self._hidden_set = set()
        self._checkboxes = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel("Default Hidden Columns:")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        # Scroll area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(100)

        self._checkbox_container = QWidget()
        self._checkbox_layout = QHBoxLayout(self._checkbox_container)
        self._checkbox_layout.setContentsMargins(0, 0, 0, 0)

        scroll.setWidget(self._checkbox_container)
        layout.addWidget(scroll)

    def set_columns(self, columns: list, hidden: list):
        """Set columns and hidden list."""
        self._columns = columns
        self._hidden_set = set(hidden)
        self._refresh_checkboxes()

    def _refresh_checkboxes(self):
        """Refresh checkboxes from column list."""
        # Clear existing
        for cb in self._checkboxes.values():
            self._checkbox_layout.removeWidget(cb)
            cb.deleteLater()
        self._checkboxes.clear()

        # Create checkboxes
        for col in self._columns:
            key = col.get('key', '')
            label = col.get('label', key)
            cb = QCheckBox(label)
            cb.setChecked(key in self._hidden_set)
            cb.stateChanged.connect(self._on_checkbox_changed)
            self._checkboxes[key] = cb
            self._checkbox_layout.addWidget(cb)

        self._checkbox_layout.addStretch()

    def _on_checkbox_changed(self):
        self._sync_to_hidden_set()
        self.changed.emit()

    def _sync_to_hidden_set(self):
        """Sync checkbox states to hidden set."""
        self._hidden_set = set()
        for key, cb in self._checkboxes.items():
            if cb.isChecked():
                self._hidden_set.add(key)

    def get_hidden(self) -> list:
        """Get list of hidden column keys."""
        return list(self._hidden_set)

    def refresh_from_columns(self, columns: list):
        """Update checkboxes when columns change."""
        self._columns = columns
        # Preserve hidden state for existing columns
        new_keys = {col.get('key') for col in columns}
        self._hidden_set = self._hidden_set & new_keys
        self._refresh_checkboxes()


class ColumnEditorDialog(QDialog):
    """Dialog for editing submission column configuration."""

    def __init__(self, initial_section: str = 'render', parent=None):
        super().__init__(parent)
        self.setWindowTitle("Submission Column Editor")
        self.resize(900, 700)

        self._api = default_client()
        self._dirty = False

        # Store column data for each section
        self._columns = {'publish': [], 'render': []}
        self._default_hidden = {'publish': [], 'render': []}

        self._load_config()
        self._setup_ui()

        # Select initial section
        if initial_section == 'publish':
            self._section_tabs.setCurrentIndex(0)
        else:
            self._section_tabs.setCurrentIndex(1)

    def _load_config(self):
        """Load current config from database."""
        try:
            props = self._api.config.get_properties(Uri.parse_unsafe('config:/submission/columns'))
            if props:
                for section in ['publish', 'render']:
                    if section in props:
                        self._columns[section] = copy.deepcopy(props[section].get('columns', []))
                        self._default_hidden[section] = list(props[section].get('default_hidden', []))
        except Exception as e:
            QMessageBox.warning(
                self,
                "Load Error",
                f"Could not load existing config: {e}\n\nStarting with empty configuration."
            )

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Section tabs
        self._section_tabs = QTabWidget()
        self._section_tabs.currentChanged.connect(self._on_section_changed)

        # Create tab content for each section
        self._publish_tab = self._create_section_tab('publish')
        self._render_tab = self._create_section_tab('render')

        self._section_tabs.addTab(self._publish_tab['widget'], "Publish")
        self._section_tabs.addTab(self._render_tab['widget'], "Render")

        layout.addWidget(self._section_tabs, 1)

        # Buttons
        btn_layout = QHBoxLayout()

        add_btn = QPushButton("+ Add Column")
        add_btn.clicked.connect(self._add_column)
        btn_layout.addWidget(add_btn)

        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_config)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _create_section_tab(self, section: str) -> dict:
        """Create UI for a section tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Main splitter
        splitter = QSplitter(Qt.Horizontal)

        # Left: Column list
        list_group = QGroupBox("Columns")
        list_layout = QVBoxLayout(list_group)
        column_list = ColumnListWidget()
        column_list.set_columns(self._columns[section])
        column_list.selection_changed.connect(lambda idx, s=section: self._on_column_selected(s, idx))
        column_list.columns_changed.connect(lambda s=section: self._on_columns_changed(s))
        list_layout.addWidget(column_list)
        splitter.addWidget(list_group)

        # Right: Property panel
        prop_group = QGroupBox("Column Properties")
        prop_layout = QVBoxLayout(prop_group)
        prop_panel = ColumnPropertyPanel()
        prop_panel.property_changed.connect(lambda s=section: self._on_property_changed(s))
        prop_layout.addWidget(prop_panel)
        splitter.addWidget(prop_group)

        # Set splitter sizes
        splitter.setSizes([300, 500])

        layout.addWidget(splitter, 1)

        # Default hidden panel
        hidden_panel = DefaultHiddenPanel()
        hidden_panel.set_columns(self._columns[section], self._default_hidden[section])
        hidden_panel.changed.connect(lambda s=section: self._on_hidden_changed(s))
        layout.addWidget(hidden_panel)

        # Select first column if available
        if self._columns[section]:
            column_list._list.setCurrentRow(0)
            prop_panel.set_column(self._columns[section][0])
        else:
            prop_panel.set_column(None)

        return {
            'widget': widget,
            'column_list': column_list,
            'prop_panel': prop_panel,
            'hidden_panel': hidden_panel,
        }

    def _get_current_section(self) -> str:
        """Get current section name."""
        return 'publish' if self._section_tabs.currentIndex() == 0 else 'render'

    def _get_current_tab(self) -> dict:
        """Get current tab widgets."""
        return self._publish_tab if self._section_tabs.currentIndex() == 0 else self._render_tab

    def _on_section_changed(self, index: int):
        """Handle section tab change."""
        pass  # No special handling needed

    def _on_column_selected(self, section: str, index: int):
        """Handle column selection change."""
        tab = self._publish_tab if section == 'publish' else self._render_tab
        if index >= 0 and index < len(self._columns[section]):
            tab['prop_panel'].set_column(self._columns[section][index])
        else:
            tab['prop_panel'].set_column(None)

    def _on_columns_changed(self, section: str):
        """Handle columns list change (add/remove/reorder)."""
        self._dirty = True
        tab = self._publish_tab if section == 'publish' else self._render_tab
        tab['hidden_panel'].refresh_from_columns(self._columns[section])

    def _on_property_changed(self, section: str):
        """Handle property change."""
        self._dirty = True
        # Update label in list
        tab = self._publish_tab if section == 'publish' else self._render_tab
        label = tab['prop_panel'].get_label()
        tab['column_list'].update_selected_label(label)
        # Refresh hidden panel labels
        tab['hidden_panel'].refresh_from_columns(self._columns[section])

    def _on_hidden_changed(self, section: str):
        """Handle default hidden change."""
        self._dirty = True
        tab = self._publish_tab if section == 'publish' else self._render_tab
        self._default_hidden[section] = tab['hidden_panel'].get_hidden()

    def _add_column(self):
        """Add a new column to current section."""
        section = self._get_current_section()
        tab = self._get_current_tab()

        # Generate unique key
        existing_keys = {col.get('key') for col in self._columns[section]}
        base_key = 'new_column'
        key = base_key
        counter = 1
        while key in existing_keys:
            key = f"{base_key}_{counter}"
            counter += 1

        new_column = {
            'key': key,
            'label': 'New Column',
            'type': 'string',
            'width': 100,
        }

        tab['column_list'].add_column(new_column)
        self._dirty = True

    def _validate(self) -> tuple[bool, str]:
        """Validate the configuration."""
        errors = []

        for section in ['publish', 'render']:
            if not self._columns[section]:
                errors.append(f"'{section}' section has no columns")
                continue

            keys_seen = set()
            for i, col in enumerate(self._columns[section]):
                key = col.get('key', '').strip()
                if not key:
                    errors.append(f"{section} column {i + 1}: key is required")
                elif key in keys_seen:
                    errors.append(f"{section}: duplicate key '{key}'")
                else:
                    keys_seen.add(key)

                if not col.get('label', '').strip():
                    errors.append(f"{section} column '{key}': label is required")

        if errors:
            return False, "\n".join(errors)
        return True, ""

    def _save_config(self):
        """Save config to database."""
        # Validate
        is_valid, error_msg = self._validate()
        if not is_valid:
            QMessageBox.warning(
                self,
                "Validation Error",
                f"Please fix the following errors:\n\n{error_msg}"
            )
            return

        # Sync hidden panels
        self._default_hidden['publish'] = self._publish_tab['hidden_panel'].get_hidden()
        self._default_hidden['render'] = self._render_tab['hidden_panel'].get_hidden()

        # Build config
        config = {
            'publish': {
                'columns': self._columns['publish'],
                'default_hidden': self._default_hidden['publish'],
            },
            'render': {
                'columns': self._columns['render'],
                'default_hidden': self._default_hidden['render'],
            },
        }

        try:
            self._api.config.set_properties(
                Uri.parse_unsafe('config:/submission/columns'),
                config
            )
            self._dirty = False
            QMessageBox.information(
                self,
                "Saved",
                "Column configuration saved successfully.\n\n"
                "Changes will take effect the next time you open the submission dialog."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(
                self,
                "Save Error",
                f"Failed to save configuration:\n\n{e}"
            )

    def closeEvent(self, event):
        """Handle close event - warn about unsaved changes."""
        if self._dirty:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Discard them?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
        event.accept()
