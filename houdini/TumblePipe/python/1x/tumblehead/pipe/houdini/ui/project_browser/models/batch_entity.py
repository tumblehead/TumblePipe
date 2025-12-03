from qtpy.QtCore import Qt, QAbstractTableModel, QModelIndex
from qtpy.QtGui import QBrush, QColor

from tumblehead.config.schema import Schema, apply_defaults, validate_properties, infer_field_type


class BatchEntityTableModel(QAbstractTableModel):
    """Model for batch entity creation with dynamic columns based on schema"""

    COLUMN_NAME = 0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._schema = None
        self._rows = []
        self._validation = []
        self._field_names = []

    def set_schema(self, schema: Schema | None):
        """Update schema and reset columns/rows"""
        self.beginResetModel()
        self._schema = schema
        self._rows = []
        self._validation = []
        if schema is not None:
            self._field_names = list(schema.fields.keys())
        else:
            self._field_names = []
        self.endResetModel()

    def get_schema(self) -> Schema | None:
        return self._schema

    def columnCount(self, parent=QModelIndex()):
        # Name + schema fields + Status
        return 1 + len(self._field_names) + 1

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def headerData(self, section, orientation, role):
        if orientation != Qt.Horizontal:
            return None
        if role != Qt.DisplayRole:
            return None
        if section == self.COLUMN_NAME:
            return "Name *"
        elif section <= len(self._field_names):
            return self._field_names[section - 1]
        else:
            return "Status"

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        # Status column is not editable
        if index.column() == self.columnCount() - 1:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable

    def data(self, index, role):
        if not index.isValid() or index.row() >= len(self._rows):
            return None

        row_data = self._rows[index.row()]
        validation = self._validation[index.row()] if index.row() < len(self._validation) else {'valid': True, 'errors': []}

        if role == Qt.DisplayRole or role == Qt.EditRole:
            if index.column() == self.COLUMN_NAME:
                return row_data.get('name', '')
            elif index.column() <= len(self._field_names):
                field_name = self._field_names[index.column() - 1]
                value = row_data.get('properties', {}).get(field_name, '')
                # Convert arrays to comma-separated string for display
                if isinstance(value, list):
                    return ', '.join(str(v) for v in value)
                return value if value is not None else ''
            else:
                # Status column
                if not validation['valid']:
                    return '\u2716'  # X mark
                elif not row_data.get('name', '').strip():
                    return '\u26A0'  # Warning sign
                else:
                    return '\u2714'  # Check mark

        elif role == Qt.ToolTipRole:
            if index.column() == self.columnCount() - 1:
                # Status column tooltip shows errors
                errors = validation.get('errors', [])
                if not row_data.get('name', '').strip():
                    errors = ['Name is required'] + errors
                if errors:
                    return '\n'.join(errors)
                return 'Valid'

        elif role == Qt.TextAlignmentRole:
            if index.column() == self.columnCount() - 1:
                return Qt.AlignCenter | Qt.AlignVCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        elif role == Qt.BackgroundRole:
            # Highlight invalid cells
            if index.column() == self.COLUMN_NAME:
                if not row_data.get('name', '').strip():
                    return QBrush(QColor("#b01c3c"))  # Red for missing name
            elif index.column() <= len(self._field_names):
                field_name = self._field_names[index.column() - 1]
                # Check if this field has an error
                for error in validation.get('errors', []):
                    if f"'{field_name}'" in error:
                        return QBrush(QColor("#b01c3c"))  # Red for field error
            return QBrush(QColor("#3a3a3a"))  # Normal background

        elif role == Qt.ForegroundRole:
            if index.column() == self.columnCount() - 1:
                # Status column color
                if not validation['valid'] or not row_data.get('name', '').strip():
                    return QBrush(QColor("#ff6b6b"))  # Red for errors/warnings
                return QBrush(QColor("#6bff6b"))  # Green for valid

        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole:
            return False
        if index.row() >= len(self._rows):
            return False

        row_data = self._rows[index.row()]

        if index.column() == self.COLUMN_NAME:
            row_data['name'] = str(value).strip()
        elif index.column() <= len(self._field_names):
            field_name = self._field_names[index.column() - 1]
            properties = row_data.setdefault('properties', {})
            # Convert value to appropriate type
            converted_value = self._convert_value(value, field_name)
            properties[field_name] = converted_value

        # Re-validate this row
        self._validate_row(index.row())

        # Emit data changed for entire row (to update status column)
        self.dataChanged.emit(
            self.index(index.row(), 0),
            self.index(index.row(), self.columnCount() - 1)
        )
        return True

    def _convert_value(self, value, field_name):
        """Convert value to appropriate type based on schema field"""
        if self._schema is None:
            return value
        field_def = self._schema.fields.get(field_name)
        if field_def is None:
            return value

        str_value = str(value).strip()

        match infer_field_type(field_def.default):
            case 'number':
                try:
                    if '.' in str_value:
                        return float(str_value)
                    return int(str_value)
                except ValueError:
                    return str_value  # Return as-is, validation will catch it
            case 'boolean':
                return str_value.lower() in ('true', '1', 'yes')
            case 'array':
                # Parse comma-separated values
                if not str_value:
                    return []
                return [v.strip() for v in str_value.split(',') if v.strip()]
            case _:
                return str_value

    def _validate_row(self, row_index):
        """Validate a single row and update validation cache"""
        if row_index >= len(self._rows):
            return

        row_data = self._rows[row_index]
        properties = row_data.get('properties', {})

        errors = []
        if self._schema is not None:
            errors = validate_properties(self._schema, properties)

        # Ensure validation list is large enough
        while len(self._validation) <= row_index:
            self._validation.append({'valid': True, 'errors': []})

        self._validation[row_index] = {
            'valid': len(errors) == 0,
            'errors': errors
        }

    def add_row(self):
        """Add a new row with schema defaults"""
        row_index = len(self._rows)
        self.beginInsertRows(QModelIndex(), row_index, row_index)

        # Create row with defaults
        properties = {}
        if self._schema is not None:
            properties = apply_defaults(self._schema, {})

        self._rows.append({
            'name': '',
            'properties': properties
        })
        self._validation.append({'valid': True, 'errors': []})
        self._validate_row(row_index)

        self.endInsertRows()

    def remove_rows(self, indices: list[int]):
        """Remove rows at specified indices"""
        # Sort in reverse to remove from end first
        for row_index in sorted(indices, reverse=True):
            if 0 <= row_index < len(self._rows):
                self.beginRemoveRows(QModelIndex(), row_index, row_index)
                del self._rows[row_index]
                if row_index < len(self._validation):
                    del self._validation[row_index]
                self.endRemoveRows()

    def get_valid_rows(self) -> list[dict]:
        """Return rows that pass validation and have a name"""
        valid_rows = []
        for i, row_data in enumerate(self._rows):
            name = row_data.get('name', '').strip()
            if not name:
                continue
            validation = self._validation[i] if i < len(self._validation) else {'valid': True}
            if validation['valid']:
                valid_rows.append({
                    'name': name,
                    'properties': row_data.get('properties', {}).copy()
                })
        return valid_rows

    def get_row_counts(self) -> tuple[int, int, int]:
        """Return (total, valid, invalid) row counts"""
        total = len(self._rows)
        valid = 0
        invalid = 0
        for i, row_data in enumerate(self._rows):
            name = row_data.get('name', '').strip()
            validation = self._validation[i] if i < len(self._validation) else {'valid': True}
            if name and validation['valid']:
                valid += 1
            elif name or not validation['valid']:
                invalid += 1
        return total, valid, invalid
