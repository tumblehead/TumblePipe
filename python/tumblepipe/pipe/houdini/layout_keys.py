"""Layout keys: what a shot's layout keyed, handed to animation as a copy.

With Layout in Animate on (``config.layout_animate``) layout animates the
shot on the USD stage in an APEX Animate LOP. Its publish carries two things
in the same version folder: the layer every department reads (baked:
placement, the camera), and the **layout keys**, the keys themselves, one
clip per character and the camera. Animation copies a character's clip into
its th::animate when it wants it, edits it or starts over; it never layers
on top of layout.

The keys file is an Animation Catalog (Houdini's own, the file the
Animation Catalog pane opens): one ``animation_clip`` item per character,
filed in a folder per asset category, each clip holding that character's
Scene Animate entry (``/animation/<clip>.clip/_<char>.char_Base``, channel
primitives named after the rig parms). Unlike a clip captured in the pane,
the keys stay on the shot's own frames, so a clip goes back onto its
character as it is.

Reading the stage: the APEX Animate LOP's scene, read back with the USD APEX
Scene Import verb, holds every layer it sees as one clip: the artist's keys
and the channels Configure APEX Rig baked from USD keys alike.

Writing the catalog needs no UI: ``hou.AssetGalleryDataSource`` is what the
pane saves through. ``addItem`` refuses an item without thumbnail bytes, so
a clip without a capture gets :data:`THUMBNAIL`.
"""

from __future__ import annotations

import base64
from pathlib import Path

#: A catalog clip needs thumbnail bytes; a 1x1 PNG until a capture.
THUMBNAIL = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
)

#: The in-memory catalog the writer fills before it saves (the pane's own).
_MEMORY_CATALOG = ':apexcatalog_memory:'

#: TumbleRig's top world control; its rig parms carry a character's place.
PLACEMENT_CONTROL = 'Main'

ANIMATE_LOP_TYPE = 'apexanimate'

#: The keys file beside a layout layer: ``<layer stem>`` + this.
CATALOG_SUFFIX = '.layout_keys.bgeo.sc'


def find_animate_lop(node):
    """The APEX Animate LOP feeding ``node`` (the nearest up its inputs);
    None when there is none, as in a layout without Layout in Animate."""
    todo, seen = list(node.inputs()), set()
    while todo and len(seen) < 500:
        current = todo.pop(0)
        if current is None or current.path() in seen:
            continue
        seen.add(current.path())
        if current.type().name() == ANIMATE_LOP_TYPE:
            return current
        todo += [i for i in current.inputs() if i is not None]
    return None


def read_scene(animate_lop, scene_prim: str | None = None):
    """The APEX scene ``animate_lop`` animates, as a packed folder geometry
    (``/<char>.char``, ``/animation/<clip>.clip``), via the USD APEX Scene
    Import verb; None when the LOP has no scene."""
    import hou

    if scene_prim is None:
        scene_prim = animate_lop.parm('sceneprim').evalAsString() or '/scene'
    verb = hou.sopNodeTypeCategory().nodeVerb('usdapexsceneimport')
    verb.setParms({'loppath': animate_lop.path(), 'sceneprimpath': scene_prim})
    geo = hou.Geometry()
    verb.execute(geo, [])
    if not geo.prims():
        return None
    return geo


def character_entries(scene) -> dict:
    """``{character: (clip path, entry geometry)}`` of every Scene Animate
    entry in ``scene`` (``/animation/<clip>.clip/_<char>.char_Base``). A
    character keyed in two clips keeps the first clip's entry."""
    import hou

    entries = {}
    for clip in sorted(scene.extractPackedPaths('/animation/*.clip')):
        for entry in sorted(scene.extractPackedPaths(clip + '/*')):
            name = entry.rsplit('/', 1)[-1]
            if not (name.startswith('_') and name.endswith('.char_Base')):
                continue
            character = name[1:-len('.char_Base')]
            if character in entries:
                continue
            entries[character] = (clip, hou.Geometry(scene.unpackFromFolder(entry)))
    return entries


