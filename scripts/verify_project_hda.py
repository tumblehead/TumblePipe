"""Verify TumblePipe ▸ Create Project HDA... end to end in hython.

    hython scripts/verify_project_hda.py

``tests/test_project_hda.py`` pins the naming rules without Houdini; this
drives the Houdini half against a throwaway project in a temp folder (set
``VERIFY_KEEP=1`` to keep it):

1. The menu entry's own code, read from ``OPmenu.xml``: its context
   expression offers the entry on a subnet, not on a box, and not in a
   session without a project.
2. ``create`` on a SOP subnet: the type, file, node name and wiring; the
   subnet's spare parm promoted onto the definition with its value and the
   inner reference to it intact; the Tools.shelf section naming the project;
   the node matching its definition; no otls/backup/ left behind.
3. The same name again is 2.0; typed in another case it is 3.0 *of the
   same spelling*; a LOP subnet gets a lop_ file; an invalid name is
   refused and writes nothing.
4. The dialog itself, offscreen: the subnet's name prefilled, Create off for
   an empty or invalid name, the plan and the kept spelling in its summary;
   ``show_dialog`` on Cancel creates nothing, on Create creates the asset and
   reports it in the status bar, and on a failure shows the error instead of
   raising. hython has no ``hou.ui`` or ``hou.qt``, so stand-ins record what
   would have been shown.
5. A fresh hython whose only extra package sets ``TH_PROJECT_PATH`` and the
   manifest's own ``HOUDINI_OTLSCAN_PATH`` entry (read from ``hpm.toml``):
   it loads every file created above, still loads Houdini's stock assets
   (the loader keeps ``&``), files the Tab entries under the project name,
   and a new instance has the promoted parm and the contents.

TH_* are set here, before tumblepipe is imported: Houdini packages loaded
at startup can point them at another project (on one workstation a stale
package sets ``P:/growth``). Any tumblepipe a package already imported is
dropped so the repo's copy is the one tested. Exits 1 on any failure.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import types
import xml.dom.minidom
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRESH_SESSION = '--fresh-session'
CHILD = FRESH_SESSION in sys.argv

if not CHILD:
    TMP = Path(tempfile.mkdtemp(prefix='verify-project-hda-'))
    PROJECT = TMP / 'VerifyProj'
    PROJECT.mkdir()
    os.environ.update(
        TH_PROJECT_PATH=PROJECT.as_posix(),
        TH_CONFIG_PATH=(PROJECT / '_config').as_posix(),
        TH_EXPORT_PATH=(PROJECT / 'export').as_posix(),
        TH_PIPELINE_PATH=REPO.as_posix(),
    )
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    for module in [m for m in sys.modules if m == 'tumblepipe' or m.startswith('tumblepipe.')]:
        del sys.modules[module]
    sys.path.insert(0, str(REPO / 'python'))

import hou

FAILURES = []


def check(label: str, ok: bool, detail: str = '') -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        FAILURES.append(label)


def same_path(a, b) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


# ── 1-4: this session ────────────────────────────────────────────────────────

def _menu_code(item_id: str, tag: str) -> str:
    document = xml.dom.minidom.parse(str(REPO / 'OPmenu.xml'))
    for item in document.getElementsByTagName('scriptItem'):
        if item.getAttribute('id') == item_id:
            (node,) = item.getElementsByTagName(tag)
            return node.firstChild.data
    raise LookupError(item_id)


def _as_function(code: str):
    """A menu snippet as Houdini runs it: the body of a function of kwargs."""
    body = textwrap.indent(textwrap.dedent(code).strip(), '    ')
    scope = {}
    exec(f'def _snippet(kwargs):\n{body}\n', scope)  # noqa: S102 — the menu's own code
    return scope['_snippet']


def _subnet(parent, name='subnet1'):
    return parent.createNode('subnet', name)


def _install_ui_stand_ins() -> dict:
    shown = {'status': [], 'messages': []}
    if not hasattr(hou, 'qt'):
        hou.qt = types.SimpleNamespace(mainWindow=lambda: None)
    if not hasattr(hou, 'ui'):
        hou.ui = types.SimpleNamespace(
            setStatusMessage=lambda text, **kwargs: shown['status'].append(text),
            displayMessage=lambda text, **kwargs: shown['messages'].append((text, kwargs)),
        )
    return shown


def verify_session() -> None:
    from tumblepipe.pipe.houdini import project_hda

    check('the repo\'s tumblepipe is the one under test',
          Path(project_hda.__file__).resolve().is_relative_to(REPO),
          project_hda.__file__)
    check('the project is the throwaway one', same_path(project_hda._project_path(), PROJECT),
          str(project_hda._project_path()))
    if FAILURES:
        return
    otls = PROJECT / project_hda.OTLS_DIR
    geo = hou.node('/obj').createNode('geo')

    # 1. The menu entry's context expression, as OPmenu.xml has it.
    expression = _as_function(_menu_code('tumblepipe_create_project_hda', 'expression'))
    box = geo.createNode('box')
    probe = _subnet(geo, 'probe')
    check('menu: offered on a subnet', expression({'node': probe}) is True)
    check('menu: not offered on a box', expression({'node': box}) is False)
    saved = os.environ.pop('TH_PROJECT_PATH')
    try:
        check('menu: not offered without a project', expression({'node': probe}) is False)
    finally:
        os.environ['TH_PROJECT_PATH'] = saved
    _as_function(_menu_code('tumblepipe_create_project_hda', 'scriptCode'))  # compiles

    # 2. A SOP subnet with an input, a spare parm and an inner reference to it.
    subnet = _subnet(geo, 'work')
    subnet.setInput(0, box)
    inner = subnet.createNode('xform')
    inner.setInput(0, subnet.indirectInputs()[0])
    group = subnet.parmTemplateGroup()
    group.append(hou.FloatParmTemplate('size', 'Size', 1, default_value=(1.0,)))
    subnet.setParmTemplateGroup(group)
    subnet.parm('size').set(3.0)
    inner.parm('scale').setExpression('ch("../size")')

    node = project_hda.create(subnet, 'mesh_cleanup')
    definition = node.type().definition()
    file = otls / 'sop_verifyproj.mesh_cleanup.1.0.hda'
    check('create: type name', node.type().name() == 'verifyproj::mesh_cleanup::1.0', node.type().name())
    check('create: file in the project otls/', same_path(definition.libraryFilePath(), file),
          definition.libraryFilePath())
    check('create: node renamed to the name', node.name() == 'mesh_cleanup', node.name())
    check('create: input kept', node.inputs() == (box,), str(node.inputs()))
    check('create: matches its definition', node.matchesCurrentDefinition())
    check('create: spare parm promoted', definition.parmTemplateGroup().find('size') is not None
          and not node.parm('size').isSpare())
    check('create: parm value kept', node.parm('size').eval() == 3.0, str(node.parm('size').eval()))
    check('create: inner reference intact', node.geometry().boundingBox().sizevec()[0] == 3.0,
          str(node.geometry().boundingBox().sizevec()))
    sections = definition.sections()
    check('create: Tools.shelf names the project',
          'Tools.shelf' in sections
          and '<toolSubmenu>VerifyProj</toolSubmenu>' in sections['Tools.shelf'].contents())
    check('create: label from the name', definition.description() == 'Mesh Cleanup', definition.description())
    check('create: no backup/ left', not (otls / 'backup').exists())

    # 3. Versions, spelling, contexts, refusal.
    again = project_hda.create(_subnet(geo), 'mesh_cleanup')
    check('again: next major version', again.type().name() == 'verifyproj::mesh_cleanup::2.0', again.type().name())
    shouted = project_hda.create(_subnet(geo), 'MESH_Cleanup')
    check('other case: same spelling, next version',
          shouted.type().name() == 'verifyproj::mesh_cleanup::3.0', shouted.type().name())
    # The listing, not is_file(): Windows and macOS would find any case.
    check('other case: file of the same spelling',
          'sop_verifyproj.mesh_cleanup.3.0.hda' in [p.name for p in otls.iterdir()],
          str(sorted(p.name for p in otls.iterdir())))
    check('other case: node named in that spelling', shouted.name().startswith('mesh_cleanup'), shouted.name())
    lop = project_hda.create(_subnet(hou.node('/stage')), 'light_rig')
    check('LOP subnet: lop_ file', (otls / 'lop_verifyproj.light_rig.1.0.hda').is_file()
          and lop.type().name() == 'verifyproj::light_rig::1.0', lop.type().name())
    before = sorted(p.name for p in otls.iterdir())
    try:
        project_hda.create(_subnet(geo), '1bad')
        refused = False
    except ValueError:
        refused = True
    check('invalid name refused, nothing written', refused and sorted(p.name for p in otls.iterdir()) == before)

    # 4. The dialog, offscreen, and show_dialog's three outcomes.
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841 — keeps Qt alive
    shown = _install_ui_stand_ins()
    target = _subnet(geo, 'scatter_fix')
    dialog = project_hda._create_dialog(target)
    check('dialog: subnet name prefilled', dialog.name() == 'scatter_fix', dialog.name())
    check('dialog: Create on for a valid name', dialog._create.isEnabled())
    dialog._name.setText('')
    check('dialog: Create off for an empty name', not dialog._create.isEnabled())
    dialog._name.setText('1bad')
    check('dialog: Create off for an invalid name', not dialog._create.isEnabled())
    dialog._name.setText('Mesh_Cleanup')
    summary = dialog._summary.text()
    check('dialog: summary shows the plan and the kept spelling',
          'verifyproj::mesh_cleanup::4.0' in summary and 'spelling is kept' in summary, summary)

    real_dialog, real_create = project_hda._create_dialog, project_hda.create

    def scripted(name: str, accept: bool):
        class Scripted(project_hda._dialog_class()):
            def exec(self):
                self._name.setText(name)
                return accept
        return lambda node, parent=None: Scripted(node, parent)

    def raise_error(node, name):
        raise RuntimeError('boom')

    try:
        project_hda._create_dialog = scripted('scatter_fix', False)
        project_hda.show_dialog(target)
        check('show_dialog: Cancel creates nothing',
              target.type().name() == 'subnet' and not list(otls.glob('*scatter_fix*')))

        project_hda._create_dialog = scripted('scatter_fix', True)
        project_hda.create = raise_error
        project_hda.show_dialog(_subnet(geo, 'failing'))
        check('show_dialog: a failure is shown, not raised',
              bool(shown['messages']) and shown['messages'][-1][1].get('details') == 'boom',
              str(shown['messages']))
        project_hda.create = real_create

        project_hda.show_dialog(target)
        created = [n for n in geo.children() if n.type().name() == 'verifyproj::scatter_fix::1.0']
        check('show_dialog: Create creates the asset', len(created) == 1, str(created))
        check('show_dialog: reports it in the status bar',
              any('verifyproj::scatter_fix::1.0' in text for text in shown['status']), str(shown['status']))
    finally:
        project_hda._create_dialog, project_hda.create = real_dialog, real_create


# ── 5: a fresh session ───────────────────────────────────────────────────────

def _hython() -> str:
    hfs = os.environ.get('HFS')
    if hfs:
        exe = Path(hfs) / 'bin' / ('hython.exe' if os.name == 'nt' else 'hython')
        if exe.is_file():
            return str(exe)
    return sys.executable


def verify_fresh_session() -> None:
    manifest = tomllib.loads((REPO / 'hpm.toml').read_text(encoding='utf-8'))
    scan = manifest['runtime']['HOUDINI_OTLSCAN_PATH']
    packages = TMP / 'packages'
    packages.mkdir()
    (packages / 'verify_project_hda.json').write_text(json.dumps({'env': [
        {'TH_PROJECT_PATH': PROJECT.as_posix()},
        {'HOUDINI_OTLSCAN_PATH': {'method': scan['method'], 'value': [scan['value']]}},
    ]}), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if not k.startswith('TH_')}
    env.pop('HOUDINI_PACKAGE_SKIP', None)
    env.update(HOUDINI_PACKAGE_DIR=str(packages), VERIFY_EXPECTED_PROJECT=PROJECT.as_posix())
    result = subprocess.run([_hython(), __file__, FRESH_SESSION], env=env,
                            capture_output=True, text=True, timeout=600, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith(('PASS', 'FAIL'))]
    for line in lines:
        print(f'{line[:4]}  fresh session: {line[6:]}')
        if line.startswith('FAIL'):
            FAILURES.append(line)
    check('fresh session ran', bool(lines) and result.returncode in (0, 1),
          f'exit {result.returncode}: {result.stderr[-800:]}')


def fresh_session() -> None:
    """Runs in the child hython: read only."""
    expected = Path(os.environ['VERIFY_EXPECTED_PROJECT'])
    project = os.environ.get('TH_PROJECT_PATH', '')
    check('the package set TH_PROJECT_PATH', same_path(project, expected),
          f'{project!r}: another Houdini package overrode it')
    otls = expected / 'otls'
    scan = os.environ.get('HOUDINI_OTLSCAN_PATH', '')
    entries = scan.split(os.pathsep)
    check('project otls/ first on the scan path', same_path(entries[0], otls), scan)
    check('the default scan path kept (&)', '&' in entries, scan)
    loaded = hou.hda.loadedFiles()
    for file in sorted(otls.glob('*.hda')):
        check(f'loads {file.name}', any(same_path(f, file) for f in loaded))
    check('stock assets still load', any('OPlib' in f for f in loaded))
    for category, name in (
        (hou.sopNodeTypeCategory(), 'verifyproj::mesh_cleanup::1.0'),
        (hou.lopNodeTypeCategory(), 'verifyproj::light_rig::1.0'),
    ):
        node_type = hou.nodeType(category, name)
        tool = node_type and hou.shelves.tool(hou.shelves.defaultToolName(category.name(), name))
        check(f'{name}: Tab entry under the project',
              tool is not None and tool.toolMenuLocations() == ('VerifyProj',),
              str(tool and tool.toolMenuLocations()))
    instance = hou.node('/obj').createNode('geo').createNode('verifyproj::mesh_cleanup::1.0')
    instance.syncDelayedDefinition()
    check('a new instance has the promoted parm', instance.parm('size') is not None)
    check('a new instance has the contents', len(instance.children()) > 0)


if __name__ == '__main__':
    if CHILD:
        fresh_session()
        sys.exit(1 if FAILURES else 0)
    try:
        verify_session()
        if not FAILURES:
            verify_fresh_session()
    finally:
        if os.environ.get('VERIFY_KEEP') == '1':
            print(f'kept {TMP}')
        else:
            # tumblepipe's log file (export/other/logs/pipeline.log) stays
            # open, and Windows will not delete an open file.
            logging.shutdown()
            shutil.rmtree(TMP, ignore_errors=True)
            if TMP.exists():
                print(f'could not remove {TMP}')
    print(f'\n{len(FAILURES)} failure(s)' if FAILURES else '\nall checks passed')
    sys.exit(1 if FAILURES else 0)
