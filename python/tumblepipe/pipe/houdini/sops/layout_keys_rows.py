"""th::layout_keys: an animation workfile copies layout's keys, per character.

With Layout in Animate on, layout publishes its keys beside its layer
(``pipe.houdini.layout_keys``: an Animation Catalog, one clip per
character). In the animation workfile th::layout_keys sits in th::animate's
dive between the rigs and the Scene Animate, one row per character:

- **On**: off leaves the character out of Copy All (its row stays);
- **Layout Version**: ``latest`` or a pinned ``v####`` of the shot's layout
  export (Discover pins new rows to the newest one);
- **Character**: the animation character the clip goes onto (the layout
  character's name by default; th::import_rig names the copies the same);
- **Controls**: which controls' channels come over (``*``: all; ``Main``:
  just the placement; space-separated globs, ``^`` excludes);
- **Frame Range** / **Offset**: only layout's keys in a range, moved by an
  offset (after a cut change);
- **Copy from Layout**: copy the row's clip into the Scene Animate.

A copy goes into the Scene Animate's own animation, the data the animator
keys into: from then on the keys are the animator's to edit, replace or
clear, and a new layout publish changes nothing until a row is copied
again. With Controls ``*`` a copy replaces the character's whole entry;
otherwise only those controls' channels, keeping the rest. **Copy All from
Layout** copies every row that is on; the animation template runs it once,
when it makes the workfile.

Copying, not layering: layout is not an animation layer the animator adds
to. Upstream keys (APEX Scene Add Animation) would keep changing under the
animator with each layout publish; a copy is theirs to edit or replace.

The node itself passes the APEX scene through; what each row copied (the
version) is kept in its user data.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import hou

from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns

DEPARTMENT = 'layout'
_ROW_PARMS = ('on', 'character', 'target', 'version', 'controls',
              'userange', 'first', 'last', 'offset')
_COPIED_KEY = 'tumblepipe_layout_keys_copied'
_CLIP = '/animation/default.clip'


# -- where layout's keys are ---------------------------------------------------------

def layout_version_paths(shot_uri: Uri, channel: str) -> list[Path]:
    """The shot's layout export versions, oldest first."""
    from tumblepipe.api import api
    from tumblepipe.pipe.paths import list_version_paths

    export_uri = Uri.parse('export:/') / shot_uri.segments / channel / DEPARTMENT
    return list_version_paths(api.storage.resolve(export_uri))


def resolve_version(shot_uri: Uri, channel: str, version: str) -> Path | None:
    """The layout export folder ``version`` names ('latest': the newest
    one that wrote layout keys); None when there is none."""
    paths = layout_version_paths(shot_uri, channel)
    if version != 'latest':
        return next((p for p in paths if p.name == version), None)
    for path in reversed(paths):
        if layout_keys_entry(path) is not None:
            return path
    return None


