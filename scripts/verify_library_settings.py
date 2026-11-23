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
sidebar then has the library's section; the Flat asset / shot list
checkboxes follow the project-defaults gate, Flat shot list writes
``flat_shots`` and the sidebar's Shots section drops its sequence rows;
turning a flat list on offers to remove the empty categories / sequences by
name (never one holding an entity, listed by a Multi, with settings of its
own, or the bucket), Cancel keeps them with the list still flat, Remove
deletes exactly those, flat off then shows the rest, and someone who may
not edit the project defaults is offered nothing and loses nothing.
Prints OK/FAIL per check; exits 1 on any FAIL.
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

# The offer to remove empty categories / sequences: what it showed, and
# Remove (True) or Cancel (False), in order. Without an answer queued it
# fails the check instead of blocking on a modal.
from tumbletrove.asset_browser.ui import dialogs as tt_dialogs  # noqa: E402

offers: list[tuple[str, str, str]] = []
answers: list[bool] = []


def _confirm_destructive(parent, title, message, action_label='Delete'):
    offers.append((title, message, action_label))
    return answers.pop(0) if answers else False


tt_dialogs.confirm_destructive = _confirm_destructive

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

# Flat asset / shot lists: enabled exactly as Layout in Animate is (the
# project-defaults gate), written at once, and the sidebar follows.
from tumblepipe.config import flat_entities  # noqa: E402

flat_boxes = widget._flat_checkboxes
editable = widget._layout_animate_checkbox.isEnabled()
check('the flat list checkboxes follow the project-defaults gate, off at first',
      all(b.isEnabled() == editable and not b.isChecked() for b in flat_boxes.values()))
