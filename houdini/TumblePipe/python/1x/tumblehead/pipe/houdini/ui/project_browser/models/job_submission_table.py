"""Table model for job submission with dynamic columns and per-cell override tracking."""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from qtpy.QtCore import Qt, QAbstractTableModel, QModelIndex
from qtpy.QtGui import QBrush, QColor, QFont

from tumblehead.util.uri import Uri
from tumblehead.config.variants import list_variants, refresh_cache as refresh_variants_cache
from .job_schemas import JobTypeSchema, ColumnDefinition, ColumnType


@dataclass
class CellData:
    """Represents a single cell's state."""
    value: Any
    is_overridden: bool = False
    validation_error: Optional[str] = None


@dataclass
class RowData:
    """Represents a single entity row."""
    entity_uri: str
    entity_name: str
    entity_context: str  # 'shots' or 'assets'
    cells: dict[str, CellData] = field(default_factory=dict)
    # Per-entity choices for columns with per_entity_choices=True
    entity_choices: dict[str, list[str]] = field(default_factory=dict)


class JobSubmissionTableModel(QAbstractTableModel):
    """
    Table model for job submission with dynamic columns per job type.

    Features:
    - Dynamic column configuration per job type
    - Per-cell override tracking (default vs user-set values)
    - Per-entity choices for MULTI_SELECT columns
    - Validation with visual feedback
    - Bulk editing support
    """

    # Custom roles for delegate communication
    ROLE_IS_OVERRIDDEN = Qt.UserRole + 1
    ROLE_VALIDATION_ERROR = Qt.UserRole + 2
    ROLE_COLUMN_TYPE = Qt.UserRole + 3
    ROLE_COLUMN_DEF = Qt.UserRole + 4
    ROLE_RAW_VALUE = Qt.UserRole + 5
    ROLE_ENTITY_URI = Qt.UserRole + 6
    ROLE_ENTITY_CONTEXT = Qt.UserRole + 7
    ROLE_ENTITY_CHOICES = Qt.UserRole + 8  # Per-entity choices for MULTI_SELECT

    # Column indices
    COLUMN_ENTITY = 0  # Always first column: entity name (read-only)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._schema: Optional[JobTypeSchema] = None
        self._rows: list[RowData] = []
        self._global_defaults: dict[str, Any] = {}  # Global overrides for defaults

    # =========================================================================
    # Schema Management
    # =========================================================================

    def set_schema(self, schema: JobTypeSchema):
        """Set the job type schema, resetting all rows to use new columns."""
        self.beginResetModel()
        self._schema = schema
        # Re-initialize all rows with new schema defaults
        for row in self._rows:
            self._init_row_cells(row)
        self.endResetModel()

    def get_schema(self) -> Optional[JobTypeSchema]:
        return self._schema

    # =========================================================================
    # Column Methods
    # =========================================================================

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        if self._schema is None:
            return 1  # Just entity column
        return 1 + len(self._schema.columns)  # Entity + schema columns

    def headerData(self, section: int, orientation, role):
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            if section == self.COLUMN_ENTITY:
                return "Entity"
            if self._schema and section <= len(self._schema.columns):
                col_def = self._schema.columns[section - 1]
                return col_def.label
        elif role == Qt.ToolTipRole:
            if section == self.COLUMN_ENTITY:
                return "Entity name"
            if self._schema and section <= len(self._schema.columns):
                col_def = self._schema.columns[section - 1]
                return col_def.tooltip
        return None

    # =========================================================================
    # Row Methods
    # =========================================================================

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def add_entities(self, entities: list[tuple[str, str, str]]):
        """Add multiple entities. Each tuple is (uri, display_name, context)."""
        if not entities:
            return
        # Filter out duplicates
        existing_uris = {row.entity_uri for row in self._rows}
        new_entities = [e for e in entities if e[0] not in existing_uris]
        if not new_entities:
            return

        start = len(self._rows)
        end = start + len(new_entities) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        for uri, name, context in new_entities:
            row = RowData(entity_uri=uri, entity_name=name, entity_context=context)
            # Fetch per-entity choices (variants)
            self._fetch_entity_choices(row)
            self._init_row_cells(row)
            self._rows.append(row)
        self.endInsertRows()

    def _fetch_entity_choices(self, row: RowData):
        """Fetch per-entity choices like variants."""
        try:
            uri = Uri.parse_unsafe(row.entity_uri)
            refresh_variants_cache()  # Pick up changes from Database Editor
            variants = list_variants(uri)
            row.entity_choices['variants'] = variants
        except Exception:
            row.entity_choices['variants'] = ['default']

    def remove_entity(self, uri: str):
        """Remove entity by URI."""
        for i, row in enumerate(self._rows):
            if row.entity_uri == uri:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
                return

    def remove_rows(self, indices: list[int]):
        """Remove rows at specified indices."""
        for row_index in sorted(indices, reverse=True):
            if 0 <= row_index < len(self._rows):
                self.beginRemoveRows(QModelIndex(), row_index, row_index)
                del self._rows[row_index]
                self.endRemoveRows()

    def clear_rows(self):
        """Remove all rows."""
        if not self._rows:
            return
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def get_entity_uris(self) -> list[str]:
        """Get all entity URIs in the model."""
        return [row.entity_uri for row in self._rows]

    def _init_row_cells(self, row: RowData):
        """Initialize cells with defaults for current schema."""
        row.cells.clear()
        if self._schema is None:
            return
        for col_def in self._schema.columns:
            default = self._global_defaults.get(col_def.key, col_def.default_value)
            # For MULTI_SELECT with per_entity_choices, default to all available
            if col_def.column_type == ColumnType.MULTI_SELECT and col_def.per_entity_choices:
                choices = row.entity_choices.get(col_def.key, [])
                default = list(choices) if choices else []
            row.cells[col_def.key] = CellData(value=default, is_overridden=False)

    # =========================================================================
    # Data Access
    # =========================================================================

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        # Entity column is not editable
        if index.column() == self.COLUMN_ENTITY:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None

        row_data = self._rows[index.row()]
        col = index.column()

        # Entity column
        if col == self.COLUMN_ENTITY:
            if role in (Qt.DisplayRole, Qt.EditRole):
                return row_data.entity_name
            if role == Qt.ToolTipRole:
                return row_data.entity_uri
            if role == self.ROLE_ENTITY_URI:
                return row_data.entity_uri
            if role == self.ROLE_ENTITY_CONTEXT:
                return row_data.entity_context
            return None

        # Schema columns
        if self._schema is None or col > len(self._schema.columns):
            return None

        col_def = self._schema.columns[col - 1]
        cell = row_data.cells.get(col_def.key)
        if cell is None:
            return None

        if role == Qt.DisplayRole:
            return self._format_display_value(cell.value, col_def)
        if role == Qt.EditRole:
            return cell.value
        if role == self.ROLE_RAW_VALUE:
            return cell.value
        if role == self.ROLE_IS_OVERRIDDEN:
            return cell.is_overridden
        if role == self.ROLE_VALIDATION_ERROR:
            return cell.validation_error
        if role == self.ROLE_COLUMN_TYPE:
            return col_def.column_type
        if role == self.ROLE_COLUMN_DEF:
            return col_def
        if role == self.ROLE_ENTITY_URI:
            return row_data.entity_uri
        if role == self.ROLE_ENTITY_CONTEXT:
            return row_data.entity_context
        if role == self.ROLE_ENTITY_CHOICES:
            # Return per-entity choices for this column
            return row_data.entity_choices.get(col_def.key, [])
        if role == Qt.BackgroundRole:
            return self._get_cell_background(cell)
        if role == Qt.ForegroundRole:
            return self._get_cell_foreground(cell)
        if role == Qt.FontRole:
            return self._get_cell_font(cell)
        if role == Qt.ToolTipRole:
            if cell.validation_error:
                return f"Error: {cell.validation_error}"
            if cell.is_overridden:
                return f"Overridden (default: {col_def.default_value})"
            return col_def.tooltip or "Default value"

        return None

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:
        if not index.isValid() or role != Qt.EditRole:
            return False
        if index.column() == self.COLUMN_ENTITY:
            return False  # Entity column is read-only

        row_data = self._rows[index.row()]
        if self._schema is None or index.column() > len(self._schema.columns):
            return False

        col_def = self._schema.columns[index.column() - 1]
        cell = row_data.cells.get(col_def.key)
        if cell is None:
            return False

        # Convert and validate value
        converted = self._convert_value(value, col_def)
        error = self._validate_value(converted, col_def)

        # Update cell
        cell.value = converted
        cell.is_overridden = True
        cell.validation_error = error

        self.dataChanged.emit(index, index)
        return True

    # =========================================================================
    # Override Management
    # =========================================================================

    def set_cell_override(self, row: int, col_key: str, value: Any, is_override: bool = True):
        """Explicitly set a cell value and override status."""
        if row >= len(self._rows) or self._schema is None:
            return

        row_data = self._rows[row]
        if col_key not in row_data.cells:
            return

        cell = row_data.cells[col_key]
        col_def = self._schema.get_column_by_key(col_key)

        cell.value = self._convert_value(value, col_def) if col_def else value
        cell.is_overridden = is_override
        cell.validation_error = self._validate_value(cell.value, col_def) if col_def else None

        # Find column index and emit change
        col_index = self._get_column_index(col_key)
        if col_index >= 0:
            index = self.index(row, col_index)
            self.dataChanged.emit(index, index)

    def reset_cell_to_default(self, row: int, col_key: str):
        """Reset a cell to its default value."""
        if row >= len(self._rows) or self._schema is None:
            return

        col_def = self._schema.get_column_by_key(col_key)
        if col_def is None:
            return

        row_data = self._rows[row]

        # For MULTI_SELECT with per_entity_choices, default to all available
        if col_def.column_type == ColumnType.MULTI_SELECT and col_def.per_entity_choices:
            choices = row_data.entity_choices.get(col_key, [])
            default = list(choices) if choices else []
        else:
            default = self._global_defaults.get(col_key, col_def.default_value)

        self.set_cell_override(row, col_key, default, is_override=False)

    def reset_column_to_default(self, col_key: str):
        """Reset all cells in a column to default."""
        for row in range(len(self._rows)):
            self.reset_cell_to_default(row, col_key)

    # =========================================================================
    # Bulk Editing
    # =========================================================================

    def apply_to_selected(self, row_indices: list[int], col_key: str, value: Any):
        """Apply a value to multiple rows."""
        for row in row_indices:
            self.set_cell_override(row, col_key, value, is_override=True)

    def apply_to_all(self, col_key: str, value: Any):
        """Apply a value to all rows in a column."""
        self.apply_to_selected(list(range(len(self._rows))), col_key, value)

    def set_global_default(self, col_key: str, value: Any):
        """Set a global default that overrides schema default for new rows."""
        self._global_defaults[col_key] = value

    # =========================================================================
    # Validation
    # =========================================================================

    def validate_all(self) -> list[tuple[int, str, str]]:
        """Validate all cells, return list of (row, col_key, error)."""
        errors = []
        for row_idx, row_data in enumerate(self._rows):
            for col_key, cell in row_data.cells.items():
                if cell.validation_error:
                    errors.append((row_idx, col_key, cell.validation_error))
        return errors

    def is_valid(self) -> bool:
        """Check if all cells pass validation."""
        return len(self.validate_all()) == 0

    # =========================================================================
    # Serialization
    # =========================================================================

    def to_dict(self) -> dict:
        """Serialize model state for presets."""
        return {
            'job_type': self._schema.job_type if self._schema else None,
            'global_defaults': self._global_defaults.copy(),
            'rows': [
                {
                    'entity_uri': row.entity_uri,
                    'entity_name': row.entity_name,
                    'entity_context': row.entity_context,
                    'overrides': {
                        key: cell.value
                        for key, cell in row.cells.items()
                        if cell.is_overridden
                    }
                }
                for row in self._rows
            ]
        }

    def from_dict(self, data: dict, schema_provider: Callable[[str], JobTypeSchema]):
        """Restore model state from preset."""
        self.beginResetModel()

        job_type = data.get('job_type')
        if job_type:
            self._schema = schema_provider(job_type)

        self._global_defaults = data.get('global_defaults', {})
        self._rows.clear()

        for row_data in data.get('rows', []):
            row = RowData(
                entity_uri=row_data['entity_uri'],
                entity_name=row_data['entity_name'],
                entity_context=row_data.get('entity_context', 'shots')
            )
            self._fetch_entity_choices(row)
            self._init_row_cells(row)
            # Apply saved overrides
            for key, value in row_data.get('overrides', {}).items():
                if key in row.cells:
                    row.cells[key].value = value
                    row.cells[key].is_overridden = True
            self._rows.append(row)

        self.endResetModel()

    def apply_preset_defaults(self, defaults: dict[str, Any]):
        """Apply preset defaults to all non-overridden cells."""
        if self._schema is None:
            return

        self.beginResetModel()
        self._global_defaults.update(defaults)

        for row in self._rows:
            for col_key, value in defaults.items():
                if col_key in row.cells and not row.cells[col_key].is_overridden:
                    col_def = self._schema.get_column_by_key(col_key)
                    row.cells[col_key].value = self._convert_value(value, col_def) if col_def else value
                    row.cells[col_key].validation_error = self._validate_value(
                        row.cells[col_key].value, col_def
                    ) if col_def else None

        self.endResetModel()

    def get_job_configs(self) -> list[dict]:
        """
        Export model data as job configs ready for submission.
        Returns list of config dicts per entity.
        """
        configs = []
        for row in self._rows:
            config = {
                'entity': {
                    'uri': row.entity_uri,
                    'name': row.entity_name,
                    'context': row.entity_context,
                },
                'settings': {}
            }
            for col_key, cell in row.cells.items():
                config['settings'][col_key] = cell.value
            configs.append(config)
        return configs

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_column_index(self, col_key: str) -> int:
        """Get column index for a column key."""
        if self._schema is None:
            return -1
        for i, col_def in enumerate(self._schema.columns):
            if col_def.key == col_key:
                return i + 1  # +1 for entity column
        return -1

    def _convert_value(self, value: Any, col_def: Optional[ColumnDefinition]) -> Any:
        """Convert value to appropriate type for column."""
        if col_def is None:
            return value

        try:
            if col_def.column_type == ColumnType.INTEGER:
                return int(value) if value is not None else col_def.default_value
            elif col_def.column_type == ColumnType.FLOAT:
                return float(value) if value is not None else col_def.default_value
            elif col_def.column_type == ColumnType.BOOLEAN:
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return value.lower() in ('true', '1', 'yes')
                return bool(value)
            elif col_def.column_type in (ColumnType.STRING, ColumnType.COMBO):
                return str(value) if value is not None else ''
            elif col_def.column_type == ColumnType.MULTI_SELECT:
                # Keep as list
                if isinstance(value, list):
                    return value
                return []
        except (ValueError, TypeError):
            return col_def.default_value

        return value

    def _validate_value(self, value: Any, col_def: Optional[ColumnDefinition]) -> Optional[str]:
        """Validate value, return error message or None."""
        if col_def is None:
            return None

        # Custom validator
        if col_def.validator:
            is_valid, error = col_def.validator(value)
            if not is_valid:
                return error

        # Built-in validation
        if col_def.column_type in (ColumnType.INTEGER, ColumnType.FLOAT):
            if col_def.min_value is not None and value < col_def.min_value:
                return f"Value must be >= {col_def.min_value}"
            if col_def.max_value is not None and value > col_def.max_value:
                return f"Value must be <= {col_def.max_value}"
        elif col_def.column_type == ColumnType.COMBO:
            choices = col_def.get_choices()
            if choices and value not in choices:
                return f"Invalid choice: {value}"

        return None

    def _format_display_value(self, value: Any, col_def: ColumnDefinition) -> str:
        """Format value for display."""
        if col_def.column_type == ColumnType.BOOLEAN:
            return "Yes" if value else "No"
        elif col_def.column_type == ColumnType.MULTI_SELECT:
            if isinstance(value, list) and value:
                return ", ".join(value)
            return "(none)"
        return str(value) if value is not None else ''

    def _get_cell_background(self, cell: CellData) -> QBrush:
        """Get background brush based on cell state."""
        if cell.validation_error:
            return QBrush(QColor("#5c1a1a"))  # Dark red for errors
        if cell.is_overridden:
            return QBrush(QColor("#1a3d1a"))  # Dark green tint for overridden
        return QBrush()  # Default

    def _get_cell_foreground(self, cell: CellData) -> QBrush:
        """Get foreground brush based on cell state."""
        if cell.validation_error:
            return QBrush(QColor("#ff6b6b"))  # Light red text for errors
        if not cell.is_overridden:
            return QBrush(QColor("#888888"))  # Dimmed for default values
        return QBrush()  # Default white

    def _get_cell_font(self, cell: CellData) -> QFont:
        """Get font based on cell state."""
        font = QFont()
        if cell.is_overridden:
            font.setBold(True)
        return font
