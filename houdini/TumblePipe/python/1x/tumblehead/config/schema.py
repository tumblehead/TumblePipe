from dataclasses import dataclass
from typing import Any

from tumblehead.util.uri import Uri


@dataclass(frozen=True)
class FieldDefinition:
    name: str
    default: Any


@dataclass(frozen=True)
class Schema:
    uri: Uri
    fields: dict[str, FieldDefinition]

    @property
    def name(self) -> str:
        return self.uri.segments[-1] if self.uri.segments else ''


def infer_field_type(value: Any) -> str:
    """Infer field type from default value."""
    if isinstance(value, bool):
        return 'boolean'
    elif isinstance(value, (int, float)):
        return 'number'
    elif isinstance(value, str):
        return 'string'
    elif isinstance(value, list):
        return 'array'
    elif isinstance(value, dict):
        return 'object'
    return 'null'


def schema_from_properties(uri: Uri, properties: dict) -> Schema:
    """Properties ARE the field definitions (flattened format)."""
    fields = {}
    for field_name, default_value in properties.items():
        fields[field_name] = FieldDefinition(
            name=field_name,
            default=default_value
        )
    return Schema(uri=uri, fields=fields)


def validate_value(value: Any, field_type: str) -> bool:
    match field_type:
        case 'string':
            return isinstance(value, str)
        case 'number':
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        case 'boolean':
            return isinstance(value, bool)
        case 'array':
            return isinstance(value, list)
        case 'object':
            return isinstance(value, dict)
        case 'null':
            return value is None
        case _:
            return False


def validate_properties(schema: Schema, properties: dict) -> list[str]:
    """Validate entity properties against schema, inferring types from defaults."""
    errors = []
    for field_name, field_def in schema.fields.items():
        if field_name not in properties:
            continue
        value = properties[field_name]
        expected_type = infer_field_type(field_def.default)
        if not validate_value(value, expected_type):
            errors.append(
                f"Field '{field_name}' has invalid type. "
                f"Expected {expected_type}, got {type(value).__name__}"
            )
    return errors


def apply_defaults(schema: Schema, properties: dict) -> dict:
    """Apply schema defaults to properties."""
    result = properties.copy()
    for field_name, field_def in schema.fields.items():
        if field_name not in result:
            result[field_name] = field_def.default
    return result
