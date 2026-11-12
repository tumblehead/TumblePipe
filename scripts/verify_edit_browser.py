"""Drive the Asset Browser's Edits section headless: its list columns (#55),
its card menu and the Rename / Delete dialogs (#62).

Runs under Houdini's python313 (PySide6, no licence; ``hou`` is the tests'
stub) against a temp project made by ``tests/_harness``, with the
TumbleTrove the catalog is meant for (``TUMBLETROVE_PYTHON``, its checkout's
``python/``)::

    S=<scratch dir with a sitecustomize adding Houdini's DLL dirs>
    TTW=<tumbletrove checkout>/python
    HW="C:/Program Files/Side Effects Software/Houdini <ver>"
    MSYS_NO_PATHCONV=1 QT_QPA_PLATFORM=offscreen HOUDINI_BIN="$HW/bin" \\
    PYTHONUTF8=1 TUMBLETROVE_PYTHON="$TTW" \\
    PYTHONPATH="$S;$TTW;$HW/python313/lib/site-packages-forced" \\
        "$HW/python313/python.exe" scripts/verify_edit_browser.py

Covers: TumbleTrove's list (``AssetGrid``) shows Name, Published, Working,
Master, User, Edited in the Edits scope, the master edit ticked, and the
catalog's own columns elsewhere (a TumbleTrove without
``get_list_columns_for`` is skipped with a note); the edit card's menu
renders through TumbleTrove's ``catalog_menu`` with Rename Edit… and Delete
Edit… after a separator; Rename Edit… answered in the real QInputDialog
renames the edit; Delete Edit… on the master edit is refused in a message,
and on another edit, confirmed in the real QMessageBox, removes it. Prints
OK/FAIL per check; exits 1 on any FAIL.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))

# The real PySide6 first: tests/_stubs (appended below) has an inert one.
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QInputDialog, QMenu, QMessageBox,
)

app = QApplication.instance() or QApplication(sys.argv)

import _catalog  # noqa: E402  (the real tumbletrove SDK + temp project)
import _harness  # noqa: E402

os.environ['HOUDINI_USER_PREF_DIR'] = str(_harness.PROJECT_PATH / '_prefs')
_harness.use_stubs()
import hou  # noqa: E402  (tests/_stubs/hou.py)

hou.qt = SimpleNamespace(mainWindow=lambda: None)
hou.severityType = getattr(hou, 'severityType', SimpleNamespace(Warning=2))

from tumbletrove.asset_browser.api.catalog import Catalog  # noqa: E402
from tumbletrove.asset_browser.ui.asset_grid import AssetGrid  # noqa: E402
from tumbletrove.asset_browser.ui.catalog_menu import fill_catalog_menu  # noqa: E402

from tumblepipe.asset_browser import edits as edits_mod  # noqa: E402
from tumblepipe.asset_browser.catalog import PipelineCatalog  # noqa: E402
from tumblepipe.pipe import edits as pipe_edits  # noqa: E402

failures = []


def check(name: str, ok: bool, detail: object = '') -> None:
    print(f'{"OK  " if ok else "FAIL"} {name}' + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        failures.append(name)


PROJ = _harness.PROJECT_PATH.name
registry = _catalog.make_registry(_catalog.make_project(
    PROJ, _harness.PROJECT_PATH, _harness.PROJECT_PATH / '_config',
))
cat = PipelineCatalog(registry)
cat._activator.activate = lambda proj: None
client = cat._clients.get(PROJ)

for name in ('cutA', 'cutB'):
    pipe_edits.create_edit(name, client)
folder = pipe_edits.working_dir('cutA', client)
folder.mkdir(parents=True, exist_ok=True)
(folder / pipe_edits.working_file_name('cutA', 'v0003')).write_bytes(b'{}')
pipe_edits.set_master_edit('cutA', client)
cat.invalidate_cache()

edits_scope = frozenset({f'source:{cat.id}', f'project:{PROJ}', edits_mod.TYPE_TAG})
cards = cat.get_assets(tags=edits_scope, page_size=100).assets
check('the Edits scope has a card per edit', sorted(c.name for c in cards) == ['cutA', 'cutB'],
      [c.name for c in cards])

# ── #55: the list's columns ──────────────────────────────────────────
if 'get_list_columns_for' not in vars(Catalog):
    print('NOTE this TumbleTrove has no get_list_columns_for: the list shows '
          'the catalog columns everywhere (the column checks are skipped)')
else:
    from tumbletrove.asset_browser.core.list_columns import list_columns_for

    grid = AssetGrid(get_deck_items_fn=lambda _a: [])
    grid.resize(900, 400)
    grid.set_list_mode(True)
    grid.set_list_columns(list_columns_for([cat], cat.id, edits_scope))
    grid.set_assets(cards)
    grid.show()
    app.processEvents()
    tree = grid._tree
    header = [tree.headerItem().text(i) for i in range(tree.columnCount())]
    check('the Edits list shows Name, Published, Working, Master, User, Edited',
          header == ['Name', 'Published', 'Working', 'Master', 'User', 'Edited'], header)
    rows = {}
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        rows[item.text(0)] = [item.text(c) for c in range(tree.columnCount())]
    check('the master edit is ticked, its working version shown; the other is not ticked',
          rows.get('cutA', [''] * 4)[2:4] == ['v0003', edits_mod.MASTER_MARK]
          and rows.get('cutB', [''] * 4)[3] == '', rows)
    grid.set_list_columns(list_columns_for(
        [cat], cat.id, frozenset({f'source:{cat.id}', f'project:{PROJ}', 'type:shot'})))
    grid.set_assets(cards)
    app.processEvents()
    header = [tree.headerItem().text(i) for i in range(tree.columnCount())]
    check('other scopes keep the catalog columns (Category, Depts)',
          'Category' in header and 'Depts' in header and 'Master' not in header, header)

# ── #62: the card menu ───────────────────────────────────────────────
by_name = {c.name: c for c in cards}
menu = QMenu()
fill_catalog_menu(menu, cat.get_card_menu_items(by_name['cutB']))
texts = [('---' if a.isSeparator() else a.text()) for a in menu.actions()]
check('the edit menu ends with a separator, Rename Edit…, Delete Edit…',
      texts[-3:] == ['---', 'Rename Edit…', 'Delete Edit…'], texts)


def answer_modal(act) -> None:
    """Answer the next modal dialog as an artist would."""
    def poke():
        dialog = app.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(20, poke)
            return
        act(dialog)
    QTimer.singleShot(20, poke)


def type_name(text):
    def act(dialog):
        if isinstance(dialog, QInputDialog):
            dialog.setTextValue(text)
            dialog.accept()
        else:
            dialog.reject()
    return act


def press_yes(dialog):
    if isinstance(dialog, QMessageBox):
        dialog.button(QMessageBox.Yes).click()
    else:
        dialog.reject()


messages = []
hou.ui.displayMessage = lambda text, *a, **k: messages.append(text)


def action(label: str, card):
    for name, act in cat.get_card_menu_items(card):
        if name == label:
            return act
    raise AssertionError(label)


answer_modal(type_name('cutRenamed'))
action('Rename Edit…', by_name['cutB'])()
check('Rename Edit… renames the edit through the real dialog',
      pipe_edits.list_edits(client) == ['cutA', 'cutRenamed'], pipe_edits.list_edits(client))

messages.clear()
action('Delete Edit…', by_name['cutA'])()
check('Delete Edit… refuses the master edit with a message',
      pipe_edits.list_edits(client) == ['cutA', 'cutRenamed']
      and len(messages) == 1 and 'master edit' in messages[0], messages)

renamed = cat.get_asset(edits_mod.EditRef(PROJ, 'cutRenamed').asset_id)
answer_modal(press_yes)
action('Delete Edit…', renamed)()
check('Delete Edit… confirmed in the real message box removes the edit',
      pipe_edits.list_edits(client) == ['cutA'], pipe_edits.list_edits(client))

print('FAILED: ' + ', '.join(failures) if failures else 'ALL OK')
sys.exit(1 if failures else 0)
