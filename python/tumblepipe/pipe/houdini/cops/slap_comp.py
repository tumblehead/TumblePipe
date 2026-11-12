"""A comp with no th::import_render: a slap comp in the render scene (#80).

Remy built a Copernicus net inside the render scene (Slap Comp Import ->
Block Begin -> ... -> Block End -> th::export_comp). Such a comp has nothing
of its own to read on the farm: Slap Comp Import gives the viewport's last
Karma render, and a farm worker has no viewport. So it comps the render of
the same Farm Submit (Soren, 2026-11-12):

- **In the session** (:func:`find_scene_comp`, :func:`scene_comps`): a shot
  with export_comps in the open workfile and no th::import_render of it is
  a scene comp. Farm Submit offers its Comp cell, and on Submit saves the
  workfile and submits a copy (:func:`snapshot`) beside the render.
- **On the farm** (:func:`swap_imports`): the composite worker points every
  Slap Comp Import at the frames that render wrote, a File COP per AOV and
  channel (several channels' beauties laid over each other, back to front,
  ``pipe.comp.slap_reads``), wired where the Slap Comp Import's output was.

What is decided lives in ``pipe.comp`` (``SceneComp``, ``slap_reads``,
``scene_comp_problems``); this is the Houdini half.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import hou

from tumblepipe.api import path_str
from tumblepipe.pipe import comp
from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns

#: Houdini's Slap Comp Import COP.
SLAP_IMPORT_TYPE = 'slapcompimport'

#: Where Farm Submit keeps the workfile copy, in the submission's folder.
SNAPSHOT_FOLDER = 'comp_scenes'

#: A File COP's 'missingdata' token for an empty image, and its colour
#: spaces (as th::import_render reads renders).
_MISSING_EMPTY = 1
_COLORSPACE_AUTO = 0
_COLORSPACE_RAW = 1


class SceneCompError(Exception):
    """A scene comp cannot be submitted or read; the message says why."""


def import_aovs(native) -> list[str]:
    """The AOVs a Slap Comp Import reads, as named on it, in output order."""
    count = native.parm('aovs').eval()
    return [native.parm(f'aov{index}').evalAsString() for index in range(1, count + 1)]


def _upstream_imports(export_natives) -> list:
    """The Slap Comp Imports the export_comps read: upstream of them, or
    inside a node upstream of them (a slap comp preset asset)."""
    found = []
    seen = set()
    for native in export_natives:
        for ancestor in native.inputAncestors():
            for node in [ancestor, *ancestor.allSubChildren()]:
                if node.type().name() != SLAP_IMPORT_TYPE: continue
                if node.path() in seen: continue
                seen.add(node.path())
                found.append(node)
    return found


def find_scene_comp(shot_uri: Uri) -> comp.SceneComp | None:
    """The open workfile's comp of ``shot_uri`` when no th::import_render
    reads the shot (a scene comp), None otherwise: no export_comp of the
    shot, or the comp template's imports (the normal comp path)."""
    from tumblepipe.pipe.houdini.cops import export_comp
    from tumblepipe.pipe.houdini.cops.import_render import find_imports
    exports = []
    natives = []
    for node in export_comp.find_exports():
        try:
            if node.get_entity_uri() != shot_uri: continue
            layer = node.get_layer_name()
        except ns.NodeError:
            continue
        exports.append(dict(node_path = node.path(), layer = layer))
        natives.append(node.native())
    if not exports: return None
    if find_imports(shot_uri): return None
    aovs = []
    for native in _upstream_imports(natives):
        aovs += [aov for aov in import_aovs(native) if aov not in aovs]
    return comp.SceneComp(exports = tuple(exports), aovs = tuple(aovs))


def scene_comps() -> dict[str, comp.SceneComp]:
    """``{shot uri: SceneComp}`` of every shot the open workfile comps
    without a th::import_render."""
    from tumblepipe.pipe.houdini.cops import export_comp
    shots = dict()
    for node in export_comp.find_exports():
        try:
            shot_uri = node.get_entity_uri()
        except ns.NodeError:
            continue
        if shot_uri is None or str(shot_uri) in shots: continue
        found = find_scene_comp(shot_uri)
        if found is not None:
            shots[str(shot_uri)] = found
    return shots


def snapshot(configs: list[dict], directory: Path) -> list[dict]:
    """``configs`` (Farm Submit rows) with each scene comp's workfile saved
    and copied into ``directory``: the comp the farm renders is the
    workfile as it is now, wherever the artist takes it next.

    A row whose settings carry ``comp_scene`` is a scene comp; its exports
    and AOVs are read again here, as the workfile now has them. Rows
    without one are returned as they are. Raises :class:`SceneCompError`
    when a row's shot is no longer a scene comp in the workfile.
    """
    wanted = [config for config in configs if config.get('settings', {}).get('comp_scene') is not None]
    if not wanted: return configs
    hou.hipFile.save()
    source = Path(hou.hipFile.path())
    target = Path(directory) / SNAPSHOT_FOLDER / source.name
    target.parent.mkdir(parents = True, exist_ok = True)
    shutil.copyfile(source, target)
    result = []
    for config in configs:
        if config.get('settings', {}).get('comp_scene') is None:
            result.append(config)
            continue
        shot_uri = Uri.parse(config['entity']['uri'])
        found = find_scene_comp(shot_uri)
        if found is None:
            raise SceneCompError(
                f'{shot_uri}: the open workfile no longer comps this shot '
                'without a th::import_render; open Farm Submit again'
            )
        settings = dict(config['settings'], comp_scene = found.settings(path_str(target)))
        result.append(dict(config, settings = settings))
    return result


