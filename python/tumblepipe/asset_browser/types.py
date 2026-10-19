"""Value types and constants for the Pipeline catalog.

Extracted from ``catalog.py`` so the catalog file focuses on behavior
(the :class:`PipelineCatalog` ABC subclass) rather than data shapes.
Imported back into ``catalog.py`` and re-exported for any internal
caller that historically pulled from there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Iterable, TypedDict

from tumbletrove.asset_browser.api.errors import CatalogInitError
from tumbletrove.asset_browser.api.types import Collection

from tumblepipe.naming import get_version_code


# ── Asset metadata schema ────────────────────────────────────────


class PipelineAssetMetadata(TypedDict, total=False):
    """Metadata schema for assets / shots produced by PipelineCatalog.

    All fields are optional (``total=False``) — different code paths
    populate different subsets:

    - Discovery (``_discover_entities`` → ``_build_asset_card`` /
      ``_build_shot_card``) sets ``departments``, ``dept_count``,
      ``has_deck_items``, ``latest_update``, plus ``category`` (assets)
      or ``sequence`` (shots), and ``project``.
    - ``get_detail`` adds ``department_versions``, plus ``frame_start``,
      ``frame_end``, ``frame_total``, ``fps`` for shots, and
      ``channels`` (the publish channels) for assets.
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

    # Department deck (deck items)
    departments: dict[str, str]  # dept name → latest "vNNNN" label
    dept_count: int
    has_deck_items: bool

    # Every workfile version per dept, for the detail panel's version
    # dropdowns. ``get_detail`` only — but note that ``departments``
    # above keeps its card shape even there: the browser turns an
    # AssetDetail into a grid card verbatim when it resolves a
    # favourite / shelf / collection ref, and the deck popup reads
    # ``departments`` off whichever it is handed. See ``latest_by_dept``.
    department_versions: dict[str, list[str]]

    # Sort / display hints
    latest_update: float  # epoch seconds of newest dept version
    last_user: str        # author of the newest dept version
    last_note: str        # that version's save note ("" if none/older file)
    is_current_scene: bool  # pin + highlight (set per-render in get_assets)

    # Shot timeline (shots only)
    frame_start: int
    frame_end: int
    frame_total: int
    fps: int

    # Publish channels (assets only). tumbletrove never names this key --
    # metadata is opaque payload to it, and the catalog rebuilds it on every
    # discovery, so nothing stored carries an older spelling.
    channels: list[str]


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


# ── Per-asset, per-dept version overrides ────────────────────────


class DeptVersionStore:
    """Session-only per-asset, per-department version overrides.

    The detail panel's dept-row combo boxes let users pick an older
    version to "view as"; that selection is stored here keyed by
    asset_id + dept_name. The store is intentionally not persisted —
    overrides are session scratch and cleared by asset refresh.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, dict[str, str]] = {}

    def set(self, asset_id: str, dept: str, version: str) -> None:
        self._overrides.setdefault(asset_id, {})[dept] = version

    def get(self, asset_id: str) -> dict[str, str]:
        return self._overrides.get(asset_id, {})

    def clear(self, asset_id: str) -> None:
        self._overrides.pop(asset_id, None)


# ── Workfile scanning ────────────────────────────────────────────


# Workfile filenames follow ``{prefix}_{version}.hip[nc|lc]``
# (``tumblepipe.pipe.paths.naming``).
WORKFILE_GLOB = "*.hip*"


def latest_version(labels: Iterable[str]) -> str | None:
    """The version label with the highest numeric code, or ``None`` if empty.

    Use instead of ``sorted(labels)[-1]``: labels can exceed four digits
    (``v10000``), where a lexical sort wrongly ranks ``v9999`` last.
    """
    labels = list(labels)
    if not labels:
        return None
    return max(labels, key=get_version_code)


def latest_by_dept(dept_versions: dict[str, list[str]]) -> dict[str, str]:
    """Collapse ``{dept: [versions]}`` to the card shape ``{dept: latest}``.

    Depts with no workfiles drop out, matching what the discovery card
    builders emit. Cards and details must agree on this shape: the
    browser turns an ``AssetDetail`` into a grid card verbatim for
    favourites / shelf / collection refs, and ``get_deck_items`` reads
    ``metadata["departments"]`` off whichever one it is handed.
    """
    out: dict[str, str] = {}
    for dept, versions in dept_versions.items():
        newest = latest_version(versions or ())
        if newest is not None:
            out[dept] = newest
    return out


def workfile_versions(dept_dir: Path) -> list[str]:
    """Return sorted ``vNNNN`` version labels parsed from .hip filenames."""
    return scan_workfiles(dept_dir)[0]


def scan_workfiles(dept_dir: Path) -> tuple[list[str], float]:
    """One directory pass: ``(sorted version labels, newest .hip mtime)``.

    The single ``os.scandir`` replaces the versions-glob + latest-lookup
    re-glob + per-file stat the card build used to do per department —
    on Windows the scandir entries carry their stat info for free, so
    the whole scan is one directory listing. Returns ``([], 0.0)`` for a
    missing directory.
    """
    import fnmatch
    from tumblepipe.pipe.paths.naming import is_versioned, version_of
    versions: set[str] = set()
    newest = 0.0
    try:
        entries = list(os.scandir(dept_dir))
    except FileNotFoundError:
        # No workfile in this department yet: its folder is made on the
        # first save. Any other OSError (an unreachable share) raises.
        return [], 0.0
    for entry in entries:
        if not fnmatch.fnmatch(entry.name, WORKFILE_GLOB):
            continue
        try:
            if not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
        except FileNotFoundError:
            # Deleted between the listing and this stat.
            continue
        if mtime > newest:
            newest = mtime
        if is_versioned(entry.name):
            versions.add(version_of(entry.name))
    # Sort by numeric code, not lexically: version labels can exceed 4
    # digits (v10000), and every consumer takes [-1] as "latest" — a
    # lexical sort would rank v9999 after v10000 and open a stale workfile.
    return sorted(versions, key=get_version_code), newest


def workfile_for_version(dept_dir: Path, version: str) -> Path | None:
    """Return the .hip whose filename trails with ``_{version}``."""
    from tumblepipe.pipe.paths.naming import is_versioned, version_of
    if not dept_dir.exists():
        return None
    for hip in dept_dir.glob(WORKFILE_GLOB):
        if is_versioned(hip) and version_of(hip) == version:
            return hip
    return None


# ── Department iconography ───────────────────────────────────────


# Lucide icon names per department — used by Shot/Asset department
# deck items. Add new depts here and they pick up consistent
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

# Lowercase 3-letter codes used as fallback for the API's
# ``Department.short`` field — drives the dept name column in the
# detail panel when the department config doesn't declare its own short.
# Matched case-insensitively on the dept name. Not the same convention
# as :data:`DEPT_SHORT_NAMES` above (which is PascalCase for DeckItem
# chips); the two coexist because they serve different label contexts.
DEPT_API_SHORT_FALLBACK = {
    "model": "mdl",
    "blendshape": "blndsp",
    "blendshapes": "blndsp",
    "lookdev": "lkd",
    "lighting": "lgt",
    "rig": "rig",
    "layout": "lay",
    "environment": "env",
    "animation": "ani",
    "crowd": "crwd",
    "fx": "fx",
    "cfx": "cfx",
    "render": "rnd",
    "comp": "cmp",
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