def layout_keys_entry(version_path: Path) -> dict | None:
    """The ``layout_keys`` entry of a layout export's context.json; None
    for a version without layout keys."""
    context_path = Path(version_path) / 'context.json'
    if not context_path.exists():
        return None
    try:
        context = json.loads(context_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    for output in context.get('outputs', [context]):
        parameters = output.get('parameters', {}) if isinstance(output, dict) else {}
        if 'layout_keys' in parameters:
            return parameters['layout_keys']
    return None


def catalog_path(version_path: Path) -> Path | None:
    entry = layout_keys_entry(version_path)
    return None if entry is None else Path(version_path) / entry['file']


# -- one row's keys ------------------------------------------------------------------

def control_of(channel: str) -> str:
    """The control a rig parm channel belongs to (``Main_tx`` -> ``Main``,
    ``lamp_ry`` -> ``lamp``): its name up to the last ``_``."""
    return channel.rsplit('_', 1)[0] if '_' in channel else channel


def matches(control: str, pattern: str) -> bool:
    """Whether ``control`` is in the row's Controls pattern (space separated
    globs, ``^`` excludes)."""
    keep = False
    for token in (pattern or '*').split():
        if token.startswith('^'):
            if fnmatch.fnmatchcase(control, token[1:]):
                keep = False
        elif fnmatch.fnmatchcase(control, token):
            keep = True
    return keep


def row_entry(catalog: Path, character: str, controls: str = '*',
              frame_range: tuple | None = None, offset: float = 0.0):
    """``character``'s clip from the layout keys ``catalog`` as an entry
    geometry: the channels of ``controls``, keys in ``frame_range``
    (inclusive; None: all), moved by ``offset``."""
    from tumblepipe.pipe.houdini import layout_keys

    clips = layout_keys.read_catalog(catalog)
    if character not in clips:
        raise ns.NodeError(f"layout's keys have no '{character}' ({Path(catalog).name})")
    entry = clips[character]
    chans = layout_keys.channels(entry)
    entry.deletePrims([p for n, p in chans.items() if not matches(control_of(n), controls)])
    if frame_range is not None:
        first, last = frame_range
        for prim in layout_keys.channels(entry).values():
            frames = prim.keyFrames()
            for index in reversed([i for i, f in enumerate(frames) if f < first or f > last]):
                prim.destroyKey(index)
    layout_keys.shift(entry, offset)
    return entry


def copy_entry(scene_animate, entry, character: str, controls: str = '*') -> int:
    """Copy ``entry`` (channel primitives) onto ``character`` in the Scene
    Animate's own animation: its whole entry with Controls ``*``, else only
    the channels of ``controls`` (the rest of the character's keys stay).
    Every other character's keys stay. Returns how many channels it wrote."""
    from tumblepipe.pipe.houdini import layout_keys

    stored = scene_animate.parm('animation').eval()
    stored = hou.Geometry(stored) if stored is not None else hou.Geometry()
    clips = stored.extractPackedPaths('/animation/*.clip')
    clip = clips[0] if clips else _CLIP
    path = f'{clip}/_{character}.char_Base'
    entries = stored.extractPackedPaths(clip + '/*') if clips else []
    if (controls or '*').strip() == '*' or path not in entries:
        merged = hou.Geometry(entry)
    else:
        merged = hou.Geometry(stored.unpackFromFolder(path))
        old = layout_keys.channels(merged)
        merged.deletePrims([p for n, p in old.items() if matches(control_of(n), controls)])
        merged.merge(entry)
    if merged.findGlobalAttrib('rig_path') is None:
        merged.addAttrib(hou.attribType.Global, 'rig_path', '')
    merged.setGlobalAttribValue('rig_path', f'/{character}.char/Base.rig')
    out = hou.Geometry()
    for other in [e for c in clips for e in stored.extractPackedPaths(c + '/*')]:
        if other != path:
            out.packToFolder(other, stored.unpackFromFolder(other))
    out.packToFolder(path, merged)
    scene_animate.parm('animation').set(out)
    return len(layout_keys.channels(entry))


# -- the node -------------------------------------------------------------------------

def scene_animate_after(node):
    """The Scene Animate fed (directly or not) by ``node``; None."""
    todo, seen = list(node.outputs()), set()
    while todo and len(seen) < 200:
        current = todo.pop(0)
        if current.path() in seen:
            continue
        seen.add(current.path())
        if current.type().name().startswith('apex::sceneanimate'):
            return current
        todo += list(current.outputs())
    return None


class LayoutKeys(ns.Node):
    """th::layout_keys: its rows, Discover and the copies."""

    def __init__(self, native):
        super().__init__(native)

    # rows
    def get_rows(self) -> list[dict]:
        rows = []
        for i in range(1, self.parm('rows').eval() + 1):
            rows.append({
                'on': bool(self.parm(f'on{i}').eval()),
                'character': self.parm(f'character{i}').evalAsString(),
                'target': self.parm(f'target{i}').evalAsString(),
                'version': self.parm(f'version{i}').evalAsString() or 'latest',
                'controls': self.parm(f'controls{i}').evalAsString() or '*',
                'userange': bool(self.parm(f'userange{i}').eval()),
                'first': self.parm(f'first{i}').eval(),
                'last': self.parm(f'last{i}').eval(),
                'offset': self.parm(f'offset{i}').eval(),
            })
        return rows

    def set_rows(self, rows: list[dict]) -> None:
        self.parm('rows').set(len(rows))
        for i, row in enumerate(rows, start=1):
            for key in _ROW_PARMS:
                if key in row:
                    self.parm(f'{key}{i}').set(row[key])

    def copied(self) -> dict:
        """``{character: layout version}`` of the copies made."""
        try:
            return json.loads(self.native().userData(_COPIED_KEY) or '{}')
        except ValueError:
            return {}

    # where
    def shot_uri(self) -> Uri | None:
        from tumblepipe.pipe.houdini import shot_start
        animate = shot_start.animate_node(self.native())
        shot = shot_start._import_shot_above(animate) if animate is not None else None
        return shot.get_entity_uri() if shot is not None else None

    def channel(self) -> str:
        return self.parm('channel').evalAsString() or 'default'

    def list_version_names(self, index: int = 0) -> list[str]:
        shot = self.shot_uri()
        if shot is None:
            return ['latest']
        return ['latest'] + [p.name for p in layout_version_paths(shot, self.channel())]

    def resolved(self, version: str):
        shot = self.shot_uri()
        if shot is None:
            return None
        return resolve_version(shot, self.channel(), version)

    def camera_rig_settings(self, version: str = 'latest') -> dict | None:
        path = self.resolved(version)
        entry = layout_keys_entry(path) if path is not None else None
        return None if entry is None else entry.get('camera_rig')

    def scene_characters(self) -> set[str] | None:
        """The characters of the APEX scene coming in (``<name>.char``);
        None when nothing is connected."""
        inputs = self.native().inputs()
        if not inputs or inputs[0] is None:
            return None
        geo = inputs[0].geometry()
        name = geo.findPrimAttrib('name') if geo is not None else None
        if name is None:
            return set()
        return {
            prim.attribValue(name)[:-len('.char')] for prim in geo.prims()
            if prim.attribValue(name).endswith('.char')
        }

    # actions
    def discover(self) -> None:
        """A row per character the newest layout keys have, keeping the
        rows already there (their choices) and adding new characters pinned
        to that version; a character not in the scene (a prop layout
        controlled) comes in off."""
        path = self.resolved('latest')
        entry = layout_keys_entry(path) if path is not None else None
        current = {row['character']: row for row in self.get_rows()}
        rows = list(current.values())
        present = self.scene_characters()
        # the frame range starts as the shot's, ready when it is ticked on
        first, last = (int(f) for f in hou.playbar.playbackRange())
        for character in sorted((entry or {}).get('characters', {})):
            if character in current:
                continue
            on = present is None or character in present
            rows.append({'on': on, 'character': character, 'target': character,
                         'version': path.name, 'controls': '*', 'userange': False,
                         'first': first, 'last': last, 'offset': 0})
        self.set_rows(rows)

    def copy_row(self, index: int, scene_animate=None) -> int:
        """Copy row ``index``'s layout keys into the Scene Animate after
        this node (see the module docstring). Returns how many channels."""
        row = self.get_rows()[index - 1]
        target = row['target'] or row['character']
        animate = scene_animate or scene_animate_after(self.native())
        if animate is None:
            raise ns.NodeError('no Scene Animate after th::layout_keys')
        version_path = self.resolved(row['version'])
        catalog = catalog_path(version_path) if version_path is not None else None
        if catalog is None:
            raise ns.NodeError(f"row {index}: no layout keys for version {row['version']}")
        entry = row_entry(catalog, row['character'], row['controls'],
                          (row['first'], row['last']) if row['userange'] else None,
                          float(row['offset']))
        count = copy_entry(animate, entry, target, row['controls'])
        copied = self.copied()
        copied[target] = version_path.name
        self.native().setUserData(_COPIED_KEY, json.dumps(copied))
        return count

    def copy_all(self, scene_animate=None) -> list[str]:
        """Copy every row that is on and whose character is in the scene;
        returns the characters copied. One undo step."""
        present = self.scene_characters()
        done = []
        with hou.undos.group('Copy all from layout'):
            for index, row in enumerate(self.get_rows(), start=1):
                target = row['target'] or row['character']
                if not row['on'] or not row['character']:
                    continue
                if present is not None and target not in present:
                    continue
                self.copy_row(index, scene_animate)
                done.append(target)
        ns.set_node_comment(self.native(), 'copied from layout: ' + ', '.join(
            f'{c} {self.copied().get(c, "")}' for c in done) if done else '')
        return done



def keyed_placement(node) -> set[str]:
    """The characters an enabled th::layout_keys row gives their placement
    (``Main``) keys to, in the th::animate dive ``node`` sits in: th::import_rig
    leaves those where layout's keys put them instead of placing them at the
    layout's first frame."""
    from tumblepipe.pipe.houdini import shot_start

    animate = shot_start.animate_node(node)
    if animate is None:
        return set()
    keyed = set()
    for child in animate.allSubChildren():
        if not child.type().name().startswith('th::layout_keys'):
            continue
        for row in LayoutKeys(child).get_rows():
            if row['on'] and matches('Main', row['controls']):
                keyed.add(row['target'] or row['character'])
    return keyed


def add_to_dive(dive, previous, shot_uri=None):
    """Layout in Animate in an animation workfile's dive, after ``previous``
    (th::import_rigs): the camera rig a package registered, rebuilt with
    the settings layout published (its character named as layout's,
    ``camera``), then th::layout_keys with its rows discovered. Returns
    th::layout_keys's node, to feed the Scene Animate; call
    :func:`copy_into_new_workfile` once the Scene Animate is wired."""
    from tumblepipe.pipe.houdini import shot_rigs

    node = create(dive, 'layout_keys')
    node.native().setInput(0, previous)
    rig = shot_rigs.camera_rig()
    published = node.camera_rig_settings()
    if rig is not None and rig.animation is not None and published is not None:
        camera = rig.animation(previous, shot_uri, published.get('settings'))
        if camera is not None:
            node.native().setInput(0, camera)
    node.discover()
    return node.native()


def copy_into_new_workfile(layout_keys_native) -> list[str]:
    """A new animation workfile starts from layout's keys: Copy All. A
    shot with no layout keys yet (layout not published, or published
    before Layout in Animate) starts from scratch instead of failing."""
    try:
        return LayoutKeys(layout_keys_native).copy_all()
    except ns.NodeError:
        return []


def create(scene, name):
    return ns.create_node(scene, name, LayoutKeys, 'layout_keys', 'Sop')


def discover_rows(kwargs):
    LayoutKeys(kwargs['node']).discover()


def copy_rows(kwargs):
    with ns.node_errors_as_message('Copy All from Layout'):
        LayoutKeys(kwargs['node']).copy_all()


def copy_one(kwargs):
    index = int(kwargs['parm'].name()[len('replace'):])
    with ns.node_errors_as_message('Copy from Layout'):
        with hou.undos.group('Copy from layout'):
            LayoutKeys(kwargs['node']).copy_row(index)
