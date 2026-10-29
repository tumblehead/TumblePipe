"""Drive the Entity Editor window headless, end to end.

Runs under Houdini's python313 (PySide6 and tumbletrove's theme, no
licence, no ``hou``) against a temp project made by ``tests/_harness``::

    S=<scratch dir with a sitecustomize adding Houdini's DLL dirs>
    TTW=$(cygpath -m ~/.hpm/fetch/tumbletrove-tumbletrove-<ver>/python)
    # (or ~/.hpm/packages/tumbletrove@<ver>/python when fetch/ is empty)
    HW="C:/Program Files/Side Effects Software/Houdini <ver>"
    HOUDINI_BIN="$HW/bin" PYTHONUTF8=1 \\
    PYTHONPATH="$S;$TTW;$HW/python313/lib/site-packages-forced" \\
        "$HW/python313/python.exe" scripts/verify_entity_editor.py [shot.png]

Covers both tabs: inherited cells grey and own cells bold (the Database
Editor's look), typing into a selection, Animatable ticks and the
"No, rigged" note (kept out of Copy), Add Assets,
Paste as New, a Root's assets edited for every shot using it, a shot given
its own Root, the Root dialog's shared-Root warning, the Multis tab
(coverage ticks across a selection, the Multis cell's dialog, overlapping
coverage refused), New Sequence…, problems blocking Apply, Apply of every tab through the
real ``pipe.entity_files``, and the database afterwards. Prints OK/FAIL per check; exits 1 on any FAIL.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))

import _harness  # noqa: E402,F401  (temp project, python/ on sys.path)
from _harness import reset_project  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton  # noqa: E402

from tumblepipe.api import default_client  # noqa: E402
from tumblepipe.config import entity_sheet as es  # noqa: E402
from tumblepipe.config.department import list_departments  # noqa: E402
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
# A sequence with no Root yet, given one in its own row below.
config.add_entity(Uri.parse('scenes:/town'), {'assets': [
    {'asset': 'entity:/assets/VEH/car', 'instances': 1, 'channel': 'default'},
]})
config.add_entity(SHOTS / 'sq020', {})
for shot in ('SH010', 'SH020'):
    config.add_entity(SHOTS / 'sq020' / shot, {})

# One shot Multi, covering the first two shot departments, with SH010.
MULTIS = es.GROUPS_URI / 'shots'
for uri in list(config.list_entity_uris(MULTIS)):
    config.remove_entity(uri)
POOL = [d.name for d in list_departments('shots', include_generated=False)]
config.add_entity(MULTIS / 'Crowd', {
    'members': ['entity:/shots/sq010/SH010'], 'departments': POOL[:2],
})

applied = []
source = ee.EntityEditorSource(
    project='verify', config=config,
    retire=entity_files.delete_entity, rename=entity_files.rename_entity,
    rename_blocker=lambda uri: '',
    on_applied=lambda: applied.append(True),
    rigged=lambda uri: uri == ASSETS / 'VEH' / 'car',
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
check('Status sits beside the name', shots.columns[:3] == (es.GROUP, es.NAME, ee.STATUS))
check('the legend is one line', not shots.legend.wordWrap() and '\n' not in shots.legend.text())

# ── Shots: typing into a selection ─────────────────────────────────────────

ids = [r.id for r in shots.sheet.rows if r.group == 'sq010' and r.name in ('SH020', 'SH030')]
shots.select_cells([(i, shots.columns.index(es.ROLL_END)) for i in ids])
shots.commit(shots.index_of(ids[0], es.ROLL_END), '4')
check('typing fills the selected cells',
      all(shots.sheet.value(shots.sheet.row(i), es.ROLL_END) == 4 for i in ids))
_row, typed = cell(shots, 'sq010', 'SH020', es.ROLL_END)
check('a pending value is bold and tinted',
      typed.data(Qt.FontRole).bold() and typed.data(Qt.BackgroundRole) == ee._PENDING)
# Selection is an outline: the pending tint still shows inside a selected
# cell (Houdini's amber highlight used to cover it).
app.processEvents()
shot = shots.table.viewport().grab().toImage()
centre = shots.table.visualRect(typed).center()
inside = shot.pixelColor(centre.x() + 8, centre.y())
plain = shots.table.viewport().palette().base().color()
check('a selected pending cell keeps its tint',
      inside.red() > inside.blue() + 15 and inside != plain, inside.name())
check('messages go to the footer, not a line under the table',
      not hasattr(shots, '_note'))

# The current cell's row is tinted along its whole width, selected or not.
from PySide6.QtCore import QItemSelectionModel  # noqa: E402
shots.table.clearSelection()
_row, other = cell(shots, 'sq010', 'SH030', es.NAME)
shots.table.selectionModel().setCurrentIndex(other, QItemSelectionModel.NoUpdate)
app.processEvents()
probe = shots.table.visualRect(cell(shots, 'sq010', 'SH020', es.NAME)[1]).center()
before = shots.table.viewport().grab().toImage().pixelColor(probe.x(), probe.y() + 6)
_row, current = cell(shots, 'sq010', 'SH020', es.FPS)
shots.table.selectionModel().setCurrentIndex(current, QItemSelectionModel.NoUpdate)
app.processEvents()
after = shots.table.viewport().grab().toImage().pixelColor(probe.x(), probe.y() + 6)
check('the current row is tinted beyond its current cell',
      after.lightness() > before.lightness(), f'{before.name()} -> {after.name()}')
if len(sys.argv) > 1:
    shots.table.viewport().grab().save(sys.argv[1].replace('.png', '-current-row.png'))

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
car_row, car = cell(assets, 'VEH', 'car', es.ANIMATABLE)
check('a rigged, unticked asset says so', car.data() == 'No, rigged', car.data())
check('its tooltip says what the rig does', car.data(Qt.ToolTipRole).startswith('Has a rig published'))
check('an unrigged one says nothing extra', tree.data() == 'No' and rock.data() == 'Yes', (tree.data(), rock.data()))
assets.select_cells([(car_row.id, assets.columns.index(es.ANIMATABLE))])
assets._copy()
check('Copy leaves the rig note out', QApplication.clipboard().text() == 'No', QApplication.clipboard().text())
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

# ── The Add buttons open their dialogs ─────────────────────────────────────

opened = []
_exec = ee.QDialog.exec
ee.QDialog.exec = lambda self: (opened.append(type(self).__name__), ee.QDialog.Rejected)[1]
for pane in (shots, assets):
    next(b for b in pane.findChildren(ee.QPushButton) if b.text().startswith('Add ')).click()
ee.QDialog.exec = _exec
check('Add Shots… and Add Assets… open their dialogs',
      opened == ['AddShotsDialog', 'AddAssetsDialog'], opened)

# A sequence named before the letters-and-digits rule, with no Root: written
# straight into the file, as add_entity refuses such names now.
legacy = config.snapshot('entity')
legacy['children']['shots']['children']['sq_0090'] = {
    'properties': {}, 'children': {'Shot_0030': {'properties': {}, 'children': {}}},
}
config.write_root('entity', legacy)
window._reload()
_row, rootless = cell(shots, 'sq_0090', 'Shot_0030', es.ASSETS)
check('a shot with no Root has an empty Assets cell', rootless.data() == '', rootless.data())
note = ee.AddShotsDialog(shots, 'sq_0090')._sequence_note.text()
check('Add Shots says when a sequence\'s names have no pattern', 'without a number pattern' in note, note)
sized = ee.AddAssetsDialog(assets, 'PROP')
check('Add Assets is as tall as its content', sized.height() == sized.sizeHint().height(),
      (sized.height(), sized.sizeHint().height()))
sized.reject()

# ── Multis ─────────────────────────────────────────────────────────────────

_row, crowd_cell = cell(shots, 'sq010', 'SH010', es.MULTIS)
check('the Multis column names the Multi', crowd_cell.data() == 'Crowd', crowd_cell.data())
check('a Multis tab', window._tabs.tabText(window._tab_index('multis')) == 'Multis')
window.show_kind('multis')
app.processEvents()
multis = window.multis_pane
multis.set_context('shots')
crowd = next(m for m in shots.sheet.multis if m.name == 'Crowd')
check('the Multis tab has a box per department',
      list(multis.columns[3:]) == list(shots.sheet.base.pool) and multis.columns[3:3 + 2] == POOL[:2],
      multis.columns)

# The Multis cell's dialog: SH020 and SH030 join Crowd.
ids = [r.id for r in shots.sheet.rows if r.group == 'sq010' and r.name in ('SH020', 'SH030')]


def _join(dialog):
    dialog.list.set_state(crowd.id, Qt.Checked)
    dialog._on_ok()
    return QDialog.Accepted


ee.MultiMembershipDialog.exec = _join
shots.select_cells([(i, shots.columns.index(es.MULTIS)) for i in ids])
shots.edit_multis(shots.index_of(ids[0], es.MULTIS))
check('the Multis cell joins the selected shots',
      all(i in crowd.members for i in ids), crowd.members)
_row, joined = cell(shots, 'sq010', 'SH020', es.MULTIS)
check('a joined shot shows the Multi, pending',
      joined.data() == 'Crowd' and joined.data(Qt.BackgroundRole) == ee._PENDING)
check('its status says so', 'Multis changed' in shots.model.status(shots.sheet.row(ids[0]))[0])

# Coverage: untick the second department, tick the third.
column = multis.columns.index(POOL[1])
multis.tick(multis._index_of(crowd.id, column), False)
if len(POOL) > 2:
    multis.tick(multis._index_of(crowd.id, multis.columns.index(POOL[2])), True)
check('ticking sets the coverage',
      set(crowd.departments) == {POOL[0], *POOL[2:3]}, crowd.departments)
check('the Multis tab shows it is changed', window._tabs.tabText(window._tab_index('multis')) == 'Multis *')

# Overlap is refused: a new Multi covering POOL[0] over SH010 too.
shots.edit(lambda: shots.sheet.add_multi('Hero', [shots.sheet.rows[0].id], POOL[:1]))
hero = next(m for m in shots.sheet.multis if m.name == 'Hero')
check('overlapping coverage is a problem', bool(shots.sheet.multi_problems()), shots.sheet.multi_problems())
check('Apply is disabled while a Multi overlaps', not window._apply_button.isEnabled())
shots.edit(lambda: shots.sheet.set_multi_departments(hero.id, POOL[3:4]))
check('the overlap clears', not shots.sheet.multi_problems(), shots.sheet.multi_problems())
check('Apply is enabled again', window._apply_button.isEnabled())
if len(sys.argv) > 1:
    window.show_kind('multis')
    multis.select_cells([(crowd.id, column)])
    app.processEvents()
    window.grab().save(sys.argv[1].replace('.png', '-multis.png'))
window.show_kind('shots')

# ── New Sequence… ──────────────────────────────────────────────────────────

check('a New Sequence… button', any(
    b.text() == 'New Sequence…' for b in shots.findChildren(QPushButton)
))
taken = ee.NewGroupDialog(shots.sheet)
taken._name.setText('SQ010')
check('a name taken in another case is refused',
      'differs only by case' in taken.problem() and not taken._create.isEnabled(), taken.problem())
taken.reject()


def _name_it(dialog):
    dialog._name.setText('sq090')
    return QDialog.Accepted


ee.NewGroupDialog.exec = _name_it
shots.new_group()
check('New Sequence… adds an empty sequence, pending', shots.sheet.new_groups == ('sq090',))
check('the dropdown shows it, marked new', shots._group_filter.currentText() == 'sq090 (new)',
      shots._group_filter.currentText())
pending = ' '.join(window._pending()[0])
check('Apply counts it', '1 new sequence' in pending, pending)
shots.remove_new_group('sq090')
check('removing the one shown shows every row again',
      shots.proxy.group == '' and shots.proxy.rowCount() == shots.model.rowCount() > 0,
      (shots.proxy.group, shots.proxy.rowCount(), shots.model.rowCount()))
shots.undo()
check('undo brings it back', shots.sheet.new_groups == ('sq090',))
shots.focus('')

# ── A sequence's own row ───────────────────────────────────────────────────

SQ010, SQ020 = ee.GroupKey('sq010'), ee.GroupKey('sq020')
keys = shots.model._ids
check('each sequence has its own row above its shots',
      keys[0] == SQ010 and all(shots.sheet.row(k).group == 'sq010' for k in keys[1:4])
      and keys[4] == SQ020 and all(shots.sheet.row(k).group == 'sq020' for k in keys[5:7]),
      keys)
seq_root = shots.index_of(SQ010, es.ROOT)
check('the sequence row shows its own Root, bold',
      seq_root.data() == 'forest' and seq_root.data(Qt.FontRole).bold(), seq_root.data())
check('its name cell counts its shots', shots.index_of(SQ010, es.NAME).data() == '3 shots',
      shots.index_of(SQ010, es.NAME).data())
_row, shot_root = cell(shots, 'sq010', 'SH020', es.ROOT)
check('a shot shows the Root it inherits from its sequence',
      shot_root.data() == 'forest' and shot_root.data(Qt.ForegroundRole) == ee._INHERITED
      and 'sequence sq010' in shot_root.data(Qt.ToolTipRole), shot_root.data(Qt.ToolTipRole))
check('a sequence\'s Status, Group and Name cannot be typed into', not any(
    shots.index_of(SQ020, c).flags() & Qt.ItemIsEditable for c in (es.GROUP, es.NAME, ee.STATUS)
))
shots.select_cells([(SQ020, shots.columns.index(es.ROOT))])
shots.commit(shots.index_of(SQ020, es.ROOT), 'scenes:/town')
_row, sq020_root = cell(shots, 'sq020', 'SH010', es.ROOT)
_row, sq020_assets = cell(shots, 'sq020', 'SH020', es.ASSETS)
check('a Root typed on a sequence reaches its shots at once',
      sq020_root.data() == 'town' and sq020_assets.data() == 'VEH/car'
      and sq020_root.data(Qt.ForegroundRole) == ee._INHERITED,
      (sq020_root.data(), sq020_assets.data()))
check('the sequence row is pending, its status says so',
      shots.index_of(SQ020, es.ROOT).data(Qt.BackgroundRole) == ee._PENDING
      and shots.index_of(SQ020, ee.STATUS).data() == 'Changed')
check('its Assets cell shows what its shots get', shots.index_of(SQ020, es.ASSETS).data() == 'VEH/car')
pending = ' '.join(window._pending()[0])
check('Apply counts the changed sequence', '1 sequence changed' in pending, pending)
shots._on_clicked(shots.index_of(SQ020, es.GROUP))
folded = shots.index_of(SQ020, es.GROUP).data()
hidden = [shots.index_of(r.id, es.NAME).isValid() for r in shots.sheet.rows if r.group == 'sq020']
check('clicking a sequence\'s name folds its shots away',
      folded.startswith('▸') and hidden == [False, False], (folded, hidden))
shots._on_clicked(shots.index_of(SQ020, es.GROUP))
check('and again shows them', all(
    shots.index_of(r.id, es.NAME).isValid() for r in shots.sheet.rows if r.group == 'sq020'
))
shots._search.setText('SH020')
shown = [shots._row_id(shots.proxy.index(p, 0)) for p in range(shots.proxy.rowCount())]
check('filtering keeps each sequence row above its matching shots',
      [k if isinstance(k, ee.GroupKey) else shots.sheet.row(k).name for k in shown]
      == [SQ010, 'SH020', SQ020, 'SH020'], shown)
shots._search.setText('')
r030, _i = cell(shots, 'sq010', 'SH030', es.ROLL_START)
r2010, _i = cell(shots, 'sq020', 'SH010', es.ROLL_START)
shots.select_cells([(r030.id, shots.columns.index(es.ROLL_START))])
shots.paste_text('2\n3\n')
check('a pasted block runs over a sequence row, not into it',
      shots.sheet.value(r030, es.ROLL_START) == 2 and shots.sheet.value(r2010, es.ROLL_START) == 3
      and es.ROLL_START not in shots.sheet.group_own('sq020'),
      (shots.sheet.value(r030, es.ROLL_START), shots.sheet.value(r2010, es.ROLL_START)))
shots.select_cells([(SQ010, shots.columns.index(es.FPS))])
shots.commit(shots.index_of(SQ010, es.FPS), '0')
check('a bad sequence value is a problem that blocks Apply',
      'FPS' in shots.index_of(SQ010, ee.STATUS).data() and not window._apply_button.isEnabled(),
      shots.index_of(SQ010, ee.STATUS).data())
shots._reset_cells()
check('Delete puts it back', 'fps' not in shots.sheet.group_own('sq010') and window._apply_button.isEnabled())
if len(sys.argv) > 1:
    app.processEvents()
    window.grab().save(sys.argv[1].replace('.png', '-sequences.png'))

# ── Apply ──────────────────────────────────────────────────────────────────

QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
ok = window.apply()
check('Apply succeeds', ok and applied == [True])
check('nothing left pending', not window.dirty)
app.processEvents()
footer = window._summary.text()
check('the footer says what Apply did', footer.startswith('Applied: ') and 'new asset' in footer, footer)
tops = []
for kind in ('shots', 'assets'):
    window.show_kind(kind)
    app.processEvents()
    tops.append(window.panes[kind].add_button.mapTo(window, window.panes[kind].add_button.rect().topLeft()).y())
check('the row actions stay put across tabs after Apply', tops[0] == tops[1], tops)
window.show_kind('shots')
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
stored = config.get_own_properties(MULTIS / 'Crowd')
check('Multi members written', stored['members'] == [
    'entity:/shots/sq010/SH010', 'entity:/shots/sq010/SH020', 'entity:/shots/sq010/SH030',
], stored)
check('Multi coverage written', stored['departments'] == [POOL[0], *POOL[2:3]], stored)
check('the new sequence is created, empty', config.get_own_properties(SHOTS / 'sq090') == {},
      config.get_own_properties(SHOTS / 'sq090'))
hero = config.get_own_properties(MULTIS / 'Hero')
check('new Multi created', hero == {'members': ['entity:/shots/sq010/SH010'], 'departments': POOL[3:4]}, hero)
check('the sequence\'s Root written on the sequence',
      config.get_own_properties(SHOTS / 'sq020') == {'scene': 'scenes:/town'},
      config.get_own_properties(SHOTS / 'sq020'))
check('its shots keep no Root of their own', all(
    'scene' not in config.get_own_properties(SHOTS / 'sq020' / s) for s in ('SH010', 'SH020')
))
check('they resolve the sequence\'s Root', all(
    config.get_properties(SHOTS / 'sq020' / s).get('scene') == 'scenes:/town' for s in ('SH010', 'SH020')
))

window.close()
app.processEvents()
print('ALL OK' if not failures else f'{len(failures)} FAILED: {failures}')
sys.exit(1 if failures else 0)
