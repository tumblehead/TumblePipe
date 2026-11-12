"""Live playblasts from Houdini: the animator's shot rendered in the
background while they keep working (designs/shot-dashboard-live-playblast.md
§5.1).

A live playblast is a SOP ``th::playblast``'s render with the output
changed: its shot, department and camera come from the node (never guessed
from the graph), its cache goes to the shot's live folder on local disk
(``pipe.live``), and husk + TumbleView render on the GPU in the background,
the current frame first, while Houdini is free again. No mp4, no version, no
daily: the player shows the frames as they land.

Writing the cache blocks Houdini (the rig evaluates every frame, which any
playblast pays); starting again cancels the render going into that folder
first, so the cache is never rewritten under a running husk.

The triggers: the Live buttons on ``th::animate`` and ``th::playblast``, the
animation radial's Live playblast and the TumblePipe shelf's tool (with its
hotkey), which find the node from the selection (``pick``).
"""

from __future__ import annotations

import atexit
import threading
from pathlib import Path

import hou

from tumblepipe.api import api, get_user_name, path_str
from tumblepipe.config.groups import is_group_uri
from tumblepipe.pipe import live
from tumblepipe.pipe import review_events
from tumblepipe.pipe.houdini import nodes as ns
from tumblepipe.pipe.houdini import tumbleview
from tumblepipe.pipe.houdini.scene_context import scene_context
from tumblepipe.pipe.houdini.sops import playblast as sop_playblast
from tumblepipe.util import events

CACHE_FILE = 'cache.usd'
WRAPPER_FILE = 'live.usda'
TITLE = 'Live playblast'

# A husk left rendering after Houdini quits would keep its live.json saying
# "rendering" for ever.
atexit.register(live.cancel_all)


class LivePlayblastError(ns.NodeError):
    """Nothing to render live, or no way to tell which node: the message
    says what to select or add."""


# ── which node ──────────────────────────────────────────────


def pick(selected_playblasts: list, selected_animates: list,
         current_animate, animates: list) -> tuple[str, list]:
    """What a live playblast without a node of its own renders:

    - ``('playblasts', nodes)``: the selected ``th::playblast`` nodes;
    - ``('animates', nodes)``: else the selected ``th::animate`` nodes, else
      the one the network editor is inside, else the scene's only one;
    - ``('ask', animates)``: the scene has several and none is chosen;
    - ``('none', [])``: the scene has none.
    """
    if selected_playblasts:
        return 'playblasts', list(selected_playblasts)
    if selected_animates:
        return 'animates', list(selected_animates)
    if current_animate is not None:
        return 'animates', [current_animate]
    if len(animates) == 1:
        return 'animates', list(animates)
    if animates:
        return 'ask', list(animates)
    return 'none', []


def _is_type(node, category: str, prefix: str) -> bool:
    node_type = node.type()
    return (
        node_type.category().name() == category
        and node_type.name().startswith(prefix)
    )


def is_playblast(node) -> bool:
    return _is_type(node, 'Sop', 'th::playblast::')


def is_animate(node) -> bool:
    return _is_type(node, 'Lop', 'th::animate::')


def enclosing_animate(node):
    """The ``th::animate`` ``node`` is (or is inside), else None."""
    while node is not None:
        if is_animate(node):
            return node
        node = node.parent()
    return None


def scene_animates() -> list:
    """Every ``th::animate`` in the scene, in path order."""
    lop_types = hou.nodeTypeCategories()['Lop'].nodeTypes()
    found = [
        instance
        for name, node_type in lop_types.items() if name.startswith('th::animate::')
        for instance in node_type.instances()
    ]
    return sorted(found, key=lambda node: node.path())


def _network_editor_node():
    pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    return pane.pwd() if pane is not None else None


def _dive(animate):
    dive = animate.node('anim_dive')
    if dive is None:
        raise LivePlayblastError(f'{animate.path()} has no anim_dive to render')
    return dive


def dive_playblasts(animate) -> list:
    return [child for child in _dive(animate).children() if is_playblast(child)]


