"""Verify the database editor's edit-commit UX by driving the real widgets.

Runs headless of Houdini — the editor stack is pure qtpy — but needs a Qt
binding and a desktop session (QTest simulates real clicks and keystrokes)::

    cd scripts
    uv run --python 3.12 --with pyside6 --with qtpy python verify_database_editor_ux.py

Covers the commit model fixed on 2026-07-08 (each case prints PASS / FAIL):

  JSON pane (JsonView / JsonItemDelegate):
    A-B. Clicking away from an open value editor commits — onto another
         widget and onto the tree's own empty background (the focus-proxy
         trap: the open editor holds the view's focus, so empty-space clicks
         never close it via focus-out).
    C.   Escape cancels the edit.
    D-F. Enter commits; int editors commit on click-away; incomplete int
         input keeps the old value.
    G-I. Key renames land in to_json under the new key in row order
         (previously silently dropped — the _fields registry was never
         rekeyed), commit on click-away, and duplicate keys are rejected.
    J-M. The right-click "Revert Changes" action restores a field from the
         pristine document: local values, inherited overrides (back to
         INHERITED origin), fields added this session (removed), and fields
         nested in an object inside an array.

  URI tree (DatabaseUriView / UriDelegate):
    N.   A rename emits exactly one change on Enter (previously one per
         keystroke, each persisted to the database) and rekeys the parent's
         _items registry.
    O-Q. Renames commit on click-away, cancel on Escape, and reject
         duplicate sibling labels.
    R.   Purpose labels are not editable (purpose renames were never
         persisted, so offering an editor was a lie).

The underlying rule these cases pin down: delegate commits must go through
``setModelData`` — never the editor's ``editingFinished``/``textChanged``
signals. A commit that touches the model makes the view rewrite the
still-open editor via ``setEditorData``, clobbering the typed text before
``editingFinished`` fires.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from qtpy.QtCore import QPoint, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from tumblepipe.config_editor.database_uri_view import (
    DatabaseUriView,
    EntityOpUpdate,
    UriChangeEntity,
)
from tumblepipe.config_editor.json_editor.items import (
    FieldOrigin,
    _revert_field_action,
)
from tumblepipe.config_editor.json_editor.view import JsonView
from tumblepipe.util.uri import Uri

results = []


def check(name, got, ok):
    results.append((name, got, ok))


def _host(view):
    host = QWidget()
    layout = QVBoxLayout(host)
    other = QLineEdit(host)
    layout.addWidget(view)
    layout.addWidget(other)
    host.resize(500, 400)
    host.show()
    QTest.qWaitForWindowExposed(host)
    return host, other


def json_view(value=None, inherited=None):
    view = JsonView(value or {"name": "old", "count": 1, "other": True})
    if inherited is not None:
        view.set_value(value or {}, inherited_data=inherited)
    host, other = _host(view)
    return view, host, other


def open_editor(app, view, index, text):
    view.setCurrentIndex(index)
    view.edit(index)
    app.processEvents()
    editor = app.focusWidget()
    assert isinstance(editor, QLineEdit), f"editor is {editor!r}"
    editor.selectAll()
    QTest.keyClicks(editor, text)
    return editor


def field_index(view, key, column):
    return view._model._fields[key].index().siblingAtColumn(column)


def run_json_cases(app):
    # A: click another widget commits a string value
    view, host, other = json_view()
    open_editor(app, view, field_index(view, "name", 1), "NEW")
    QTest.mouseClick(other, Qt.LeftButton)
    app.processEvents()
    check("A value commits on click-other-widget", view.to_json()["name"],
          view.to_json()["name"] == "NEW" and view.has_change())
    host.close()

    # B: click the tree's own empty background commits
    view, host, other = json_view()
    open_editor(app, view, field_index(view, "name", 1), "NEW")
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=QPoint(400, 350))
    app.processEvents()
    check("B value commits on click-empty-tree", view.to_json()["name"],
          view.to_json()["name"] == "NEW")
    host.close()

    # C: Escape reverts
    view, host, other = json_view()
    editor = open_editor(app, view, field_index(view, "name", 1), "NEW")
    QTest.keyClick(editor, Qt.Key_Escape)
    app.processEvents()
    check("C escape reverts", (view.to_json()["name"], view.has_change()),
          view.to_json()["name"] == "old" and not view.has_change())
    host.close()

    # D: Enter commits
    view, host, other = json_view()
    editor = open_editor(app, view, field_index(view, "name", 1), "NEW")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    check("D enter commits", view.to_json()["name"],
          view.to_json()["name"] == "NEW")
    host.close()

    # E: integer commits on click-away
    view, host, other = json_view()
    open_editor(app, view, field_index(view, "count", 1), "42")
    QTest.mouseClick(other, Qt.LeftButton)
    app.processEvents()
    check("E int commits on click-away", view.to_json()["count"],
          view.to_json()["count"] == 42)
    host.close()

    # F: incomplete integer input keeps the old value
    view, host, other = json_view()
    editor = open_editor(app, view, field_index(view, "count", 1), "")
    editor.selectAll()
    QTest.keyClick(editor, Qt.Key_Delete)
    QTest.mouseClick(other, Qt.LeftButton)
    app.processEvents()
    check("F empty int keeps old value", view.to_json()["count"],
          view.to_json()["count"] == 1)
    host.close()

    # G: key rename via Enter lands in to_json under the new key, order kept
    view, host, other = json_view()
    editor = open_editor(app, view, field_index(view, "name", 0), "renamed")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    got = view.to_json()
    check("G key rename saves", list(got.keys()),
          list(got.keys()) == ["renamed", "count", "other"]
          and got["renamed"] == "old" and view.has_change())
    host.close()

    # H: key rename commits on click-away
    view, host, other = json_view()
    open_editor(app, view, field_index(view, "name", 0), "renamed")
    QTest.mouseClick(other, Qt.LeftButton)
    app.processEvents()
    check("H key rename commits on click-away", list(view.to_json().keys()),
          "renamed" in view.to_json())
    host.close()

    # I: rename to an existing sibling key is rejected
    view, host, other = json_view()
    editor = open_editor(app, view, field_index(view, "name", 0), "count")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    got = view.to_json()
    check("I duplicate key rename rejected", list(got.keys()),
          list(got.keys()) == ["name", "count", "other"]
          and not view.has_change())
    host.close()

    # J: revert restores a changed local field
    view, host, other = json_view()
    editor = open_editor(app, view, field_index(view, "name", 1), "NEW")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    assert view.has_change()
    _revert_field_action(view._model._fields["name"])
    app.processEvents()
    check("J revert restores local field",
          (view.to_json()["name"], view.has_change()),
          view.to_json()["name"] == "old" and not view.has_change())
    host.close()

    # K: revert on an override of an inherited field goes back to inherited
    view, host, other = json_view(value={}, inherited={"speed": 5})
    editor = open_editor(app, view, field_index(view, "speed", 1), "9")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    assert view.to_json() == {"speed": 9} and view.has_change()
    _revert_field_action(view._model._fields["speed"])
    app.processEvents()
    item = view._model._fields["speed"]
    check("K revert restores inherited origin",
          (view.to_json(), item._origin, view.has_change()),
          view.to_json() == {} and item._origin == FieldOrigin.INHERITED
          and not view.has_change())
    host.close()

    # L: revert removes a field added this session
    view, host, other = json_view()
    view._model._add_field("added", "x")
    assert view.has_change()
    _revert_field_action(view._model._fields["added"])
    app.processEvents()
    check("L revert removes added field",
          (sorted(view.to_json()), view.has_change()),
          "added" not in view.to_json() and not view.has_change())
    host.close()

    # M: revert a field nested in an object inside an array
    view, host, other = json_view(value={"arr": [{"x": 1}]})
    view.expandAll()
    item = view._model._fields["arr"]._items[0]._fields["x"]
    editor = open_editor(app, view, item.index().siblingAtColumn(1), "7")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    assert view.to_json() == {"arr": [{"x": 7}]} and view.has_change()
    _revert_field_action(item)
    app.processEvents()
    check("M revert field in object-in-array",
          (view.to_json(), view.has_change()),
          view.to_json() == {"arr": [{"x": 1}]} and not view.has_change())
    host.close()


class _StubAdapter:
    def list_purposes(self):
        return ["assets"]

    def list_entities(self, root):
        return [
            Uri.parse_unsafe("assets:/char/Chair"),
            Uri.parse_unsafe("assets:/char/Table"),
        ]


def uri_view():
    view = DatabaseUriView(_StubAdapter())
    host, other = _host(view)
    changes = []
    view.change.connect(changes.append)
    view.expandAll()
    return view, host, other, changes


def _char_item(view):
    return view._model._items["assets"]._items["char"]


def run_uri_cases(app):
    # N: rename via Enter commits exactly once, registry rekeyed
    view, host, other, changes = uri_view()
    editor = open_editor(app, view, _char_item(view)._items["Chair"].index(), "Sofa")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    renames = [
        change.change.op for change in changes
        if isinstance(change, UriChangeEntity)
        and isinstance(change.change.op, EntityOpUpdate)
    ]
    items = _char_item(view)._items
    check("N enter rename: single change, registry rekeyed",
          (renames, list(items.keys())),
          renames == [EntityOpUpdate("Chair", "Sofa")]
          and list(items.keys()) == ["Sofa", "Table"]
          and items["Sofa"]._label == "Sofa")
    host.close()

    # O: rename commits on click-away
    view, host, other, changes = uri_view()
    open_editor(app, view, _char_item(view)._items["Chair"].index(), "Sofa")
    QTest.mouseClick(other, Qt.LeftButton)
    app.processEvents()
    items = _char_item(view)._items
    check("O click-away rename commits", list(items.keys()),
          list(items.keys()) == ["Sofa", "Table"])
    host.close()

    # P: Escape cancels the rename, nothing emitted
    view, host, other, changes = uri_view()
    editor = open_editor(app, view, _char_item(view)._items["Chair"].index(), "Sofa")
    QTest.keyClick(editor, Qt.Key_Escape)
    app.processEvents()
    items = _char_item(view)._items
    check("P escape cancels rename", (list(items.keys()), len(changes)),
          list(items.keys()) == ["Chair", "Table"] and len(changes) == 0)
    host.close()

    # Q: rename to an existing sibling label is rejected
    view, host, other, changes = uri_view()
    editor = open_editor(app, view, _char_item(view)._items["Chair"].index(), "Table")
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
    items = _char_item(view)._items
    check("Q duplicate rename rejected", (list(items.keys()), len(changes)),
          list(items.keys()) == ["Chair", "Table"] and len(changes) == 0)
    host.close()

    # R: purposes are not editable
    view, host, other, changes = uri_view()
    purpose = view._model._items["assets"]
    check("R purpose not editable", purpose.isEditable(),
          not purpose.isEditable())
    host.close()


def main():
    app = QApplication(sys.argv)
    run_json_cases(app)
    run_uri_cases(app)

    print()
    for name, got, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}")

    return 0 if all(ok for _, _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
