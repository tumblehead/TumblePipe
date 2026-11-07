"""Drive the Projects settings page's Libraries list headless.

Runs under Houdini's python313 (PySide6 and tumbletrove's theme, no licence,
no ``hou``) against a temp project made by ``tests/_harness``, like
``verify_entity_editor.py``::

    S=<scratch dir with a sitecustomize adding Houdini's DLL dirs>
    TTW=<tumbletrove checkout or fetched package>/python
    HW="C:/Program Files/Side Effects Software/Houdini <ver>"
    QT_QPA_PLATFORM=offscreen HOUDINI_BIN="$HW/bin" PYTHONUTF8=1 \\
    PYTHONPATH="$S;$TTW;$HW/python313/lib/site-packages-forced" \\
        "$HW/python313/python.exe" scripts/verify_library_settings.py [shot.png]

Covers: the launched project's TH_LIBRARY_PATH rows listed first, greyed
out and not removable; Add… on a folder without ``library.json`` asks for a
name and writes it; Add… on a named library lists it as is; a bad name is
refused with nothing listed; Remove; another project's list is its own;
Apply writes ``library_paths`` to ``projects.json`` and the catalog's
sidebar then has the library's section. Prints OK/FAIL per check; exits 1
on any FAIL.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))

import _harness  # noqa: E402,F401  (temp project, TH_* env, python/ on sys.path)

os.environ['HOUDINI_USER_PREF_DIR'] = str(_harness.PROJECT_PATH / '_prefs')

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFileDialog, QInputDialog, QMessageBox,
)

app = QApplication.instance() or QApplication(sys.argv)

from tumblepipe.asset_browser import libraries as L  # noqa: E402
from tumblepipe.asset_browser.catalog import PipelineCatalog  # noqa: E402
from tumblepipe.asset_browser.projects import (  # noqa: E402
    PipelineProjectRegistry, ProjectConfig,
)

failures = []


def check(name: str, ok: bool, detail: object = '') -> None:
    print(f'{"OK  " if ok else "FAIL"} {name}' + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        failures.append(name)


root = Path(tempfile.mkdtemp(prefix='th-libsettings-'))
env_lib = root / 'envlib'
L.write_library_file(env_lib, 'FromEnv')
named_lib = root / 'named'
L.write_library_file(named_lib, 'Studio')
bare_lib = root / 'Bare Folder'
bare_lib.mkdir()
os.environ[L.ENV_VAR] = str(env_lib)

launched = _harness.PROJECT_PATH.name
registry = PipelineProjectRegistry(root / 'projects.json')
registry.add(ProjectConfig(launched, str(_harness.PROJECT_PATH), '',
                           str(_harness.PROJECT_PATH / '_config')))
other_dir = root / 'other'
(other_dir / '_config').mkdir(parents=True)
registry.add(ProjectConfig('other', str(other_dir), '', str(other_dir / '_config'),
                           library_paths=[str(named_lib)]))

catalog = PipelineCatalog(registry)
catalog._activator.activate = lambda proj: None

# Dialog answers, in order.
folders: list[str] = []
names: list[tuple[str, bool]] = []
warnings: list[str] = []
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: folders.pop(0))
QInputDialog.getText = staticmethod(lambda *a, **k: names.pop(0))
QMessageBox.warning = staticmethod(lambda parent, title, text, *a, **k: warnings.append(text))
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

from tumblepipe.asset_browser.settings_widget import PipelineSettingsWidget  # noqa: E402

widget = PipelineSettingsWidget(catalog)
widget.resize(640, 900)
widget.show()
app.processEvents()


def rows():
    out = []
    for i in range(widget._lib_list.count()):
        item = widget._lib_list.item(i)
        out.append((item.text(), bool(item.flags() & Qt.ItemIsEnabled),
                    item.data(Qt.UserRole)))
    return out


def select_project(name: str) -> None:
    for i, proj in enumerate(widget._working):
        if proj.name == name:
            widget._list.setCurrentRow(i)
            app.processEvents()
            return
    raise AssertionError(name)


select_project(launched)
first = rows()
check('launched project lists its TH_LIBRARY_PATH row first, greyed out',
      len(first) == 1 and first[0][0].startswith(str(env_lib))
      and L.ENV_VAR in first[0][0] and not first[0][1] and first[0][2] is None, first)
widget._lib_list.setCurrentRow(0)
app.processEvents()
check('an env row cannot be removed', not widget._lib_remove_btn.isEnabled())

# Add a folder that is not a library yet: named on the way in.
folders.append(bare_lib.as_posix())
names.append(('Shared', True))
widget._on_add_library()
marker = json.loads((bare_lib / L.LIBRARY_FILE).read_text(encoding='utf-8'))
check('Add… names a bare folder and writes its library.json', marker.get('name') == 'Shared', marker)
check('the added folder is listed after the env row',
      [r[2] for r in rows()] == [None, bare_lib.as_posix()], rows())

# A bad name is refused and nothing is listed.
bad = root / 'bad'
bad.mkdir()
folders.append(bad.as_posix())
names.append(('no:colons', True))
before = rows()
widget._on_add_library()
check('a bad library name is refused with a warning, nothing listed',
      rows() == before and warnings and not (bad / L.LIBRARY_FILE).exists(), warnings)

# A named library is listed as it is (no question asked).
folders.append(named_lib.as_posix())
widget._on_add_library()
check('a named library is added without asking',
      [r[2] for r in rows()][-1] == named_lib.as_posix() and not names, rows())

# Remove it again.
widget._lib_list.setCurrentRow(2)
app.processEvents()
check('a settings row can be removed', widget._lib_remove_btn.isEnabled())
widget._on_remove_library()
check('Remove drops it', [r[2] for r in rows()] == [None, bare_lib.as_posix()], rows())

# The other project's list is its own, with no env rows.
select_project('other')
check("another project shows only its own libraries",
      [r[2] for r in rows()] == [str(named_lib)], rows())
select_project(launched)
check('switching back keeps the unapplied list',
      [r[2] for r in rows()] == [None, bare_lib.as_posix()], rows())

if len(sys.argv) > 1:
    widget.grab().save(sys.argv[1])

# Apply: persisted and seen by the catalog.
widget._on_apply()
stored = json.loads((root / 'projects.json').read_text(encoding='utf-8'))
by_name = {p['name']: p for p in stored['projects']}
check('Apply writes library_paths (never the env ones)',
      by_name[launched].get('library_paths') == [bare_lib.as_posix()]
      and by_name['other'].get('library_paths') == [str(named_lib)], by_name)
catalog._libraries.current()
sections = [c.id for c in catalog.get_collections()]
check('the sidebar then has every library once',
      {'library_section:FromEnv', 'library_section:Shared', 'library_section:Studio'}
      <= set(sections), sections)

print('FAILED: ' + ', '.join(failures) if failures else 'ALL OK')
sys.exit(1 if failures else 0)
