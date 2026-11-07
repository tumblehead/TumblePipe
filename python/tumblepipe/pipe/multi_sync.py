"""A Multi's workfile and its Multi, kept in step.

A Multi is set up in the Entity Editor: its members, the departments it
covers, and from there each covered department's workfile, made from the
department template with a graph per member. Artists also work the other
way round: they add an export node for one more shot to a Multi's
workfile, or add a member to the Multi and leave the workfile as it was.
Either way the Multi and its workfile disagree. This module says how, and
what to do about it; ``pipe.houdini.multi_sync`` reads the scene, asks the
artist and makes the change.

Two differences, for the workfile's department only:

* **strangers**: an export pinned to a shot (asset) that is not a member.
  Offered: add it to the Multi, through ``config.groups.add_member`` as the
  browser and the Entity Editor do, so the overlap rule holds (a member's
  department covered by one Multi only). A stranger that cannot join is
  listed with the reason.
* **missing**: a member with no export here. Offered: add its graph, a copy
  of another member's *column* (:func:`column`) re-pinned to it, or, with
  no column to copy, the department template's.

A department that publishes nothing (``composite``) has no exports to
compare and is never out of step. An export reading its entity from the
context has none in a Multi's workfile and is left out.

Everything here is free of Houdini: the scene comes in as a :class:`Graph`
of plain :class:`Node` records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median
from typing import Iterable, Mapping, Sequence

from tumblepipe.util.uri import Uri

#: A node's size in network units, for telling whether a spot is free.
NODE_WIDTH = 1.0
NODE_HEIGHT = 0.4
#: Space kept clear around a copied column.
GAP = 1.0


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def _uri(member: Uri | str) -> Uri:
    return member if isinstance(member, Uri) else Uri.parse(member)


def _parsed(member: Uri | str) -> Uri | None:
    """``member`` as a URI; None for a value that is none (an export's
    entity parm can hold anything)."""
    try:
        return _uri(member)
    except Exception:
        return None


def member_suffix(member: Uri | str) -> str:
    """How the department templates name a member's nodes in a Multi's
    workfile: its URI below the context, joined with ``_``
    (``entity:/shots/sq010/sh010`` names ``import_shot_sq010_sh010``)."""
    return '_'.join(_uri(member).segments[1:])


def _suffixes(member: Uri | str, members: Iterable[Uri | str]) -> list[str]:
    """The name endings that mark a node as ``member``'s, longest first: its
    full suffix, and its own name alone (``sh010``) when no other member's
    name is the same."""
    full = member_suffix(member)
    last = _uri(member).segments[-1]
    found = [full]
    names = [_uri(m).segments[-1] for m in members]
    if last != full and names.count(last) <= 1:
        found.append(last)
    return found


def named_for(name: str, member: Uri | str, members: Iterable[Uri | str]) -> str | None:
    """The ending of ``name`` that marks it as ``member``'s node
    (``key_light_sq010_sh010``, or ``geo_sh010``); None when it has none."""
    for suffix in _suffixes(member, list(members)):
        tail = f'_{suffix}'
        if name.endswith(tail) and len(name) > len(tail):
            return suffix
    return None


def renamed(
    name: str, source: Uri | str, target: Uri | str,
    members: Iterable[Uri | str],
) -> str:
    """The name of ``target``'s copy of ``source``'s node ``name``: the
    source's ending swapped for the target's in the same form (the full
    suffix for the full suffix, the shot's name for the shot's name), or
    the target's full suffix added to a name without one."""
    members = list(members)
    found = named_for(name, source, members)
    target_full = member_suffix(target)
    if found is None:
        return f'{name}_{target_full}'
    if found == member_suffix(source):
        new = target_full
    else:
        target_name = _uri(target).segments[-1]
        others = [_uri(m).segments[-1] for m in members if str(m) != str(target)]
        new = target_full if target_name in others else target_name
    return name[: -len(found)] + new


def repinned(value: str, source: Uri | str, target: Uri | str) -> str | None:
    """A string parm's value on a copied node, pinned to ``target`` where
    it was pinned to ``source``; None when it is not a pin to the source
    (another entity, ``from_context``, anything else)."""
    if value.strip() == str(source):
        return str(target)
    return None


