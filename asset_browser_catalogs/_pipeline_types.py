"""Value types and constants for the Pipeline catalog.

Extracted from ``pipeline.py`` so the catalog file focuses on behavior
(the :class:`PipelineCatalog` ABC subclass) rather than data shapes.
Imported back into ``pipeline.py`` and re-exported for any internal
caller that historically pulled from there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TypedDict

from asset_browser.api.errors import CatalogInitError
from asset_browser.api.types import Collection


# ── Asset metadata schema ────────────────────────────────────────


class PipelineAssetMetadata(TypedDict, total=False):
    """Metadata schema for assets / shots produced by PipelineCatalog.

    All fields are optional (``total=False``) — different code paths
    populate different subsets:

    - Discovery (``_discover_assets`` / ``_discover_shots``) sets
      ``departments``, ``dept_count``, ``has_sub_cards``,
      ``latest_update``, plus ``category`` (assets) or ``sequence``
      (shots), and ``project``.
    - ``get_detail`` adds ``frame_start``, ``frame_end``, ``frame_total``,
      ``fps`` for shots, and ``variants`` for assets.
    - ``get_assets`` overlays ``is_current_scene`` for the asset whose
      hip file is open in Houdini.

    Pipeline assets are stored as ``Asset.metadata: dict[str, Any]`` on
    the wire — this TypedDict is for static type checking and as a
    discoverability aid; it documents which keys catalogs and
    consumers can read without typo risk.
    """

    # Identification
    project: str
    category: str        # assets only
    sequence: str        # shots only

    # Department deck (sub-cards)
    departments: dict[str, list[str]]  # dept name → list of "vNNNN" version labels
    dept_count: int
    has_sub_cards: bool

    # Sort / display hints
    latest_update: float  # epoch seconds of newest dept version
    is_current_scene: bool  # pin + highlight (set per-render in get_assets)

    # Shot timeline (shots only)
    frame_start: int
    frame_end: int
    frame_total: int
    fps: int

    # Variants (assets only)
    variants: list[str]


# ── Per-project Client lifecycle ─────────────────────────────────


class ClientState(Enum):
    """Lifecycle of a per-project Client.

    UNTRIED            — never built; next access will attempt construction.
    READY              — Client constructed; reuse without retry.
    FAILED_TRANSIENT   — last attempt raised; will retry on next access.
                         Used for resource issues that may resolve themselves
                         (network, SMB timeout, momentary IO failure).
    FAILED_PERMANENT   — last attempt raised an unrecoverable error
                         (missing env var, missing config dir, ImportError).
                         Won't retry until the project is re-registered or
                         caches are reset explicitly.
    """

    UNTRIED = auto()
    READY = auto()
    FAILED_TRANSIENT = auto()
    FAILED_PERMANENT = auto()


@dataclass
class ClientSlot:
    """Per-project Client state.

    Replaces the historical ``_clients`` dict + ``_init_attempted`` set,
    which conflated "not tried", "transient failure" and "permanent
    failure" into the same silent-None outcome.
    """

    state: ClientState = ClientState.UNTRIED
    client: object | None = None  # tumblepipe.api.Client when READY
    error: CatalogInitError | None = None
    last_attempt: float = 0.0


# Errors that are unrecoverable without project re-registration or
# re-installation. Anything else is treated as transient (retried on
# next access) so a flaky network share doesn't permanently disable a
# project until Houdini restart.
PERMANENT_INIT_ERRORS: tuple[type[BaseException], ...] = (
    ImportError, KeyError, FileNotFoundError, NotADirectoryError,
)


# ── Asset id ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class AssetId:
    """Parsed 3-segment asset id of the form ``"PROJECT/SECOND/THIRD"``.

    The same shape encodes both assets and shots:

    - Assets: ``project / category / name``  (e.g. ``"growth/Heroes/Knight"``)
    - Shots:  ``project / sequence / shot``  (e.g. ``"growth/SEQ010/SH020"``)

    The disambiguation between asset and shot requires looking up the
    project's category list — see ``PipelineCatalog._uri_for_asset_id``.
    The fields are named ``second`` and ``third`` (rather than
    ``category`` / ``sequence``) so the type doesn't lie about a
    semantic it can't determine on its own. Use
    :attr:`as_category` / :attr:`as_sequence` when context is known.
    """

    project: str
    second: str
    third: str

    @classmethod
    def parse(cls, asset_id: str) -> AssetId | None:
        """Parse a string id; return ``None`` for malformed input."""
        if not asset_id:
            return None
        parts = asset_id.split("/", 2)
        if len(parts) != 3:
            return None
        return cls(parts[0], parts[1], parts[2])

    def __str__(self) -> str:
        return f"{self.project}/{self.second}/{self.third}"

    def __iter__(self):
        # Allow ``project, second, third = aid`` unpacking — the
        # historical tuple shape that callers already use.
        yield self.project
        yield self.second
        yield self.third

    def __getitem__(self, index: int) -> str:
        # Allow ``aid[0]`` for legacy call sites; field access
        # (``aid.project``) is preferred for new code.
        return (self.project, self.second, self.third)[index]

    @property
    def as_category(self) -> str:
        """Read ``second`` as an asset category (caller knows the kind)."""
        return self.second

    @property
    def as_sequence(self) -> str:
        """Read ``second`` as a shot sequence (caller knows the kind)."""
        return self.second

    @property
    def name(self) -> str:
        """Read ``third`` as the asset/shot name."""
        return self.third


# ── Department iconography ───────────────────────────────────────


# Lucide icon names per department — used by Shot/Asset department
# sub-cards. Add new depts here and they pick up consistent
# iconography.
DEPT_ICONS = {
    "model": "shapes",          # 3D shapes
    "lookdev": "palette",        # color palette for shading
    "rig": "bone",              # skeleton/rigging
    "animation": "move-3d",     # 3D movement
    "layout": "grid-3x3",       # scene layout
    "render": "camera",         # camera/render
    "light": "lamp",            # lighting
    "cfx": "sparkles",          # cloth/hair FX
    "composite": "layers",      # compositing layers
    "effects": "zap",           # VFX
    "blendshape": "blend",      # blending shapes
    "environment": "mountain",  # environment/landscape
    "crowd": "users",           # crowd of people
}

# Shot department subcards use a stagier, scene-centric iconography
# (boxes for blocking, drama mask for performance, etc.) so they read
# differently from asset subcards at a glance.
SHOT_DEPT_ICONS = {
    "layout":      "boxes",
    "environment": "trees",
    "animation":   "drama",
    "effects":     "flame",
    "light":       "lightbulb",
    "render":      "camera",
}

DEPT_SHORT_NAMES = {
    "animation": "Anim",
    "blendshape": "Blend",
    "composite": "Comp",
    "environment": "Enviro",
}


# ── Module-level helpers ─────────────────────────────────────────


def cascade_counts(col: Collection) -> Collection:
    """Return a new Collection whose count is the sum of all descendant counts."""
    import dataclasses
    if not col.children:
        return col
    cascaded_children = tuple(cascade_counts(c) for c in col.children)
    total = sum(c.count for c in cascaded_children)
    return dataclasses.replace(col, children=cascaded_children, count=total)


def projects_json_path() -> Path:
    """Resolve the on-disk location of ``projects.json`` using the
    same conventions as ``asset_browser._config_dir``."""
    houdini_pref = os.environ.get("HOUDINI_USER_PREF_DIR")
    if houdini_pref:
        return Path(houdini_pref) / "asset_browser" / "projects.json"
    return Path.home() / ".config" / "asset_browser" / "projects.json"
