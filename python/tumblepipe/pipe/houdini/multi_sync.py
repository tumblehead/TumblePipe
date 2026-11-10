"""Sync a Multi's open workfile with its Multi.

``pipe.multi_sync`` says how a Multi's workfile and the Multi disagree and
what can be done about it; this reads the open scene, asks the artist and
makes the change:

* after every save of a Multi's workfile (the browser's Save and Houdini's
  own, through :func:`schedule_check` from ``asset_browser.load_hook``),
  once the save is done, and only when something differs;
* on demand, from **Sync with Multi** (TumbleTrove > TumblePipe, and the
  Multi's department row in the browser), which also says when the two
  agree.

The save always goes through: the question comes after it. *Don't ask
again for this workfile* silences the after-save check for that Multi and
department for the rest of the session; Sync with Multi still answers.

An export pinned to a shot that is not a member offers to add the shot to
the Multi (``config.groups.add_member``, so the overlap rule holds). A
member with no export here offers to add its graph: a copy of another
member's column, placed at a free spot to the right, every node renamed to
the new member's suffix and every parm pinned to the old member pinned to
the new one; with no column to copy, the department template's graph for
that one member. Either way the new import nodes are run, as a new
workfile's are.
"""

from __future__ import annotations

import logging
import weakref
from dataclasses import replace
from pathlib import Path
from typing import Callable

import hou

from tumblepipe.config import groups as groups_mod
from tumblepipe.pipe import multi_sync as policy
from tumblepipe.util.uri import Uri

log = logging.getLogger(__name__)

TITLE = 'Sync with Multi'
STAGE = '/stage'
FROM_CONTEXT = 'from_context'
#: The parms an entity node holds its entity in (``EntityNode.ENTITY_PARM``).
_ENTITY_PARMS = ('entity', 'shot', 'asset')
_DONT_ASK = "Don't ask again for this workfile"

_quiet = policy.Quiet()
#: The workfile a check is waiting on, between a save and Houdini's idle.
_pending: str | None = None
_listeners: list = []


class SyncError(Exception):
    """Why a graph could not be added."""


# ---------------------------------------------------------------------------
# Listeners: the browser refreshes when a sync adds members
# ---------------------------------------------------------------------------

def add_listener(callback: Callable[[Uri], None]) -> None:
    """Call ``callback(group_uri)`` after a sync adds members to a Multi.
    Held weakly: a bound method goes with its object. The browser's change
    feed skips this session's own writes, so it needs telling."""
    try:
        ref = weakref.WeakMethod(callback)
    except TypeError:
        ref = weakref.ref(callback)
    _listeners.append(ref)


def _notify(group_uri: Uri) -> None:
    for ref in list(_listeners):
        callback = ref()
        if callback is None:
            _listeners.remove(ref)
            continue
        try:
            callback(group_uri)
        except Exception:
            log.exception('Sync with Multi: a listener failed')


# ---------------------------------------------------------------------------
# Reading the scene
# ---------------------------------------------------------------------------

def _export_nodes() -> list:
    """Every ``th::export_layer`` node in the scene."""
    found = []
    for node_type in hou.lopNodeTypeCategory().nodeTypes().values():
        parts = node_type.name().lower().split('::')
        if len(parts) >= 2 and parts[0] == 'th' and parts[1] == 'export_layer':
            found.extend(node_type.instances())
    return found


def _string_values(node) -> list[str]:
    values = []
    for parm in node.parms():
        try:
            if parm.parmTemplate().type() != hou.parmTemplateType.String:
                continue
            values.append(parm.unexpandedString())
        except hou.OperationFailed:
            continue
    return values


def _with_contents(node) -> list:
    """``node`` and, for a subnet the artist can edit, everything in it."""
    nodes = [node]
    try:
        if node.childTypeCategory() is not None and node.isEditable():
            nodes.extend(node.allSubChildren())
    except hou.OperationFailed:
        pass
    return nodes


