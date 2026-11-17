#!/usr/bin/env hython
"""Verify what reopening a scene does to the Scene Invoke in th::animate's dive.

th::animate's OnLoaded switches a dive's Scene Invoke from Unpacked
Geometry to Packed Folders when it feeds the dive's output straight
(``animate_points.optimize_dive``): th::animate unpacks the folders itself,
faster. Up to 1.111 it did that on every load, whatever else read the
Scene Invoke: a deformer branched off it, a shot sculpt network merging it
got packed folders, and an artist who set it back to Unpacked (as the node's
own comment says to) found it on Packed again the next time the scene
opened (Remy, tumblepipe__bugs 2026-11-17).

Each th::animate here gets a Scene Invoke on Unpacked Geometry wired
straight into ``output0``; the scene is saved and reopened, and:

1. ``plain`` (nothing else reads it): switched to Packed Folders, with the
   comment and the user data marking the switch;
2. ``plain`` set back to Unpacked, saved and reopened twice: stays Unpacked;
3. ``branch`` (a Bend SOP wired after the Scene Invoke beside output0):
   stays Unpacked, no comment;
4. ``sculpt`` (an Object Merge in /obj reading the Scene Invoke): stays
   Unpacked;
5. ``merged_dive`` (an Object Merge in /obj reading the dive): stays
   Unpacked;
6. ``legacy`` (switched by 1.111, the comment only, set back to Unpacked):
   stays Unpacked.

``tests/test_animate_points.py`` pins the rule without Houdini; this runs
the HDA's real OnLoaded on a real reload. Needs a Houdini license and
Houdini's own APEX nodes (run with an empty package dir, not
HOUDINI_PACKAGE_SKIP=1):

    HOUDINI_PACKAGE_DIR=<empty dir> HOUDINI_PATH="<repo>;&" \\
        PYTHONPATH=<repo>/python TH_PIPELINE_PATH=<repo> \\
        hython scripts/verify_animate_invoke_reload.py

Prints one line per check and exits 1 on any failure.
"""

import sys
import tempfile
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

KEY = 'th_animate_packed'
NOTE = 'Packed Folders by th::animate'
LEGACY = ('Output set to Packed Folders by th::animate (2026-11-10) for faster '
          'playback; the animation output is unchanged.')
FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}")
    if not ok:
        FAILED.append(name)


def install():
    """Houdini's own packages (apex: the Scene SOPs) and the expanded HDA
    sources, which must win over any compiled .hda."""
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def build(label):
    """A th::animate (its dive as OnCreated builds it) with a Scene Invoke
    on Unpacked Geometry between Scene Animate and output0."""
    animate = hou.node('/stage').createNode('th::animate::2.0', label)
    dive = animate.node('anim_dive')
    invoke = dive.createNode('apex::sceneinvoke::2.0', 'sceneinvoke1')
    invoke.setInput(0, dive.node('scene_animate'))
    dive.node('output0').setInput(0, invoke)
    invoke.parm('outputmode').set('unpacked')
    return invoke


def reader(label, path):
    merge = hou.node('/obj').createNode('geo', f'shot_sculpt_{label}').createNode('object_merge')
    merge.parm('objpath1').set(path)


def invoke_of(label):
    return hou.node(f'/stage/{label}/anim_dive/sceneinvoke1')


def mode(label):
    return invoke_of(label).parm('outputmode').evalAsString()


def reopen(hip):
    hou.hipFile.save(hip)
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.hipFile.load(hip, suppress_save_prompt=True, ignore_load_warnings=True)


def main():
    install()
    build('plain')
    branch = build('branch')
    branch.parent().createNode('bend', 'deform').setInput(0, branch)
    reader('sculpt', build('sculpt').path())
    reader('merged_dive', build('merged_dive').parent().path())
    build('legacy').setComment(LEGACY)
    with tempfile.TemporaryDirectory() as folder:
        hip = str(Path(folder) / 'invoke_reload.hip').replace('\\', '/')
        reopen(hip)
        plain = invoke_of('plain')
        check('plain: an Unpacked Scene Invoke only th::animate reads is switched on load',
              mode('plain') == 'packedfolders', mode('plain'))
        check('plain: the switch leaves its comment and user data',
              NOTE in plain.comment() and bool(plain.userData(KEY)),
              repr(plain.userData(KEY)))
        for label, what in (('branch', 'a deformer wired after it'),
                            ('sculpt', 'an Object Merge reading it'),
                            ('merged_dive', 'an Object Merge reading the dive'),
                            ('legacy', 'switched once already (1.111 comment)')):
            node = invoke_of(label)
            check(f'{label}: {what} keeps it on Unpacked',
                  mode(label) == 'unpacked', mode(label))
            if label != 'legacy':
                check(f'{label}: and untouched (no comment, no user data)',
                      not node.comment() and not node.userData(KEY))
        plain.parm('outputmode').set('unpacked')  # the artist sets it back
        reopen(hip)
        reopen(hip)
        check('plain: set back to Unpacked, it stays Unpacked over two reopens',
              mode('plain') == 'unpacked', mode('plain'))
        hou.hipFile.clear(suppress_save_prompt=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    sys.exit(1 if FAILED else 0)
