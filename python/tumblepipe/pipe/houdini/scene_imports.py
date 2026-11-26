"""Re-execute the pipeline import nodes of the loaded scene.

Import nodes resolve their version when they execute and write the result
into their parms (a file path, a pinned ``&version=`` URI, rebuilt child
nodes). ``hou.hipFile.load`` restores those parms and runs nothing, so a
reopened scene shows whatever was imported when it was last saved — a model
published since then does not appear. :func:`refresh_scene_imports` re-runs
every import node so ``current``/``latest`` pick up the newest publish.

Only when something moved, though: re-running an import re-sets its parms,
and Houdini marks the scene changed for a ``Parm.set`` of the value it
already holds, so a launch with nothing new published opened the workfile
dirty and the first switch asked to save changes nobody made (QA ledger 51,
2026-11-10). After running the nodes the refresh keeps a fingerprint per
node of what it read: the version folders it listed, the config files it
loaded, the ``entity:`` URIs it resolved, and its own parms. The next
refresh compares them all first; when every node's fingerprint still holds,
nothing runs. When any one moved, every node runs, as before: an import
downstream of the moved one reads what it imports, which no fingerprint
holds. A spec marked ``independent`` (``th::import_render``) is the
exception: while only such nodes moved, only they run. A 22-shot Multi comp
on the share re-ran all 25 render imports (15-20 s) on every open, because
one shot had a new render or an import still failed (2026-11-26).

:data:`REFRESH_SPECS` is the list of node types that get re-run. HDAs that
choose a version but are refreshed through a node inside them, or that do
not import upstream publishes at all, are named in
:data:`REFRESHED_BY_INNER_NODE` and :data:`NOT_UPSTREAM_IMPORTS`;
``tests/test_scene_imports.py`` fails when a shipped HDA with a ``version``
parm is in none of the three. ``th::import_rig`` went unrefreshed for as
long as the list was written by hand.

A spec's ``batch`` holds work every execute of its type asks for and that
is the same for all of them until the sweep ends, so it runs once: each
``th::import_render`` re-checked every export_comp, which made opening a
Multi comp cost imports x exports x scene walks (NRI's 7 shots: 9 min 40 s,
2026-11-25; ``tests/test_export_refresh_held.py``).

When the refresh ran any node, its last step is Rig Sync
(``animate_sync.sync_all``): every ``th::import_rigs`` on Follow Stage in a
``th::animate`` makes its rows follow the stage the imports just rebuilt.
The imports' own sync is skipped while the deferred resolver refresh is
open (the stages are not settled yet), so it runs once, here. A refresh
that ran nothing leaves the stages, and so the rows, as they were.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportSpec:
    """One node type the refresh re-executes.

    ``wrapper`` is the ``"module:Class"`` Python wrapper whose ``execute()``
    re-imports.

    ``newest`` is the node's own name for "the newest publish" in its version
    menu: ``latest`` where it has one, ``current`` where that is the newest
    version on disk. ``rows`` names the multiparm count of a node that
    imports one entity per row, each row with its own ``version#`` parm.

    ``batch`` is the ``"module:function"`` context manager the refresh
    runs every node of the type inside, for work one execute asks for that
    is the same for all of them (once, at the end).

    ``independent`` marks a type whose nodes read nothing another import
    writes, and whose writes no import reads: a moved one re-runs alone,
    not with every node of the scene.
    """

    type_name: str
    context: str
    wrapper: str
    newest: str
    rows: Optional[str] = None
    batch: Optional[str] = None
    independent: bool = False


REFRESH_SPECS = (
    ImportSpec("import_shot", "Lop",
               "tumblepipe.pipe.houdini.lops.import_shot:ImportShot",
               newest="latest"),
    ImportSpec("import_assets", "Lop",
               "tumblepipe.pipe.houdini.lops.import_assets:ImportAssets",
               newest="latest", rows="asset_imports"),
    ImportSpec("import_asset", "Lop",
               "tumblepipe.pipe.houdini.lops.import_asset:ImportAsset",
               newest="latest"),
    ImportSpec("import_layer", "Lop",
               "tumblepipe.pipe.houdini.lops.import_layer:ImportLayer",
               newest="current"),
    ImportSpec("import_rigs", "Sop",
               "tumblepipe.pipe.houdini.sops.import_rigs:ImportRigs",
               newest="latest", rows="rig_imports"),
    ImportSpec("import_rig", "Sop",
               "tumblepipe.pipe.houdini.sops.import_rig:ImportRig",
               newest="latest"),
    # Layout in Animate: each row loads an asset's published rig; Build
    # (execute) loads the newest one again. The keys live in the APEX
    # Animate LOP after it, so a rebuild keeps them.
    ImportSpec("layout_rigs", "Lop",
               "tumblepipe.pipe.houdini.lops.layout_rigs:LayoutRigs",
               newest="latest", rows="rows"),
    # Run the outer HDA, not only its inner import_layer: its execute()
    # copies the version label and bypass state up from the inner node.
    ImportSpec("import_model", "Sop",
               "tumblepipe.pipe.houdini.sops.import_model:ImportModel",
               newest="current"),
    # A comp's renders: on `latest` it moves to the newest complete render
    # when the workfile opens; a composite submit freezes it in the job.
    # Each execute re-checks every export_comp: held to one re-check for
    # the batch (NRI's 7-shot Multi comp: 9 min 40 s to open).
    ImportSpec("import_render", "Cop",
               "tumblepipe.pipe.houdini.cops.import_render:ImportRender",
               newest="latest",
               batch="tumblepipe.pipe.houdini.cops.import_render:held_export_refresh",
               # A render import reads its shot's renders and config; no
               # import reads what it writes. A 22-shot Multi comp on the
               # share re-ran all 25 imports (15-20 s) on every open while
               # any one had moved or still failed (2026-11-26).
               independent=True),
)

# Expanded HDA dir name (without version) -> the REFRESH_SPECS type inside it
# that does the importing. The outer node keeps no state of its own: its
# labels are chs() references to the inner node.
REFRESHED_BY_INNER_NODE = {
    "sop_th.import_asset": "import_asset",
    "cop_th.import_lop_camera": "import_shot",
    "cop_th.import_scene": "import_shot",
}

# HDAs with a version parm that pick among caches the node wrote itself,
# not upstream publishes. Writing a cache pins the version just written.
NOT_UPSTREAM_IMPORTS = frozenset({
    "lop_th.cache",
    "sop_th.cache",
    "lop_th.sop_modify",
    # Each row's layout version is the animator's pin, never refreshed or
    # forced to the newest on a farm publish: a copy of layout's keys
    # changes only when the animator moves a row to a newer layout.
    "sop_th.layout_keys",
    # A texture import pins its set version; the node shows an update note
    # and the artist moves it with Update, never a scene refresh.
    "lop_th.import_texture",
})


def spec_for(type_name: str, context: str) -> Optional[ImportSpec]:
    """The spec for a node type, given ``hou.NodeType.name()`` and its
    category name (``th::import_rig::1.0``, ``Sop``)."""
    parts = type_name.lower().split("::")
    if len(parts) < 2 or parts[0] != "th":
        return None
    for spec in REFRESH_SPECS:
        if spec.type_name == parts[1] and spec.context == context:
            return spec
    return None


def outermost(paths: Iterable[str]) -> list[str]:
    """Drop every path that has an ancestor in *paths*, keeping order.

    An outer import node rebuilds or drives the import nodes inside it, so
    running the inner ones as well imports twice (import_assets deletes and
    recreates its children, which the sweep then ran a second time).
    """
    paths = list(dict.fromkeys(paths))
    present = set(paths)
    kept = []
    for path in paths:
        parts = path.rstrip("/").split("/")
        ancestors = ("/".join(parts[:i]) for i in range(2, len(parts)))
        if not any(a in present for a in ancestors):
            kept.append(path)
    return kept


def _wrapper_class(spec: ImportSpec):
    module_name, class_name = spec.wrapper.split(":")
    return getattr(importlib.import_module(module_name), class_name)


def batches(specs) -> list:
    """The ``batch`` context managers of ``specs``, once each, in order."""
    found = []
    for spec in specs:
        if spec.batch is not None and spec.batch not in found:
            found.append(spec.batch)
    managers = []
    for name in found:
        module_name, function_name = name.split(":")
        managers.append(getattr(importlib.import_module(module_name), function_name)())
    return managers


def execute(native, spec: ImportSpec) -> None:
    """Run a node's import action, raising on failure."""
    _wrapper_class(spec)(native).execute()