def _pins(node) -> frozenset[str]:
    found = set()
    for each in _with_contents(node):
        for value in _string_values(each):
            value = value.strip()
            if value.startswith('entity:/'):
                found.add(value)
    return frozenset(found)


def _raw(node, names) -> str | None:
    for name in names:
        parm = node.parm(name)
        if parm is not None:
            try:
                return parm.unexpandedString().strip()
            except hou.OperationFailed:
                return parm.evalAsString().strip()
    return None


def _anchor(node, network: str) -> str:
    """The node of ``network`` that is or holds ``node``; '' outside it."""
    prefix = network.rstrip('/') + '/'
    path = node.path()
    if not path.startswith(prefix):
        return ''
    return prefix + path[len(prefix):].split('/', 1)[0]


def read_exports(department: str, network: str = STAGE) -> list[policy.Export]:
    """The scene's export nodes as the policy reads them: the entity each
    is pinned to (None from the context) and its department (the
    workfile's, from the context)."""
    found = []
    for node in _export_nodes():
        entity = _raw(node, _ENTITY_PARMS)
        dept = _raw(node, ('department',))
        found.append(policy.Export(
            node.path(), _anchor(node, network),
            None if not entity or entity == FROM_CONTEXT else entity,
            department if not dept or dept == FROM_CONTEXT else dept,
        ))
    return found


def read_graph(department: str, network: str = STAGE) -> policy.Graph:
    """``network``'s nodes and the scene's exports."""
    net = hou.node(network)
    nodes = []
    if net is not None:
        for node in net.children():
            position = node.position()
            nodes.append(policy.Node(
                node.path(), node.name(),
                tuple(n.path() if n is not None else None for n in node.inputs()),
                _pins(node), position[0], position[1],
            ))
    return policy.Graph(network, tuple(nodes), tuple(read_exports(department, network)))


# ---------------------------------------------------------------------------
# Adding graphs
# ---------------------------------------------------------------------------

def _repin_expression(parm, source: str, target: str) -> bool:
    """A parm driven by one expression naming ``source`` (a camera's
    aspect, ``render_camera.bind_aspect``) made to name ``target``. Whether
    it was. Keyframed animation is left as it is."""
    try:
        if len(parm.keyframes()) != 1:
            return False
        expression, language = parm.expression(), parm.expressionLanguage()
    except hou.OperationFailed:
        return False
    new = policy.repinned_expression(expression, source, target)
    if new is None:
        return False
    try:
        parm.setExpression(new, language)
    except (hou.OperationFailed, hou.PermissionError):
        log.debug('Sync with Multi: %s is locked', parm.path())
        return False
    return True


def _repinned_type(parm) -> bool:
    """Whether ``parm`` can hold a pin a copy must move: a String parm, or
    a Label showing one (an entity node's 'entity_label'). With Labels left
    out a copied export read the source's shot while it published the
    target's (QA 2026-11-10). Labels are not pins: :func:`_pins` ignores
    them."""
    kind = parm.parmTemplate().type()
    return kind in (hou.parmTemplateType.String, hou.parmTemplateType.Label)


def _repin(node, source: str, target: str, network: str, renames: dict) -> int:
    """Pin ``node`` (and an editable subnet's contents) to ``target`` where
    it was pinned to ``source``, in values and in expressions, and point
    references to copied nodes at the copies. The number of parms changed."""
    changed = 0
    for each in _with_contents(node):
        for parm in each.parms():
            if _repin_expression(parm, source, target):
                changed += 1
                continue
            try:
                if not _repinned_type(parm):
                    continue
                value = parm.unexpandedString()
            except hou.OperationFailed:
                continue   # keyframes: left as they are
            new = policy.repinned(value, source, target)
            if new is None:
                new = policy.renamed_reference(value, network, renames)
            if new is None:
                continue
            try:
                parm.set(new)
                changed += 1
            except (hou.OperationFailed, hou.PermissionError):
                log.debug('Sync with Multi: %s is locked', parm.path())
    return changed


