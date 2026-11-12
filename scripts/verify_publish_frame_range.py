"""Drive the publish window's First/Last edit (#76) headless.

Runs under Houdini's python313 (real PySide6, no licence, the headless
``hou`` stub) against a temp project made by ``tests/_harness``::

    S=<scratch dir with a sitecustomize adding Houdini's DLL dirs>
    HW="C:/Program Files/Side Effects Software/Houdini <ver>"
    QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts \\
    HOUDINI_BIN="$HW/bin" PYTHONUTF8=1 \\
    PYTHONPATH="$S;$HW/python313/lib/site-packages-forced" \\
        "$HW/python313/python.exe" scripts/verify_publish_frame_range.py [shot.png]

A shot with an anim export group (a shared layer and two channels), its
validation and its Build USD, in a real ProcessDialog: the range cells of
export and build rows edit (an int spin box), validation's do not; a group
edit lands on its children, marks them and the following build rows, and
says so on the status line; First after Last and junk are refused with the
cell put back; the edited range reaches the export node's run and its farm
submission, and the build's; reset goes back; no node parm is set. Prints
OK/FAIL per check; exits 1 on any FAIL.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))

import _harness  # noqa: E402  (temp project, python/ on sys.path)

_harness.use_stubs()  # hou only: the real PySide6 comes first

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QSpinBox, QStyleOptionViewItem  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.pipe.houdini.ui import task_factory, task_range  # noqa: E402
from tumblepipe.pipe.houdini.ui.process_dialog import ProcessDialog  # noqa: E402
from tumblepipe.pipe.houdini.ui.process_task import RANGE_EDITED_COLOR  # noqa: E402
from tumblepipe.pipe.houdini.ui.task_collection import follow_exports  # noqa: E402

failures = []


def check(name: str, ok: bool, detail: object = '') -> None:
    print(f'{"OK  " if ok else "FAIL"} {name}' + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        failures.append(name)


SHOT = Uri.parse('entity:/shots/SQ010/SH010')
runs = []


class Node:
    def __init__(self, kind, channel):
        self.kind, self.channel, self.parm_sets = kind, channel, []

    def path(self):
        return f'/stage/{self.kind}_{self.channel}'

    def get_channel_name(self):
        return self.channel

    def parm(self, name):
        return SimpleNamespace(set=lambda value: self.parm_sets.append(name))

    def native(self):
        return SimpleNamespace(node=lambda name: None)

    def execute(self, force_local=False, render_range=None):
        runs.append(('local', self.path(), render_range))

    def _export_farm(self, build=None, render_range=None):
        runs.append(('farm', self.path(), render_range))


task_factory.latest_export_path = lambda *args: None
task_factory._get_build_version = lambda *args: None
builds = []
task_factory._execute_build_local = lambda *a: builds.append(('local', *a))

split = Node('split', 'anim')
exports = [Node('export', 'default'), Node('export', 'bg')]
nodes = [split, *exports]
validate = task_factory._create_validation_task(SHOT, 'anim', exports)
group = task_factory._create_export_group_task(
    SHOT, 'anim', [split], exports, 993, 1108, depends_on=[validate.id])
build = task_factory._create_build_group_task(
    SHOT, ['bg', 'default'], depends_on=[group.id], first_frame=993, last_frame=1108)
follow_exports(build, [group])

dialog = ProcessDialog('Publish', [validate, group, build])
dialog.show()
model = dialog._model
view = dialog._tree_view
F, L = model.COLUMN_FIRST_FRAME, model.COLUMN_LAST_FRAME


def cell(task, column):
    task_item = model._task_items[task.id]
    return task_item.parent().child(task_item.row(), column)


def amber(item) -> bool:
    return item.foreground().color().name().lower() == RANGE_EDITED_COLOR.lower()


# Editable where a run uses the range
check('export group First editable', cell(group, F).isEditable())
check('export child Last editable', cell(group.children[1], L).isEditable())
check('build child First editable', cell(build.children[0], F).isEditable())
check('validation First read-only', not cell(validate, F).isEditable())
check('opens on the node range', cell(group, F).data(Qt.DisplayRole) == 993
      and cell(build.children[1], L).data(Qt.DisplayRole) == 1108)
editor = view.itemDelegate().createEditor(
    view.viewport(), QStyleOptionViewItem(), model.indexFromItem(cell(group, F)))
check('the editor is an int spin box', isinstance(editor, QSpinBox)
      and editor.minimum() <= -100000 and editor.maximum() >= 100000,
      type(editor).__name__)

# A group edit
model.setData(model.indexFromItem(cell(group, F)), 1001)
model.setData(model.indexFromItem(cell(group, L)), 1010)
app.processEvents()
check('group edit lands on every child',
      all(task_range.effective_range(c) == (1001, 1010) for c in group.children))
check('node range kept on the task', (group.children[0].first_frame,
      group.children[0].last_frame) == (993, 1108))
check('edited cells amber and italic', amber(cell(group.children[1], F))
      and cell(group.children[1], F).font().italic())
check('build rows follow, marked', cell(build.children[0], F).data(Qt.DisplayRole) == 1001
      and cell(build, L).data(Qt.DisplayRole) == 1010
      and cell(build.children[0], F).font().italic(),
      cell(build.children[0], F).data(Qt.DisplayRole))
check('status line says so', 'changed for this submit' in dialog._status_label.text(),
      dialog._status_label.text())

# Refused edits put the cell back
model.setData(model.indexFromItem(cell(group.children[0], F)), 1020)
app.processEvents()
check('First after Last refused, cell back',
      cell(group.children[0], F).data(Qt.DisplayRole) == 1001
      and task_range.effective_range(group.children[0]) == (1001, 1010))
check('refusal on the status line', 'not changed' in dialog._status_label.text(),
      dialog._status_label.text())
model.setData(model.indexFromItem(cell(group.children[1], L)), 1050)  # makes the group vary
check('group reads varies', cell(group, L).data(Qt.DisplayRole) == task_range.VARIES)
check('group keeps the First its children share', cell(group, F).data(Qt.DisplayRole) == 1001,
      cell(group, F).data(Qt.DisplayRole))
model.setData(model.indexFromItem(cell(group, L)), 'abc')
app.processEvents()
check('junk refused', cell(group, L).data(Qt.DisplayRole) == task_range.VARIES
      and task_range.effective_range(group.children[1]) == (1001, 1050))

# The runs
runs.clear()
for child in group.children:
    child.execute_local()
    child.execute_farm()
check('session and farm runs get the range', runs == [
    ('local', '/stage/split_anim', task_range.block_range((1001, 1010))),
    ('local', '/stage/split_anim', task_range.block_range((1001, 1010))),
    ('local', '/stage/export_default', task_range.block_range((1001, 1050))),
    ('farm', '/stage/export_default', task_range.block_range((1001, 1050))),
    ('local', '/stage/export_bg', task_range.block_range((1001, 1010))),
    ('farm', '/stage/export_bg', task_range.block_range((1001, 1010))),
], runs)
build.children[0].execute_local()
check('build runs over the exports\' span', builds == [
    ('local', SHOT, 'bg', 1001, 1050)], builds)
check('no node parm set', not any(n.parm_sets for n in nodes))

if len(sys.argv) > 1:
    dialog.resize(760, 420)
    dialog.grab().save(sys.argv[1])

# Reset
model.reset_all_ranges()
check('reset all: node range, unmarked', cell(group, F).data(Qt.DisplayRole) == 993
      and not cell(group.children[0], F).font().italic()
      and not amber(cell(group.children[0], F))
      and not model.has_range_edits())
runs.clear()
group.children[1].execute_local()
check('after reset the node reads its own range', runs == [
    ('local', '/stage/export_default', None)], runs)
dialog._update_status()
check('status line plain again', 'changed' not in dialog._status_label.text())

print(f'\n{"FAILED: " + ", ".join(failures) if failures else "ALL OK"}')
sys.exit(1 if failures else 0)