def repinned_expression(
    expression: str, source: Uri | str, target: Uri | str,
) -> str | None:
    """A copied parm's expression with ``source`` written in it as a whole
    string literal (``'entity:/...'`` or ``"entity:/..."``, as
    ``render_camera.bind_aspect`` writes a camera's aspect) pinned to
    ``target``; None when it names no such literal. A longer URI that only
    starts with the source's is another entity and stays."""
    out = expression
    for quote in ("'", '"'):
        out = out.replace(f'{quote}{source}{quote}', f'{quote}{target}{quote}')
    return out if out != expression else None


def renamed_reference(
    value: str, network: str, names: Mapping[str, str],
) -> str | None:
    """A string parm's value with its references to copied nodes moved to
    their copies: ``../<old>`` and ``<network>/<old>`` for each old name in
    ``names`` (old name -> new name). None when nothing changed. A path
    elsewhere (``/obj/<old>``) is not one of the copies and stays."""
    out = value
    prefixes = ('../', network.rstrip('/') + '/')
    for old, new in names.items():
        for prefix in prefixes:
            out = re.sub(
                re.escape(prefix + old) + r'(?![A-Za-z0-9_.\-])',
                (prefix + new).replace('\\', '\\\\'), out,
            )
    return out if out != value else None


def label(member: Uri | str, among: Iterable[Uri | str] = ()) -> str:
    """A member as a prompt names it: its own name (``SH050``), or with its
    sequence (``SQ010/SH050``) when another one in ``among`` has that name."""
    uri = _parsed(member)
    if uri is None or not uri.segments:
        return str(member)
    name = uri.segments[-1]
    others = [_parsed(m) for m in among if str(m) != str(member)]
    if name in [m.segments[-1] for m in others if m is not None and m.segments]:
        return '/'.join(uri.segments[1:])
    return name


def listing(members: Sequence[Uri | str]) -> str:
    """``SH050``, ``SH050 and SH060``, ``SH050, SH060 and SH070``."""
    names = [label(m, members) for m in members]
    if len(names) <= 1:
        return ''.join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


# ---------------------------------------------------------------------------
# The scene, as plain records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Node:
    """A node of the network the workfile's exports sit in.

    ``inputs`` are the paths of the nodes wired into it, in input order
    (None for an empty input). ``pins`` are the entity URIs its string
    parms (and those of the nodes inside it, for a subnet) are set to.
    ``x``/``y`` is its position in the network.
    """
    path: str
    name: str
    inputs: tuple[str | None, ...] = ()
    pins: frozenset[str] = frozenset()
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class Export:
    """An export node: the entity and department it publishes, as it reads
    them (None for an entity from the context, or none set). ``anchor`` is
    the node of the network it is, or the subnet that holds it."""
    path: str
    anchor: str
    entity: str | None
    department: str | None


@dataclass(frozen=True)
class Graph:
    """The network a Multi's workfile builds its members in (``/stage``)."""
    network: str
    nodes: tuple[Node, ...]
    exports: tuple[Export, ...]

    def node(self, path: str) -> Node | None:
        for node in self.nodes:
            if node.path == path:
                return node
        return None


# ---------------------------------------------------------------------------
# What differs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Difference:
    """How a Multi workfile's exports and its Multi's members disagree.

    ``strangers``: entities exported here that are not members, in node
    order. ``missing``: members nothing here exports, in the Multi's
    order. URIs as strings.
    """
    strangers: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.strangers and not self.missing


def exported(exports: Iterable[Export], department: str) -> list[str]:
    """The entities the workfile's exports publish ``department`` for, each
    once, first export first. Exports from the context are left out."""
    found: list[str] = []
    for export in exports:
        if export.entity is None or export.department != department:
            continue
        if export.entity not in found:
            found.append(export.entity)
    return found


def compare(
    exports: Iterable[Export], department: str,
    members: Iterable[Uri | str], publishable: bool = True,
) -> Difference:
    """What differs between the exports of a Multi's ``department``
    workfile and the Multi's ``members``. Nothing for a department that
    publishes nothing."""
    if not publishable:
        return Difference()
    found = exported(exports, department)
    keys = [str(member) for member in members]
    return Difference(
        tuple(entity for entity in found if entity not in keys),
        tuple(key for key in keys if key not in found),
    )


# ---------------------------------------------------------------------------
# Strangers: who can join
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Admission:
    """Which strangers can join a Multi, and why each of the rest cannot
    (``(URI, reason)``)."""
    addable: tuple[str, ...] = ()
    refused: tuple[tuple[str, str], ...] = ()


_KIND = {'shots': 'shot', 'assets': 'asset'}


