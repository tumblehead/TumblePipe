#!/usr/bin/env hython
"""Verify th::animate's warning for a character keyed in two Scene Animates
of one chain (``lops.animate_keys``), with real APEX Scene Animates.

A shot that pans across rooms is animated in one th::animate dive with a
Scene Animate per room in series, each keying its own characters (issue
#98). A Scene Animate that holds keys of its own for a character replaces
the entry it is given whole, so keys upstream for that character do
nothing; th::animate names the character, both nodes and the fix on
``points_sops/placed`` and the status bar. ``tests/test_animate_keys.py``
pins the rule over plain data; this cooks the real nodes.

TumbleRig's example figure stands in for two published rigs, ``hero`` and
``villain`` (each one's render shape given its own pipeline path,
``/CHAR/<name>/geo/body``), added to the scene with Scene Add Character in
place of Import Rigs. Keys are ``Main_t`` channels, as Animate keys a
control. The cases:

1. ``same``: two Scene Animates in series both keying hero: a warning
   naming hero and both; editing the upstream keys changes nothing on the
   stage (they do nothing), and keying villain instead clears it;
   ``given``: the one below holds hero's keys exactly as it is given (as
   Animate saves an entry, the keys from above in it), so its output and
   input agree and only its own ``animation`` tells: it warns, and an edit
   above then does nothing on the stage;
2. ``different``: hero keyed in the first, villain in the second: no
   warning, and both move on the stage (the series recipe combines them);
3. ``pass``: a Scene Animate with no keys below a keyed one: no warning;
4. ``layer``: Copy Animation puts room A's hero keys on a New Layer of
   room B's stream (with its rest key under it in the base), a Scene
   Animate below keys hero: no warning, and the layer survives it;
5. ``base``: the same copy on Base Animation: a warning naming the copy;
6. ``merge``: Copy Animation on Base Animation merging into hero's keys
   from a Scene Animate above, nothing keying hero below: no warning;
7. ``output1``: the double key in a chain feeding an Output SOP with index
   1 only (a Segments stream) is named too;
8. ``frames``: with the dive's own Scene Invoke recooking every frame,
   stepping frames does not work the warning out again; a key edit does,
   once.

Without TumbleRig the checks are skipped. Needs a Houdini license; run as
preflight's hython stage runs it (``.ci/preflight.py --only hython``), or:

    HOUDINI_PACKAGE_DIR=<dir with only TumbleRig> HOUDINI_PATH="<repo>;&" \
        PYTHONPATH=<repo>/python TH_PIPELINE_PATH=<repo> \
        hython scripts/verify_animate_double_keys.py

Prints one line per check and exits 1 on any failure.
"""

import sys
import traceback
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

SHAPE = 'Primitives'
CLIP = '/animation/default.clip'
FAILED = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}")
    if not ok:
        FAILED.append(name)


def install():
    """Houdini's own packages (apex) and the expanded HDA sources, which
    must win over any compiled .hda."""
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def newest_type(prefix):
    names = sorted(n for n in hou.sopNodeTypeCategory().nodeTypes() if n.startswith(prefix))
    return names[-1] if names else None


def body(character):
    return f'/CHAR/{character}/geo/body'


def path_shapes(character):
    return f'''
geo = hou.pwd().geometry()
for prim in geo.prims():
    if prim.attribValue('name') != '{SHAPE}.shp':
        continue
    inner = prim.getEmbeddedGeometry().freeze()
    if inner.findPrimAttrib('path') is None:
        inner.addAttrib(hou.attribType.Prim, 'path', '')
    inner.setPrimStringAttribValues('path', ['{body(character)}'] * len(inner.prims()))
    prim.setEmbeddedGeometry(inner)
'''


def keys(entries):
    """A Scene Animate's `animation`: per character, channel -> (first,
    last) keyed at frames 1 and 24, as Animate keys a control."""
    geo = hou.Geometry()
    for character, channels in entries.items():
        entry = hou.Geometry()
        name = entry.addAttrib(hou.attribType.Prim, 'name', '')
        entry.addAttrib(hou.attribType.Global, 'rig_path', '')
        entry.setGlobalAttribValue('rig_path', f'/{character}.char/Base.rig')
        for channel_name, (first, last) in channels.items():
            channel = entry.createChannelPrim()
            channel.setAttribValue(name, channel_name)
            channel.insertKey(1)
            channel.insertKey(24)
            channel.setKeyValues([first, last])
        geo.packToFolder(f'{CLIP}/_{character}.char_Base', entry)
    return geo


