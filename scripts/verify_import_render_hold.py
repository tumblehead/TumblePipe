"""Audit: th::import_render's Missing Frames on a check render, cooked.

    hython scripts/verify_import_render_hold.py

Remy, 2026-11-18: a check render (Farm Submit's Step) read into a comp is
black between its rendered frames; he asked for the nearest frame held.
In a throwaway project (staged as scripts/verify_comp.py stages renders,
with hoiiotool) a shot of frames 1001-1013 with a published render v0001
(every frame) and a newer check render v0002 (every 4th frame: 1001, 1005,
1009, 1013, ``"published": false``). Every frame's red says which frame it
is, so a cooked pixel tells which file was read. Then:

- the Version menu lists the check render, labelled ``v0002 (check, every
  4th)``; ``latest`` still reads v0001, the newest published one;
- pinned to v0002 with Missing Frames on Automatic, each scene frame reads
  the nearest rendered frame, the earlier one when two are as near (1003
  reads 1001), frames before the first and after the last the first and
  the last; checked against a brute-force nearest over the frames on disk,
  for every File COP of the node, and by the pixels at a held frame; the
  line under the parm says a check render is held, and Reads names it;
- the file names are plain HScript arithmetic (no Python, nothing that
  lists a directory when a frame cooks);
- Empty: the same version reads its own frame, so a gap is empty (black);
- the published v0001: Automatic leaves a frame outside its range empty,
  as before; Hold Nearest Frame holds its first and last frame there;
- with a time offset, the scene's frame less the offset is what is held;
- an all-channels import (``latest`` only) holds nothing on Automatic;
- a submit's freeze of an import reading a check render is refused.

Needs Houdini with the comp HDAs compiled and on HOUDINI_OTLSCAN_PATH, and
this checkout's python/ on PYTHONPATH (the preflight ``hython`` stage sets
both, and HOUDINI_USE_HFS_OCL=0, which COP cooks need). Exits 1 on any
failure; nothing outside a temp dir is written.
"""

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
sys.argv = [sys.argv[0]]
import verify_comp as vc  # noqa: E402  (its throwaway project and oiiotool)

import hou  # noqa: E402

from tumblepipe.api import api  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.pipe import comp, hold_frames  # noqa: E402
from tumblepipe.pipe.houdini.cops import import_render  # noqa: E402
import tumblepipe.pipe.houdini.nodes as ns  # noqa: E402

check = vc.check
FIRST, LAST, STEP = 1001, 1013, 4
CHECK_FRAMES = list(range(FIRST, LAST + 1, STEP))
ALL_FRAMES = list(range(FIRST, LAST + 1))


def red(frame: int, version: int) -> float:
    """The red of ``frame`` in version ``version``: tells both apart."""
    return round((frame - 1000) / 100 + (0.5 if version == 2 else 0.0), 4)


def stage(shot, version: int, frames, published: bool, step: int):
    name = f'v{version:04d}'
    root = api.storage.resolve(
        Uri.parse('render:/render') / shot.segments / 'render' / 'default' / name)
    stem = '_'.join(shot.segments[1:])
    for aov in ('beauty', 'depth'):
        (root / aov).mkdir(parents=True, exist_ok=True)
        for frame in frames:
            colour = (red(frame, version), 0.25, 0.5, 1.0) if aov == 'beauty' else (5.0, 5.0, 5.0)
            names = [f'{aov}.{n}' for n in ('R', 'G', 'B', 'A')[:len(colour)]]
            vc.oiiotool('--pattern', 'constant:color=' + ','.join(map(str, colour)),
                        '32x16', len(colour), '--chnames', ','.join(names),
                        *vc.CHROMA, '-d', 'float', '-o',
                        root / aov / f'{stem}_default_{aov}_{name}.{frame}.exr')
    context = dict(entity=str(shot), department='light', channel='default',
                   render_department_name='render', version_name=name,
                   first_frame=FIRST, last_frame=LAST, step_size=step)
    if not published:
        context['published'] = False
    (root / 'context.json').write_text(json.dumps(context))


def nearest(frames, frame: int) -> int:
    """Brute force: the nearest of ``frames``, the earlier of two as near."""
    return min(frames, key=lambda have: (abs(have - frame), have))


def files(node):
    dive = node.native().node(import_render.DIVE)
    return [c for c in dive.children() if c.type().name() == 'file']


