#!/usr/bin/env hython
"""Verify the TumblePipe slap comp preset in Houdini, and husk running it (#81).

``tests/test_slap_comp.py`` pins the preset's spec, the publish layout and
the farm plumbing without Houdini; this drives the real nodes:

1. ``pipe.houdini.cops.slap_comp_preset.build`` in a COP network under /stage and
   under /obj: the Slap Comp Import, Block Begin and Block End read back
   ``beauty`` on every slap comp AOV row (never Karma's ``C``), are wired
   import -> begin -> end, the Block Begin's Block Path finds the Block End,
   which is registered as a slap comp block and carries the TumblePipe
   folder (Entity from_context, Use on Farm Renders on, Publish to Farm).
   A second preset in the same network gets numbered names that still find
   each other.
2. The block cooks, and ``compile_program`` writes the program (the Block to
   Geometry COP's geometry) to disk, leaving no compiler node behind.
3. With ``--husk`` (a Karma licence): husk renders a 32x32 Karma frame whose
   colour AOV is named ``beauty`` without and with ``--slap-comp <program>``
   of a preset with an Invert COP in the block; the slap-comped beauty is
   one minus the plain one (RGB), so husk ran the published program on the
   ``beauty`` AOV.

Needs a Houdini license, no project. Run with an empty package dir and the
repo's python/ on the path:

    HOUDINI_PACKAGE_DIR= PYTHONPATH=<repo>/python hython scripts/verify_slap_comp.py [--husk]

Exits 1 on any failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

import hou  # noqa: E402

from tumblepipe.pipe import slap_comp  # noqa: E402
from tumblepipe.pipe.houdini.cops import slap_comp_preset as th_slap  # noqa: E402

FAILURES = []


def check(condition: bool, message: str) -> None:
    print(('PASS ' if condition else 'FAIL ') + message)
    if not condition:
        FAILURES.append(message)


def rows(node, count_parm, pattern):
    return [
        node.evalParm(slap_comp.row_name(pattern, index))
        for index in range(node.evalParm(count_parm))
    ]


def check_preset(parent, label: str) -> dict:
    nodes = th_slap.build(parent)
    imp = nodes[slap_comp.IMPORT_NAME]
    begin = nodes[slap_comp.BEGIN_NAME]
    end = nodes[slap_comp.END_NAME]
    check(imp.type().name() == 'slapcompimport' and begin.type().name() == 'block_begin'
          and end.type().name() == 'block_end', f'{label}: stock node types')
    check(rows(imp, 'aovs', 'aov#') == ['beauty'], f'{label}: Slap Comp Import AOV is beauty')
    check(rows(begin, 'ports', 'name#') == ['beauty'], f'{label}: Block Begin input is beauty')
    check(rows(end, 'ports', 'name#') == ['beauty'], f'{label}: Block End output is beauty')
    check(begin.inputs()[:1] == (imp,), f'{label}: import wired into Block Begin')
    check(end.inputs()[:1] == (begin,), f'{label}: Block Begin wired into Block End')
    check(begin.node(begin.evalParm('blockpath')) == end, f'{label}: Block Path finds the Block End')
    check(end.evalParm('slapcomp') == 1, f'{label}: Block End registered as slap comp block')
    check(imp.evalParm('slapcompcameraspace') == end.evalParm('slapcompcameraspace'),
          f'{label}: import camera space follows the Block End')
    check(end.evalParm(th_slap.ENTITY_PARM) == th_slap.FROM_CONTEXT
          and end.evalParm(th_slap.FARM_PARM) == 1
          and end.parm(th_slap.PUBLISH_PARM) is not None,
          f'{label}: Block End carries the TumblePipe folder')
    return nodes


def main(argv) -> int:
    run_husk = '--husk' in argv
    work = Path(tempfile.mkdtemp(prefix='th_slapcomp_'))

    stage_net = hou.node('/stage').createNode('copnet', 'slap')
    obj_net = hou.node('/obj').createNode('copnet', 'slap')
    nodes = check_preset(stage_net, '/stage copnet')
    check_preset(obj_net, '/obj copnet')

    second = th_slap.build(stage_net)
    second_end = second[slap_comp.END_NAME]
    second_begin = second[slap_comp.BEGIN_NAME]
    check(second_end.name() == f'{slap_comp.END_NAME}2', 'second preset is numbered')
    check(second_begin.node(second_begin.evalParm('blockpath')) == second_end,
          'second preset Block Path finds its own Block End')

    end = nodes[slap_comp.END_NAME]
    # No viewport render in hython: feed the import's fallback input, which
    # it passes through when no render posted a layer (as a File COP of a
    # rendered plate would be).
    plate = stage_net.createNode('constant')
    nodes[slap_comp.IMPORT_NAME].setInput(0, plate, 0)
    try:
        end.cook(force=True)
    except hou.OperationFailed:
        pass
    check(not end.errors(), f'the preset block cooks on a fallback plate ({end.errors()})')
    before = {child.name() for child in stage_net.children()}
    program = work / 'preset.bgeo.sc'
    th_slap.compile_program(end, program)
    check(program.is_file() and program.stat().st_size > 0, 'compile_program writes the program')
    check({child.name() for child in stage_net.children()} == before,
          'compile_program leaves no compiler node behind')

    if run_husk:
        check_husk(work, nodes)

    print(f'\n{len(FAILURES)} failure(s); work dir {work}')
    return 1 if FAILURES else 0


def check_husk(work: Path, nodes: dict) -> None:
    """husk applies a compiled preset to the ``beauty`` AOV."""
    begin = nodes[slap_comp.BEGIN_NAME]
    end = nodes[slap_comp.END_NAME]
    invert = end.parent().createNode('invert')
    invert.setInput(0, begin, 0)
    end.setInput(0, invert, 0)
    program = work / 'invert.bgeo.sc'
    th_slap.compile_program(end, program)

    lop = hou.node('/stage')
    sphere = lop.createNode('sphere')
    dome = lop.createNode('domelight')
    dome.setInput(0, sphere)
    camera = lop.createNode('camera')
    camera.setInput(0, dome)
    camera.parm('tz').set(6)
    karma = lop.createNode('karmarenderproperties')
    karma.setInput(0, camera)
    karma.parm('camera').set('/cameras/camera1')
    stage = karma.stage()
    from pxr import Usd
    scene = work / 'scene.usda'
    stage.Flatten().Export(str(scene))
    edit = Usd.Stage.Open(str(scene))
    renamed = False
    for prim in edit.Traverse():
        attr = prim.GetAttribute('driver:parameters:aov:name')
        if prim.GetTypeName() == 'RenderVar' and attr and attr.Get() == slap_comp.KARMA_COLOR:
            attr.Set(slap_comp.BEAUTY)
            renamed = True
    edit.Save()
    check(renamed, "the test scene's colour AOV is renamed to beauty")

    hfs = Path(os.environ['HFS'])
    husk = hfs / 'bin' / ('husk.exe' if os.name == 'nt' else 'husk')
    oiiotool = hfs / 'bin' / ('hoiiotool.exe' if os.name == 'nt' else 'hoiiotool')

    def render(name, extra):
        out = work / f'{name}.exr'
        result = subprocess.run(
            [str(husk), '--no-mplay', '--res', '32', '32', '-f', '1',
             '-o', str(out), *extra, str(scene)],
            capture_output=True, text=True,
        )
        ok = result.returncode == 0 and out.is_file()
        check(ok, f'husk renders {name}' + (
            '' if ok else f' (exit {result.returncode}: {result.stderr[-300:]})'))
        return out

    def averages(path):
        result = subprocess.run([str(oiiotool), str(path), '--printstats'],
                                capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if 'Stats Avg' in line:
                return [float(v) for v in line.split(':')[1].split('(')[0].split()]
        return []

    plain = averages(render('plain', []))
    slapped = averages(render('slapped', slap_comp.husk_args([program])))
    check(len(plain) >= 3 and len(slapped) >= 3
          and all(abs((1.0 - p) - s) < 1e-3 for p, s in zip(plain[:3], slapped[:3])),
          f'husk --slap-comp inverted beauty: plain {plain[:3]} slapped {slapped[:3]}')


if __name__ == '__main__':
    try:
        code = main(sys.argv[1:])
    except Exception:
        traceback.print_exc()
        code = 1
    sys.exit(code)