###############################################################################
# The farm: point each Slap Comp Import at the submit's render
###############################################################################
def _unlock_parents(native):
    """Let the worker rewire inside a locked asset (a slap comp preset):
    it edits its own copy of the workfile."""
    ancestors = []
    parent = native.parent()
    while parent is not None and parent.path() != '/':
        ancestors.append(parent)
        parent = parent.parent()
    # Outermost first: an asset inside a locked one unlocks once its
    # container has.
    for ancestor in reversed(ancestors):
        if ancestor.type().definition() is not None and ancestor.isLockedHDA():
            ancestor.allowEditingOfContents()


def _configure_file(file_node, aov, label: str, type_token: str):
    render_range = aov.get_frame_range()
    file_node.parm('filename').set(path_str(aov.get_aov_frame_path('$F4')))
    if render_range is not None:
        file_node.parm('videoframestart').deleteAllKeyframes()
        file_node.parm('videoframestart').set(render_range.first_frame)
    file_node.parm('missingdata').set(_MISSING_EMPTY)
    file_node.parm('colorspace').set(
        _COLORSPACE_AUTO if comp.is_colour_aov(label) else _COLORSPACE_RAW
    )
    file_node.parm('aovs').set(1)
    file_node.parm('aov1').set(comp.aov_plane_name(aov))
    file_node.parm('type1').set(type_token)


def _render_aov(renders_on_disk, render: dict, label: str):
    """The AOV ``label`` of one channel's render (``render``: ``{channel,
    department, version}``); raises SceneCompError when it has none."""
    channel, department, version = render['channel'], render['department'], render['version']
    try:
        layer = renders_on_disk[department].layers[channel][version]
    except KeyError:
        raise SceneCompError(
            f'{department} {version} of channel "{channel}" is not on disk'
        ) from None
    aovs = {name.lower(): aov for name, aov in layer.aovs.items()}
    aov = aovs.get(label.lower())
    if aov is None:
        raise SceneCompError(
            f'{department} {version} of channel "{channel}" has no AOV '
            f'"{label}" (it has {", ".join(sorted(layer.aovs)) or "none"})'
        )
    return aov


def swap_imports(entity_uri: Uri, renders: list[dict], export_natives) -> list[str]:
    """Point every Slap Comp Import the export_comps ``export_natives`` read
    at the render of their submit (``renders``: ``[{channel, department,
    version}]``, in the render's channel order); returns a line per AOV it
    rewired. A Slap Comp Import no export reads is left alone. Raises
    :class:`SceneCompError` (or ``comp.CompError``) saying why it cannot.

    Each output of a Slap Comp Import becomes a File COP of that AOV per
    channel it reads (``comp.slap_reads``), several laid over each other
    back to front with Blend COPs on Over, wired to everything the output
    fed."""
    from tumblepipe.pipe.paths import get_render_context
    renders_on_disk = get_render_context(entity_uri, purpose = 'render').renders
    by_channel = {render['channel']: render for render in renders}
    channels = [render['channel'] for render in renders]
    lines = []
    for native in _upstream_imports(export_natives):
        aovs = import_aovs(native)
        reads = comp.slap_reads(aovs, channels)
        _unlock_parents(native)
        parent = native.parent()
        for index, aov_name in enumerate(aovs):
            label = comp.slap_aov(aov_name)
            type_token = native.parm(f'type{index + 1}').evalAsString()
            out = None
            for channel in reads[aov_name]:
                aov = _render_aov(renders_on_disk, by_channel[channel], label)
                file_node = parent.createNode(
                    'file', f'{native.name()}_{index + 1}_{channel}'
                )
                _configure_file(file_node, aov, label, type_token)
                file_node.setPosition(native.position() + hou.Vector2(-3.0, -1.5 * index))
                if out is None:
                    out = file_node
                    continue
                over = parent.createNode('blend', f'{native.name()}_{index + 1}_over_{channel}')
                over.parm('mode').set('over')
                over.setInput(0, out, 0)
                over.setInput(1, file_node, 0)
                out = over
            for connection in native.outputConnections():
                if connection.outputIndex() != index: continue
                connection.outputNode().setInput(connection.inputIndex(), out, 0)
            render = by_channel[reads[aov_name][0]]
            lines.append(
                f'{native.path()} {aov_name}: {label} of '
                + ', '.join(reads[aov_name])
                + f' ({render["department"]})'
            )
    return lines