def admit(group_uri: Uri, strangers: Iterable[str]) -> Admission:
    """Sort ``strangers`` into those that can join the Multi and those that
    cannot, with the reason: not a shot of a shots Multi (an asset of an
    assets one), not in the project, or the overlap rule
    (``config.groups.overlap``: one of the departments the Multi covers is
    covered for it by another Multi already)."""
    from tumblepipe.api import api
    from tumblepipe.config import groups as groups_mod

    context = group_uri.segments[0]
    group = groups_mod.get_group(group_uri)
    departments = list(group.departments) if group is not None else []
    kind = _KIND.get(context, context)
    addable: list[str] = []
    refused: list[tuple[str, str]] = []
    for raw in dict.fromkeys(strangers):
        try:
            uri = Uri.parse(raw)
        except Exception:
            refused.append((raw, f'{raw} is not an entity'))
            continue
        name = uri.segments[-1] if uri.segments else raw
        if (
            uri.purpose != 'entity' or len(uri.segments) != 3
            or uri.segments[0] != context
        ):
            refused.append((raw, f'{name} is not a {kind}; {group_uri.segments[-1]} holds {context}'))
            continue
        if api.config.get_properties(uri) is None:
            refused.append((raw, f'{name} is not in the project'))
            continue
        clash = groups_mod.overlap(context, [uri], departments, group_uri)
        if clash:
            refused.append((raw, clash))
            continue
        addable.append(raw)
    return Admission(tuple(addable), tuple(refused))


def add_members(group_uri: Uri, uris: Iterable[str]) -> Admission:
    """Add ``uris`` to the Multi one by one through
    ``config.groups.add_member`` (the browser's and the Entity Editor's
    path: context, membership and overlap checked, a shared timeline's
    place given). What it refuses is returned with its reason."""
    from tumblepipe.config import groups as groups_mod

    added: list[str] = []
    refused: list[tuple[str, str]] = []
    for raw in uris:
        try:
            groups_mod.add_member(group_uri, Uri.parse(raw))
        except ValueError as exc:
            refused.append((raw, str(exc)))
            continue
        added.append(raw)
    return Admission(tuple(added), tuple(refused))


# ---------------------------------------------------------------------------
# Missing: a member's column
# ---------------------------------------------------------------------------

def _outputs(graph: Graph) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {node.path: [] for node in graph.nodes}
    for node in graph.nodes:
        for source in node.inputs:
            if source in found:
                found[source].append(node.path)
    return found


def _reach(start: Iterable[str], step: Mapping[str, Iterable[str]]) -> set[str]:
    seen: set[str] = set()
    todo = list(start)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        todo.extend(p for p in step.get(path, ()) if p is not None)
    return seen


def anchors(graph: Graph, member: Uri | str, members: Iterable[Uri | str]) -> set[str]:
    """The nodes that say they are ``member``'s: pinned to it, or named
    with its suffix."""
    key = str(member)
    members = list(members)
    return {
        node.path for node in graph.nodes
        if key in node.pins or named_for(node.name, member, members)
    }


def _ends(graph: Graph, member: Uri | str, department: str) -> set[str]:
    key = str(member)
    return {
        export.anchor for export in graph.exports
        if export.entity == key and export.department == department
    }


def _naming(graph: Graph, members: Sequence[Uri | str]) -> list[str]:
    """``members`` and every other entity an export here is pinned to."""
    found = [str(m) for m in members]
    for export in graph.exports:
        if export.entity is not None and export.entity not in found:
            found.append(export.entity)
    return found


def _between(
    graph: Graph, member: Uri | str, members: list, department: str,
    outputs: Mapping[str, list[str]], inputs: Mapping[str, Iterable[str]],
) -> set[str]:
    """``member``'s anchors and every node on a path from one of them to
    one of its exports."""
    own = anchors(graph, member, members)
    ends = _ends(graph, member, department)
    return own | (_reach(own, outputs) & _reach(ends, inputs))


def columns(
    graph: Graph, members: Iterable[Uri | str], department: str,
) -> dict[str, list[str]]:
    """Each member's column, by member URI string (see :func:`column`)."""
    members = list(members)
    # A shot's name alone marks its nodes only when no other shot this
    # workfile knows (a member, or one an export is pinned to) has it.
    naming = _naming(graph, members)
    outputs = _outputs(graph)
    inputs = {node.path: node.inputs for node in graph.nodes}
    own = {str(m): anchors(graph, m, naming) for m in members}
    between = {
        str(m): _between(graph, m, naming, department, outputs, inputs)
        for m in members
    }
    order = [node.path for node in graph.nodes]
    found = {}
    for member in members:
        key = str(member)
        other_own = set().union(*(own[k] for k in own if k != key))
        other_between = set().union(*(between[k] for k in between if k != key))
        keep = (own[key] - other_own) | (between[key] - other_between - other_own)
        found[key] = [path for path in order if path in keep]
    return found


