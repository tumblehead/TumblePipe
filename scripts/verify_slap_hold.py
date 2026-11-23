"""Audit: a scene comp's File COP over a check render holds the nearest frame, cooked.

    hython scripts/verify_slap_hold.py

A scene comp (#80: a Slap Comp Import into th::export_comp in the render
workfile, no th::import_render) comps the render of its Farm Submit: the
composite worker swaps each Slap Comp Import for File COPs of that render
(``cops.slap_comp.swap_imports``). Beside a stepped render (Soren,
2026-11-23) a comp on frames the render lacks holds the nearest rendered
frame (``comp.slap_frame_token``), as th::import_render's Missing Frames
holds them. ``tests/test_check_comp_hold.py`` evaluates the file names
without Houdini; this cooks them.

In a throwaway project (staged as scripts/verify_import_render_hold.py
stages renders, with hoiiotool) a shot of frames 1001-1013 with a Step 4
check render v0002 (1001, 1005, 1009, 1013, ``"published": false``) and a
published v0001 (every frame). Every frame's red says which frame and
version it is, so a cooked pixel tells which file was read. Then:

- swapped onto v0002, the Slap Comp Import's output is a File COP wired
  where it was, its file name HScript arithmetic (no Python, no directory
  listing), and every scene frame reads the nearest rendered frame, the
  earlier of two as near, before the first the first and after the last
  the last; every file it reads is on disk;
- a held frame (1003, 1004, 1011, 1012) and a rendered one cook the pixels
  of the nearest rendered frame, and the block downstream sees them;
- a th::import_render pinned to the same check render (Missing Frames
  Automatic) reads the same file on every frame (the two comp paths agree
  under Houdini);
- swapped onto the published v0001 (Step 1) the File COP reads ``$F4``,
  each frame itself.

Needs Houdini with the comp HDAs compiled and on HOUDINI_OTLSCAN_PATH, and
this checkout's python/ on PYTHONPATH (the preflight ``hython`` stage sets
both, and HOUDINI_USE_HFS_OCL=0, which COP cooks need). Exits 1 on any
failure; nothing outside a temp dir is written.
"""

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
sys.argv = [sys.argv[0]]
import verify_comp as vc  # noqa: E402  (its throwaway project and oiiotool)
import verify_import_render_hold as vh  # noqa: E402  (its staged renders)

import hou  # noqa: E402

from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.pipe import comp  # noqa: E402
from tumblepipe.pipe.houdini.cops import export_comp, import_render, slap_comp  # noqa: E402

check = vc.check
FIRST, LAST, STEP = vh.FIRST, vh.LAST, vh.STEP
CHECK_FRAMES = vh.CHECK_FRAMES


def slap_scene(name: str):
    """A render workfile's slap comp: Slap Comp Import (Karma's C) -> a
    block stand-in -> th::export_comp; ``(copnet, import, block, export)``."""
    copnet = hou.node('/stage').createNode('copnet', name)
    slap = copnet.createNode('slapcompimport', 'slap_comp_import')
    slap.parm('aovs').set(1)
    slap.parm('aov1').set('C')
    slap.parm('type1').set('vector4')
    block = copnet.createNode('null', 'slapcomp_block')
    block.setInput(0, slap, 0)
    node = export_comp.create(copnet, 'export_comp')
    node.native().setInput(0, block, 0)
    return copnet, slap, block, node


def frame_of(file_node, frame: int) -> int:
    return int(file_node.parm('filename').evalAtFrame(frame).rsplit('.', 2)[-2])


def red_of(cop, frame: int):
    hou.setFrame(frame)
    try:
        cop.cook(force=True)
    except hou.OperationFailed:
        return None
    if cop.errors():
        return None
    layer = cop.layer(0)
    return None if layer is None else layer.computeAverage(0)


def swapped(shot, version: str):
    copnet, slap, block, node = slap_scene(f'slap_{version}')
    renders = [dict(channel='default', department='render', version=version)]
    lines = slap_comp.swap_imports(shot, renders, [node.native()])
    file_node = copnet.node(f'{slap.name()}_1_default')
    return lines, file_node, block


