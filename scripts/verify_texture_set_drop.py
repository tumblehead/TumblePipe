"""Drop a published texture set card in hython, as the asset browser does.

Publishes an asset's texture set on a throwaway project, scans it into a
card (``asset_browser.texture_sets``) and drops it (``TextureSetManager.
on_drop``) into each place a card can go:

- LOPs: a ``th::import_texture`` pinned to the newest version, wired to
  the selected node;
- ``/mat``: a MaterialX material whose images read the published files
  through ``$TH_EXPORT_PATH`` (never a copy);
- Copernicus: a File COP on the base colour;
- SOPs: declined.

Needs tumbletrove (the asset browser) importable: pass its ``python``
folder as ``TUMBLETROVE_PYTHON`` (default: ``../asset-browser/python``)::

    hython scripts/verify_texture_set_drop.py [HDA dir, default otls/]
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix='verify-texture-set-drop-'))
PROJECT = TMP / 'VerifyProj'
shutil.copytree(REPO / 'scripts' / 'project_template' / '_config',
                PROJECT / '_config')
os.environ.update(
    TH_PROJECT_PATH=PROJECT.as_posix(),
    TH_CONFIG_PATH=(PROJECT / '_config').as_posix(),
    TH_EXPORT_PATH=(PROJECT / 'export').as_posix(),
    TH_PIPELINE_PATH=REPO.as_posix(),
)
for module in [m for m in sys.modules
               if m == 'tumblepipe' or m.startswith('tumblepipe.')]:
    del sys.modules[module]
sys.path.insert(0, str(REPO / 'python'))
sys.path.insert(0, os.environ.get(
    'TUMBLETROVE_PYTHON', str(REPO.parent / 'asset-browser' / 'python')))

import atexit  # noqa: E402

import hou  # noqa: E402

atexit.register(shutil.rmtree, TMP, True)

from tumblepipe.api import api  # noqa: E402
from tumblepipe.asset_browser import texture_sets as TS  # noqa: E402
from tumblepipe.pipe import textures as T  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402
from tumbletrove.asset_browser.core.drop import DropContext  # noqa: E402

FAILED = []


def check(label: str, ok: bool, detail: str = '') -> None:
    print(('PASS  ' if ok else 'FAIL  ') + label + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        FAILED.append(label)


otls = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / 'otls'
for name in ('lop_th.import_texture.1.0.hda',):
    hou.hda.installFile(str(otls / name))
    for definition in hou.hda.definitionsInFile(str(otls / name)):
        definition.setIsPreferred(True)

chair = Uri.parse('entity:/assets/props/chair')
api.config.add_entity(Uri.parse('entity:/assets/props'), {})
api.config.add_entity(chair, {'name': 'chair'})
src = TMP / 'src'
src.mkdir()
for name in ('col.png', 'rough.png'):
    (src / name).write_bytes(b'img')
T.publish_set(T.set_dir('wood', chair), 'wood',
              {'diffuse': str(src / 'col.png'), 'rough': str(src / 'rough.png')},
              owner=chair)

manager = TS.TextureSetManager(
    'pipeline', export_roots=lambda: {'VerifyProj': PROJECT / 'export'})
cards = manager.cards()
check('one card for the published set', [c.name for c in cards] == ['wood'],
      str([c.id for c in cards]))
card = cards[0]
detail = manager.get_detail(card.id)


def drop(network, context, selected=()):
    return DropContext(
        pane_type='network_editor', pane=None, network=network,
        context=context, position=hou.Vector2(0, 0),
        selected_nodes=list(selected),
        texture_destination=SimpleNamespace(id='must-not-be-used'))


stage = hou.node('/stage')
prim = stage.createNode('primitive', 'chair_prim')
prim.parm('primpath').set('/chair')
check('LOP drop handled', manager.on_drop(detail, drop(stage, 'lop', [prim])))
imports = [n for n in stage.children() if n.type().name().startswith('th::import_texture')]
check('LOP drop makes an import_texture', len(imports) == 1)
if imports:
    node = imports[0]
    check('pinned to the newest version', node.parm('version').eval() == 'v0001')
    check('wired to the selected node', node.inputs() and node.inputs()[0] == prim)

mat = hou.node('/mat')
before = set(mat.children())
check('/mat drop handled', manager.on_drop(detail, drop(mat, 'vop')))
made = [n for n in mat.children() if n not in before]
check('/mat drop makes one material', len(made) == 1, str(made))
if made:
    images = [n for n in made[0].allSubChildren() if n.type().name() == 'mtlximage']
    files = sorted(n.parm('file').unexpandedString() for n in images)
    check('images read the published version through $TH_EXPORT_PATH',
          files and all(f.startswith('$TH_EXPORT_PATH/assets/props/chair/_textures/wood/v0001/')
                        for f in files), str(files))
    check('nothing copied beside the scene', not (TMP / 'tex').exists())

cop = hou.node('/obj').createNode('copnet', 'cop')
check('COP drop handled', manager.on_drop(detail, drop(cop, 'cop')))
files = [n for n in cop.children() if n.type().name() == 'file']
check('COP drop reads the base colour',
      files and files[0].parm('filename').unexpandedString().endswith('wood_diffuse.png'),
      str([f.parm('filename').unexpandedString() for f in files]))

geo = hou.node('/obj').createNode('geo', 'g')
check('SOP drop declined', not manager.on_drop(detail, drop(geo, 'sop')))

print(f'\n{len(FAILED)} failed' if FAILED else '\nall passed')
sys.exit(1 if FAILED else 0)
