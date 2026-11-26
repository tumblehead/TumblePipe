"""What a card id names, parsed once.

The browser hands every catalog hook a card by its id. An id is one of:

- a library card (recipe, texture set, edit): ``<kind>:<owner>:<path>``,
  answered by its :class:`~.library_kinds.LibraryKind`;
- a package node card: ``node:<context>/<type>`` (``nodes``);
- a Multi or Root container card: ``group:<project>:<path>`` /
  ``scene:<project>:<path>`` (``containers``);
- an entity card (an asset or a shot): any other id, the
  ``<project>/<category>/<name>`` of ``uris.asset_id_for``.

:func:`card_ref` turns an id into one :data:`CardRef`, and each hook matches
on it, so every hook routes an id the same way. An id with a known kind's
prefix that does not parse as that kind raises
:class:`~.uris.MalformedCardId` in every hook alike: the catalog mints
every card id, so one is a bug, not an entity to look up.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import nodes
from .containers import GroupContainer, SceneContainer
from . import containers
from .library_kinds import LibraryKind, LibraryKinds


@dataclass(frozen=True)
class LibraryCard:
    """A recipe, texture set or edit card, and the kind that answers for it."""

    kind: LibraryKind
    id: str


@dataclass(frozen=True)
class NodeCard:
    """A package node card."""

    id: str


@dataclass(frozen=True)
class EntityCard:
    """An asset or shot card."""

    id: str


CardRef = LibraryCard | NodeCard | GroupContainer | SceneContainer | EntityCard


def card_ref(asset_id: str, kinds: LibraryKinds) -> CardRef:
    """What *asset_id* names, the library kinds being *kinds*. Raises
    :class:`~.uris.MalformedCardId` for an id with a container's or a
    library kind's prefix that does not parse as one."""
    container = containers.parse(asset_id)
    if container is not None:
        return container
    kind = kinds.for_id(asset_id)
    if kind is not None:
        return LibraryCard(kind, asset_id)
    if nodes.is_node_id(asset_id):
        return NodeCard(asset_id)
    return EntityCard(asset_id)