def _display_node(network):
    try:
        return network.displayNode()
    except AttributeError:
        return None


def copy_column(plan: policy.ColumnCopy, network: str = STAGE) -> list:
    """Make ``plan``'s copy of a member's column; the new nodes."""
    net = hou.node(network)
    sources = [hou.node(path) for path in plan.nodes]
    if net is None or any(node is None for node in sources):
        raise SyncError(f"{policy.label(plan.source)}'s graph changed while syncing")
    shown = _display_node(net)
    copies = list(hou.copyNodesTo(sources, net))
    by_source = dict(zip(plan.nodes, copies))
    names = dict(plan.names)
    dx, dy = plan.offset
    for path, copy in by_source.items():
        copy.setName(names[path], unique_name=True)
        copy.setPosition(hou.node(path).position() + hou.Vector2(dx, dy))
    for path, index, outside in plan.inputs:
        source_node, outside_node = hou.node(path), hou.node(outside)
        if outside_node is None:
            continue
        output = 0
        for connection in source_node.inputConnections():
            if connection.inputIndex() == index:
                output = connection.outputIndex()
        by_source[path].setInput(index, outside_node, output)
    for collector, path in plan.collectors:
        node = hou.node(collector)
        if node is not None:
            node.setNextInput(by_source[path])
    renames = {hou.node(path).name(): copy.name() for path, copy in by_source.items()}
    for copy in copies:
        _repin(copy, plan.source, plan.target, network, renames)
    if shown is not None:
        shown.setDisplayFlag(True)
    return copies


def _template(context: str, department: str):
    from tumblepipe.api import api
    from tumblepipe.pipe.houdini.ui.helpers import load_module

    path = Path(api.storage.resolve(
        Uri.parse(f'config:/templates/{context}/{department}/template.py'),
    ))
    if not path.exists():
        raise SyncError(f'The {department} department has no template')
    return load_module(path, f'tumblepipe_multi_sync_{context}_{department}')


def _pin_from_context(node, member: str, department: str, naming: list[str]) -> None:
    """A template's entity-branch node, made the member's: its entity and
    department pinned where they come from the context, its name given the
    member's suffix."""
    for each in _with_contents(node):
        for name in _ENTITY_PARMS:
            parm = each.parm(name)
            if parm is not None and _raw(each, (name,)) == FROM_CONTEXT:
                parm.set(member)
        parm = each.parm('department')
        if parm is not None and _raw(each, ('department',)) == FROM_CONTEXT:
            parm.set(department)
    node.setName(policy.renamed(node.name(), member, member, naming), unique_name=True)


def template_graph(
    group: groups_mod.Group, member: str, department: str,
    graph: policy.Graph, network: str = STAGE,
) -> list:
    """Build ``member``'s graph from the department template, the way the
    template builds it for a Multi of that one member, and move it to a
    free spot to the right; the new nodes. The rest of the network stays
    where it was, even when the template lays the network out."""
    net = hou.node(network)
    if net is None:
        raise SyncError(f'{network} is missing')
    module = _template(group.uri.segments[0], department)
    before = {node.path(): node.position() for node in net.children()}
    shown = _display_node(net)
    member_uri = Uri.parse(member)
    naming = [str(m) for m in group.members]
    if hasattr(module, 'get_group') and hasattr(module, '_create_group'):
        # The template's own Multi branch, for this member alone: its
        # nodes come pinned and named with the member's suffix.
        alone = replace(group, members=[member_uri], stored_members=[member_uri])
        module.get_group = lambda uri: alone if uri == group.uri else groups_mod.get_group(uri)
        module.create(net, group.uri, department)
        pinned = True
    else:
        module.create(net, member_uri, department)
        pinned = False
    for path, position in before.items():
        node = hou.node(path)
        if node is not None:
            node.setPosition(position)
    new = [node for node in net.children() if node.path() not in before]
    if not pinned:
        for node in new:
            _pin_from_context(node, member, department, naming)
    if new:
        columns = policy.columns(graph, group.members, department)
        by_path = {node.path: node for node in graph.nodes}
        lefts = [min(by_path[p].x for p in paths) for paths in columns.values() if paths]
        xy = [(node.position()[0], node.position()[1]) for node in new]
        (dx, dy), = policy.free_offsets(list(before.values()), xy, lefts, 1)
        for node in new:
            node.setPosition(node.position() + hou.Vector2(dx, dy))
    if shown is not None:
        shown.setDisplayFlag(True)
    return new


