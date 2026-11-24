"""Audit: a re-comp beside a stepped render reads that render, cooked.

    hython scripts/verify_recomp_follows.py

Soren, 2026-11-22 (a00b102e): a Comp ticked beside a stepped render in Farm
Submit re-comps the shot's composite workfile on the farm, reading the
render it was submitted with, a check render, not the shot's older
published one. The composite job carries ``follows`` (the render's
``[{channel, department, version}]``) and ``comp_frames``; the worker
(``farm.workers.composite_houdini.resolve_imports``) pins each import to
it (``pipe.comp.follow_pins``) and checks the comp's frames against the
check renders it then reads (``pipe.comp.check_render_refusal``: any
frames, the frames they lack held, their first and last past their range;
Soren, 2026-11-23/24). The
property tests pin both pure halves; this runs them on real
th::import_render nodes, which no test did.

In a throwaway project (staged as scripts/verify_import_render_hold.py
stages, with oiiotool) a shot of frames 1001-1013 and two channels, each
with a published render v0001 (every frame) and a newer check render v0002
(every 2nd frame, ``"published": false``). The workfile holds a one-channel
import of ``default`` on latest, an all-channels import, and a one-channel
import of ``chars`` the artist pinned to v0001. Then:

- with no ``follows``, latest reads v0001 everywhere (a check render never
  takes latest), so the comp reads no check render, on any frames;
- following v0002 of both channels, on the check render's frames (Step 2):
  the latest import reads v0002, the all-channels import reads v0002 of
  every channel, the pinned import still reads v0001, and the worker
  reports a check render read (the comp is a check comp);
- the same again changes nothing (pinning is idempotent);
- following only ``default``: the all-channels import reads v0002 of it
  and latest (v0001) of ``chars``;
- on every frame (Step 1), or off the render's first frame, it reads the
  check render too, logging a WARNING that names the frames it holds
  (a Step 1 comp of a Step 2 render, Antoine and Magnus);
- reaching past the render's range it reads the check render too, the
  import holding its first frame before it (Soren, 2026-11-24); with the
  frames not known, the worker refuses.

Needs Houdini with the comp HDAs compiled and on HOUDINI_OTLSCAN_PATH, and
this checkout's python/ on PYTHONPATH (the preflight ``hython`` stage sets
both, and HOUDINI_USE_HFS_OCL=0). Exits 1 on any failure; nothing outside a
temp dir is written.
"""

import contextlib
import io
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
from tumblepipe.pipe import comp  # noqa: E402
from tumblepipe.pipe.houdini.cops import import_render  # noqa: E402
from tumblepipe.farm.workers import composite_houdini  # noqa: E402

check = vc.check
FIRST, LAST, STEP = 1001, 1013, 2
CHANNELS = ['default', 'chars']


def stage(shot, channel: str, version: int, step: int):
    """``channel``'s render ``v<version>`` of every ``step``-th frame;
    ``step`` above 1 a check render (``"published": false``)."""
    name = f'v{version:04d}'
    root = api.storage.resolve(
        Uri.parse('render:/render') / shot.segments / 'render' / channel / name)
    stem = '_'.join(shot.segments[1:])
    for aov in ('beauty', 'depth'):
        (root / aov).mkdir(parents=True, exist_ok=True)
        for frame in range(FIRST, LAST + 1, step):
            colour = (0.1 * version, 0.25, 0.5, 1.0) if aov == 'beauty' else (5.0, 5.0, 5.0)
            names = [f'{aov}.{n}' for n in ('R', 'G', 'B', 'A')[:len(colour)]]
            vc.oiiotool('--pattern', 'constant:color=' + ','.join(map(str, colour)),
                        '32x16', len(colour), '--chnames', ','.join(names),
                        *vc.CHROMA, '-d', 'float', '-o',
                        root / aov / f'{stem}_{channel}_{aov}_{name}.{frame}.exr')
    context = dict(entity=str(shot), department='light', channel=channel,
                   render_department_name='render', version_name=name,
                   first_frame=FIRST, last_frame=LAST, step_size=step)
    if step > 1:
        context['published'] = False
    (root / 'context.json').write_bytes(json.dumps(context).encode())


def reads_of(node):
    """``{channel: (department, version)}`` the node reads now."""
    return {c: (r.department, r.version) for c, r in node.execute().items()}


def follows(*channels):
    return [dict(channel=c, department='render', version='v0002') for c in channels]


