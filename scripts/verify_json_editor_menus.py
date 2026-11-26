"""The Database Editor's right-click menus, opened and fired as Qt does.

Runs in preflight's qt stage (PySide6, no ``hou``). Every row of a random
document (and the empty space under the rows, which is the document
itself) has its context menu built by its own ``_on_context_menu``, with
``QMenu.exec_`` recorded instead of shown. Each action is then fired with
``QAction.trigger()``, so Qt passes ``triggered``'s ``checked`` flag the way
a click does; the Add Field name prompt and the Remove confirmations are
answered by the harness.

- the menu offers exactly the actions the row allows: Add Field / Add Item
  on a container, Move Up/Down/Top/Bottom by the item's place among its
  siblings, Convert to every other type, Remove, and Revert Changes only
  on an edited field; an inherited value row has no menu;
- a value cell (column 1) opens the same menu as its row;
- firing an action changes the document like the same edit on a plain
  dict / list, and raises nothing (a slot whose signature no longer fits
  what the section passes it raises TypeError inside Qt, which Qt prints
  and swallows: the harness counts those through ``sys.excepthook``);
- Add Field with a cancelled or empty name, or a name already there,
  changes nothing (a name already there warns); Remove answered No changes
  nothing.

Prints ok/FAIL per check; exits 1 on any FAIL.
"""

import os
import random
import sys
from copy import deepcopy
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget  # noqa: E402

app = QApplication([])
holder = QWidget()  # a JsonView parents its model; the dialogs hang off it

from PySide6.QtCore import QPoint  # noqa: E402

from tumblepipe.config_editor.json_editor import JsonModel  # noqa: E402
from tumblepipe.config_editor.json_editor import items, view  # noqa: E402
from tumblepipe.config_editor.json_editor.path import _json_path_parts  # noqa: E402
from tumblepipe.config_editor.json_editor.types import FieldOrigin  # noqa: E402

SEQUENCES = 200
STEPS = 10
failures = []


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


# ── Qt seams: the menu is recorded, the dialogs are answered ──

opened = []
raised = []
answers = {'name': ('', False), 'remove': QMessageBox.No}
warned = []


class RecordingMenu(QMenu):
    def exec_(self, *_args):
        opened.append(self)


items.QMenu = RecordingMenu
view.QMenu = RecordingMenu
items.QInputDialog.getText = staticmethod(lambda *_a, **_k: answers['name'])
items.QMessageBox.question = staticmethod(lambda *_a, **_k: answers['remove'])
items.QMessageBox.warning = staticmethod(lambda *_a, **_k: warned.append(_a))


def fire(action):
    """Trigger ``action``; what its slot raises (Qt prints and swallows it)
    lands in ``raised``."""
    hook = sys.excepthook
    sys.excepthook = lambda kind, error, trace: raised.append(f'{kind.__name__}: {error}')
    try:
        action.trigger()
        app.processEvents()
    finally:
        sys.excepthook = hook


def leaves(menu, prefix=''):
    """The menu's actions by their path ("Move/Up"), submenus walked."""
    found = {}
    for action in menu.actions():
        if action.isSeparator():
            continue
        name = prefix + action.text()
        if action.menu() is not None:
            found.update(leaves(action.menu(), name + '/'))
        else:
            found[name] = action
    return found


def menu_of(target):
    opened.clear()
    target._on_context_menu(QPoint())
    return leaves(opened[-1]) if opened else None


# ── The plain model ──

TYPES = {'null': None, 'boolean': False, 'integer': 0, 'float': 0.0, 'string': '',
         'array': [], 'object': {}}


def type_name(value):
    for name, default in TYPES.items():
        if type(value) is type(default):
            return name
    raise AssertionError(value)


def scalar(rng):
    return rng.choice([rng.randint(-3, 9), rng.choice(['', 'a', 'x y']), True, None, 2.5])


def document(rng, depth=0):
    """A random JSON object of up to four fields, nested up to two deep."""
    def value(depth):
        roll = rng.random()
        if depth < 2 and roll < 0.2:
            return document(rng, depth + 1)
        if depth < 2 and roll < 0.4:
            return [value(depth + 1) for _ in range(rng.randint(0, 4))]
        return scalar(rng)
    keys = rng.sample(['a', 'b', 'c', 'd', 'e', 'f'], rng.randint(0, 4))
    return {key: value(depth) for key in keys}


def rows(container):
    """Every row item under ``container``, depth first."""
    found = []
    children = (container._fields.values() if hasattr(container, '_fields')
                else container._items)
    for child in children:
        found.append(child)
        if isinstance(child, (items.FieldObjectItem, items.IndexObjectItem,
                              items.FieldArrayItem, items.IndexArrayItem)):
            found.extend(rows(child))
    return found


def at(plain, parts):
    for part in parts:
        plain = plain[part]
    return plain