def read_frames(node, frame: int) -> set:
    """The frame numbers the node's File COPs read at scene frame ``frame``."""
    return {int(f.parm('filename').evalAtFrame(frame).rsplit('.', 2)[-2]) for f in files(node)}


def beauty_red(node, frame: int):
    hou.setFrame(frame)
    native = node.native()
    native.cook(force=True)
    layer = native.layer(import_render.RGBA_OUTPUT)
    return None if layer is None else layer.computeAverage(0)


def close(got, want) -> bool:
    return got is not None and abs(got - want) < 2e-3


def empty(got) -> bool:
    return got is None or abs(got) < 1e-6


def main():
    shot = Uri.parse('entity:/shots/SQ010/SH300')
    api.config.add_entity(shot, {
        'frame_start': FIRST, 'frame_end': LAST, 'roll_start': 0, 'roll_end': 0,
        'fps': 24, 'channels': ['default'],
    })
    stage(shot, 1, ALL_FRAMES, published=True, step=1)
    stage(shot, 2, CHECK_FRAMES, published=False, step=STEP)

    hou.hipFile.clear(suppress_save_prompt=True)
    hou.playbar.setFrameRange(FIRST - 10, LAST + 110)
    hou.setFrame(FIRST)
    vc.use_context(shot)
    copnet = hou.node('/stage').createNode('copnet', 'comp')
    node = import_render.create(copnet, 'import_hold')
    node.parm('department').set('render')
    node.execute()
    native = node.native()

    print('--- the Version menu and latest')
    menu = node.version_menu()
    pairs = list(zip(menu[::2], menu[1::2]))
    check('the Version menu lists the check render, labelled, newest first',
          pairs == [('latest', 'latest'), ('v0002', 'v0002 (check, every 4th)'), ('v0001', 'v0001')],
          str(pairs))
    check('latest reads the newest published version, not the newer check render',
          node.get_resolved() == ('render', 'v0001'), str(node.get_resolved()))
    check('a published version on Automatic holds nothing: plain frames',
          node.get_held() == {} and all(
              import_render.PLAIN_FRAME in f.parm('filename').unexpandedString() for f in files(node)))
    check('Automatic says a published version is empty where it has no frame',
          native.parm('missing_frames_label').eval()
          == 'Automatic: empty (black) where the render has no frame (not a check render)',
          native.parm('missing_frames_label').eval())
    check('a frame after a published version is empty', empty(beauty_red(node, LAST + 5)),
          str(beauty_red(node, LAST + 5)))

    print('--- pinned to the check render, Automatic')
    native.parm('version').set('v0002')
    node.execute()
    check('the check render resolves when picked', node.get_resolved() == ('render', 'v0002'),
          str(node.get_resolved()))
    check('Reads names it as a check render',
          '(check, every 4th)' in native.parm('version_label').eval(),
          native.parm('version_label').eval())
    check('Automatic holds a check render, and says so',
          node.get_held() == {'default': (FIRST, LAST, STEP)}
          and native.parm('missing_frames_label').eval()
          == 'Automatic: holding the nearest rendered frame (a check render, every 4th frame)',
          f'{node.get_held()} {native.parm("missing_frames_label").eval()!r}')
    check('the node reads two files (beauty and depth)', len(files(node)) == 2, str(files(node)))
    wrong = {frame: read_frames(node, frame) for frame in range(FIRST - 8, LAST + 9)
             if read_frames(node, frame) != {nearest(CHECK_FRAMES, frame)}}
    check('every scene frame reads the nearest rendered frame, the earlier of two as near',
          not wrong, str(wrong))
    check('1003 is as near 1001 as 1005: it reads 1001', read_frames(node, 1003) == {1001},
          str(read_frames(node, 1003)))
    on_disk = all(Path(f.parm('filename').evalAtFrame(frame)).is_file()
                  for f in files(node) for frame in range(FIRST - 8, LAST + 9))
    check('every file a frame reads is on disk', on_disk)
    names = [f.parm('filename').unexpandedString() for f in files(node)]
    check('the file names are HScript arithmetic: no Python, no directory listing',
          all('python' not in name.lower() and 'clamp(floor(' in name for name in names), str(names))
    for frame, want in ((1001, 1001), (1003, 1001), (1004, 1005), (1011, 1009), (1012, 1013)):
        got = beauty_red(node, frame)
        check(f'frame {frame} shows the pixels of {want}', close(got, red(want, 2)),
              f'got {got}, want {red(want, 2)}')
    check('the node cooks with no error', not native.errors(), str(native.errors()))

    print('--- Empty')
    native.parm('missing_frames').set('empty')
    node.execute()
    check('Empty: the same check render reads its own frames',
          node.get_held() == {} and read_frames(node, 1003) == {1003},
          f'{node.get_held()} {read_frames(node, 1003)}')
    check('Empty: a gap is empty (black), a rendered frame is not',
          empty(beauty_red(node, 1003)) and close(beauty_red(node, 1005), red(1005, 2)))
    check('Empty says so, and that it is a check render',
          native.parm('missing_frames_label').eval()
          == 'Empty (black) where the render has no frame (a check render, every 4th frame)',
          native.parm('missing_frames_label').eval())

    print('--- Hold Nearest Frame on a published version')
    native.parm('version').set('v0001')
    native.parm('missing_frames').set('hold')
    node.execute()
    check('Hold on a published version: every frame of its range reads itself',
          all(read_frames(node, frame) == {frame} for frame in ALL_FRAMES))
    check('Hold: before the first frame the first, after the last the last',
          read_frames(node, FIRST - 6) == {FIRST} and read_frames(node, LAST + 6) == {LAST},
          f'{read_frames(node, FIRST - 6)} {read_frames(node, LAST + 6)}')
    check('Hold: a frame after the range shows the last frame\'s pixels',
          close(beauty_red(node, LAST + 5), red(LAST, 1)), str(beauty_red(node, LAST + 5)))
    check('Hold says so', native.parm('missing_frames_label').eval()
          == 'Holding the nearest rendered frame', native.parm('missing_frames_label').eval())

    print('--- a time offset')
    native.parm('version').set('v0002')
    native.parm('missing_frames').set('auto')
    native.parm('time_offset_mode').set('manual')
    native.parm('time_offset').set(100)
    node.execute()
    wrong = {frame: read_frames(node, frame) for frame in range(FIRST + 95, LAST + 106)
             if read_frames(node, frame) != {nearest(CHECK_FRAMES, frame - 100)}}
    check('with an offset of 100, scene frame F holds the nearest to F - 100',
          node.get_applied_offset() == 100 and not wrong, f'{node.get_applied_offset()} {wrong}')
    native.parm('time_offset').set(40)
    import_render.sync_time_offsets()
    check('a changed offset moves the held frames without a rebuild',
          read_frames(node, 1043) == {1001} and read_frames(node, 1044) == {1005},
          f'{read_frames(node, 1043)} {read_frames(node, 1044)}')
    native.parm('time_offset_mode').set('none')
    node.execute()

    print('--- a submit')
    try:
        node.import_states()
        refused = ''
    except ns.NodeError as error:
        refused = str(error)
    check('a submit of an import reading a check render is refused, saying why',
          'check render' in refused and 'v0002' in refused, refused)
    native.parm('version').set('latest')
    node.execute()
    check('back on latest, a submit freezes the published version',
          [(s.resolved_department, s.resolved_version) for s in node.import_states()]
          == [('render', 'v0001')])

    print('--- all channels')
    everything = import_render.create(copnet, 'import_all')
    everything.parm('department').set('render')
    everything.set_channel_name(comp.ALL_CHANNELS)
    everything.execute()
    check('all channels on Automatic read latest and hold nothing',
          everything.get_held() == {} and read_frames(everything, 1003) == {1003},
          f'{everything.get_held()} {read_frames(everything, 1003)}')
    everything.native().parm('missing_frames').set('hold')
    everything.execute()
    check('all channels on Hold hold each channel\'s first and last frame',
          everything.get_held() == {'default': (FIRST, LAST, 1)}
          and read_frames(everything, LAST + 3) == {LAST}, str(everything.get_held()))

    check('the pure rule agrees with the node on every frame',
          all(hold_frames.held_frame(frame, FIRST, LAST, STEP) == nearest(CHECK_FRAMES, frame)
              for frame in range(FIRST - 8, LAST + 9)))

    print()
    print(f'{len(vc._failures)} failure(s)' if vc._failures else 'all checks passed')
    shutil.rmtree(vc.TEMP_ROOT, ignore_errors=True)
    return 1 if vc._failures else 0


if __name__ == '__main__':
    hou.exit(main())