def add_playblast(animate):
    """A ``th::playblast`` in the dive of ``animate``, rendering its scene,
    on the workfile's shot, as the animation template makes in a Multi's
    member dives. Refused in a Multi's workfile, which names no one shot:
    there the playblast says which member it is for."""
    context = scene_context()
    if context is not None and is_group_uri(context.entity_uri):
        raise LivePlayblastError(
            f'{animate.path()} has no th::playblast in its dive. In a Multi '
            'workfile add one there and set its Shot to the member it animates.'
        )
    dive = _dive(animate)
    source = dive.node('scene_animate')
    if source is None:
        output = dive.node('output0')
        source = output.input(0) if output is not None else None
    if source is None:
        raise LivePlayblastError(
            f'{animate.path()}: nothing in its dive to render (no scene_animate)'
        )
    node = sop_playblast.create(dive, 'playblast')
    node.parm('camera').set('render_camera')
    node.native().setInput(0, source)
    node.native().moveToGoodPosition()
    return node.native()


def playblast_of(animate):
    """The ``th::playblast`` a live playblast of ``animate`` renders: the
    one in its dive (asking which when there are several), else a new one
    (``add_playblast``). None when the artist cancels the choice."""
    found = dive_playblasts(animate)
    if len(found) == 1:
        return found[0]
    if not found:
        return add_playblast(animate)
    chosen = hou.ui.selectFromList(
        [node.name() for node in found], exclusive=True, title=TITLE,
        message=f'{animate.path()} has several playblasts. Which one?',
    )
    return found[chosen[0]] if chosen else None


def _targets_from_selection() -> list:
    selected = list(hou.selectedNodes())
    current = _network_editor_node()
    kind, nodes = pick(
        [n for n in selected if is_playblast(n)],
        [n for n in selected if is_animate(n)],
        enclosing_animate(current) if current is not None else None,
        scene_animates(),
    )
    if kind == 'none':
        raise LivePlayblastError(
            'this scene has no th::animate to playblast live'
        )
    if kind == 'ask':
        chosen = hou.ui.selectFromList(
            [n.path() for n in nodes], title=TITLE,
            message='Which animation to playblast live?',
        )
        nodes = [nodes[i] for i in chosen]
        kind = 'animates'
    if kind == 'animates':
        nodes = [playblast_of(animate) for animate in nodes]
    return [node for node in nodes if node is not None]


# ── the render ──────────────────────────────────────────────


def folder_of(plan: sop_playblast.Plan) -> Path:
    return live.live_folder(
        live.live_root(), path_str(api.PROJECT_PATH),
        plan.entity_uri.segments, plan.department_name,
    )


def shot_frame(session_frame: float, time_offset: int) -> int:
    """The shot's own frame the playbar's ``session_frame`` shows (a
    shared-timeline Multi moves its members by ``time_offset``)."""
    return int(round(session_frame)) - time_offset


def live_info(plan: sop_playblast.Plan, user: str, workfile: str) -> dict:
    """What live.json says about the render (pipe.live.start adds the run)."""
    r = plan.render_range
    return {
        'uri': str(plan.entity_uri),
        'department': plan.department_name,
        'range': [r.first_frame, r.last_frame],
        'step': r.step_size,
        'fps': plan.fps,
        'resolution': list(plan.res),
        'camera': plan.camera_path,
        'user': user,
        'workfile': workfile,
    }


def _status(text: str, severity=None) -> None:
    """Say ``text`` in the status bar; nothing without a UI (hython)."""
    if not hou.isUIAvailable():
        return
    if severity is None:
        hou.ui.setStatusMessage(text)
    else:
        hou.ui.setStatusMessage(text, severity)


def _announce(name: str, frames: int):
    """The status bar's word when a render ends (from husk's thread, so it
    waits for Houdini's)."""
    def on_end(state: str, folder: Path):
        if state == live.DONE:
            text = f'{TITLE} of {name}: {frames} frames done'
            severity = hou.severityType.Message
        elif state == live.CANCELLED:
            return
        else:
            text = f'{TITLE} of {name} failed: see {path_str(folder / live.LOG_FILE)}'
            severity = hou.severityType.Warning

        def show():
            _status(text, severity)
        try:
            import hdefereval
            hdefereval.executeDeferred(show)
        except Exception:
            pass
    return on_end


