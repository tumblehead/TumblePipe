#!/usr/bin/env hython
"""Verify th::animate's Attributes folder on the real HDA.

What th::animate brings to the stage was fixed in the HDA: the imports'
Attributes pattern, their Set Missing Widths, and the rules ``apply``
follows. A problem attribute meant a release (Remy, tumblepipe__rfe
2026-11-19). The node now has an Attributes folder, whose defaults are what
it did before: ``tests/test_animate_points.py`` pins the pattern
(``attribute_pattern``) and the options ``apply`` reads on plain data; this
cooks the real HDA with each parm at its default and changed.

The dive is plain SOPs: ``/CHAR/hero/geo/body``, two boxes under one path
with ``Cd``, ``uv``, point ``N``, a ``rest`` attribute and ``usdvisibility``
that hides the second box on frame 2; ``/CHAR/hero/geo/hair``, a curve with
no width. The incoming stage has ``st`` on the body.

- at the defaults: the imports read the old pattern's attributes (the
  static import with the stamps), the body has ``displayColor`` and
  normals, no ``st`` of its own (the asset's stands) and no ``rest``, the
  hair is 0.01 wide, and on frame 2 the body is visible with holes;
- **Attributes** with ``rest`` added: ``primvars:rest`` is on the body;
  with ``* ^Cd ^P``: no ``displayColor``, the points still come, and no
  ``__`` or ``usd`` bookkeeping attribute becomes a primvar;
- **Normals** off: no normals are authored;
- **Keep Asset Primvars** off: the body's ``st`` is in th::animate's layer;
- **Missing Width** 0.05: the hair is 0.05 wide;
- **Hide Parts** off: no ``holeIndices`` is authored and the body's first
  shape decides on frame 2 (visible).

Needs a Houdini license; preflight's hython stage runs it. Prints one line
per check and exits 1 on any failure.
"""

import sys
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

FAILED = []
BODY, HAIR = '/CHAR/hero/geo/body', '/CHAR/hero/geo/hair'
PARMS = {'attribs': 'Cd Alpha width pscale uv*', 'normals': 1, 'hide_parts': 1,
         'asset_primvars': 1, 'missing_width': 0.01}
# What the imports' Attributes held up to 1.114, hardcoded.
OLD = 'P N visibility bounds Cd Alpha transform width pscale uv*'

DIVE_GEO = '''
node = hou.pwd()
geo = node.geometry()
frame = int(hou.frame())

def box(x, hidden):
    verb = hou.sopNodeTypeCategory().nodeVerb('box')
    verb.setParms({'type': 1, 'divrate': (2, 2, 2)})
    out = hou.Geometry()
    verb.execute(out, [])
    out.transform(hou.hmath.buildTranslate(x + 0.1 * frame, 0, 0))
    for name, default in (('Cd', (1.0, 0.5, 0.25)), ('uv', (0.5, 0.5, 0.0)),
                          ('rest', (0.0, 0.0, 0.0)), ('N', (0.0, 1.0, 0.0))):
        out.addAttrib(hou.attribType.Point, name, default)
    out.addAttrib(hou.attribType.Prim, 'path', '')
    out.setPrimStringAttribValues('path', ['%(body)s'] * len(out.prims()))
    out.addAttrib(hou.attribType.Prim, 'usdvisibility', '')
    out.setPrimStringAttribValues(
        'usdvisibility', ['invisible' if hidden else 'inherited'] * len(out.prims()))
    return out

geo.merge(box(-2.0, False))
geo.merge(box(2.0, frame == 2))
hair = hou.Geometry()
line = hair.createPolygon(is_closed=False)
for point in hair.createPoints([(0.0, 1.0 + i, 0.1 * frame) for i in range(3)]):
    line.addVertex(point)
hair.addAttrib(hou.attribType.Prim, 'path', '')
hair.setPrimStringAttribValues('path', ['%(hair)s'])
geo.merge(hair)
''' % {'body': BODY, 'hair': HAIR}

# The asset under th::animate: the body with UVs of its own (8 points).
UNDER = '''
def Xform "CHAR"
{
    def Xform "hero"
    {
        def Xform "geo"
        {
            def Mesh "body"
            {
                texCoord2f[] primvars:st = [(0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0)] (
                    interpolation = "vertex"
                )
            }
        }
    }
}
'''


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}", flush=True)
    if not ok:
        FAILED.append(name)


def install():
    """Houdini's own packages and the expanded HDA sources."""
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def build(label, **parms):
    animate = hou.node('/stage').createNode('th::animate::2.0', f'animate_{label}')
    dive = animate.node('anim_dive')
    output = dive.node('output0')
    for child in dive.children():
        if child != output:
            child.destroy()
    source = dive.createNode('python', 'shapes')
    source.parm('python').set(DIVE_GEO)
    output.setInput(0, source)
    below = hou.node('/stage').createNode('inlineusd', f'under_{label}')
    below.parm('usdsource').set(UNDER)
    animate.setInput(0, below)
    for name, value in parms.items():
        animate.parm(name).set(value)
    return animate


