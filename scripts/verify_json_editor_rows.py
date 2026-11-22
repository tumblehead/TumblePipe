"""The Database Editor's four container rows against a plain dict or list.

Runs in preflight's qt stage (PySide6, no ``hou``). A JSON object is a
``FieldObjectItem`` under a key and an ``IndexObjectItem`` inside an
array; an array is a ``FieldArrayItem`` or an ``IndexArrayItem``. Each gets
random sequences of its row operations (objects: add, set, remove,
reorder; arrays: add, set, remove, adjacent reorder, move), run alongside
the same operation on a plain ``dict`` / ``list``:

- after every step the container's ``to_json()`` equals the plain model;
- every step emits one change, at the container's own path, naming the
  row it touched (key or index) and the value it wrote;
- every child row's path is the container's path and its own key or
  index, in row order (a stale index would address another row);
- Revert on a field restores its pristine value and records the change,
  whichever kind of object holds it.

Prints ok/FAIL per check; exits 1 on any FAIL.
"""

import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from tumblepipe.config_editor.json_editor import JsonModel  # noqa: E402
from tumblepipe.config_editor.json_editor import items  # noqa: E402
from tumblepipe.config_editor.json_editor.ops import (  # noqa: E402
    JsonOpFieldInsert,
    JsonOpFieldRemove,
    JsonOpFieldReorder,
    JsonOpFieldUpdate,
    JsonOpIndexInsert,
    JsonOpIndexRemove,
    JsonOpIndexReorder,
    JsonOpIndexUpdate,
)

SEQUENCES = 150
STEPS = 12
failures = []


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def value(rng):
    return rng.choice([rng.randint(-5, 50), rng.choice(['', 'a', 'é', 'x y']), True, None, 1.5])


def containers():
    """A fresh document's four containers, each with its plain model."""
    doc = {'o': {'a': 1, 'b': 'two'}, 'a': [1, 2, 3], 'n': [{'k': 1, 'm': 2}, [4, 5]]}
    model = JsonModel(doc)
    nested = model._fields['n']
    return model, [
        ('FieldObjectItem', model._fields['o'], dict(doc['o'])),
        ('FieldArrayItem', model._fields['a'], list(doc['a'])),
        ('IndexObjectItem', nested._items[0], dict(doc['n'][0])),
        ('IndexArrayItem', nested._items[1], list(doc['n'][1])),
    ]


def object_step(rng, item, plain):
    """One random row operation on an object; the change op it should emit."""
    keys = list(plain)
    kind = rng.choice(['add', 'set', 'remove', 'reorder'] if keys else ['add'])
    if kind == 'add':
        key = rng.choice(['p', 'q', 'r', 's', 't', 'u']) + str(rng.randint(0, 99))
        if key in plain:
            return None
        v = value(rng)
        item._add_field(key, v)
        plain[key] = v
        return JsonOpFieldInsert(key, v)
    key = rng.choice(keys)
    if kind == 'set':
        v = value(rng)
        item._set_field(key, v)
        plain[key] = v
        return JsonOpFieldUpdate(key, v)
    if kind == 'remove':
        item._remove_field(key)
        del plain[key]
        return JsonOpFieldRemove(key)
    to_key = rng.choice(keys)
    if to_key == key:
        return None
    from_value, to_value = plain[key], plain[to_key]
    item._reorder_field(key, to_key)
    keys.insert(keys.index(to_key), keys.pop(keys.index(key)))
    moved = {k: plain[k] for k in keys}
    plain.clear()
    plain.update(moved)
    return JsonOpFieldReorder(key, to_key, from_value, to_value)


def array_step(rng, item, plain):
    """One random row operation on an array; the change op it should emit."""
    kind = rng.choice(['add', 'set', 'remove', 'reorder', 'move'] if plain else ['add'])
    if kind == 'add':
        v = value(rng)
        item._add_item(v)
        plain.append(v)
        return JsonOpIndexInsert(len(plain) - 1, v)
    index = rng.randrange(len(plain))
    if kind == 'set':
        v = value(rng)
        item._set_item(index, v)
        plain[index] = v
        return JsonOpIndexUpdate(index, v)
    if kind == 'remove':
        item._remove_item(index)
        del plain[index]
        return JsonOpIndexRemove(index)
    if kind == 'reorder':
        to = index + rng.choice([-1, 1])
        if not 0 <= to < len(plain):
            return None
        op = JsonOpIndexReorder(index, to, plain[index], plain[to])
        item._reorder_item(index, to)
    else:
        to = rng.randrange(len(plain))
        if to == index:
            return None
        op = JsonOpIndexReorder(index, to, plain[index], plain[to])
        item._move_item(index, to)
    plain.insert(to, plain.pop(index))
    return op


def rows(item):
    """Each child row's path, in row order."""
    return [item.child(row, 0).path() for row in range(item.rowCount())]


for name in ('FieldObjectItem', 'FieldArrayItem', 'IndexObjectItem', 'IndexArrayItem'):
    agree, emitted, typed, addressed = True, True, True, True
    for seed in range(SEQUENCES):
        rng = random.Random(f'{name}:{seed}')
        _model, built = containers()
        (_, item, plain), = [c for c in built if c[0] == name]
        typed &= type(item).__name__ == name
        changes = []
        item.change.connect(changes.append)
        step = object_step if isinstance(plain, dict) else array_step
        for _ in range(STEPS):
            changes.clear()
            op = step(rng, item, plain)
            if op is None:
                continue
            if item.to_json() != plain:
                agree = False
            if len(changes) != 1 or changes[0].path != item.path() or changes[0].op != op:
                emitted = False
            if rows(item) != [item.path() / k for k in (plain if isinstance(plain, dict) else range(len(plain)))]:
                addressed = False
    check(f'{name}: the row under test is a {name}', typed)
    check(f'{name}: to_json follows the plain model through every step', agree)
    check(f'{name}: each step emits one change at its path, naming its row', emitted)
    check(f'{name}: every row addresses its own key or index, in order, after every step', addressed)


def reverted(name):
    """Edit field ``k`` of the object, then Revert it: (json, changes)."""
    model, built = containers()
    (_, item, _plain), = [c for c in built if c[0] == name]
    item._set_field('k' if 'k' in item._fields else 'a', 99)
    key = 'k' if 'k' in item._fields else 'a'
    changes = []
    item.change.connect(changes.append)
    items._revert_field_action(item._fields[key])
    return item.to_json(), changes, key, item, model


for name, pristine in (('FieldObjectItem', 1), ('IndexObjectItem', 1)):
    got, changes, key, item, _alive = reverted(name)
    check(f'{name}: Revert restores the pristine value', got[key] == pristine)
    check(
        f'{name}: Revert records the change at the object',
        len(changes) == 1 and changes[0].path == item.path()
        and changes[0].op == JsonOpFieldUpdate(key, pristine),
    )

sys.exit(1 if failures else 0)