HERO_X = {'hero': {'Main_tx': (0.0, 2.0)}}
HERO_Z = {'hero': {'Main_tz': (0.0, 3.0)}}
VILLAIN_X = {'villain': {'Main_tx': (0.0, 4.0)}}


class Shot:
    """A th::animate whose dive adds hero and villain to the scene (the
    rigs in place of Import Rigs) and ends in `output0`."""

    def __init__(self, stage, rigs, label):
        self.animate = stage.createNode('th::animate::2.0', f'animate_{label}')
        self.animate.parm('shapes').set(SHAPE)
        self.dive = self.animate.node('anim_dive')
        for name in ('import_rigs', 'scene_animate'):
            node = self.dive.node(name)
            if node is not None:
                node.destroy()
        self.output = self.dive.node('output0')
        scene = None
        for character, rig in rigs.items():
            merge = self.dive.createNode('object_merge', f'rig_{character}')
            merge.parm('objpath1').set(rig.path())
            add = self.dive.createNode('apex::sceneaddcharacter', f'add_{character}')
            if scene is not None:
                add.setInput(0, scene)
            add.setInput(1, merge)
            add.parm('charactername').set(character)
            scene = add
        self.scene = scene

    def scene_animate(self, name, entries=None, src=None):
        node = self.dive.createNode('apex::sceneanimate', name)
        node.setInput(0, src if src is not None else self.scene)
        if entries:
            node.parm('animation').set(keys(entries))
        return node

    def copy(self, name, target, source, layer):
        node = self.dive.createNode(newest_type('th::apex_copy_animation'), name)
        node.setInput(0, target)
        node.setInput(1, source)
        node.parm('from_char').set('hero')
        node.parm('to_char').set('hero')
        node.parm('write_to').set(1 if layer else 0)
        return node

    def end(self, node):
        self.output.setInput(0, node)

    def warning(self):
        """The double-key lines of th::animate's warning on placed, after
        a cook; and what `animate_keys` says outright."""
        from tumblepipe.pipe.houdini.lops import animate_keys
        hou.setFrame(1)
        self.animate.cook(force=True)
        placed = self.animate.node('points_sops/placed')
        on_node = '\n'.join(line for warning in placed.warnings()
                            for line in warning.splitlines() if 'holds its own keys' in line)
        return on_node, animate_keys.double_keys_warning(self.animate)

    def centre_x(self, character, frame):
        from pxr import UsdGeom
        hou.setFrame(frame)
        prim = self.animate.stage().GetPrimAtPath(body(character))
        if not prim.IsValid():
            return None
        points = UsdGeom.PointBased(prim).GetPointsAttr().Get(frame)
        if not points:
            return None
        xs = [p[0] for p in points]
        return (min(xs) + max(xs)) / 2

    def moved(self, character):
        first, last = self.centre_x(character, 1), self.centre_x(character, 24)
        hou.setFrame(1)
        return None if first is None or last is None else round(last - first, 3)


def names_all(text, *words):
    return bool(text) and all(word in text for word in words)


def case_same(shot, announced):
    sa1 = shot.scene_animate('room_bedroom', HERO_X)
    sa2 = shot.scene_animate('room_kitchen', HERO_Z, src=sa1)
    shot.end(sa2)
    on_node, said = shot.warning()
    check('same: the warning on placed names hero, the Scene Animate keying it '
          'again and the one whose keys are lost',
          names_all(on_node, 'hero', 'room_kitchen', 'room_bedroom'), on_node[:200])
    check('same: it says the fix (one Scene Animate, Copy Animation, New Layer)',
          names_all(on_node, 'one Scene Animate', 'Copy Animation', 'New Layer'))
    check('same: animate_keys says the same', said == on_node, said[:120])
    check('same: on the status bar', any('room_kitchen' in m and 'hero' in m for m in announced),
          announced[-1:])
    check('same: th::animate still cooks', not shot.animate.errors(), shot.animate.errors()[:1])
    before = shot.moved('hero')
    sa1.parm('animation').set(keys({'hero': {'Main_tx': (0.0, 9.0)}}))
    after = shot.moved('hero')
    check('same: editing the keys above changes nothing on the stage (they do nothing)',
          before is not None and before == after, f'{before} -> {after}')
    sa2.parm('animation').set(keys(VILLAIN_X))
    on_node, said = shot.warning()
    check('same: keying villain there instead clears the warning', not on_node and not said,
          on_node[:120])