def force_newest(native, spec: ImportSpec) -> None:
    """Point a node (every row of a multi-row node) at its newest publish.

    Sets the parm directly: the wrappers' ``set_version_name`` ignores a
    name missing from the version menu, and a node inside an HDA may hold a
    channel reference, which ``Parm.set`` follows to the outer parm.
    """
    if spec.rows is None:
        native.parm("version").set(spec.newest)
        return
    for index in range(1, native.parm(spec.rows).eval() + 1):
        native.parm(f"version{index}").set(spec.newest)



def find_import_nodes() -> list:
    """Every refreshable import node in the scene, outermost only."""
    import hou

    matched = {}
    for native in hou.node("/").allSubChildren():
        spec = spec_for(native.type().name(), native.type().category().name())
        if spec is not None:
            matched[native.path()] = (native, spec)
    return [matched[path] for path in outermost(matched)]


# Root-node user data: {node path: fingerprint} as JSON, saved with the hip.
# User data set to the value it holds does not mark the scene changed.
FINGERPRINTS_KEY = "th_import_refresh"
_FINGERPRINT_VERSION = 1


def _parms_digest(native) -> str:
    """A digest of a node's parms as authored (expressions, not values)."""
    digest = hashlib.sha1()
    parms = getattr(native, "parms", None)
    for parm in (parms() if parms is not None else ()):
        try:
            raw = parm.rawValue()
        except Exception:
            raw = ""
        digest.update(f"{parm.name()}={raw}\n".encode("utf-8", "replace"))
    return digest.hexdigest()


