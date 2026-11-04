"""Drive ``th::export_texture`` (COP) end to end in hython on a throwaway project.

Wires Copernicus constants into some of the node's role inputs, presses
Publish (``export_texture.publish``) and checks:

- only the connected roles are written, and the version holds them, the
  material layer and ``context.json``;
- a data map is written raw: a roughness of 0.25 reads back 0.25 from the
  published file, whatever the format's usual encoding;
- a second publish is the next version; the library owner publishes into
  ``export/textures``;
- no inputs, or a bad set name, raise and publish nothing.

Run with the build's hython; an HDA file to test can be given as the first
argument (default: the repo's ``otls/cop_th.export_texture.1.0.hda``)::

    hython scripts/verify_export_texture.py [C:/hx/export_texture.hda]
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix='verify-export-texture-'))
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

atexit.register(shutil.rmtree, TMP, True)

from tumblepipe.api import api  # noqa: E402
from tumblepipe.pipe import textures as T  # noqa: E402
from tumblepipe.pipe.houdini.cops import export_texture  # noqa: E402
from tumblepipe.pipe.houdini.nodes import NodeError  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

FAILED = []


def check(label: str, ok: bool, detail: str = '') -> None:
    print(('PASS  ' if ok else 'FAIL  ') + label + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        FAILED.append(label)


def first_png_byte(path: Path):
    """The first channel of a PNG's first pixel (8-bit), or None.

    Every PNG filter leaves the first pixel of the first row as stored,
    so it is the first byte after the row's filter byte."""
    import struct
    import zlib

    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    pos, chunks, depth = 8, b'', None
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            depth = body[8]
        elif kind == b'IDAT':
            chunks += body
        pos += 12 + length
    raw = zlib.decompress(chunks)
    if depth == 16:
        return round(((raw[1] << 8) | raw[2]) / 257)
    return raw[1]


hda = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    REPO / 'otls' / 'cop_th.export_texture.1.0.hda')
hou.hda.installFile(str(hda))
for definition in hou.hda.definitionsInFile(str(hda)):
    definition.setIsPreferred(True)

chair = Uri.parse('entity:/assets/props/chair')
api.config.add_entity(Uri.parse('entity:/assets/props'), {})
api.config.add_entity(chair, {'name': 'chair'})

net = hou.node('/obj').createNode('copnet', 'tex')
node_type = export_texture.ns.find_node_type('export_texture', 'Cop')
node = net.createNode(node_type.name(), 'export_wood')
node.parm('entity').set(str(chair))
node.parm('set').set('wood')


def constant(name, signature, value):
    c = net.createNode('constant', name)
    c.parm('signature').set(signature)
    if signature == 'f1':
        c.parm('f1').set(value)
    else:
        c.parmTuple('f3').set(value)
    return c


node.setInput(0, constant('col', 'f3', (0.5, 0.5, 0.5)))  # grey: no gamut shift
node.setInput(1, constant('rough', 'f1', 0.25))
node.setInput(3, constant('nrm', 'f3', (0.5, 0.5, 1.0)))
wrap = export_texture.ExportTexture(node)
check('connected roles', wrap.connected_roles() == ['diffuse', 'rough', 'normal'],
      str(wrap.connected_roles()))
check('status before publishing', node.parm('status').eval().startswith('Not published'),
      node.parm('status').eval())

for fmt in ('exr', 'png'):
    node.parm('format').set(fmt)
    version = wrap.publish()
    data = T.read_version(version)
    check(f'{fmt}: a finished texture version', data is not None)
    roles = sorted(m['role'] for m in data['maps']) if data else []
    check(f'{fmt}: only connected roles published',
          roles == ['diffuse', 'normal', 'rough'], str(roles))
    check(f'{fmt}: material layer written', (version / 'wood.usda').is_file())
    rough_file = version / f'wood_rough.{fmt}'
    check(f'{fmt}: roughness file', rough_file.is_file(), str(sorted(p.name for p in version.iterdir())))
    if fmt == 'png' and rough_file.is_file():
        value = first_png_byte(rough_file)
        # Raw: 0.25 * 255 = 64. sRGB-encoded it would be about 137.
        check('png: roughness written raw (0.25 -> 64)',
              value is not None and abs(value - 64) <= 1, str(value))
        col = first_png_byte(version / 'wood_diffuse.png')
        # Grey 0.5 scene-linear, sRGB-encoded for the file: about 188.
        check('png: base colour written sRGB (0.5 -> ~188)',
              col is not None and abs(col - 188) <= 2, str(col))

check('second publish is the next version',
      T.finished_versions(T.set_dir('wood', chair)) == ['v0001', 'v0002'])
check('status names the latest', node.parm('status').eval().startswith('Latest v0002'),
      node.parm('status').eval())

node.parm('owner').set('library')
node.parm('set').set('concrete')
lib_version = wrap.publish()
check('library publish lands in export/textures',
      lib_version.parent == T.set_dir('concrete'), str(lib_version))

for label, setup in (
        ('a bad set name raises', lambda: node.parm('set').set('con crete')),
        ('no inputs raises', lambda: ([node.setInput(i, None) for i in range(7)],
                                      node.parm('set').set('empty')))):
    setup()
    try:
        wrap.publish()
        check(label, False, 'published')
    except (NodeError, T.TextureError):
        check(label, True)
check('nothing published for the refusals',
      not T.set_dir('empty').exists())

print(f'\n{len(FAILED)} failed' if FAILED else '\nall passed')
sys.exit(1 if FAILED else 0)
