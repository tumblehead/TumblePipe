"""Drive Settings > Project defaults > Farm (#34) headless.

Runs like ``verify_library_settings.py`` under Houdini's python313 (PySide6
and tumbletrove's theme on PYTHONPATH, Houdini's bin on the DLL path, no
licence, no ``hou``) against a temp project made by ``tests/_harness``.

Covers: Add... writes a pool, Set Default marks it (bold "(default)") and
writes farm.default_pool, Add... on Priorities writes a named preset,
removing the default pool clears the default, and a read-only project greys
out every button. Exits 1 on a failure.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))

import _harness  # noqa: E402  (temp project + TH_* env)

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402


def main() -> int:
    _harness.reset_project()
    app = QApplication.instance() or QApplication([])  # noqa: F841
    answers = iter([('rnd', True), ('urgent', True)])
    QInputDialog.getText = staticmethod(lambda *a, **k: next(answers))
    QInputDialog.getInt = staticmethod(lambda *a, **k: (90, True))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: print('WARN', a[2]))

    from tumblepipe.asset_browser.farm_defaults import FarmDefaultsEditor
    from tumblepipe.config import farm

    activator = SimpleNamespace(activate=lambda project: None)
    failures = []

    def check(ok, what):
        print(('OK   ' if ok else 'FAIL ') + what)
        if not ok:
            failures.append(what)

    editor = FarmDefaultsEditor(activator=activator)
    editor.set_project(object(), editable=True)
    editor._on_add_pool()
    check('rnd' in [pool.name for pool in farm.list_pools()], 'Add... writes the pool')
    editor._pool_list.setCurrentRow(editor._pools.index('rnd'))
    editor._on_default_pool()
    labels = [editor._pool_list.item(i).text() for i in range(editor._pool_list.count())]
    check(farm.get_default_pool() == 'rnd' and 'rnd  (default)' in labels,
          'Set Default writes farm.default_pool and marks the row')
    editor._on_add_preset()
    presets = [editor._preset_list.item(i).text() for i in range(editor._preset_list.count())]
    check('urgent: 90' in presets, 'Add... on Priorities writes the named preset')
    editor._pool_list.setCurrentRow(editor._pools.index('rnd'))
    editor._on_remove_pool()
    check('rnd' not in [pool.name for pool in farm.list_pools()]
          and farm.get_default_pool() is None,
          'removing the default pool clears the default')

    read_only = FarmDefaultsEditor(activator=activator)
    read_only.set_project(object(), editable=False, note='read only')
    check(not any(b.isEnabled() for b in read_only._pool_buttons + read_only._preset_buttons),
          'a read-only project greys out every button')

    print('ALL OK' if not failures else f'{len(failures)} FAILED')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
