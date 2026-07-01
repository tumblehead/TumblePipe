"""JSON tree editor widget.

Split by concern; this package re-exports the public surface so callers keep
importing from ``...ui.database.json_editor``:

- ``types``  — JsonValue/JsonRoot aliases, FieldOrigin, value helpers (Qt-free)
- ``diff``   — value-tree / diff-tree computation (Qt-free)
- ``path``   — JsonPath address family + lookups (Qt-free)
- ``ops``    — JsonOp / JsonChange edit operations (Qt-free)
- ``items``  — the Qt item stack (menu/action helpers, factories, QStandardItem subclasses)
- ``view``   — the Qt model/delegate/view stack (builds on ``items``)
"""

from .types import FieldOrigin, JsonValue, JsonRoot
from .path import JsonPath, JsonPathRoot, JsonPathIndex, JsonPathField
from .ops import JsonOp, JsonChange
from .view import JsonView, JsonModel, JsonItemDelegate

__all__ = [
    'FieldOrigin',
    'JsonValue',
    'JsonRoot',
    'JsonPath',
    'JsonPathRoot',
    'JsonPathIndex',
    'JsonPathField',
    'JsonOp',
    'JsonChange',
    'JsonView',
    'JsonModel',
    'JsonItemDelegate',
]
