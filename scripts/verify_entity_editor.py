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
coverage refused, a shots Multi's Timeline, the selected Multi's workfiles
opened or made one at a time, the browser's New Multi…), the Layers
cell's dialog (a shot's and a sequence's, Use Selection, match counts, an
exported channel kept, Inherit) and a Layers value typed, New Sequence…,
problems blocking Apply, Apply of every tab through the
real ``pipe.entity_files``, and the database afterwards. Prints OK/FAIL per check; exits 1 on any FAIL.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))

import _harness  # noqa: E402,F401  (temp project, python/ on sys.path)
from _harness import reset_project  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox, QPushButton  # noqa: E402

from tumblepipe.api import default_client  # noqa: E402
from tumblepipe.config import entity_sheet as es  # noqa: E402
from tumblepipe.config.channels import list_channel_layers  # noqa: E402
from tumblepipe.config.department import list_departments  # noqa: E402
from tumblepipe.config.multi_timeline import TIMELINE_CHOICES  # noqa: E402
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

# A Multi's workfiles, as the browser would answer: Crowd has a first
# department workfile, not a second; making one waits for ``done``.
versions = {'Crowd': {POOL[0]: 'v0003'}}
workfile_calls = []
making = []


def _multi_workfiles(uri):
    workfile_calls.append(('read', uri.segments[-1]))
    return dict(versions.get(uri.segments[-1], {}))


def _open_multi_workfile(uri, department):
    workfile_calls.append(('open', uri.segments[-1], department))


def _new_multi_workfile(uri, department, done):
    workfile_calls.append(('new', uri.segments[-1], department))
    making.append((uri.segments[-1], department, done))