def column(
    graph: Graph, member: Uri | str, members: Iterable[Uri | str], department: str,
) -> list[str]:
    """The nodes that make ``member``'s graph in a Multi's workfile, in
    network order: the nodes pinned to it or named with its suffix, and the
    nodes on the paths from those to its exports for ``department``, less
    anything another member's graph uses too (a shared camera, a merge of
    every member)."""
    return columns(graph, members, department).get(str(member), [])


def source_member(
    graph: Graph, members: Iterable[Uri | str], department: str,
) -> str | None:
    """The member whose graph a missing member's copies: the first, in the
    Multi's order, with an export here and a column. None when no member
    has one."""
    members = list(members)
    found = columns(graph, members, department)
    for member in members:
        key = str(member)
        if _ends(graph, member, department) and found.get(key):
            return key
    return None


@dataclass(frozen=True)
class ColumnCopy:
    """How to give ``target`` a copy of ``source``'s column.

    ``nodes``: the source's nodes to copy, in network order. ``names``: the
    copy's name for each (source path -> name). ``offset``: how far the
    copy moves from the source. ``inputs``: wires into the column from
    outside it, to keep on the copy (source path, input index, outside
    path). ``collectors``: outside nodes fed by several members' columns (a
    merge of every member), which take the copy as one more input
    (outside path, the source path feeding it).
    """
    source: str
    target: str
    nodes: tuple[str, ...]
    names: tuple[tuple[str, str], ...]
    offset: tuple[float, float]
    inputs: tuple[tuple[str, int, str], ...]
    collectors: tuple[tuple[str, str], ...]

    def name_of(self, path: str) -> str:
        return dict(self.names)[path]


def free_offsets(
    occupied: Iterable[tuple[float, float]],
    column_xy: Sequence[tuple[float, float]],
    lefts: Sequence[float],
    count: int,
) -> list[tuple[float, float]]:
    """Where ``count`` copies of a column at ``column_xy`` go: one after
    another to the right of the members' columns (whose left edges are
    ``lefts``), a column's stride apart (the median gap between the
    members' columns, as the department templates lay them out; else the
    column's width and a gap), each moved on by a stride until no node of
    ``occupied`` (or an earlier copy) is in its way. Offsets ``(dx, dy)``
    from the column's place; the copies keep its height."""
    if count <= 0 or not column_xy:
        return [(0.0, 0.0)] * max(count, 0)
    xs = [x for x, _ in column_xy]
    left, right = min(xs), max(xs) + NODE_WIDTH
    width = right - left
    ordered = sorted(lefts)
    steps = [b - a for a, b in zip(ordered, ordered[1:]) if b - a > 1e-6]
    stride = max(median(steps) if steps else width + GAP, 0.5)
    boxes = [(x, y) for x, y in occupied]
    x = ordered[-1] + stride if ordered else left
    found = []

    def clear(dx: float) -> bool:
        for cx, cy in column_xy:
            for ox, oy in boxes:
                if (
                    cx + dx < ox + NODE_WIDTH + GAP / 2
                    and ox < cx + dx + NODE_WIDTH + GAP / 2
                    and cy < oy + NODE_HEIGHT + GAP / 4
                    and oy < cy + NODE_HEIGHT + GAP / 4
                ):
                    return False
        return True

    for _ in range(count):
        while not clear(x - left):
            x += stride
        dx = x - left
        found.append((dx, 0.0))
        boxes.extend((cx + dx, cy) for cx, cy in column_xy)
        x += stride
    return found