def channels(entry) -> dict:
    """``{channel name: hou.ChannelPrim}`` of one entry geometry."""
    import hou

    name = entry.findPrimAttrib('name')
    if name is None:
        return {}
    return {
        prim.attribValue(name): prim for prim in entry.prims()
        if isinstance(prim, hou.ChannelPrim)
    }


def key_range(entry) -> tuple[float, float] | None:
    """The first and last key over every channel of ``entry``; None for
    an entry without keys."""
    frames = [f for prim in channels(entry).values() for f in prim.keyFrames()]
    if not frames:
        return None
    return min(frames), max(frames)


def shift(entry, offset: float) -> None:
    """Move every key of ``entry`` by ``offset`` frames (edited in place):
    a Multi's shared timeline cooks the shot at other frames, and the keys
    go back on the shot's own, as the layer's samples do."""
    if not offset:
        return
    for prim in channels(entry).values():
        count = len(prim.keyFrames())
        if count:
            prim.moveKeyframes(tuple(range(count)), (float(offset),))


def placement(entry, frame: float, control: str = PLACEMENT_CONTROL) -> tuple | None:
    """The character's move at ``frame`` from ``control``'s channels
    (``Main_tx`` .. ``Main_sz`` on a TumbleRig rig; a prop control's are
    named after its prim) as (translate, rotate, scale); None when the
    entry has no such channels (a rig without ``Main``, an unkeyed prop)."""
    chans = channels(entry)
    prefix = control
    if not any(name.startswith(prefix + '_') for name in chans):
        return None

    def value(parm: str, axis: str, default: float) -> float:
        prim = chans.get(f'{prefix}_{parm}{axis}')
        return default if prim is None else prim.eval(frame)

    return (
        tuple(value('t', a, 0.0) for a in 'xyz'),
        tuple(value('r', a, 0.0) for a in 'xyz'),
        tuple(value('s', a, 1.0) for a in 'xyz'),
    )


def write_catalog(path: Path, clips: dict, *, folders: dict | None = None,
                  thumbnails: dict | None = None) -> dict:
    """Save ``clips`` (``{character: entry geometry}``) as an Animation
    Catalog at ``path``: one ``animation_clip`` item per character, its clip
    ``/animation/<character>.clip``, filed in ``folders[character]`` (a
    folder per name, made once; none: the root). Returns ``{character:
    item id}``. Overwrites ``path``."""
    import apex
    import hou

    folders = folders or {}
    thumbnails = thumbnails or {}
    apex.clearCatalog(_MEMORY_CATALOG)
    source = hou.AssetGalleryDataSource(_MEMORY_CATALOG)
    folder_ids = {}
    for folder in sorted({f for f in folders.values() if f}):
        folder_ids[folder] = source.addItem(folder, '', THUMBNAIL, type_name='folder')
    items = {}
    for character in sorted(clips):
        entry = clips[character]
        clip_path = f'/animation/{character}.clip'
        clip = hou.Geometry()
        clip.packToFolder(f'{clip_path}/_{character}.char_Base', entry)
        span = key_range(entry) or (0.0, 0.0)
        clip.addAttrib(hou.attribType.Global, 'clip_range', hou.Vector2())
        clip.setGlobalAttribValue('clip_range', hou.Vector2(*span))
        clip.addAttrib(hou.attribType.Global, 'control_count', 0)
        clip.setGlobalAttribValue('control_count', 1)
        item = source.addItem(
            character, clip_path, thumbnails.get(character, THUMBNAIL),
            type_name='animation_clip', blind_data=clip.data(),
            parent_id=folder_ids.get(folders.get(character), ''),
        )
        if not item:
            raise RuntimeError(f'the Animation Catalog refused the clip for {character}')
        items[character] = item
    path = Path(path)
    if path.exists():
        path.unlink()
    if not source.saveAs(str(path).replace('\\', '/')):
        raise RuntimeError(f'could not save the layout keys to {path}')
    apex.clearCatalog(_MEMORY_CATALOG)
    return items


def read_catalog(path: Path) -> dict:
    """``{character: entry geometry}`` from a layout keys catalog, keys on
    the shot's own frames, as :func:`write_catalog` wrote them."""
    import hou

    geo = hou.Geometry()
    geo.loadFromFile(str(path))
    return {
        character: entry
        for character, (_clip, entry) in character_entries(geo).items()
    }