applied = []
source = ee.EntityEditorSource(
    project='verify', config=config,
    retire=entity_files.delete_entity, rename=entity_files.rename_entity,
    rename_blocker=lambda uri: '',
    on_applied=lambda: applied.append(True),
    rigged=lambda uri: uri == ASSETS / 'VEH' / 'car',
    multi_workfiles=_multi_workfiles,
    open_multi_workfile=_open_multi_workfile,
    new_multi_workfile=_new_multi_workfile,
    # SH020 has an fx export; the open workfile is SH010's.
    channel_exports=lambda uri: ['fx'] if uri == SHOTS / 'sq010' / 'SH020' else [],
    selected_prims=lambda: ['/CHAR/ryan', '/CHAR/kate'],
    count_prims=lambda uri, pattern: (
        len(pattern.split()) if uri == SHOTS / 'sq010' / 'SH010' else None
    ),
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
first_box = multis.columns.index(POOL[0])
check('the Multis tab has a box per department, after the Timeline',
      list(multis.columns[first_box:]) == list(shots.sheet.base.pool)
      and multis.columns[first_box - 1] == ee.TIMELINE,
      multis.columns)

# The selected Multi's workfiles, below the table: Crowd as stored.
panel = multis.workfiles
multis.select_cells([(crowd.id, 0)], (crowd.id, 0))
app.processEvents()
OPEN_0, NEW_1 = f'workfile_open_{POOL[0]}', f'workfile_new_{POOL[1]}'
check('the workfiles are the selected Multi\'s', panel.title.text() == 'Workfiles of Crowd',
      panel.title.text())
check('one row per covered department: Open for one with a workfile, New from Template for one without',
      sorted(panel.buttons) == sorted([OPEN_0, NEW_1])
      and panel.buttons[NEW_1].text() == 'New from Template', sorted(panel.buttons))
check('both can be pressed', all(b.isEnabled() for b in panel.buttons.values()))
check('it says the open scene is replaced, after asking to save it',
      'asked to save it first' in panel.note.text(), panel.note.text())
panel.buttons[OPEN_0].click()
check('Open opens that workfile', workfile_calls[-1] == ('open', 'Crowd', POOL[0]), workfile_calls)
panel.buttons[NEW_1].click()
app.processEvents()
check('New from Template makes that department\'s workfile',
      workfile_calls[-1] == ('new', 'Crowd', POOL[1]), workfile_calls)
check('one at a time: while it is made, nothing else can be pressed',
      panel.buttons[NEW_1].text() == 'Making…'
      and not any(b.isEnabled() for b in panel.buttons.values()))
panel._open(crowd, POOL[0])
check('and nothing opens meanwhile', workfile_calls[-1][0] == 'new', workfile_calls)
reads = workfile_calls.count(('read', 'Crowd'))
versions['Crowd'][POOL[1]] = 'v0001'
_name, _dept, done = making.pop()
done()
app.processEvents()
check('once made, it shows and opens like the other',
      sorted(panel.buttons) == sorted([OPEN_0, f'workfile_open_{POOL[1]}'])
      and all(b.isEnabled() for b in panel.buttons.values()), sorted(panel.buttons))
check('the versions are read again only after one is made',
      workfile_calls.count(('read', 'Crowd')) == reads + 1, workfile_calls)

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
multis.select_cells([(crowd.id, 0)], (crowd.id, 0))
app.processEvents()
check('a changed Multi\'s workfiles wait for Apply',
      panel.note.text().startswith('Apply first') and bool(panel.buttons)
      and not any(b.isEnabled() for b in panel.buttons.values()), panel.note.text())

# Overlap is refused: a new Multi covering POOL[0] over SH010 too.
shots.edit(lambda: shots.sheet.add_multi('Hero', [shots.sheet.rows[0].id], POOL[:1]))
hero = next(m for m in shots.sheet.multis if m.name == 'Hero')
check('overlapping coverage is a problem', bool(shots.sheet.multi_problems()), shots.sheet.multi_problems())
check('Apply is disabled while a Multi overlaps', not window._apply_button.isEnabled())
shots.edit(lambda: shots.sheet.set_multi_departments(hero.id, POOL[3:4]))
check('the overlap clears', not shots.sheet.multi_problems(), shots.sheet.multi_problems())
check('Apply is enabled again', window._apply_button.isEnabled())
multis.select_cells([(hero.id, 0)], (hero.id, 0))
app.processEvents()
check('a new Multi has no workfiles until Apply: its departments show none, and nothing can be pressed',
      sorted(panel.buttons) == [f'workfile_new_{d}' for d in POOL[3:4]]
      and not any(b.isEnabled() for b in panel.buttons.values())
      and 'new Multi has no workfiles' in panel.note.text(), (sorted(panel.buttons), panel.note.text()))

# A shots Multi's Timeline: a choice in its own column; the selected Multis follow.
column_t = multis.columns.index(ee.TIMELINE)
crowd_t = multis._index_of(crowd.id, column_t)
check('the Timeline cell shows Per shot, and can be changed',
      crowd_t.data() == 'Per shot' and bool(crowd_t.flags() & Qt.ItemIsEditable), crowd_t.data())
delegate = multis.table.itemDelegate()
editor = delegate.createEditor(multis.table.viewport(), None, crowd_t)
check('its editor offers the three Timelines', isinstance(editor, QComboBox) and [
    editor.itemText(i) for i in range(editor.count())] == list(TIMELINE_CHOICES))
delegate.setEditorData(editor, crowd_t)
check('starting at the Multi\'s', editor.currentText() == 'Per shot', editor.currentText())
multis.select_cells([(crowd.id, column_t), (hero.id, column_t)], (crowd.id, column_t))
editor.setCurrentText('Shared, with bookmarks')
delegate.setModelData(editor, multis.proxy, crowd_t)
check('choosing it sets every selected Multi\'s',
      crowd.timeline == (True, True) and hero.timeline == (True, True), (crowd.timeline, hero.timeline))
crowd_t = multis._index_of(crowd.id, column_t)
crowd_status = multis._index_of(crowd.id, multis.columns.index(ee.STATUS)).data()
check('the cell shows it, pending, and the status says so',
      crowd_t.data() == 'Shared, with bookmarks' and crowd_t.data(Qt.BackgroundRole) == ee._PENDING
      and 'Timeline changed' in (crowd_status or ''), (crowd_t.data(), crowd_status))
multis.select_cells([(hero.id, column_t)], (hero.id, column_t))
editor.setCurrentText('Per shot')
delegate.setModelData(editor, multis.proxy, multis._index_of(hero.id, column_t))
check('and back for one', hero.timeline == (False, False) and crowd.timeline == (True, True))
multis.set_context('assets')
check('an assets Multi has no Timeline', ee.TIMELINE not in multis.columns, multis.columns)
multis.set_context('shots')

# The browser's New Multi…: this tab, of its context, asking for a Multi
# that starts empty, whatever is selected on the Shots tab.
asked = []


def _new_chase(dialog):
    asked.append((dialog._sheet.kind.key, dialog.start_with is None, dialog._timeline is not None))
    dialog._name.setText('Chase' if dialog._timeline is not None else 'Props')
    if dialog._timeline is not None:
        dialog._timeline.setCurrentText('Shared')
    return QDialog.Accepted


shots.select_cells([(shots.sheet.rows[0].id, 0)])
ee.NewMultiDialog.exec = _new_chase
window.new_multi('shots')
window.new_multi('assets')
check('New Multi… asks on the Multis tab, of each context, with a Timeline for shots only',
      asked == [('shots', True, True), ('assets', True, False)], asked)
check('it shows the Multis tab, on the context asked for',
      window._tabs.tabData(window._tabs.currentIndex()) == 'multis' and multis.key == 'assets')
chase = next((m for m in shots.sheet.multis if m.name == 'Chase'), None)
props = next((m for m in assets.sheet.multis if m.name == 'Props'), None)
check('each is a new pending Multi, empty, covering every department, with its Timeline',
      chase is not None and props is not None and chase.is_new and not chase.members
      and chase.timeline == (True, False) and list(chase.departments) == list(shots.sheet.base.pool)
      and not props.members and props.timeline == (False, False),
      (chase, props))
members = ee.MultiMembersDialog(window, shots.sheet, chase)
members.show()
app.processEvents()
_table = members.list.table
_widest = max(
    _table.fontMetrics().horizontalAdvance(_table.item(r, 0).text())
    for r in range(_table.rowCount())
)
check('the members dialog shows each name whole, not "sq010/..."',
      _table.columnWidth(0) >= _widest, (_table.columnWidth(0), _widest))
members.close()
multis.set_context('shots')
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
# Magnus 2026-11-04: a Multi assigned on a sequence's own row. Its Multis
# cell opens the dialog and lists the sequence whole; Remove from Multi on
# the row takes it out again.
crowd = next(m for m in shots.sheet.multis if m.name == 'Crowd')
column = shots.columns.index(es.MULTIS)
seq_multis = shots.index_of(SQ020, es.MULTIS)
check('a sequence row\'s Multis cell is editable',
      bool(seq_multis.flags() & Qt.ItemIsEditable), seq_multis.flags())
ee.MultiMembershipDialog.exec = _join
shots.select_cells([(SQ020, column)], current=(SQ020, column))
shots.edit_multis(seq_multis)
check('the sequence row lists the sequence whole in the Multi',
      'entity:/shots/sq020' in crowd.members, crowd.members)
check('the sequence row shows the Multi', shots.index_of(SQ020, es.MULTIS).data() == 'Crowd',
      shots.index_of(SQ020, es.MULTIS).data())
_row, seq_shot = cell(shots, 'sq020', 'SH020', es.MULTIS)
check('its shots are members through it', seq_shot.data() == 'Crowd', seq_shot.data())
shots.set_membership(crowd.id, False)
check('Remove from Multi on the sequence row takes it out',
      'entity:/shots/sq020' not in crowd.members and shots.index_of(SQ020, es.MULTIS).data() == '',
      crowd.members)

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

# ── Layers ─────────────────────────────────────────────────────────────────

LAYERS_COLUMN = shots.columns.index(es.LAYERS)
seen = {}


def _fx_on_sequence(dialog):
    seen['sequence inherit'] = not dialog._inherit.isHidden()
    seen['sequence default locked'] = dialog._table.cellWidget(0, dialog._NAME).isReadOnly()
    dialog._on_add()
    dialog._table.cellWidget(1, dialog._NAME).setText('fx')
    dialog._table.cellWidget(1, dialog._PRIMS).setText('/FX/*')
    dialog._on_ok()
    return QDialog.Accepted


ee.LayersDialog.exec = _fx_on_sequence
shots.select_cells([(SQ010, LAYERS_COLUMN)], current=(SQ010, LAYERS_COLUMN))
shots.edit_layers(shots.index_of(SQ010, es.LAYERS))
check("a sequence's Layers dialog has no Inherit while it sets none",
      seen['sequence inherit'] is False, seen)
check('its default row cannot be renamed', seen['sequence default locked'])
check('the sequence row shows its layers, pending',
      shots.index_of(SQ010, es.LAYERS).data() == 'fx: /FX/*'
      and shots.index_of(SQ010, es.LAYERS).data(Qt.BackgroundRole) == ee._PENDING,
      shots.index_of(SQ010, es.LAYERS).data())
_row, sh020_layers = cell(shots, 'sq010', 'SH020', es.LAYERS)
check('its shots show them inherited, at once',
      sh020_layers.data() == 'fx: /FX/*' and sh020_layers.data(Qt.ForegroundRole) == ee._INHERITED,
      sh020_layers.data())


def _characters_from_selection(dialog):
    seen['matches before'] = dialog._table.item(1, dialog._MATCHES).text()
    seen['rest'] = dialog._table.item(0, dialog._MATCHES).text()
    dialog._on_add()
    dialog._table.setCurrentCell(0, dialog._PRIMS)
    seen['remove default'] = dialog._remove.isEnabled()
    dialog._table.setCurrentCell(2, dialog._PRIMS)
    dialog._table.cellWidget(2, dialog._NAME).setText('characters')
    seen['ok without prims'] = dialog._ok.isEnabled()
    dialog._on_pick()
    seen['matches'] = dialog._table.item(2, dialog._MATCHES).text()
    dialog._table.cellWidget(2, dialog._NAME).setText('char acters')
    seen['bad name blocks'] = (not dialog._ok.isEnabled(), dialog._problem.text())
    dialog._table.cellWidget(2, dialog._NAME).setText('characters')
    dialog._on_ok()
    return QDialog.Accepted


r010, _i = cell(shots, 'sq010', 'SH010', es.LAYERS)
ee.LayersDialog.exec = _characters_from_selection
shots.select_cells([(r010.id, LAYERS_COLUMN)], current=(r010.id, LAYERS_COLUMN))
shots.edit_layers(shots.index_of(r010.id, es.LAYERS))
check("the open shot's layers are counted on its stage",
      seen['matches before'] == '1 prim' and seen['rest'] == 'the rest', seen)
check('default cannot be removed', seen['remove default'] is False)
check('a layer without prims is allowed', seen['ok without prims'])
check('Use Selection fills the layer and counts it', seen['matches'] == '2 prims', seen['matches'])
check('a bad channel name blocks OK', seen['bad name blocks'][0], seen['bad name blocks'])
_row, sh010_layers = cell(shots, 'sq010', 'SH010', es.LAYERS)
check("the shot shows its own layers on top of the sequence's",
      sh010_layers.data() == 'fx: /FX/*; characters: /CHAR/ryan /CHAR/kate'
      and sh010_layers.data(Qt.FontRole).bold(), sh010_layers.data())


def _drop_export(dialog):
    row = next(r for r in range(dialog._table.rowCount())
               if dialog._table.cellWidget(r, dialog._NAME).text() == 'fx')
    dialog._table.setCurrentCell(row, dialog._PRIMS)
    seen['export remove'] = dialog._remove.isEnabled()
    seen['export locked'] = dialog._table.cellWidget(row, dialog._NAME).isReadOnly()
    dialog._set_rows([('default', '')], 0)
    seen['export ok'] = dialog._ok.isEnabled()
    seen['export problem'] = dialog._problem.text()
    return QDialog.Rejected


r020, _i = cell(shots, 'sq010', 'SH020', es.LAYERS)
ee.LayersDialog.exec = _drop_export
shots.select_cells([(r020.id, LAYERS_COLUMN)], current=(r020.id, LAYERS_COLUMN))
shots.edit_layers(shots.index_of(r020.id, es.LAYERS))
check('an exported channel cannot be removed or renamed',
      seen['export remove'] is False and seen['export locked'], seen)
check('dropping it blocks OK and says why',
      seen['export ok'] is False and 'fx has exports' in seen['export problem'], seen['export problem'])
check('Cancel changes nothing', shots.sheet.state(shots.sheet.row(r020.id), es.LAYERS) == es.INHERITED,
      shots.sheet.state(shots.sheet.row(r020.id), es.LAYERS))

# Typed (or pasted) like any cell: ``name: prims; name``.
r030, _i = cell(shots, 'sq010', 'SH030', es.LAYERS)
shots.select_cells([(r030.id, LAYERS_COLUMN)])
shots.commit(shots.index_of(r030.id, es.LAYERS), 'fx: /FX/*; matte')
check('a typed Layers value adds a channel without prims',
      shots.index_of(r030.id, es.LAYERS).data() == 'fx: /FX/*; matte',
      shots.index_of(r030.id, es.LAYERS).data())


def _inherit(dialog):
    seen['inherit shown'] = not dialog._inherit.isHidden()
    dialog._on_inherit()
    return QDialog.Accepted


ee.LayersDialog.exec = _inherit
shots.select_cells([(r020.id, LAYERS_COLUMN)])
shots.commit(shots.index_of(r020.id, es.LAYERS), 'fx: /FX/sparks')
shots.select_cells([(r020.id, LAYERS_COLUMN)], current=(r020.id, LAYERS_COLUMN))
shots.edit_layers(shots.index_of(r020.id, es.LAYERS))
check("Inherit puts a shot's layers back to its sequence's",
      seen['inherit shown'] and shots.index_of(r020.id, es.LAYERS).data() == 'fx: /FX/*'
      and shots.sheet.state(shots.sheet.row(r020.id), es.LAYERS) == es.INHERITED,
      (seen, shots.index_of(r020.id, es.LAYERS).data()))

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
check('Multi Timeline written',
      stored.get('shared_timeline') is True and stored.get('timeline_bookmarks') is True, stored)
hero = config.get_own_properties(MULTIS / 'Hero')
check('new Multi created', hero == {'members': ['entity:/shots/sq010/SH010'], 'departments': POOL[3:4]}, hero)
chase = config.get_own_properties(MULTIS / 'Chase')
check('New Multi… created, empty, with its Timeline', chase == {
    'members': [], 'departments': POOL, 'shared_timeline': True, 'timeline_bookmarks': False,
}, chase)
props = config.get_own_properties(es.GROUPS_URI / 'assets' / 'Props')
check('and the assets one', props is not None and props.get('members') == []
      and 'shared_timeline' not in props, props)
check('the sequence\'s Root written on the sequence',
      config.get_own_properties(SHOTS / 'sq020') == {'scene': 'scenes:/town'},
      config.get_own_properties(SHOTS / 'sq020'))
check('its shots keep no Root of their own', all(
    'scene' not in config.get_own_properties(SHOTS / 'sq020' / s) for s in ('SH010', 'SH020')
))
sq010 = config.get_own_properties(SHOTS / 'sq010')
check("the sequence's layers written on the sequence",
      sq010.get('channels') == ['default', 'fx'] and sq010.get('channel_prims') == {'fx': '/FX/*'}, sq010)
sh010 = config.get_own_properties(SHOTS / 'sq010' / 'SH010')
check('a shot keeps only the layers it adds',
      sh010.get('channels') == ['default', 'fx', 'characters']
      and sh010.get('channel_prims') == {'characters': '/CHAR/ryan /CHAR/kate'}, sh010)
sh020 = config.get_own_properties(SHOTS / 'sq010' / 'SH020')
check('an inheriting shot writes no layers',
      'channels' not in sh020 and 'channel_prims' not in sh020, sh020)
sh030 = config.get_own_properties(SHOTS / 'sq010' / 'SH030')
check('a typed channel without prims is written as a channel only',
      sh030.get('channels') == ['default', 'fx', 'matte'] and 'channel_prims' not in sh030, sh030)
layers = list_channel_layers(SHOTS / 'sq010' / 'SH010')
check('the farm reads the layers the column showed', layers == [
    ('default', None), ('fx', '/FX/*'), ('characters', '/CHAR/ryan /CHAR/kate'),
], layers)
check('they resolve the sequence\'s Root', all(
    config.get_properties(SHOTS / 'sq020' / s).get('scene') == 'scenes:/town' for s in ('SH010', 'SH020')
))

window.close()
app.processEvents()
print('ALL OK' if not failures else f'{len(failures)} FAILED: {failures}')
sys.exit(1 if failures else 0)
