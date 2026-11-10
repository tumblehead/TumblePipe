"""Audit: the stock blendshape template authors its shapes under <asset>/blshp.

    hython scripts/verify_blendshape_template.py

QA 2026-11-10: in a new CHAR/hero blendshape workfile every publish failed
validation ("Missing required 'blshp' child prim"). The template's sopcreate
pulls the model in through lopimport + unpackusd, and the unpacked polygons
carry the model mesh's absolute ``path`` (``/CHAR/hero/geo/mesh_0``). The
sopcreate's default path attribute is ``path,name``, and an absolute path is
not prefixed, so the sculpted shape was authored over the MODEL's mesh (an
opinion on geo/mesh_0 in the blendshape layer) and nothing reached
``<asset>/blshp``. The template's Name SOP is what names a shape, so the
sopcreate now reads ``name`` only.

In a throwaway project this builds the stock template for CHAR/hero, feeds
it a stand-in model (a mesh at /CHAR/hero/geo/body whose polygons carry an
absolute ``path``, as unpackusd gives them), and checks:

- the shape is a Mesh at /CHAR/hero/blshp/<Name SOP's name>, under a Scope;
- the model's mesh gets no opinion from the blendshape network;
- a Multi workfile (two members) does the same per member.

The Multi build also used to raise: the template set the model import's
department before pinning its member entity, and in a Multi's workfile an
unpinned import offers no department but 'from_context'. groom and lookdev
had the same order, so their Multi builds are checked here too.

Needs Houdini (hython) and this checkout's python/ on PYTHONPATH. Nothing
outside a temp dir is written.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

TEMP_ROOT = Path(tempfile.mkdtemp(prefix='verify-blendshape-'))
PROJECT = TEMP_ROOT / 'proj'
shutil.copytree(REPO / 'scripts' / 'project_template' / '_config', PROJECT / '_config')
os.environ['TH_PROJECT_PATH'] = str(PROJECT)
os.environ['TH_CONFIG_PATH'] = str(PROJECT / '_config')
os.environ['TH_PIPELINE_PATH'] = str(REPO)
os.environ['TH_EVENTS_DIR'] = str(TEMP_ROOT / 'events')
os.environ.setdefault('TH_USER_NAME', 'verify')
for name in list(sys.modules):
    if name == 'tumblepipe' or name.startswith('tumblepipe.'):
        del sys.modules[name]

import hou  # noqa: E402

# sculpt::2.0 ships in Houdini's own packages/sculpt, which a
# HOUDINI_PACKAGE_SKIP=1 hython does not load.
_SCULPT_OTL = Path(hou.text.expandString('$HFS')) / 'packages' / 'sculpt' / 'otls' / 'OPlibSculpt.hda'
if _SCULPT_OTL.exists() and hou.nodeType(hou.sopNodeTypeCategory(), 'sculpt::2.0') is None:
    hou.hda.installFile(str(_SCULPT_OTL))

from tumblepipe.api import api  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.config import groups  # noqa: E402
from tumblepipe.pipe.paths import Context  # noqa: E402
from tumblepipe.pipe.houdini import entity_node  # noqa: E402

TEMPLATE = REPO / 'scripts' / 'project_template' / '_config' / 'templates' / 'assets' / 'blendshape' / 'template.py'

FAILURES = []


def check(ok, what):
    print(('PASS ' if ok else 'FAIL ') + what)
    if not ok:
        FAILURES.append(what)


def load_template(department='blendshape'):
    path = TEMPLATE.parent.parent / department / 'template.py'
    spec = importlib.util.spec_from_file_location(f'{department}_template', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stand_in_model(stage_node, asset_path: str, name: str):
    """A LOP authoring <asset>/geo/body whose polygons carry an absolute
    ``path`` (what unpackusd hands the template's sculpt)."""
    sop = stage_node.createNode('sopcreate', 'MODEL_STANDIN_' + name.replace(' ', '_'))
    sop.parm('pathprefix').set(f'{asset_path}/geo/')
    inner = sop.node('sopnet/create')
    box = inner.createNode('box')
    wrangle = inner.createNode('attribwrangle')
    wrangle.parm('class').set(1)  # primitives
    wrangle.parm('snippet').set(f's@path = "{asset_path}/geo/body";')
    wrangle.setInput(0, box)
    out = inner.createNode('output')
    out.setInput(0, wrangle)
    out.setDisplayFlag(True)
    return sop


def column(stage_node, suffix=''):
    return {
        'sop': stage_node.node(f'BLENDSHAPES{suffix}'),
        'scope': stage_node.node(f'BLSHP_SCOPE{suffix}'),
    }


def _points(stage, prim_path):
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.GetAttribute('points'):
        return None
    return [tuple(round(c, 5) for c in p) for p in prim.GetAttribute('points').Get(1)]


def assert_column(stage_node, asset_path: str, suffix: str, label: str):
    nodes = column(stage_node, suffix)
    # The template's import has no published model to read in a throwaway
    # project, so its sopcreate is fed the stand-in instead.
    stand_in = stand_in_model(stage_node, asset_path, label)
    nodes['sop'].setInput(0, stand_in)
    # A Peak between sculpt and cache plays the artist's stroke, so the
    # shape differs from the model and an opinion on geo/body shows.
    inner = nodes['sop'].node('sopnet/create')
    peak = inner.createNode('peak', 'stroke')
    peak.parm('dist').set(0.1)
    peak.setInput(0, inner.node('sculpt'))
    inner.node('cache').setInput(0, peak)

    # Read everything off one stage before cooking another: a LOP node's
    # stage() is a shared object the next cook may rewrite.
    stage = nodes['scope'].stage()
    shape_name = inner.node('name').evalParm('name1')
    shape_path = f'{asset_path}/blshp/{shape_name}'
    shape = stage.GetPrimAtPath(shape_path)
    shape_ok = bool(shape) and shape.GetTypeName() == 'Mesh'
    blshp = stage.GetPrimAtPath(f'{asset_path}/blshp')
    blshp_ok = bool(blshp) and blshp.GetTypeName() == 'Scope'
    body = _points(stage, f'{asset_path}/geo/body')
    sculpted = _points(stage, shape_path)
    model = _points(stand_in.stage(), f'{asset_path}/geo/body')

    check(shape_ok, f'{label}: the shape is a Mesh at {shape_path}')
    check(blshp_ok, f'{label}: {asset_path}/blshp is a Scope')
    check(model is not None and body == model,
          f'{label}: the model mesh {asset_path}/geo/body keeps its points')
    check(sculpted not in (None, model),
          f'{label}: the shape carries the sculpted points')


def main():
    hero = Uri.parse('entity:/assets/CHAR/hero')
    toy = Uri.parse('entity:/assets/CHAR/toy')
    for uri in (hero, toy):
        api.config.add_entity(uri, {})
    template = load_template()
    stage_node = hou.node('/stage')

    # Answer 'from_context' as the workfile New from Template saves would.
    entity_node.scene_context = lambda: Context(hero, 'blendshape')
    template.create(stage_node, hero, 'blendshape')
    assert_column(stage_node, '/CHAR/hero', '', 'single')

    for child in stage_node.children():
        child.destroy()
    group_uri = groups.add_group('assets', 'chars', [hero, toy], ['blendshape'])
    entity_node.scene_context = lambda: Context(group_uri, 'blendshape')
    template.create(stage_node, group_uri, 'blendshape')
    assert_column(stage_node, '/CHAR/hero', '_CHAR_hero', 'multi hero')
    assert_column(stage_node, '/CHAR/toy', '_CHAR_toy', 'multi toy')

    # groom and lookdev pin their model import the same way.
    for department in ('groom', 'lookdev'):
        for child in stage_node.children():
            child.destroy()
        dept_group = groups.add_group('assets', f'chars{department}', [hero, toy], [department])
        entity_node.scene_context = lambda g=dept_group, d=department: Context(g, d)
        try:
            load_template(department).create(stage_node, dept_group, department)
            error = None
        except Exception as exc:  # noqa: BLE001 - reported as a FAIL
            error = exc
        check(error is None, f'multi {department}: New from Template builds ({error})')
        for member, suffix in ((hero, '_CHAR_hero'), (toy, '_CHAR_toy')):
            imp = stage_node.node(f'IMPORT_MODEL{suffix}')
            check(imp is not None and imp.evalParm('department') == 'model'
                  and imp.evalParm('entity') == str(member),
                  f'multi {department}: IMPORT_MODEL{suffix} imports model of {member}')

    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    print('FAILED: ' + '; '.join(FAILURES) if FAILURES else 'ALL PASS')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