def catalog_name(layer_file_name: str) -> str:
    """The layout keys file beside the layer ``layer_file_name``."""
    return layer_file_name.rsplit('.', 1)[0] + CATALOG_SUFFIX


def character_folders(stage, scene_prim: str) -> dict:
    """``{character: folder}`` from the scene prim's character bindings
    (``character:<name>:binding`` -> ``/<category>/...``): a character is
    filed under its prim's top level, the asset category (``CHAR``,
    ``PROP``) or ``cameras``."""
    prim = stage.GetPrimAtPath(scene_prim) if stage is not None else None
    if prim is None or not prim.IsValid():
        return {}
    folders = {}
    for rel in prim.GetRelationships():
        name = rel.GetName()
        if not (name.startswith('character:') and name.endswith(':binding')):
            continue
        targets = rel.GetTargets()
        if targets:
            folders[name[len('character:'):-len(':binding')]] = str(targets[0]).split('/')[1]
    return folders


def _layout_rig_folders(animate_lop, folders: dict) -> dict:
    """The folders of the characters th::layout_rigs loads under
    ``/layout_rigs``: a rig's asset category (its row's prim), the camera
    rig ``cameras``."""
    from tumblepipe.pipe.houdini.lops import layout_rigs

    staged = {c for c, f in folders.items() if f == layout_rigs.RIGS_ROOT.strip('/')}
    if not staged:
        return {}
    todo, seen, rigs = list(animate_lop.inputs()), set(), None
    while todo and rigs is None and len(seen) < 500:
        node = todo.pop(0)
        if node is None or node.path() in seen:
            continue
        seen.add(node.path())
        if node.type().name().startswith('th::layout_rigs'):
            rigs = node
        todo += [i for i in node.inputs() if i is not None]
    categories = {}
    if rigs is not None:
        for row in layout_rigs.LayoutRigs(rigs).get_rows():
            categories[layout_rigs.character_name(row['prim'])] = row['prim'].split('/')[1]
    out = {}
    for character in staged:
        if character == layout_rigs.character_name(layout_rigs.CAMERA_RIG_PATH):
            out[character] = 'cameras'
        elif character in categories:
            out[character] = categories[character]
    return out


def write_layout_keys(animate_lop, target_dir: Path, layer_file_name: str,
                      time_offset: int = 0) -> dict | None:
    """Write the layout keys of ``animate_lop`` beside a layout layer: an
    Animation Catalog ``catalog_name(layer_file_name)`` in ``target_dir``,
    keys moved back by ``time_offset`` (a Multi's shared timeline). Returns
    the ``layout_keys`` entry for the layer's context.json, None when the
    scene has no keys.

    The entry: ``file`` (the catalog's name), ``characters`` (each one's
    clip, folder and key range) and ``camera_rig`` (the registered rig's
    name and its ``settings``, which animation rebuilds the rig from)."""
    from tumblepipe.pipe.houdini import shot_rigs

    scene = read_scene(animate_lop)
    if scene is None:
        return None
    entries = character_entries(scene)
    if not entries:
        return None
    clips = {}
    for character, (_clip, entry) in entries.items():
        shift(entry, -time_offset)
        clips[character] = entry
    scene_prim = animate_lop.parm('sceneprim').evalAsString() or '/scene'
    folders = character_folders(animate_lop.stage(), scene_prim)
    folders.update(_layout_rig_folders(animate_lop, folders))
    name = catalog_name(layer_file_name)
    write_catalog(Path(target_dir) / name, clips, folders=folders)
    info = {
        'file': name,
        'characters': {
            character: {
                'clip': f'/animation/{character}.clip',
                'folder': folders.get(character, ''),
                'range': list(key_range(entry) or ()),
            }
            for character, entry in sorted(clips.items())
        },
    }
    rig = shot_rigs.camera_rig()
    if rig is not None and rig.settings is not None:
        info['camera_rig'] = {'name': rig.name, 'settings': rig.settings(animate_lop)}
    return info
