"""Drive the Database Editor's JSON array rows against real Qt.

Runs under Houdini's python313 (PySide6, no licence, no ``hou``)::

    S=<scratch dir with a sitecustomize adding Houdini's DLL dirs>
    HW="C:/Program Files/Side Effects Software/Houdini <ver>"
    HOUDINI_BIN="$HW/bin" QT_QPA_PLATFORM=offscreen \\
    PYTHONPATH="$S;$HW/python313/lib/site-packages-forced" \\
        "$HW/python313/python.exe" scripts/verify_json_editor_arrays.py

``{"a": [[10, 20, 30], 1, 2, 3]}``: ``a`` is a ``FieldArrayItem`` and its row
0 an ``IndexArrayItem`` (an array inside an array). Covers what the
headless PySide6 stub cannot, since it has no real item rows:

- ``IndexArrayItem._set_item`` (editing a value in a nested array) used to
  disconnect ``item_added`` / ``item_removed``, which no item class has, so
  the edit raised ``AttributeError`` and never reached the tree;
- the replaced row's ``change`` still reaches the model;
- both arrays' ``_remove_item`` record the index removed, not the last one
  their re-index loop left in the shadowed ``index``.

Prints ok/FAIL per check; exits 1 on any FAIL.
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from tumblepipe.config_editor.json_editor import JsonModel  # noqa: E402
from tumblepipe.config_editor.json_editor.ops import (  # noqa: E402
    JsonOpIndexRemove,
    JsonOpIndexUpdate,
)

failures = []


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


model = JsonModel({'a': [[10, 20, 30], 1, 2, 3]})
changes = []
field_array = model._fields['a']
field_array.change.connect(changes.append)
index_array = field_array._items[0]
check('row 0 of a nested array is an IndexArrayItem', type(index_array).__name__ == 'IndexArrayItem')

try:
    index_array._set_item(1, 99)
    check('IndexArrayItem._set_item runs', True)
except AttributeError as exc:
    check(f'IndexArrayItem._set_item runs ({exc})', False)
check('the set reaches the tree', model.to_json() == {'a': [[10, 99, 30], 1, 2, 3]})
check(
    'the set records an update at 1',
    bool(changes) and isinstance(changes[-1].op, JsonOpIndexUpdate) and changes[-1].op.index == 1,
)

index_array._items[1].change.emit('probe')
check('the replaced row is connected', bool(changes) and changes[-1] == 'probe')

index_array._remove_item(0)
check('IndexArrayItem remove reaches the tree', model.to_json() == {'a': [[99, 30], 1, 2, 3]})
check(
    'IndexArrayItem remove records index 0',
    isinstance(changes[-1].op, JsonOpIndexRemove) and changes[-1].op.index == 0,
)

field_array._remove_item(1)
check('FieldArrayItem remove reaches the tree', model.to_json() == {'a': [[99, 30], 2, 3]})
check(
    'FieldArrayItem remove records index 1',
    isinstance(changes[-1].op, JsonOpIndexRemove) and changes[-1].op.index == 1,
)

print('FAILED' if failures else 'ALL OK')
sys.exit(1 if failures else 0)
