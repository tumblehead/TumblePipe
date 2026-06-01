"""Group / Scene container abstraction.

Replaces the ``if kind == "group" ... elif kind == "scene"`` branching
that used to be sprinkled across :class:`PipelineCatalog`. A
``collection_id`` of the form ``"group:<project>:<path>"`` or
``"scene:<project>:<path>"`` parses into a :class:`GroupContainer`
or :class:`SceneContainer` via :meth:`ContainerRef.parse`. Catalog
callsites then dispatch on type instead of re-stringifying.

The parsed refs carry exactly the URI helpers and identifying fields
the catalog used to compute inline:

- ``ref.uri`` — the ``groups:/<path>`` / ``scenes:/<path>`` URI.
- ``ref.context`` (group only) — the leading segment (``"shots"`` /
  ``"assets"``) used to scope member entity URIs.
- ``ref.collection_id`` — the canonical string form, round-tripping
  through :meth:`ContainerRef.parse`.

Behaviour (members, departments, USD export) still lives on the
catalog for now; this module is the typed boundary the catalog
dispatches across.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import _pipeline_uris as uris

if TYPE_CHECKING:
    from tumblepipe.util.uri import Uri


@dataclass(frozen=True)
class GroupContainer:
    """A Multi (group). JSON-backed members list + per-context
    department coverage. Always belongs to one project."""

    project_name: str
    path: str

    kind: str = "group"

    @property
    def collection_id(self) -> str:
        return f"group:{self.project_name}:{self.path}"

    @property
    def uri(self) -> "Uri":
        return uris.group(self.path)

    @property
    def context(self) -> str:
        """``"shots"`` or ``"assets"`` — the first path segment. The
        single-segment form (no slash) is treated as ``"assets"``; the
        legacy ``_get_member_groups_for_project`` did the same thing
        and we preserve it so existing JSON keeps round-tripping."""
        return self.path.split("/", 1)[0] if "/" in self.path else "assets"


@dataclass(frozen=True)
class SceneContainer:
    """A Root (scene). Owns an asset list and acts as a shot's
    ``scene_ref`` destination."""

    project_name: str
    path: str

    kind: str = "scene"

    @property
    def collection_id(self) -> str:
        return f"scene:{self.project_name}:{self.path}"

    @property
    def uri(self) -> "Uri":
        return uris.scene(self.path)


ContainerRef = GroupContainer | SceneContainer


def parse(collection_id: str) -> ContainerRef | None:
    """Parse ``"<kind>:<project>:<path>"`` into the typed container.

    Returns ``None`` for empty input, missing prefix, or missing
    ``project:path`` split. Unknown kinds also return ``None`` —
    callers can use this as a single test for "is this a recognised
    container id at all".
    """
    if not collection_id:
        return None
    if collection_id.startswith("group:"):
        rest = collection_id[len("group:"):]
        if ":" not in rest:
            return None
        proj_name, path = rest.split(":", 1)
        return GroupContainer(proj_name, path)
    if collection_id.startswith("scene:"):
        rest = collection_id[len("scene:"):]
        if ":" not in rest:
            return None
        proj_name, path = rest.split(":", 1)
        return SceneContainer(proj_name, path)
    return None