def plan_copies(
    graph: Graph, source: Uri | str, targets: Sequence[Uri | str],
    members: Iterable[Uri | str], department: str,
) -> list[ColumnCopy]:
    """A :class:`ColumnCopy` of ``source``'s column for each of
    ``targets``, placed one after another at free spots to the right."""
    members = list(members)
    naming = _naming(graph, members)
    naming += [str(t) for t in targets if str(t) not in naming]
    found = columns(graph, members, department)
    nodes = found.get(str(source), [])
    if not nodes:
        return []
    in_column = set(nodes)
    by_path = {node.path: node for node in graph.nodes}
    owner = {path: key for key, paths in found.items() for path in paths}
    outside_inputs = tuple(
        (path, index, source_path)
        for path in nodes
        for index, source_path in enumerate(by_path[path].inputs)
        if source_path is not None and source_path not in in_column
    )
    collectors = []
    for node in graph.nodes:
        if node.path in in_column:
            continue
        fed_by = {owner[p] for p in node.inputs if p in owner}
        if len(fed_by) < 2 or str(source) not in fed_by:
            continue
        for p in node.inputs:
            if p in in_column:
                collectors.append((node.path, p))
    lefts = [
        min(by_path[p].x for p in paths) for paths in found.values() if paths
    ]
    offsets = free_offsets(
        [(node.x, node.y) for node in graph.nodes],
        [(by_path[p].x, by_path[p].y) for p in nodes],
        lefts, len(targets),
    )
    return [
        ColumnCopy(
            str(source), str(target), tuple(nodes),
            tuple(
                (path, renamed(by_path[path].name, source, target, naming))
                for path in nodes
            ),
            offset, outside_inputs, tuple(collectors),
        )
        for target, offset in zip(targets, offsets)
    ]


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------

def _is(names: Sequence) -> str:
    return 'is' if len(names) == 1 else 'are'


def strangers_prompt(
    multi: str, department: str, admission: Admission,
    covers: Sequence[str],
) -> str:
    """The question for exports pinned to shots (assets) that are not
    members of the Multi."""
    parts = []
    if admission.addable:
        names = listing(admission.addable)
        they = 'it' if len(admission.addable) == 1 else 'they'
        parts.append(
            f'This {department} workfile exports {names} for {multi}, '
            f'but {they} {_is(admission.addable)} not in {multi}.'
        )
        parts.append(f'Add {names} to {multi}?')
        their = 'its' if len(admission.addable) == 1 else 'their'
        covered = ', '.join(covers) if covers else 'none'
        parts.append(
            f'Joining a Multi makes its workfile theirs: once in {multi}, '
            f'{names} {_is(admission.addable)} worked on in '
            f"{multi}'s workfiles for every department it covers "
            f'({covered}), not in {their} own.'
        )
    if admission.refused:
        lines = '\n'.join(
            f'  {label(uri, [u for u, _ in admission.refused])}: {why}'
            for uri, why in admission.refused
        )
        kinds = {
            uri.segments[0] for uri in map(_parsed, (u for u, _ in admission.refused))
            if uri is not None and uri.segments
        }
        noun = 'assets' if kinds == {'assets'} else 'shots'
        lead = (
            'These cannot join' if admission.addable
            else f'This {department} workfile exports for {noun} not in {multi}, '
                 'and they cannot join'
        )
        parts.append(f'{lead}:\n{lines}')
    return '\n\n'.join(parts)


def missing_prompt(
    multi: str, department: str, missing: Sequence[str],
    source: str | None,
) -> str:
    """The question for members the workfile has no export for."""
    names = listing(missing)
    one = len(missing) == 1
    head = (
        f'{names} {_is(missing)} in {multi}, but this {department} workfile '
        f'has no export for {"it" if one else "them"}.'
    )
    graphs = 'its graph' if one else 'their graphs'
    if source is not None:
        how = (
            f"Each is a copy of {label(source)}'s graph, placed to the right "
            'and pinned to the new member.'
        )
    else:
        how = f'Each is built from the {department} template.'
    return f'{head}\n\nAdd {graphs}? {how}'


def in_sync_message(multi: str, department: str) -> str:
    return (
        f"This {department} workfile and {multi} agree: every export is "
        'for a member, and every member has one.'
    )


class Quiet:
    """The Multi workfiles the artist asked not to be asked about again
    this session, by ``(Multi URI, department)``."""

    def __init__(self) -> None:
        self._keys: set[tuple[str, str]] = set()

    def add(self, group_uri: Uri | str, department: str) -> None:
        self._keys.add((str(group_uri), department))

    def __contains__(self, key: tuple) -> bool:
        group_uri, department = key
        return (str(group_uri), department) in self._keys

    def clear(self) -> None:
        self._keys.clear()


def should_ask(
    difference: Difference, quiet: Quiet, group_uri: Uri | str,
    department: str, on_demand: bool,
) -> bool:
    """Whether a save (or Sync with Multi) asks: only when something
    differs, and after a save only for a workfile not silenced this
    session. Sync with Multi always asks, and says when nothing differs."""
    if on_demand:
        return True
    return not difference.empty and (group_uri, department) not in quiet