if editable:
    store = catalog._registry.get(launched).store()
    flat_boxes['shots'].setChecked(True)
    app.processEvents()
    bucket = flat_entities.flat_bucket_in(store, 'shots')
    def walk(cols):
        for c in cols:
            yield c
            yield from walk(c.children or ())
    shots = next(c for c in walk(catalog.get_collections())
                 if c.id == f'{launched}:shots_section')
    check('Flat shot list writes flat_shots and the Shots section lists no sequence',
          bucket is not None
          and not [c for c in shots.children if c.kind == 'sequence']
          and flat_entities.flat_bucket_in(store, 'assets') is None, (bucket, shots))
    flat_boxes['shots'].setChecked(False)
    app.processEvents()
    check('turning it off drops flat_shots', not flat_entities.is_flat_in(store, 'shots'))
    check('nothing to remove (the template has no sequence): no offer shown', not offers, offers)

    # Turning flat on offers to remove the empty categories / sequences.
    from tumblepipe.config import groups  # noqa: E402
    from tumblepipe.util.uri import Uri  # noqa: E402
    client = catalog._clients.get(launched)

    def buckets_now(kind):
        paths = {tuple(u.segments) for u in store.list_entity_uris(None, closure=True)}
        return flat_entities.buckets(paths, kind)

    def offered(i=-1):
        title, message, action = offers[i]
        listed = [line.strip()[1:].strip() for line in message.splitlines()
                  if line.strip().startswith('•')]
        return title, listed, action

    def rows_of(section_id, kind):
        catalog.invalidate_cache()
        section = next(c for c in walk(catalog.get_collections()) if c.id == section_id)
        return {c.label for c in section.children if c.kind == kind}

    client.config.add_entity(Uri.parse('entity:/assets/PROP/hero'), {'name': 'hero'})
    catalog.invalidate_cache()
    warned = len(warnings)
    answers.append(False)
    flat_boxes['assets'].setChecked(True)
    app.processEvents()
    check('Flat asset list offers the empty categories by name: CHAR and SET, never '
          'PROP (holds hero) or the bucket ASSETS',
          len(offers) == 1 and offered() == ('Remove empty categories', ['CHAR', 'SET'], 'Remove')
          and 'into ASSETS' in offers[-1][1], offers)
    check('Cancel keeps every category and the list stays flat in ASSETS',
          buckets_now('assets') == {'ASSETS', 'CHAR', 'PROP', 'SET'}
          and flat_entities.flat_bucket_in(store, 'assets') == 'ASSETS'
          and flat_boxes['assets'].isChecked(), buckets_now('assets'))
    flat_boxes['assets'].setChecked(False)
    app.processEvents()
    answers.append(True)
    flat_boxes['assets'].setChecked(True)
    app.processEvents()
    check('Remove deletes exactly the listed ones; ASSETS and PROP stay, still flat',
          len(offers) == 2 and offered()[1] == ['CHAR', 'SET']
          and buckets_now('assets') == {'ASSETS', 'PROP'}
          and flat_entities.flat_bucket_in(store, 'assets') == 'ASSETS'
          and len(warnings) == warned, (buckets_now('assets'), warnings[warned:]))
    flat_boxes['assets'].setChecked(False)
    app.processEvents()
    check('turning it off shows exactly the remaining categories',
          rows_of(f'{launched}:assets_section', 'category') == {'ASSETS', 'PROP'},
          rows_of(f'{launched}:assets_section', 'category'))
    flat_boxes['assets'].setChecked(True)
    app.processEvents()
    check('on again: the same bucket, nothing empty left, no offer',
          len(offers) == 2 and flat_entities.flat_bucket_in(store, 'assets') == 'ASSETS')
    flat_boxes['assets'].setChecked(False)
    app.processEvents()

    # Sequences: SQ1 empty, SQ2 holds a shot, SQ3 is empty but a Multi lists
    # it, SQ4 is empty with a frame range of its own; SHOTS is left over
    # from the Flat shot list check above.
    client.config.add_entity(Uri.parse('entity:/shots/SQ1'), {})
    client.config.add_entity(Uri.parse('entity:/shots/SQ2/sh010'),
                             {'frame_start': 1001, 'frame_end': 1010})
    client.config.add_entity(Uri.parse('entity:/shots/SQ3'), {})
    groups.add_group_in(client.config, 'shots', 'M1', [Uri.parse('entity:/shots/SQ3')], [])
    client.config.add_entity(Uri.parse('entity:/shots/SQ4'), {'frame_start': 1001})
    catalog.invalidate_cache()
    answers.append(True)
    flat_boxes['shots'].setChecked(True)
    app.processEvents()
    check('Flat shot list offers only SQ1: not SQ2 (a shot), SQ3 (a Multi), SQ4 '
          '(its own settings) or the bucket SHOTS',
          len(offers) == 3 and offered() == ('Remove empty sequences', ['SQ1'], 'Remove'), offers[2:])
    check('Remove deletes SQ1 and nothing else',
          buckets_now('shots') == {'SHOTS', 'SQ2', 'SQ3', 'SQ4'}
          and flat_entities.flat_bucket_in(store, 'shots') == 'SHOTS', buckets_now('shots'))
    flat_boxes['shots'].setChecked(False)
    app.processEvents()

    # Someone who may not edit the project defaults: nothing written,
    # offered or removed, even when the toggle is called directly.
    client.config.add_entity(Uri.parse('entity:/assets/VEH'), {})
    project = catalog._registry.get(launched)
    widget._show_layout_animate(project, False)
    widget._on_flat_toggled('assets', True)
    check('read-only: the toggle writes nothing and offers nothing',
          len(offers) == 3 and not flat_entities.is_flat_in(store, 'assets')
          and 'VEH' in buckets_now('assets'))
    widget._show_project_defaults()
    gate = {'TT_PROJECT_NAME': launched, 'TT_ORGANIZATION_SLUG': 'studio',
            'TT_ORGANIZATION_ROLE': 'MEMBER'}
    saved_env = {k: os.environ.get(k) for k in gate}
    os.environ.update(gate)
    flat_entities.set_flat_in(store, 'assets', True)
    widget._offer_remove_empty(project, 'assets')
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    check('a member who is not an owner or admin is never offered a removal',
          len(offers) == 3 and 'VEH' in buckets_now('assets'))
    flat_entities.set_flat_in(store, 'assets', False)
    widget._show_project_defaults()

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