def fingerprint(native, versions: dict, purposes, resolves: dict,
                config) -> dict:
    """What a node read while it ran, as JSON-ready data: version reads
    (``recorded_version_reads``), config purposes with their stamps now,
    entity URI resolves (``recorded_resolves``), its parms digest, and the
    resolver latest mode it left set."""
    from tumblepipe import resolver

    return {
        "v": _FINGERPRINT_VERSION,
        "parms": _parms_digest(native),
        "versions": sorted([kind, folder, value]
                           for (kind, folder), value in versions.items()),
        "config": sorted([purpose, list(config.loaded_stamp(purpose) or ())]
                         for purpose in purposes),
        "resolves": sorted([uri, latest, value]
                           for (uri, latest), value in resolves.items()),
        "latest_mode": resolver.get_latest_mode(),
    }


def fingerprint_holds(native, stored, config) -> bool:
    """Whether everything a stored fingerprint read still reads the same."""
    from tumblepipe import resolver
    from tumblepipe.pipe.paths.version import read_version_listing

    if not isinstance(stored, dict) or stored.get("v") != _FINGERPRINT_VERSION:
        return False
    try:
        if stored["parms"] != _parms_digest(native):
            return False
        for purpose, stamp in stored["config"]:
            if list(config.loaded_stamp(purpose) or ()) != stamp:
                return False
        for kind, folder, value in stored["versions"]:
            if read_version_listing(kind, folder) != value:
                return False
        for uri, latest, value in stored["resolves"]:
            if resolver.resolve_as(uri, latest) != value:
                return False
    except Exception:
        logger.debug("Import refresh: fingerprint check of %s failed",
                     native.path(), exc_info=True)
        return False
    return True


def _root():
    import hou

    return hou.node("/")


def _load_fingerprints(root) -> dict:
    user_data = getattr(root, "userData", None)
    raw = user_data(FINGERPRINTS_KEY) if user_data is not None else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _store_fingerprints(root, fingerprints: dict) -> None:
    set_user_data = getattr(root, "setUserData", None)
    if set_user_data is not None:
        set_user_data(FINGERPRINTS_KEY,
                      json.dumps(fingerprints, sort_keys=True))