def case_different(shot, announced):
    sa1 = shot.scene_animate('room_bedroom', HERO_X)
    sa2 = shot.scene_animate('room_kitchen', VILLAIN_X, src=sa1)
    shot.end(sa2)
    on_node, said = shot.warning()
    check('different: no warning', not on_node and not said, (on_node or said)[:120])
    hero, villain = shot.moved('hero'), shot.moved('villain')
    check('different: both characters move with their own keys',
          hero == 2.0 and villain == 4.0, f'hero {hero}, villain {villain}')


def case_pass(shot, announced):
    sa1 = shot.scene_animate('room_bedroom', HERO_X)
    sa2 = shot.scene_animate('room_kitchen', src=sa1)
    shot.end(sa2)
    on_node, said = shot.warning()
    check('pass: a Scene Animate with no keys below a keyed one, no warning',
          not on_node and not said, (on_node or said)[:120])


def _base_entry_keys(node, character):
    try:
        entry = node.geometry().unpackFromFolder(f'{CLIP}/_{character}.char_Base')
    except (ValueError, hou.Error):
        return 0
    return sum(len(prim.keyFrames()) for prim in entry.prims())


def case_layer(shot, announced):
    room_a = shot.scene_animate('room_a', HERO_X)
    room_b = shot.scene_animate('room_b', VILLAIN_X)
    copy = shot.copy('copy_hero', room_b, room_a, layer=True)
    below = shot.scene_animate('room_c', HERO_Z, src=copy)
    shot.end(below)
    check('layer: the copy puts rest keys under its layer in hero\'s base (what a Scene '
          'Animate below replaces)', _base_entry_keys(copy, 'hero') > 0,
          _base_entry_keys(copy, 'hero'))
    on_node, said = shot.warning()
    check('layer: Copy Animation on a New Layer, then a Scene Animate keying hero: no warning',
          not on_node and not said, (on_node or said)[:160])
    paths = below.geometry().extractPackedPaths(f'{CLIP}/*/*')
    check('layer: the layer survives the Scene Animate below',
          f'{CLIP}/copied/_hero.char_Base' in paths, paths)


def case_base(shot, announced):
    room_a = shot.scene_animate('room_a', HERO_X)
    room_b = shot.scene_animate('room_b', VILLAIN_X)
    copy = shot.copy('copy_hero', room_b, room_a, layer=False)
    below = shot.scene_animate('room_c', HERO_Z, src=copy)
    shot.end(below)
    on_node, said = shot.warning()
    check('base: Copy Animation on Base Animation, then a Scene Animate keying hero: '
          'the warning names the copy',
          names_all(on_node, 'hero', 'room_c', 'copy_hero') and 'room_a' not in on_node,
          on_node[:200])


def case_merge(shot, announced):
    room_a = shot.scene_animate('room_a', HERO_X)
    room_b = shot.scene_animate('room_b', HERO_Z)
    copy = shot.copy('copy_hero', room_b, room_a, layer=False)
    below = shot.scene_animate('room_c', VILLAIN_X, src=copy)
    shot.end(below)
    check('merge: the copy merges room A\'s hero keys into room B\'s',
          _base_entry_keys(copy, 'hero') > 0)
    on_node, said = shot.warning()
    check('merge: Copy Animation merging into hero\'s keys, nothing keying hero below: '
          'no warning', not on_node and not said, (on_node or said)[:160])


