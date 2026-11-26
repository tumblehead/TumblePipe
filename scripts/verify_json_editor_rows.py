"""The Database Editor's four container rows against a plain dict or list.

Runs in preflight's qt stage (PySide6, no ``hou``). A JSON object is a
``FieldObjectItem`` under a key and an ``IndexObjectItem`` inside an
array; an array is a ``FieldArrayItem`` or an ``IndexArrayItem``. Each gets
random sequences of its row operations (objects: add, set, remove,
reorder; arrays: add, set, remove, move by one, move anywhere), run alongside
the same operation on a plain ``dict`` / ``list``:

- after every step the container's ``to_json()`` equals the plain model;
- every step emits one change, at the container's own path, naming the
  row it touched (key or index) and the value it wrote;
- every child row's path is the container's path and its own key or
  index, in row order (a stale index would address another row);
- Revert on a field restores its pristine value and records the change,
  whichever kind of object holds it; a field the document never had goes
  back to its inherited value, or goes.

And the rows as the editor shows and drives them: each scalar cell's text
and colour by origin, an array item's Move menu, the view's Delete (pinned
back to inherited, own fields and array items gone), its expanded state
kept across ``set_value``, and drag-to-reorder (a row lands right before or
after its target; never across parents, never an inherited one).

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

from PySide6.QtCore import Qt  # noqa: E402

from tumblepipe.config_editor.json_editor import JsonModel  # noqa: E402
from tumblepipe.config_editor.json_editor import items  # noqa: E402
from tumblepipe.config_editor.json_editor.types import FieldOrigin  # noqa: E402
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
        item._move_item(index, to)
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
            got = item.to_json()
            if got != plain or (isinstance(plain, dict) and list(got) != list(plain)):
                agree = False
            if len(changes) != 1 or changes[0].path != item.path() or changes[0].op != op:
                emitted = False
            if rows(item) != [item.path() / k for k in (plain if isinstance(plain, dict) else range(len(plain)))]:
                addressed = False
    check(f'{name}: the row under test is a {name}', typed)
    check(f'{name}: to_json follows the plain model, keys in order, through every step', agree)
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

# ── Revert's other branches: to the inherited value, or gone ──
model = JsonModel({'o': 2}, inherited_data={'o': 1, 'i': 5, 'obj': {'x': 1}})
model._set_field('i', 7, FieldOrigin.OVERRIDE, 5)
model._add_field('n', 3)
items._revert_field_action(model._fields['i'])
items._revert_field_action(model._fields['n'])
check(
    'Revert of a field the document never had: inherited value back, an own field gone',
    model._fields['i']._origin == FieldOrigin.INHERITED and 'n' not in model._fields
    and model.to_json() == {'o': 2},
)
nested = model._fields['obj']
nested._set_field('x', 4, FieldOrigin.OVERRIDE, 1)
items._revert_field_action(nested._fields['x'])
check(
    "Revert inside an object: back to the object's inherited value",
    nested._fields['x']._origin == FieldOrigin.INHERITED and nested._fields['x'].to_json() == 1,
)


# ── The value cells: what each scalar shows, and its colour by the row's origin ──
shows = True
for v, text in ((True, 'true'), (False, 'false'), (0, '0'), (-3, '-3'), (1.5, '1.5'),
                (2.0, '2.0'), ('', ''), ('é x', 'é x')):
    cell = items._value_item(v)
    shows &= (str(cell), cell.data(Qt.DisplayRole), cell.data(Qt.EditRole)) == (text, text, v)
    shows &= type(cell).__name__ == {bool: 'BooleanItem', int: 'IntegerItem',
                                     float: 'FloatItem', str: 'StringItem'}[type(v)]
check('each scalar cell shows its JSON text and edits its value, in its own class', shows)
SCALARS = {'b': True, 'i': 3, 'f': 1.5, 's': 'x'}
model = JsonModel(
    {**{f'own_{k}': v for k, v in SCALARS.items()},
     **{f'pin_{k}': v for k, v in SCALARS.items()}},
    inherited_data={**{f'pin_{k}': v for k, v in SCALARS.items()},
                    **{f'up_{k}': v for k, v in SCALARS.items()}},
)


def _cell_look(key):
    field = model._fields[key]
    cell = model.item(field.row(), 1)
    brush, font = cell.data(Qt.ForegroundRole), cell.data(Qt.FontRole)
    return (None if brush is None else brush.color().getRgb()[:3],
            None if font is None else font.bold())


check(
    "every scalar cell is grey when inherited, bright and bold when pinned, plain when local",
    all(
        _cell_look(f'up_{k}') == (items.INHERITED_FG.getRgb()[:3], None)
        and _cell_look(f'pin_{k}') == (items.OVERRIDE_FG.getRgb()[:3], True)
        and _cell_look(f'own_{k}') == (None, None)
        for k in SCALARS
    ),
)


# ── An array item's Move menu ──
moves = True
for seed in range(60):
    rng = random.Random(f'move:{seed}')
    doc = [1, {'k': 1}, [2], 'three', {'z': 2}, [4], None]
    rng.shuffle(doc)
    model = JsonModel({'m': list(doc)})
    array, plain = model._fields['m'], list(doc)
    for _ in range(8):
        index = rng.randrange(len(plain))
        action = rng.choice(['up', 'down', 'top', 'bottom'])
        to = {'up': index - 1, 'down': index + 1, 'top': 0, 'bottom': len(plain) - 1}[action]
        if not 0 <= to < len(plain) or to == index:
            continue
        array._items[index]._on_reorder_action(action)
        plain.insert(to, plain.pop(index))
        moves &= array.to_json() == plain and [i._index for i in array._items] == list(range(len(plain)))
check("Move Up / Down / Top / Bottom on an array item (a value, object or array) moves it there",
      moves)


# ── The view: Delete, the expanded state, and drag-to-reorder ──
from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView  # noqa: E402

from tumblepipe.config_editor.json_editor import JsonView  # noqa: E402


def _view(value, inherited=None):
    view = JsonView(value, inherited)
    view.resize(600, 900)
    view.show()
    view.expandAll()
    app.processEvents()
    return view


def _select(view, *found):
    view.selectionModel().clearSelection()
    for item in found:
        view.selectionModel().select(item.index(), QItemSelectionModel.Select)


view = _view(
    {'o': 2, 'l': 1, 'r': 5, 'arr': [1, 2, 3], 'obj': {'x': 2, 'y': 1}},
    {'o': 1, 'r': 5, 'i': 9, 'obj': {'x': 1}},
)
root = view._model
obj = root._fields['obj']
_select(view, root._fields['o'], root._fields['l'], root._fields['r'], root._fields['i'],
        root._fields['arr']._items[1], obj._fields['x'], obj._fields['y'])
view._delete_selected_items()
check(
    'Delete: a pinned field goes back to inherited, an own field and an array item go, '
    'an inherited field stays',
    root.to_json() == {'arr': [1, 3], 'obj': {}}
    and [root._fields[k]._origin for k in ('o', 'r', 'i')] == [FieldOrigin.INHERITED] * 3
    and root._fields['o'].to_json() == 1 and 'l' not in root._fields
    and obj._fields['x']._origin == FieldOrigin.INHERITED and 'y' not in obj._fields,
)

doc = {'a': {'b': {'c': 1}, 'd': [1, {'e': 2}]}, 'f': [[1], {'g': 1}], 'h': 1}


def _expansion(view):
    found = {}

    def walk(item):
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            found[str(child.path())] = view.isExpanded(child.index())
            walk(child)
    walk(view._model.invisibleRootItem())
    return found


kept = True
for seed in range(20):
    rng = random.Random(f'expand:{seed}')
    view = _view(doc)

    def walk(item):
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            view.setExpanded(child.index(), rng.random() < 0.5)
            walk(child)
    walk(view._model.invisibleRootItem())
    before = _expansion(view)
    view.set_value(doc, preserve_state=True)
    kept &= _expansion(view) == before
check('set_value keeping the state opens and closes every row as it was', kept)


class _Drop:
    def __init__(self, pos):
        self._pos, self.accepted = pos, None

    def pos(self):
        return self._pos

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def _drop(view, source, target, below):
    view.setCurrentIndex(source.index())
    view._drop_indicator_position = (
        QAbstractItemView.BelowItem if below else QAbstractItemView.AboveItem
    )
    event = _Drop(view.visualRect(target.index()).center())
    view.dropEvent(event)
    return event.accepted


def _landed(order, src, tgt, below):
    """``order`` with the row at ``src`` dropped directly before (or below:
    after) the row at ``tgt``."""
    moved = order[src]
    rest = order[:src] + order[src + 1:]
    at = rest.index(order[tgt]) + (1 if below else 0)
    return rest[:at] + [moved] + rest[at:]


dropped = True
for seed in range(60):
    rng = random.Random(f'drop:{seed}')
    keys = ['k0', 'k1', 'k2', 'k3']
    doc = {'top': 1, 'obj': {k: i for i, k in enumerate(keys)}, 'arr': [10, {'z': 1}, [3], 40]}
    view = _view(doc)
    obj, arr = view._model._fields['obj'], view._model._fields['arr']
    src, tgt = rng.sample(range(4), 2)
    below = rng.random() < 0.5
    if rng.random() < 0.5:
        want = _landed(keys, src, tgt, below)
        ok = _drop(view, obj._fields[keys[src]], obj._fields[keys[tgt]], below)
        dropped &= list(obj.to_json()) == want and (ok is True) == (want != keys)
    else:
        plain = list(doc['arr'])
        want = _landed(plain, src, tgt, below)
        ok = _drop(view, arr._items[src], arr._items[tgt], below)
        dropped &= arr.to_json() == want and (ok is True) == (want != plain)
        dropped &= [i._index for i in arr._items] == list(range(4))
    # across kinds or parents: refused
    dropped &= _drop(view, view._model._fields['top'], obj._fields['k0'], False) is False
    dropped &= _drop(view, obj._fields['k0'], arr._items[0], False) is False
check('a dragged field or array item lands right before or after its target; '
      'across parents is refused', dropped)
view = _view({'o': 1, 'p': 2}, {'o': 1, 'q': 3})
check('an inherited field cannot be dragged',
      _drop(view, view._model._fields['q'], view._model._fields['o'], False) is False
      and list(view._model.to_json()) == ['o', 'p'])

view = _view({'o': 1}, {'i': 5, 'obj': {'x': 1}})
root = view._model
root._fields['i']._on_value_changed(7)
_select(view, root._fields['i'])
view._delete_selected_items()
check('Delete on an inherited field edited here shows the inherited value again',
      root._fields['i']._origin == FieldOrigin.INHERITED and root._fields['i'].to_json() == 5
      and root.to_json() == {'o': 1})

sys.exit(1 if failures else 0)