def _run_imports(nodes: list) -> tuple[int, int]:
    """Run the import nodes among ``nodes`` (and in them), as a new
    workfile's are; ``(run, failed)``."""
    from tumblepipe import resolver
    from tumblepipe.pipe.houdini import playbar
    from tumblepipe.pipe.houdini import scene_imports

    matched = {}
    for node in nodes:
        for each in [node, *node.allSubChildren()]:
            spec = scene_imports.spec_for(each.type().name(), each.type().category().name())
            if spec is not None:
                matched[each.path()] = (each, spec)
    run = failed = 0
    with playbar.update_mode(hou.updateMode.Manual), resolver.deferred_refresh():
        for path in scene_imports.outermost(matched):
            native, spec = matched[path]
            try:
                scene_imports.execute(native, spec)
                run += 1
            except Exception:
                log.exception('Sync with Multi: importing %s failed', path)
                failed += 1
    return run, failed


def add_graphs(
    group: groups_mod.Group, department: str, missing: list[str],
    network: str = STAGE,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Give each of ``missing`` its graph: a copy of the first member's
    column that has one, else the template's. ``(added, failed with why)``."""
    graph = read_graph(department, network)
    members = [str(m) for m in group.members]
    source = policy.source_member(graph, members, department)
    added: list[str] = []
    failed: list[tuple[str, str]] = []
    made = []
    if source is not None:
        for plan in policy.plan_copies(graph, source, missing, members, department):
            try:
                made.extend(copy_column(plan, network))
                added.append(plan.target)
            except (SyncError, hou.Error) as exc:
                failed.append((plan.target, str(exc)))
    else:
        for member in missing:
            try:
                made.extend(template_graph(group, member, department, graph, network))
                added.append(member)
            except (SyncError, hou.Error) as exc:
                failed.append((member, str(exc)))
            graph = read_graph(department, network)
    if made:
        _run_imports(made)
    return added, failed


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------

def _ask(text: str, buttons: tuple[str, ...], severity=None) -> int:
    """The artist's choice; closing the window is the choice before the
    last (Not now), never Don't ask again."""
    close = len(buttons) - 2 if len(buttons) > 2 else 0
    return hou.ui.displayMessage(
        text, buttons=buttons, default_choice=0, close_choice=close,
        title=TITLE, severity=severity or hou.severityType.Message,
    )


def _tell(text: str, severity=None) -> None:
    hou.ui.displayMessage(text, title=TITLE, severity=severity or hou.severityType.Message)


def _publishable(context: str, department: str) -> bool:
    from tumblepipe.config.department import list_departments
    for each in list_departments(context, include_disabled=True):
        if each.name == department:
            return each.publishable
    return True


def _scene_multi():
    from tumblepipe.pipe.houdini.scene_context import scene_context
    context = scene_context()
    if context is None or not groups_mod.is_group_uri(context.entity_uri):
        return None, None
    return context.entity_uri, context.department_name


def sync(on_demand: bool = False) -> bool:
    """Compare the open Multi workfile with its Multi and ask about what
    differs. ``on_demand``: Sync with Multi, which always answers and
    ignores *Don't ask again*. Whether anything was changed."""
    group_uri, department = _scene_multi()
    if group_uri is None:
        if on_demand:
            _tell("The open scene is not a Multi's workfile.")
        return False
    group = groups_mod.get_group(group_uri)
    name = group_uri.segments[-1]
    if group is None:
        if on_demand:
            _tell(f'Multi {name} is no longer in the project.', hou.severityType.Warning)
        return False
    context = group_uri.segments[0]
    publishable = _publishable(context, department)
    exports = read_exports(department)
    difference = policy.compare(exports, department, group.members, publishable)
    if not policy.should_ask(difference, _quiet, group_uri, department, on_demand):
        return False
    if difference.empty:
        if not publishable:
            _tell(f'{department} publishes nothing, so there are no exports to compare with {name}.')
        else:
            _tell(policy.in_sync_message(name, department))
        return False
    changed = False
    if difference.strangers:
        admission = policy.admit(group_uri, difference.strangers)
        text = policy.strangers_prompt(name, department, admission, group.departments)
        if admission.addable:
            choice = _ask(text, (f'Add to {name}', 'Not now', _DONT_ASK))
            if choice == 2:
                _quiet.add(group_uri, department)
                return False
            if choice == 0:
                result = policy.add_members(group_uri, admission.addable)
                if result.addable:
                    changed = True
                    _members_changed(group_uri)
                if result.refused:
                    _tell(policy.strangers_prompt(
                        name, department, policy.Admission((), result.refused), (),
                    ), hou.severityType.Warning)
        else:
            choice = _ask(text, ('OK', _DONT_ASK), hou.severityType.Warning)
            if choice == 1:
                _quiet.add(group_uri, department)
                return False
    if difference.missing:
        group = groups_mod.get_group(group_uri) or group
        missing = [m for m in difference.missing if m in {str(x) for x in group.members}]
        if missing:
            graph = read_graph(department)
            source = policy.source_member(graph, [str(m) for m in group.members], department)
            text = policy.missing_prompt(name, department, missing, source)
            choice = _ask(text, ('Add graphs' if len(missing) > 1 else 'Add graph', 'Not now', _DONT_ASK))
            if choice == 2:
                _quiet.add(group_uri, department)
            elif choice == 0:
                added, failed = add_graphs(group, department, missing)
                changed = changed or bool(added)
                if failed:
                    _tell('\n'.join(
                        f'{policy.label(m)}: {why}' for m, why in failed
                    ), hou.severityType.Warning)
                if added:
                    hou.ui.setStatusMessage(
                        f'Sync with Multi: added graphs for {policy.listing(added)}',
                    )
    return changed


def _members_changed(group_uri: Uri) -> None:
    try:
        from tumblepipe.pipe.houdini import multi_timeline as session
        session.apply_if_open(group_uri)
    except Exception:
        log.exception('Sync with Multi: updating the playbar failed')
    _notify(group_uri)


def sync_with_multi() -> None:
    """Sync with Multi: the menu action."""
    try:
        sync(on_demand=True)
    except Exception as exc:
        log.exception('Sync with Multi failed')
        _tell(f'Sync with Multi failed: {exc}', hou.severityType.Error)


def schedule_check() -> None:
    """After a save: check the Multi workfile once Houdini is idle, so the
    save is done first. Several saves in one go (New from Template saves
    twice) check once."""
    global _pending
    if _pending is not None or not hou.isUIAvailable():
        return
    try:
        import hdefereval
    except ImportError:
        return
    _pending = hou.hipFile.path()
    try:
        hdefereval.executeDeferred(_check)
    except Exception:
        _pending = None     # or no save would ever check again
        raise


def _check() -> None:
    global _pending
    saved, _pending = _pending, None
    # Opening a workfile saves the open one first, then loads the next:
    # only the scene that was saved asks, not the one that replaced it.
    if hou.hipFile.path() != saved:
        return
    # An autosave writes a backup and leaves the scene unsaved: only the
    # artist's own save asks.
    if hou.hipFile.hasUnsavedChanges():
        return
    try:
        sync(on_demand=False)
    except Exception:
        log.exception('Sync with Multi: the after-save check failed')