def case_output1(shot, announced):
    sa = shot.scene_animate('room_bedroom', HERO_X)
    shot.end(sa)
    x = shot.scene_animate('segment_a', HERO_X)
    y = shot.scene_animate('segment_b', HERO_Z, src=x)
    second = shot.dive.createNode('output', 'output1')
    second.parm('outputidx').set(1)
    second.setInput(0, y)
    on_node, said = shot.warning()
    check('output1: a double key feeding an Output SOP with index 1 is named',
          names_all(on_node, 'hero', 'segment_b', 'segment_a') and 'room_bedroom' not in on_node,
          on_node[:200])


def case_given(shot, announced):
    sa1 = shot.scene_animate('room_bedroom', HERO_X)
    sa2 = shot.scene_animate('room_kitchen', HERO_X, src=sa1)
    shot.end(sa2)
    check('given: the Scene Animate below outputs hero as it is given (its output and input '
          'agree, so only its own animation tells)',
          _base_entry_keys(sa2, 'hero') == _base_entry_keys(sa1, 'hero') > 0)
    on_node, said = shot.warning()
    check('given: holding hero\'s keys exactly as given still warns, naming both',
          names_all(on_node, 'hero', 'room_kitchen', 'room_bedroom'), on_node[:160])
    sa1.parm('animation').set(keys({'hero': {'Main_tx': (0.0, 9.0)}}))
    moved = shot.moved('hero')
    check('given: then an edit above does nothing (hero still moves by 2, not 9)',
          moved == 2.0, f'moved {moved}')


def case_frames(shot, announced):
    from tumblepipe.pipe.houdini.lops import animate_keys
    sa1 = shot.scene_animate('room_bedroom', HERO_X)
    sa2 = shot.scene_animate('room_kitchen', HERO_Z, src=sa1)
    # The dive's own Scene Invoke (David's dive has one) recooks every frame.
    invoke = shot.dive.createNode('apex::sceneinvoke::2.0', 'sceneinvoke1')
    invoke.setInput(0, sa2)
    shot.end(invoke)
    shot.warning()
    calls = []
    original = animate_keys.find_double_keys

    def counted(dive):
        calls.append(dive.path())
        return original(dive)

    animate_keys.find_double_keys = counted
    try:
        cooks = invoke.cookCount()
        for frame in range(1, 13):
            hou.setFrame(frame)
            shot.animate.stage()
        stepped = len(calls)
        recooked = invoke.cookCount() - cooks
        sa2.parm('animation').set(keys({'hero': {'Main_tz': (0.0, 5.0)}}))
        for frame in range(1, 4):
            hou.setFrame(frame)
            shot.animate.stage()
        edited = len(calls) - stepped
    finally:
        animate_keys.find_double_keys = original
    check('frames: the dive\'s Scene Invoke recooks as the frames step (else this proves '
          'nothing)', recooked >= 10, f'{recooked} cooks')
    check('frames: stepping 12 frames does not work the warning out again', stepped == 0,
          f'{stepped} times')
    check('frames: a key edit works it out again, once', edited == 1, f'{edited} times')


CASES = {
    'same': case_same, 'given': case_given, 'different': case_different, 'pass': case_pass,
    'layer': case_layer, 'base': case_base, 'merge': case_merge,
    'output1': case_output1, 'frames': case_frames,
}


def main():
    install()
    figure = newest_type('th::apex_rigExample_figure')
    if figure is None or newest_type('th::apex_copy_animation') is None:
        print('SKIP  TumbleRig is not installed: no rig or Copy Animation to build with')
        return
    from tumblepipe.pipe.houdini.lops import animate_points
    announced = []
    animate_points.announce = announced.append
    hou.playbar.setFrameRange(1, 24)
    hou.playbar.setPlaybackRange(1, 24)
    hou.setFrame(1)
    obj = hou.node('/obj').createNode('geo', 'rigs')
    rigs = {}
    for character in ('hero', 'villain'):
        pathed = obj.createNode('python', f'pathed_{character}')
        pathed.setInput(0, obj.createNode(figure, f'figure_{character}'))
        pathed.parm('python').set(path_shapes(character))
        rigs[character] = pathed
    stage = hou.node('/stage')
    for label, case in CASES.items():
        try:
            case(Shot(stage, rigs, label), announced)
        except Exception:
            traceback.print_exc()
            check(f'{label}: ran', False)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        FAILED.append('exception')
    sys.exit(1 if FAILED else 0)
