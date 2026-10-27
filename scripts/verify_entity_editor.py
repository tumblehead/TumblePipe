"""Drive the Entity Editor window headless, end to end.

Runs under Houdini's python313 (PySide6 and tumbletrove's theme, no
licence, no ``hou``) against a temp project made by ``tests/_harness``::

    S=<scratch dir with a sitecustomize adding Houdini's DLL dirs>
    TTW=$(cygpath -m ~/.hpm/fetch/tumbletrove-tumbletrove-<ver>/python)
    HW="C:/Program Files/Side Effects Software/Houdini <ver>"
    HOUDINI_BIN="$HW/bin" PYTHONUTF8=1 \\
    PYTHONPATH="$S;$TTW;$HW/python313/lib/site-packages-forced" \\
        "$HW/python313/python.exe" scripts/verify_entity_editor.py [shot.png]

Covers both tabs: inherited cells grey and own cells bold (the Database
Editor's look), typing into a selection, Animatable ticks, Add Assets,
Paste as New, a Root's assets edited for every shot using it, a shot given
its own Root, the Root dialog's shared-Root warning, problems blocking
Apply, Apply of both tabs through the real ``pipe.entity_files``, and the
database afterwards. Prints OK/FAIL per check; exits 1 on any FAIL.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))

import _harness  # noqa: E402,F401  (temp project, python/ on sys.path)
from _harness import reset_project  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tumblepipe.api import default_client  # noqa: E402
from tumblepipe.config import entity_sheet as es  # noqa: E402
from tumblepipe.pipe import entity_files  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from tumblepipe.asset_browser import entity_editor as ee  # noqa: E402

failures = []


def check(name: str, ok: bool, detail: object = '') -> None:
    print(f'{"OK  " if ok else "FAIL"} {name}' + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        failures.append(name)


# ── A project ──────────────────────────────────────────────────────────────

reset_project()
config = default_client().config
SHOTS, ASSETS = es.SHOTS_URI, es.ASSETS_URI
config.set_own_properties(SHOTS, {**(config.get_own_properties(SHOTS) or {}), 'frame_start': 1001, 'frame_end': 1100})
for category, names in (('PROP', ('tree', 'rock')), ('VEH', ('car',))):
    if config.get_own_properties(ASSETS / category) is None:
        config.add_entity(ASSETS / category, {})
    for name in names:
        config.add_entity(ASSETS / category / name, {'name': name})
config.set_own_properties(ASSETS / 'PROP' / 'rock', {'name': 'rock', 'animatable': True})
config.add_entity(Uri.parse('scenes:/forest'), {'assets': [
    {'asset': 'entity:/assets/PROP/tree', 'instances': 3, 'channel': 'default'},
]})
config.add_entity(SHOTS / 'sq010', {'scene': 'scenes:/forest'})
for shot, own in (('SH010', {'frame_end': 1050}), ('SH020', {}), ('SH030', {})):
    config.add_entity(SHOTS / 'sq010' / shot, own)

applied = []
submitted = []
source = ee.EntityEditorSource(
    project='verify', config=config,
    retire=entity_files.delete_entity, rename=entity_files.rename_entity,
    rename_blocker=lambda uri: '',
    on_applied=lambda: applied.append(True),
    submit=lambda context, uris: submitted.append((context, uris)),
)
window = ee.EntityEditorWindow(source, kind='shots')
window.show()
app.processEvents()
shots = window.panes['shots']
assets = window.panes['assets']


def cell(pane, group, name, column):
    row = next(r for r in pane.sheet.rows if r.group == group and r.name == name)
    return row, pane.index_of(row.id, column)


# ── Look ───────────────────────────────────────────────────────────────────

_row, own_end = cell(shots, 'sq010', 'SH010', es.FRAME_END)
_row, inherited_end = cell(shots, 'sq010', 'SH020', es.FRAME_END)
check('own value is bright and bold',
      own_end.data(Qt.ForegroundRole) == ee._OVERRIDE and own_end.data(Qt.FontRole).bold())
check('inherited value is grey, not bold',
      inherited_end.data(Qt.ForegroundRole) == ee._INHERITED and not inherited_end.data(Qt.FontRole))
_row, assets_cell = cell(shots, 'sq010', 'SH020', es.ASSETS)
check('Assets column shows the Root list', assets_cell.data() == 'PROP/tree ×3', assets_cell.data())
check('Assets of an inherited Root are grey', assets_cell.data(Qt.ForegroundRole) == ee._INHERITED)

# ── Shots: typing into a selection ─────────────────────────────────────────

ids = [r.id for r in shots.sheet.rows if r.name in ('SH020', 'SH030')]
shots.select_cells([(i, shots.columns.index(es.ROLL_END)) for i in ids])
shots.commit(shots.index_of(ids[0], es.ROLL_END), '4')
check('typing fills the selected cells',
      all(shots.sheet.value(shots.sheet.row(i), es.ROLL_END) == 4 for i in ids))
_row, typed = cell(shots, 'sq010', 'SH020', es.ROLL_END)
check('a pending value is bold and tinted',
      typed.data(Qt.FontRole).bold() and typed.data(Qt.BackgroundRole) == ee._PENDING)

# ── Root assets: the shared Root ───────────────────────────────────────────

row020, _ = cell(shots, 'sq010', 'SH020', es.ASSETS)
dialog = ee.RootAssetsDialog(shots, row020)
intro = dialog.findChildren(ee.QLabel)[0].text()
check('dialog warns the Root is shared', '2 other shots' in intro and 'SH010' in intro, intro)
dialog.picker.set_checked('entity:/assets/VEH/car', True, 2)
check('picker returns own list', [e.label for e in dialog.picker.own()] == ['PROP/tree ×3', 'VEH/car ×2'],
      dialog.picker.own())
dialog.reject()
shots.apply_assets_action(row020.id, ('root', 'scenes:/forest', dialog.picker.own()))
_row, a010 = cell(shots, 'sq010', 'SH010', es.ASSETS)
check('changing the Root changes every shot using it',
      a010.data() == 'PROP/tree ×3, VEH/car ×2' and a010.data(Qt.BackgroundRole) == ee._PENDING, a010.data())

# ── Root assets: a shot's own Root ─────────────────────────────────────────

row030, _ = cell(shots, 'sq010', 'SH030', es.ASSETS)
shots.apply_assets_action(row030.id, ('own', 'sq010_SH030', (es.RootAsset('entity:/assets/PROP/rock'),)))
_row, a030 = cell(shots, 'sq010', 'SH030', es.ASSETS)
_row, r030 = cell(shots, 'sq010', 'SH030', es.ROOT)
check('own Root: the shot points at it', r030.data() == 'sq010_SH030', r030.data())
check('own Root: its assets show', a030.data() == 'PROP/rock', a030.data())
check('own Root: other shots keep the shared list', a010.data() == 'PROP/tree ×3, VEH/car ×2')

# ── Assets tab ─────────────────────────────────────────────────────────────

window.show_kind('assets')
_row, rock = cell(assets, 'PROP', 'rock', es.ANIMATABLE)
_row, tree = cell(assets, 'PROP', 'tree', es.ANIMATABLE)
check('Animatable shows a box', rock.data(Qt.CheckStateRole) == Qt.Checked
      and tree.data(Qt.CheckStateRole) == Qt.Unchecked)
assets.proxy.setData(tree, Qt.Checked, Qt.CheckStateRole)
check('ticking Animatable sets it', assets.sheet.value(assets.sheet.row(_row.id), es.ANIMATABLE) is True)
add = ee.AddAssetsDialog(assets, 'PROP')
add.set_names('bush, cone')
assets.add_rows(add.rows())
add.reject()
check('Add Assets adds rows', {('PROP', 'bush'), ('PROP', 'cone')}
      <= {(r.group, r.name) for r in assets.sheet.rows if r.is_new})
assets.paste_new('FX\tspark\nsq010\tbad')
problems = assets.sheet.problems()
check('a category named like a sequence is a problem',
      any('a sequence' in p for p in problems.values()), problems)
check('Apply is disabled while a row needs fixing', not window._apply_button.isEnabled())
bad = next(r for r in assets.sheet.rows if r.group == 'sq010')
assets.edit(lambda: assets.sheet.set_retired([bad.id]))
check('choices include new assets', 'entity:/assets/FX/spark' in window.asset_choices())
check('footer counts both tabs', 'new asset' in window._summary.text() and 'shot' in window._summary.text(),
      window._summary.text())

if len(sys.argv) > 1:
    window.resize(1400, 520)
    app.processEvents()
    window.grab().save(sys.argv[1])
    window.show_kind('shots')
    app.processEvents()
    window.grab().save(sys.argv[1].replace('.png', '_shots.png'))

# ── Submit asks for Apply first ────────────────────────────────────────────

QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
window._on_submit()
check('Submit waits for Apply', submitted == [])

# ── Apply ──────────────────────────────────────────────────────────────────

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
ok = window.apply()
check('Apply succeeds', ok and applied == [True])
check('nothing left pending', not window.dirty)
forest = config.get_own_properties(Uri.parse('scenes:/forest'))['assets']
check('shared Root written', [(e['asset'], e['instances']) for e in forest]
      == [('entity:/assets/PROP/tree', 3), ('entity:/assets/VEH/car', 2)], forest)
own_root = config.get_own_properties(Uri.parse('scenes:/sq010_SH030'))
check('own Root created', own_root and own_root['assets'][0]['asset'] == 'entity:/assets/PROP/rock', own_root)
check('shot points at its Root', config.get_own_properties(SHOTS / 'sq010' / 'SH030').get('scene') == 'scenes:/sq010_SH030')
check('roll written', config.get_own_properties(SHOTS / 'sq010' / 'SH020').get('roll_end') == 4)
check('animatable written', config.get_own_properties(ASSETS / 'PROP' / 'tree').get('animatable') is True)
spark = config.get_own_properties(ASSETS / 'FX' / 'spark')
check('new asset in a new category, with its name', spark == {'name': 'spark'}, spark)
check('retired new row was never created', config.get_own_properties(ASSETS / 'sq010') is None)

shots.select_cells([(shots.sheet.rows[0].id, 1)])
window.show_kind('shots')
window._on_submit()
check('Submit opens Farm Submit on the selection',
      submitted and submitted[0][0] == 'shots' and str(submitted[0][1][0]) == 'entity:/shots/sq010/SH010',
      submitted)

window.close()
app.processEvents()
print('ALL OK' if not failures else f'{len(failures)} FAILED: {failures}')
sys.exit(1 if failures else 0)