def main():
    shot = Uri.parse('entity:/shots/SQ010/SH310')
    api.config.add_entity(shot, {
        'frame_start': FIRST, 'frame_end': LAST, 'roll_start': 0, 'roll_end': 0,
        'fps': 24, 'channels': CHANNELS,
    })
    for channel in CHANNELS:
        stage(shot, channel, 1, 1)
        stage(shot, channel, 2, STEP)

    hou.hipFile.clear(suppress_save_prompt=True)
    hou.playbar.setFrameRange(FIRST, LAST)
    hou.setFrame(FIRST)
    vc.use_context(shot)
    copnet = hou.node('/stage').createNode('copnet', 'comp')
    latest = import_render.create(copnet, 'import_default')
    latest.parm('department').set('render')
    latest.set_channel_name('default')
    everything = import_render.create(copnet, 'import_all')
    everything.parm('department').set('render')
    everything.set_channel_name(comp.ALL_CHANNELS)
    pinned = import_render.create(copnet, 'import_chars')
    pinned.parm('department').set('render')
    pinned.set_channel_name('chars')
    pinned.parm('version').set('v0001')
    nodes = (latest, everything, pinned)
    for node in nodes:
        node.execute()

    def fresh():
        """The workfile as the artist saved it: the worker loads a copy."""
        for node in nodes:
            node.parm('version').set('v0001' if node is pinned else 'latest')
            node.parm('department').set('render')
            if node is everything:
                node.pin_channels({})
            node.execute()

    def run(frames, follow):
        """``(plan, check, problem)`` of the worker's resolve."""
        return composite_houdini.resolve_imports(shot, frames, follow)

    print('--- no follows: latest is the published render')
    fresh()
    plan, read_check, problem = run((FIRST, LAST, 1), None)
    check('without follows the re-comp resolves, reading no check render',
          problem is None and read_check is False, f'{problem} {read_check}')
    check('latest reads v0001 of every channel; the pinned import its v0001',
          reads_of(latest) == {'default': ('render', 'v0001')}
          and reads_of(everything) == {c: ('render', 'v0001') for c in CHANNELS}
          and reads_of(pinned) == {'chars': ('render', 'v0001')},
          f'{reads_of(latest)} {reads_of(everything)} {reads_of(pinned)}')

    print('--- following v0002 of both channels, on its frames')
    fresh()
    frames = (FIRST, LAST, STEP)
    plan, read_check, problem = run(frames, follows(*CHANNELS))
    check('the re-comp on the check render\'s own frames resolves, and reads a check render',
          problem is None and read_check is True, f'{problem} {read_check}')
    check('the import on latest reads the followed version',
          reads_of(latest) == {'default': ('render', 'v0002')}, str(reads_of(latest)))
    check('the all-channels import reads the followed version of every channel',
          reads_of(everything) == {c: ('render', 'v0002') for c in CHANNELS},
          str(reads_of(everything)))
    check('the import the artist pinned stays on its version',
          reads_of(pinned) == {'chars': ('render', 'v0001')}, str(reads_of(pinned)))
    frozen = None if plan is None else {
        key: (entry['department'], entry['version']) for key, entry in plan.items()}
    want = {
        latest.path(): ('render', 'v0002'),
        comp.plan_key(everything.path(), 'default', True): ('render', 'v0002'),
        comp.plan_key(everything.path(), 'chars', True): ('render', 'v0002'),
        pinned.path(): ('render', 'v0001'),
    }
    check('what it records reading is what each import read', frozen == want,
          f'{frozen} != {want}')

    print('--- the same again')
    plan_again, read_check, problem = run(frames, follows(*CHANNELS))
    check('following again changes nothing (idempotent)',
          problem is None and read_check is True and plan_again == plan,
          f'{problem} {plan_again}')

    print('--- following only default')
    fresh()
    plan, read_check, problem = run(frames, follows('default'))
    check('following one channel resolves and reads a check render',
          problem is None and read_check is True, f'{problem} {read_check}')
    check('the all-channels import reads the followed default and latest chars',
          reads_of(everything) == {'default': ('render', 'v0002'), 'chars': ('render', 'v0001')},
          str(reads_of(everything)))

    print('--- frames the check render does not have: held')
    held_count = len([f for f in range(FIRST, LAST + 1) if (f - FIRST) % STEP])
    for label, frames_held, held_text in (
        ('every frame (Step 1)', (FIRST, LAST, 1),
         f'every frame of {FIRST}-{LAST} from a render every 2nd frame: {held_count} frames held'),
        ('off its first frame', (FIRST + 1, LAST, STEP),
         f'every 2nd frame of {FIRST + 1}-{LAST} from a render every 2nd frame'),
    ):
        fresh()
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            plan, read_check, problem = run(frames_held, follows(*CHANNELS))
        check(f'a re-comp of {label} reads the check render, a check comp',
              problem is None and read_check is True
              and reads_of(latest) == {'default': ('render', 'v0002')},
              f'{problem} {read_check}')
        check(f'a re-comp of {label} logs the frames it holds',
              f'WARNING: comps {held_text}' in log.getvalue(), log.getvalue()[-600:])
        # What the farm comp renders: the worker's import holds a frame the
        # check render lacks from the nearest one it has, as in the session.
        dive = latest.native().node(import_render.DIVE)
        read = {int(f.parm('filename').evalAtFrame(FIRST + 1).rsplit('.', 2)[-2])
                for f in dive.children() if f.type().name() == 'file'}
        check(f'a re-comp of {label}: the import holds frame {FIRST + 1} from {FIRST}',
              read == {FIRST}, str(read))
    fresh()
    outside = (FIRST - 1, LAST, 1)
    plan, read_check, problem = run(outside, follows(*CHANNELS))
    dive = latest.native().node(import_render.DIVE)
    read = {int(f.parm('filename').evalAtFrame(FIRST - 1).rsplit('.', 2)[-2])
            for f in dive.children() if f.type().name() == 'file'}
    check('a re-comp reaching past the check render reads it, holding its first frame before it',
          problem is None and read_check is True and read == {FIRST},
          f'{problem} {read}')
    fresh()
    plan, read_check, problem = run(None, follows(*CHANNELS))
    check('a re-comp whose frames are not known is refused',
          plan is None and problem is not None and 'not known' in problem, str(problem))
    fresh()
    plan, read_check, problem = run((FIRST, LAST, 2 * STEP), follows(*CHANNELS))
    check('a Step that is a multiple of the render\'s goes',
          problem is None and read_check is True, str(problem))

    print()
    print(f'{len(vc._failures)} failure(s)' if vc._failures else 'all checks passed')
    shutil.rmtree(vc.TEMP_ROOT, ignore_errors=True)
    return 1 if vc._failures else 0


if __name__ == '__main__':
    hou.exit(main())