def own(animate, path):
    """The properties th::animate's own layers author on the prim at
    `path` (the layers that have its points, not the incoming stage's)."""
    names = set()
    for layer in animate.stage().GetLayerStack(False):
        spec = layer.GetPrimAtPath(path)
        if spec is not None and 'points' in spec.properties:  # th::animate's
            names |= set(spec.properties.keys())
    return names


def read(animate, frame):
    """What the node's stage has at `frame`, for the checks below."""
    from pxr import UsdGeom
    hou.setFrame(frame)
    stage = animate.stage()
    body = stage.GetPrimAtPath(BODY)
    holes = body.GetAttribute('holeIndices')
    widths = stage.GetPrimAtPath(HAIR).GetAttribute('widths').Get(frame)
    return {
        'own': own(animate, BODY),
        'st': tuple(round(v, 3) for v in
                    (body.GetAttribute('primvars:st').Get(frame) or [(9, 9)])[0]),
        'visibility': UsdGeom.Imageable(body).GetVisibilityAttr().Get(frame),
        'holes': len(holes.Get(frame) or ()) if holes and holes.HasAuthoredValue() else None,
        'points': len(body.GetAttribute('points').Get(frame) or ()),
        'width': None if not widths else round(float(widths[0]), 5),
        'errors': animate.errors(),
    }


def main():
    install()
    hou.playbar.setFrameRange(1, 3)
    hou.playbar.setPlaybackRange(1, 3)
    hou.setFrame(1)
    from tumblepipe.pipe.houdini.lops import animate_points

    default = build('default')
    for name, value in PARMS.items():
        parm = default.parm(name)
        found = None if parm is None else parm.eval()
        ok = parm is not None and (abs(found - value) < 1e-9 if isinstance(value, float)
                                   else found == value)
        check(f'the node has {name} with the default', ok, found)
    stamps = {animate_points.STAMP, animate_points.VERTEX_STAMP, animate_points.PRIM_STAMP}
    for node, extra in (('static_import', stamps), ('remainder_import', set()),
                        ('full_import', set())):
        pattern = default.node(node).evalParm('attribs')
        check(f'{node} reads what it read up to 1.114' + (', and the stamps' if extra else ''),
              {name for name in pattern.split() if not name.startswith('^')}
              == set(OLD.split()) | extra, pattern)
        width = default.node(node).evalParm('setmissingwidths')
        check(f'{node} sets missing widths to 0.01', abs(width - 0.01) < 1e-9, width)

    first, second = read(default, 1), read(default, 2)
    check('defaults: the node cooks', not first['errors'] and not second['errors'],
          (first['errors'], second['errors']))
    names = first['own']
    check('defaults: displayColor and normals, no st over the asset\'s, no rest',
          'primvars:displayColor' in names and 'normals' in names
          and first['st'] == (0.0, 0.0) and 'primvars:rest' not in names
          and not any('__' in name for name in names), (first['st'], sorted(names)))
    check('defaults: the hair is 0.01 wide', first['width'] == 0.01, first['width'])
    check('defaults: frame 2 hides half the body, not the body',
          second['visibility'] == 'inherited' and second['holes'] == 6
          and first['holes'] == 0, (second['visibility'], first['holes'], second['holes']))

    names = read(build('rest', attribs='Cd Alpha width pscale uv* rest'), 1)['own']
    check('Attributes with rest added: primvars:rest is authored',
          'primvars:rest' in names and 'primvars:displayColor' in names, sorted(names))
    found = read(build('no_cd', attribs='* ^Cd ^P ^rest'), 1)
    check('Attributes with ^Cd ^P: no displayColor, and the points still come',
          'primvars:displayColor' not in found['own'] and 'primvars:rest' not in found['own']
          and 'points' in found['own'] and found['points'] == 16 and not found['errors']
          and not any('__' in name or 'usd' in name for name in found['own']),
          (sorted(found['own']), found['points'], found['errors']))
    found = read(build('no_normals', normals=0), 1)
    check('Normals off: no normals authored',
          'normals' not in found['own'] and 'primvars:displayColor' in found['own']
          and not found['errors'], (sorted(found['own']), found['errors']))
    found = read(build('dive_uvs', asset_primvars=0), 1)
    check('Keep Asset Primvars off: the dive\'s st is authored', found['st'] == (0.5, 0.5),
          found['st'])
    found = read(build('wide', missing_width=0.05), 1)
    check('Missing Width 0.05: the hair is 0.05 wide', found['width'] == 0.05, found['width'])
    whole = build('whole', hide_parts=0)
    first, second = read(whole, 1), read(whole, 2)
    check('Hide Parts off: no holeIndices, the first shape decides',
          'holeIndices' not in first['own'] and 'holeIndices' not in second['own']
          and second['visibility'] == 'inherited' and not second['errors'],
          (sorted(second['own']), second['visibility'], second['errors']))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    print(f"{'FAILED: ' + ', '.join(FAILED) if FAILED else 'ALL OK'}", flush=True)
    sys.exit(1 if FAILED else 0)
