"""Drive ``th::import_texture`` end to end in hython on a throwaway project.

Publishes a texture set (``tumblepipe.pipe.textures.publish_set``) into an
asset and into the project library, places the node the way the asset
browser does (``import_texture.place``), and checks:

- the newest version is pinned and its material is on the stage at
  ``<input prim>/mtl/<set>``, wired to images that resolve to the version's
  files, with the colour space by role;
- the inner reference reads ``$TH_EXPORT_PATH/...``, not an absolute path;
- a newer publish changes nothing but Status, until Update;
- the library owner lists library sets, and Status explains an unset entity.

Run with the build's hython; an HDA file to test can be given as the first
argument (default: the repo's ``otls/lop_th.import_texture.1.0.hda``)::

    hython scripts/verify_import_texture.py [C:/hx/import_texture.hda]
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix='verify-import-texture-'))
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

import atexit  # noqa: E402

import hou  # noqa: E402
from pxr import UsdShade  # noqa: E402

atexit.register(shutil.rmtree, TMP, True)

from tumblepipe.pipe import textures as T  # noqa: E402
from tumblepipe.pipe.houdini.lops import import_texture  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

FAILED = []


def check(label: str, ok: bool, detail: str = '') -> None:
    print(('PASS  ' if ok else 'FAIL  ') + label + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        FAILED.append(label)


hda = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    REPO / 'otls' / 'lop_th.import_texture.1.0.hda')
hou.hda.installFile(str(hda))
for definition in hou.hda.definitionsInFile(str(hda)):
    definition.setIsPreferred(True)

src = TMP / 'src'
src.mkdir()
for name in ('wood_col.png', 'wood_rough.png', 'wood_nrm.exr'):
    (src / name).write_bytes(b'img')
maps = {'diffuse': str(src / 'wood_col.png'), 'rough': str(src / 'wood_rough.png'),
        'normal': str(src / 'wood_nrm.exr')}
chair = Uri.parse('entity:/assets/props/chair')
from tumblepipe.api import api  # noqa: E402

api.config.add_entity(Uri.parse('entity:/assets/props'), {})
api.config.add_entity(chair, {'name': 'chair'})
asset_dir = T.set_dir('wood', chair)
v1 = T.publish_set(asset_dir, 'wood', maps, owner=chair)
T.publish_set(T.set_dir('concrete'), 'concrete', {'diffuse': str(src / 'wood_col.png')})

stage = hou.node('/stage')
prim = stage.createNode('primitive', 'chair_prim')
prim.parm('primpath').set('/chair')
node = import_texture.place(stage, 'wood', chair)
node.setInput(0, prim)
wrap = import_texture.ImportTexture(node)

check('pins the newest version', node.parm('version').eval() == v1.name,
      node.parm('version').eval())
check('reads the layer through $TH_EXPORT_PATH',
      node.parm('layer').unexpandedString().startswith('$TH_EXPORT_PATH/'),
      node.parm('layer').unexpandedString())
check('status says latest', node.parm('status').eval() == 'wood v0001 (latest)',
      node.parm('status').eval())
check('set menu lists the asset sets', wrap.list_set_names() == ['wood'],
      str(wrap.list_set_names()))

node.cook(force=True)
usd = node.stage()
mat = UsdShade.Material(usd.GetPrimAtPath('/chair/mtl/wood'))
check('material on the stage at <input>/mtl/<set>', bool(mat),
      str([p.GetPath() for p in usd.Traverse()]))
if mat:
    surf, _, _ = mat.GetSurfaceOutput('mtlx').GetConnectedSource()
    check('surface connected', surf is not None and surf.GetPath().name == 'surf')
    img = UsdShade.Shader(usd.GetPrimAtPath('/chair/mtl/wood/tex_basecolor'))
    file_input = img.GetInput('file')
    resolved = Path(file_input.Get().resolvedPath or '')
    check('base colour resolves into the version',
          resolved.is_file() and resolved.parent == v1, str(resolved))
    check('base colour is sRGB',
          file_input.GetAttr().GetColorSpace() == T.COLOUR_SPACE_SRGB)
    rough = UsdShade.Shader(usd.GetPrimAtPath('/chair/mtl/wood/tex_roughness'))
    check('roughness is Raw',
          rough.GetInput('file').GetAttr().GetColorSpace() == T.COLOUR_SPACE_DATA)

v2 = T.publish_set(asset_dir, 'wood', {'diffuse': str(src / 'wood_rough.png')}, owner=chair)
check('a newer publish leaves the pin', node.parm('version').eval() == v1.name)
check('status names the newer version',
      node.parm('status').eval() == f'wood v0001: {v2.name} is newer, press Update',
      node.parm('status').eval())
node.parm('update').pressButton()
check('Update pins the newest', node.parm('version').eval() == v2.name)
check('Update moves the layer', v2.name in node.parm('layer').eval())
check('version menu is newest first',
      wrap.list_version_names() == [v2.name, v1.name], str(wrap.list_version_names()))

lib = import_texture.place(stage, 'concrete', None)
check('library owner lists library sets',
      import_texture.ImportTexture(lib).list_set_names() == ['concrete'])
check('library node pins its set', lib.parm('version').eval() == 'v0001')

loose = stage.createNode(import_texture.ns.find_node_type('import_texture', 'Lop').name())
check('an unset entity explains itself',
      loose.parm('status').eval().startswith('No asset'), loose.parm('status').eval())

print(f'\n{len(FAILED)} failed' if FAILED else '\nall passed')
sys.exit(1 if FAILED else 0)