def expected_menu(item, model, plain):
    """The action paths a row's menu should offer."""
    parts = _json_path_parts(item.path())
    current = at(plain, parts)
    keyed = isinstance(item, (items.FieldBasicItem, items.FieldObjectItem,
                              items.FieldArrayItem))
    want = set()
    if isinstance(current, dict):
        want.add('Add Field')
    if isinstance(current, list):
        want.add('Add Item')
    if keyed and item._origin == FieldOrigin.INHERITED:
        return want if want else None
    if not keyed:
        siblings = len(at(plain, parts[:-1]))
        index = parts[-1]
        if siblings > 1:
            if index > 0:
                want |= {'Move/Up', 'Move/Top'}
            if index < siblings - 1:
                want |= {'Move/Down', 'Move/Bottom'}
    mine = type_name(current)
    want |= {f'Convert/{name}' for name in TYPES if name != mine}
    want.add('Remove Field' if keyed else 'Remove Item')
    if keyed and model.has_change(item.path()):
        want.add('Revert Changes')
    return want


def apply(plain, parts, action, name, remove):
    """``action`` on the plain document; returns whether it changes it."""
    target = at(plain, parts)
    parent = at(plain, parts[:-1]) if parts else None
    if action == 'Add Field':
        key, ok = name
        if not ok or not key or key in target:
            return
        target[key] = None
    elif action == 'Add Item':
        # an array under a key seeds the new item from its first one's type
        first = target[0] if target and isinstance(parts[-1], str) else None
        target.append(TYPES[type_name(first)])
    elif action in ('Remove Field', 'Remove Item'):
        if remove == QMessageBox.Yes:
            del parent[parts[-1]]
    elif action.startswith('Move/'):
        index, last = parts[-1], len(parent) - 1
        to = {'Up': index - 1, 'Down': index + 1, 'Top': 0, 'Bottom': last}[action[5:]]
        parent.insert(to, parent.pop(index))
    elif action.startswith('Convert/'):
        parent[parts[-1]] = deepcopy(TYPES[action[8:]])
    else:
        raise AssertionError(action)


def same(a, b):
    """JSON equality with types and key order (1 vs 1.0 and 0 vs False differ)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


# ── The properties ──

offered, cells, fired, quiet, refused = True, True, True, True, True
misses = []
for seed in range(SEQUENCES):
    rng = random.Random(f'menus:{seed}')
    plain = document(rng)
    model = JsonModel(deepcopy(plain), None, holder)
    for _ in range(STEPS):
        targets = [model] + rows(model)
        target = rng.choice(targets)
        parts = [] if target is model else _json_path_parts(target.path())
        menu = menu_of(target)
        want = {'Add Field'} if target is model else expected_menu(target, model, plain)
        got = None if menu is None else set(menu)
        if got != want:
            offered = False
            misses.append(f'seed {seed} {parts}: offered {sorted(got or [])}, '
                          f'want {sorted(want or [])}')
            break
        if target is not model:
            sibling = items._sibling(target, 1)
            if sibling is not None and set(menu_of(sibling) or {}) != got:
                cells = False
        if not menu:
            continue
        action = rng.choice(sorted(a for a in menu if a != 'Revert Changes')
                            or ['Revert Changes'])
        if action == 'Revert Changes':
            continue
        answers['name'] = rng.choice([('g', True), ('h', True), ('', True), ('g', False)]
                                     + [(k, True) for k in (at(plain, parts) if action == 'Add Field' else [])])
        answers['remove'] = rng.choice([QMessageBox.Yes, QMessageBox.No])
        before = deepcopy(plain)
        warned.clear()
        raised.clear()
        apply(plain, parts, action, answers['name'], answers['remove'])
        fire(menu_of(target)[action])
        if raised:
            quiet = False
            misses.append(f'seed {seed} {parts} {action}: {raised[0]}')
        if not same(model.to_json(), plain):
            fired = False
            misses.append(f'seed {seed} {parts} {action}: got {model.to_json()}, want {plain}')
            break
        if same(before, plain) and action == 'Add Field':
            key, ok = answers['name']
            wants_warning = ok and bool(key)
            refused &= bool(warned) == wants_warning
for miss in misses[:5]:
    print('     ' + miss)
check('each row (and the empty space) offers exactly the actions it allows', offered)
check("a value cell opens its row's menu", cells)
check('firing an offered action edits the document like the plain model', fired)
check('no action raises inside Qt when triggered', quiet)
check('Add Field warns only on a name already there; a cancelled or empty name is silent',
      refused)

# An inherited value row has no menu; an inherited container offers only Add.
model = JsonModel({'own': 1}, {'i': 5, 'io': {'x': 1}, 'ia': [1]}, holder)
check('an inherited value has no menu, an inherited object or array only its Add',
      menu_of(model._fields['i']) is None
      and set(menu_of(model._fields['io'])) == {'Add Field'}
      and set(menu_of(model._fields['ia'])) == {'Add Item'})

sys.exit(1 if failures else 0)