def refresh_scene_imports(force: bool = False) -> tuple[int, int]:
    """Re-execute the loaded scene's import nodes when anything they read
    has moved. Returns ``(executed, failed)``; ``(0, 0)`` when nothing
    moved and nothing ran (the scene is left unchanged).

    ``force`` runs them whatever the fingerprints say (the artist's Update
    Imports). One bad node (stale HDA, missing export) is logged and
    counted, not raised, so the rest of the scene still refreshes; it keeps
    no fingerprint, so the next refresh runs it again (alone when it is
    ``independent``, see :func:`moved_nodes`). The caller owns the
    update mode: run this under ``util.update_mode(hou.updateMode.Manual)``
    in a GUI session so the re-imports don't each cook the graph.
    """
    from tumblepipe import resolver
    from tumblepipe.api import api
    from tumblepipe.pipe.paths import cached_version_listings
    from tumblepipe.pipe.paths.render import cached_render_reads

    executed = 0
    failed = 0
    # Each LOP import requests a resolver refresh over every loaded layer;
    # defer them so the batch costs one refresh at the end. The coherent
    # config scope stamps each config file once for the batch instead of
    # once per read, and the listing cache reads each version folder once:
    # a 26-node Multi on an SMB share made 7,080 stat() calls (42 s)
    # without them. The render reads cache does the same for the render
    # imports' context.json and AOV folders.
    with (resolver.deferred_refresh(),
          api.config.coherent(),
          cached_version_listings(),
          cached_render_reads()):
        try:
            nodes = find_import_nodes()
        except Exception:
            logger.exception("Import refresh: listing import nodes failed")
            return (0, 1)
        root = _root()
        stored = _load_fingerprints(root)
        holds = {} if force else {
            native.path(): fingerprint_holds(
                native, stored.get(native.path()), api.config)
            for native, _ in nodes
        }
        if nodes and not force and all(holds.values()):
            # Leave the resolver mode as the last import would have.
            resolver.set_latest_mode(
                bool(stored[nodes[-1][0].path()]["latest_mode"]))
            logger.info(
                "Import refresh: %d import node(s), nothing moved", len(nodes))
            return (0, 0)
        run = moved_nodes(nodes, holds, force)
        # The nodes left alone keep what they read last time.
        ran = {native.path() for native, _ in run}
        fingerprints = {
            native.path(): stored[native.path()]
            for native, _ in nodes if native.path() not in ran
        }
        held = ExitStack()
        try:
            for manager in batches(spec for _, spec in run):
                held.enter_context(manager)
        except Exception:
            logger.exception("Import refresh: holding batch work failed")
        with held:
            executed, failed = _run_all(run, fingerprints)
        if nodes and nodes[-1][0].path() not in ran:
            resolver.set_latest_mode(
                bool(stored[nodes[-1][0].path()]["latest_mode"]))
        if stored or fingerprints:
            _store_fingerprints(root, fingerprints)

    if executed:
        # The resolver refresh has run: rig rows on Follow Stage follow the
        # stage the imports made (they skip syncing while it is deferred).
        try:
            from tumblepipe.pipe.houdini import animate_sync
            animate_sync.sync_all()
        except Exception:
            logger.exception("Import refresh: syncing rig rows failed")
    if executed or failed:
        logger.info(
            "Import refresh: re-executed %d import node(s), %d failed",
            executed, failed,
        )
    return (executed, failed)


def moved_nodes(nodes, holds: dict, force: bool = False) -> list:
    """The ``(native, spec)`` of ``nodes`` a refresh runs, in order.

    ``holds`` maps a node path to whether its fingerprint still holds.
    Every node when ``force``, or when a node that is not ``independent``
    moved (an import downstream of it reads what it imports, which no
    fingerprint holds). Otherwise only the moved ones: each ``independent``
    node reads nothing another import writes.
    """
    if force or any(not holds.get(native.path(), False) and not spec.independent
                    for native, spec in nodes):
        return list(nodes)
    return [(native, spec) for native, spec in nodes
            if not holds.get(native.path(), False)]


def _run_all(nodes, fingerprints: dict) -> tuple[int, int]:
    """Execute each of ``nodes`` (``(native, spec)``), recording its
    fingerprint into ``fingerprints``; ``(executed, failed)``."""
    from tumblepipe import resolver
    from tumblepipe.api import api
    from tumblepipe.pipe.paths.version import recorded_version_reads

    executed = 0
    failed = 0
    for native, spec in nodes:
        try:
            with (recorded_version_reads() as versions,
                  api.config.recorded_reads() as purposes,
                  resolver.recorded_resolves() as resolves):
                execute(native, spec)
            executed += 1
        except Exception:
            logger.exception(
                "Import refresh: executing %s failed", native.path(),
            )
            failed += 1
            continue
        try:
            fingerprints[native.path()] = fingerprint(
                native, versions, purposes, resolves, api.config)
        except Exception:
            logger.debug("Import refresh: fingerprinting %s failed",
                         native.path(), exc_info=True)
    return (executed, failed)