def start(native) -> live.LiveRun | None:
    """Write the live cache of the SOP ``th::playblast`` ``native`` and
    start its background render; the running render, None when the artist
    pressed Esc while the cache was written."""
    node = sop_playblast.Playblast(native)
    plan = node.plan()
    folder = folder_of(plan)
    name = '/'.join(plan.entity_uri.segments[1:])
    live.cancel(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cache_path = folder / CACHE_FILE
    try:
        node.cache_stage(cache_path, plan.camera_path, plan.cache_range, plan.fps)
    except hou.OperationInterrupted:
        _status(f'{TITLE} of {name} cancelled')
        return None
    wrapper_path = tumbleview.write_wrapper(
        cache_path, plan.camera_path, plan.fps, name=WRAPPER_FILE,
        time_offset=plan.time_offset,
    )
    r = plan.render_range
    order = live.frame_order(
        r.first_frame, r.last_frame, r.step_size,
        shot_frame(hou.frame(), plan.time_offset),
    )
    command = tumbleview.husk_command(
        wrapper_path, folder / live.FRAME_PATTERN, plan.res, order
    )
    env = tumbleview.husk_env(plan.fps, tumbleview.Look(
        gpu=True, lighting=plan.lighting, cull=plan.cull))
    info = live_info(plan, get_user_name(), hou.hipFile.path())
    try:
        run = live.start(folder, command, env, order, info,
                         on_end=_announce(name, len(order)))
    except OSError as error:
        raise LivePlayblastError(f'{TITLE}: husk did not start ({error})') from None
    # This machine's player resolves the shot again and plays the new run.
    review_events.announce(events.LIVE, plan.entity_uri, plan.department_name)
    _status(
        f'{TITLE} of {name}: rendering {len(order)} frames in the background'
    )
    if hou.isUIAvailable():
        show_live_view(str(plan.entity_uri), shot_frame(hou.frame(), plan.time_offset))
    return run


def show_live_view(shot: str, frame: int) -> None:
    """Live view: the editor shows ``shot`` on the master edit, looping the
    shots around it, at ``frame``: an open editor on the master edit comes to
    the front (keeping Houdini's keyboard), else one starts on it. Off
    Houdini's thread (a call may wait on the editor); the outcome goes to the
    status bar."""
    from tumblepipe.pipe import live_view, review_playlist
    folder = live_view.players_folder(path_str(api.PROJECT_PATH))

    def run():
        try:
            outcome = live_view.show(folder, shot, frame, review_playlist.view_on_master)
            text, severity = f'Live view: {outcome}', hou.severityType.Message
        except live_view.LiveViewError as error:
            text, severity = f'Live view: {error}', hou.severityType.Warning

        def say():
            _status(text, severity)
        try:
            import hdefereval
            hdefereval.executeDeferred(say)
        except Exception:
            pass

    threading.Thread(target=run, name='tumblepipe live view', daemon=True).start()


def stop(native) -> bool:
    """Stop the live render of the SOP ``th::playblast`` ``native``;
    whether one was going."""
    plan = sop_playblast.Playblast(native).plan()
    stopped = live.cancel(folder_of(plan))
    name = '/'.join(plan.entity_uri.segments[1:])
    _status(
        f'{TITLE} of {name} stopped' if stopped
        else f'No {TITLE.lower()} of {name} is rendering'
    )
    return stopped


# ── the triggers ────────────────────────────────────────────


def start_selected() -> list:
    """The shelf tool, hotkey and radial entry: start a live playblast of
    each node ``pick`` finds."""
    runs = []
    with ns.node_errors_as_message(TITLE):
        for node in _targets_from_selection():
            run = start(node)
            if run is None:
                break
            runs.append(run)
    return runs


def start_from_animate(kwargs):
    """th::animate's Live button."""
    with ns.node_errors_as_message(TITLE):
        node = playblast_of(kwargs['node'])
        if node is not None:
            start(node)


def stop_from_animate(kwargs):
    with ns.node_errors_as_message(TITLE):
        for node in dive_playblasts(kwargs['node']):
            stop(node)


def start_from_playblast(kwargs):
    """th::playblast's Live button."""
    with ns.node_errors_as_message(TITLE):
        start(kwargs['node'])


def stop_from_playblast(kwargs):
    with ns.node_errors_as_message(TITLE):
        stop(kwargs['node'])


# ── the hotkey ──────────────────────────────────────────────

#: The shelf tool (toolbar/tumblepipe.shelf) and its default key.
LIVE_TOOL = 'tumblepipe_live_playblast'
LIVE_HOTKEY = 'Alt+Shift+L'


def bind_hotkey(key: str = LIVE_HOTKEY) -> bool:
    """Give the Live Playblast tool ``key`` for this session, unless the
    artist has a key for it already or ``key`` is taken; whether it did.
    Never saved: the artist's own hotkey file stays theirs."""
    hotkeys = getattr(hou, 'hotkeys', None)
    if hotkeys is None:
        return False
    symbol = f'h.tool:{LIVE_TOOL}'
    if hotkeys.assignments(symbol):
        return False
    if hotkeys.findConflicts('h', key):
        return False
    return bool(hotkeys.addAssignment(symbol, key))