def main():
    shot = Uri.parse('entity:/shots/SQ010/SH310')
    vc.api.config.add_entity(shot, {
        'frame_start': FIRST, 'frame_end': LAST, 'roll_start': 0, 'roll_end': 0,
        'fps': 24, 'channels': ['default'],
    })
    vh.stage(shot, 1, vh.ALL_FRAMES, published=True, step=1)
    vh.stage(shot, 2, CHECK_FRAMES, published=False, step=STEP)

    hou.hipFile.clear(suppress_save_prompt=True)
    hou.playbar.setFrameRange(FIRST - 10, LAST + 10)
    hou.setFrame(FIRST)
    vc.use_context(shot)

    print('--- swapped onto the Step 4 check render')
    lines, file_node, block = swapped(shot, 'v0002')
    check('the Slap Comp Import is swapped for a File COP of the check render',
          file_node is not None and file_node.type().name() == 'file' and len(lines) == 1,
          f'{lines} {file_node}')
    check('the block reads the File COP where it read the Slap Comp Import',
          block.inputs()[:1] == (file_node,), str(block.inputs()))
    name = file_node.parm('filename').unexpandedString()
    check('the file name is HScript arithmetic: no Python, no directory listing',
          'clamp(floor(' in name and 'python' not in name.lower() and '$F4' not in name, name)
    span = range(FIRST - 6, LAST + 7)
    wrong = {f: frame_of(file_node, f) for f in span
             if frame_of(file_node, f) != vh.nearest(CHECK_FRAMES, f)}
    check('every scene frame reads the nearest rendered frame, the earlier of two as near',
          not wrong, str(wrong))
    check('every file a frame reads is on disk',
          all(Path(file_node.parm('filename').evalAtFrame(f)).is_file() for f in span))
    for frame, want in ((1003, 1001), (1004, 1005), (1005, 1005), (1011, 1009), (1012, 1013)):
        got = red_of(file_node, frame)
        check(f'frame {frame} cooks the pixels of {want}', vh.close(got, vh.red(want, 2)),
              f'got {got}, want {vh.red(want, 2)}')
    got = red_of(block, 1004)
    check('the block downstream sees the held frame', vh.close(got, vh.red(1005, 2)),
          f'got {got}, want {vh.red(1005, 2)}')
    held = comp.held_frames(
        [comp.Resolved('default', 'render', 'v0002', {}, STEP, check_range=(FIRST, LAST))],
        (FIRST, LAST, 1))
    moved = [f for f in range(FIRST, LAST + 1) if frame_of(file_node, f) != f]
    check('the frames a Submit calls held are the ones the File COP reads from another frame',
          held == moved, f'{held} {moved}')

    print('--- th::import_render on the same check render')
    copnet = hou.node('/stage').createNode('copnet', 'comp')
    node = import_render.create(copnet, 'import_hold')
    node.parm('department').set('render')
    node.native().parm('version').set('v0002')
    node.execute()
    beauty = [f for f in vh.files(node) if '/beauty/' in f.parm('filename').evalAtFrame(FIRST)]
    check('the import reads the check render, holding it',
          node.get_resolved() == ('render', 'v0002') and len(beauty) == 1
          and node.get_held() == {'default': (FIRST, LAST, STEP)},
          f'{node.get_resolved()} {beauty} {node.get_held()}')
    if beauty:
        differ = {f: (beauty[0].parm('filename').evalAtFrame(f), file_node.parm('filename').evalAtFrame(f))
                  for f in span
                  if beauty[0].parm('filename').evalAtFrame(f) != file_node.parm('filename').evalAtFrame(f)}
        check('the import and the scene comp read the same file on every frame', not differ,
              str(differ))

    print('--- swapped onto the published render (Step 1)')
    lines, plain, _ = swapped(shot, 'v0001')
    check('a render on Step 1 reads $F4, each frame itself',
          plain is not None and plain.parm('filename').unexpandedString().count('$F4') == 1
          and all(frame_of(plain, f) == f for f in vh.ALL_FRAMES),
          plain.parm('filename').unexpandedString() if plain else str(lines))
    got = red_of(plain, 1003)
    check('frame 1003 of it cooks its own pixels', vh.close(got, vh.red(1003, 1)),
          f'got {got}, want {vh.red(1003, 1)}')

    print()
    print(f'{len(vc._failures)} failure(s)' if vc._failures else 'all checks passed')
    shutil.rmtree(vc.TEMP_ROOT, ignore_errors=True)
    return 1 if vc._failures else 0


if __name__ == '__main__':
    hou.exit(main())
