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
from typing import ClassVar, Iterable, Literal, TypedDict, Union

from tumbletrove.asset_browser.api.errors import CatalogInitError
from tumbletrove.asset_browser.api.types import Collection


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
      ``variants`` for assets.
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


# ── Entity refs (assets vs shots) ────────────────────────────────


@dataclass(frozen=True)
class AssetRef:
    """A parsed asset id of the form ``"PROJECT/CATEGORY/Name"``."""

    project: str
    category: str
    name: str

    kind: ClassVar[Literal["assets"]] = "assets"

    def __str__(self) -> str:
        return f"{self.project}/{self.category}/{self.name}"


@dataclass(frozen=True)
class ShotRef:
    """A parsed shot id of the form ``"PROJECT/SEQUENCE/Shot"``."""

    project: str
    sequence: str
    shot: str

    kind: ClassVar[Literal["shots"]] = "shots"

    def __str__(self) -> str:
        return f"{self.project}/{self.sequence}/{self.shot}"


EntityRef = Union[AssetRef, ShotRef]


def parse_entity_ref(
    asset_id: str, categories: Iterable[str],
) -> EntityRef | None:
    """Parse ``"PROJECT/SECOND/THIRD"`` into an :class:`AssetRef` or
    :class:`ShotRef`, using ``categories`` (the project's category list)
    to disambiguate.

    When ``categories`` is empty (e.g. the project's Client is not
    READY), the id is classified as a shot — same behavior as the
    legacy code path. Callers that need correctness in that case must
    ensure the Client is ready before calling.
    """
    if not asset_id:
        return None
    # split() with no maxsplit (not split("/", 2)): a 4-segment id like
    # "a/b/c/d" must be rejected, not glued into third="c/d".
    parts = asset_id.split("/")
    if len(parts) != 3:
        return None
    project, second, third = parts
    if second in set(categories):
        return AssetRef(project, second, third)
    return ShotRef(project, second, third)


def parse_project_name(asset_id: str) -> str | None:
    """Extract just the project name from a 3-segment asset id."""
    if not asset_id:
        return None
    parts = asset_id.split("/")
    if len(parts) != 3:
        return None
    return parts[0]


def entity_base_dir(ref: EntityRef, root: Path) -> Path:
    """Return the on-disk base directory for ``ref`` under ``root``."""
    if isinstance(ref, AssetRef):
        return root / "assets" / ref.category / ref.name
    return root / "shots" / ref.sequence / ref.shot


def entity_uri_tail(ref: EntityRef) -> str:
    """Return the URI tail after ``entity:/``, e.g. ``"assets/Heroes/Knight"``."""
    if isinstance(ref, AssetRef):
        return f"assets/{ref.category}/{ref.name}"
    return f"shots/{ref.sequence}/{ref.shot}"


# ── Workfile scanning ────────────────────────────────────────────


# Workfile filenames follow ``{prefix}_{version}.hip[nc|lc]``. We
# extract the trailing ``vNNNN`` token from the stem; this matches the
# convention used by ``tumblepipe.pipe.paths.next_hip_file_path``.
WORKFILE_GLOB = "*.hip*"


def version_code(label: str) -> int:
    """Numeric code of a ``vNNNN`` version label (``version_code('v0007') == 7``)."""
    return int(label[1:])


def latest_version(labels: Iterable[str]) -> str | None:
    """The version label with the highest numeric code, or ``None`` if empty.

    Use instead of ``sorted(labels)[-1]``: labels can exceed four digits
    (``v10000``), where a lexical sort wrongly ranks ``v9999`` last.
    """
    labels = list(labels)
    if not labels:
        return None
    return max(labels, key=version_code)


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
    versions: set[str] = set()
    newest = 0.0
    try:
        entries = list(os.scandir(dept_dir))
    except OSError:
        return [], 0.0
    for entry in entries:
        if not fnmatch.fnmatch(entry.name, WORKFILE_GLOB):
            continue
        try:
            if not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime > newest:
            newest = mtime
        tail = Path(entry.name).stem.rsplit("_", 1)
        if (
            len(tail) == 2
            and tail[1].startswith("v")
            and tail[1][1:].isdigit()
        ):
            versions.add(tail[1])
    # Sort by numeric code, not lexically: version labels can exceed 4
    # digits (v10000), and every consumer takes [-1] as "latest" — a
    # lexical sort would rank v9999 after v10000 and open a stale workfile.
    return sorted(versions, key=version_code), newest


def workfile_for_version(dept_dir: Path, version: str) -> Path | None:
    """Return the .hip whose filename trails with ``_{version}``."""
    if not dept_dir.exists():
        return None
    for hip in dept_dir.glob(WORKFILE_GLOB):
        stem = hip.stem
        tail = stem.rsplit("_", 1)
        if len(tail) == 2 and tail[1] == version:
            return hip
    return None


def latest_workfile(dept_dir: Path) -> Path | None:
    """Return the newest .hip in *dept_dir* by mtime, or ``None``."""
    if not dept_dir.exists():
        return None
    hip_files = sorted(
        dept_dir.glob(WORKFILE_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return hip_files[0] if hip_files else None


# ── Framework capability probes ──────────────────────────────────


def _type_supports(type_name: str, field_name: str) -> bool:
    """Whether the installed tumbletrove's *type_name* has *field_name*.

    hpm declares no dependency between this package and tumbletrove —
    the coupling is a convention (ship tumbletrove first) and a comment.
    That is survivable for a field the framework *reads*, which an older
    version simply ignores. It is not survivable for one we *pass*: these
    are frozen dataclasses, so an unknown keyword is a ``TypeError`` at
    construction. For ``SessionInfo`` that means a studio on an older
    tumbletrove (whose ``SessionInfo`` still takes ``rows``, not
    ``sections``) would crash ``get_session`` every scope/hip change; the
    probe lets it degrade to an empty session pane instead.
    """
    try:
        import dataclasses
        import importlib
        types = importlib.import_module(
            "tumbletrove.asset_browser.api.types"
        )
        cls = getattr(types, type_name, None)
        if cls is None:
            return False
        return any(
            f.name == field_name for f in dataclasses.fields(cls)
        )
    except Exception:
        return False


#: tumbletrove >= 0.22 (SessionInfo carries ``sections`` of SessionSection
#: rather than ``rows`` of DeckItem). Below this, ``get_session`` must
#: return ``None`` — building a ``sections=`` SessionInfo against the old
#: rows-based type is a ``TypeError`` on a frozen dataclass.
SESSION_HAS_SECTIONS = _type_supports("SessionInfo", "sections")


# ── Workfile licence labels ──────────────────────────────────────


# Human label per workfile extension. The extension *is* the licence the
# file was saved under, and mixing them has consequences a filename does
# not advertise — an .hipnc cannot be opened commercially, and saving a
# commercial scene over an Indie licence silently downgrades it. The
# session panel surfaces this as the "License" field of its Current
# Workspace block so the fact is visible at a glance.
#
# (This replaces the per-extension badge the retired Project Browser and
# the earlier session panel used; the colour vocabulary went with the
# badge, the fact it conveyed did not.)
EXTENSION_LICENCES = {
    "hip": "Commercial",
    "hiplc": "Indie",
    "hipnc": "Non-commercial",
}


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
