"""Re-execute the pipeline import nodes of the loaded scene.

Import nodes resolve their version when they execute and write the result
into their parms (a file path, a pinned ``&version=`` URI, rebuilt child
nodes). ``hou.hipFile.load`` restores those parms and runs nothing, so a
reopened scene shows whatever was imported when it was last saved — a model
published since then does not appear. :func:`refresh_scene_imports` re-runs
every import node so ``current``/``latest`` pick up the newest publish.

:data:`REFRESH_SPECS` is the list of node types that get re-run. HDAs that
choose a version but are refreshed through a node inside them, or that do
not import upstream publishes at all, are named in
:data:`REFRESHED_BY_INNER_NODE` and :data:`NOT_UPSTREAM_IMPORTS`;
``tests/test_scene_imports.py`` fails when a shipped HDA with a ``version``
parm is in none of the three. ``th::import_rig`` went unrefreshed for as
long as the list was written by hand.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportSpec:
    """One node type the refresh re-executes.

    ``wrapper`` is ``"module:Class"`` for a node with a Python wrapper whose
    ``execute()`` re-imports. ``None`` means the HDA's own PythonModule
    ``execute(node)`` is the import action.

    ``newest`` is the node's own name for "the newest publish" in its version
    menu: ``latest`` where it has one, ``current`` where that is the newest
    version on disk. ``rows`` names the multiparm count of a node that
    imports one entity per row, each row with its own ``version#`` parm.
    """

    type_name: str
    context: str
    wrapper: Optional[str]
    newest: str
    rows: Optional[str] = None


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
    # Run the outer HDA, not only its inner import_layer: its execute()
    # copies the version label and bypass state up from the inner node.
    ImportSpec("import_model", "Sop", None, newest="current"),
)

# Expanded HDA dir name (without version) -> the REFRESH_SPECS type inside it
# that does the importing. The outer node keeps no state of its own: its
# labels are chs() references to the inner node.
REFRESHED_BY_INNER_NODE = {
    "sop_th.import_asset": "import_asset",
    "cop_th.import_lop_camera": "import_shot",
}

# HDAs with a version parm that pick among caches the node wrote itself,
# not upstream publishes. Writing a cache pins the version just written.
NOT_UPSTREAM_IMPORTS = frozenset({
    "lop_th.cache",
    "sop_th.cache",
    "lop_th.sop_modify",
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


def execute(native, spec: ImportSpec) -> None:
    """Run a node's import action, raising on failure."""
    if spec.wrapper is None:
        native.hdaModule().execute(native)
    else:
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


def _category_name(native) -> Optional[str]:
    try:
        return native.type().category().name()
    except Exception:
        return None


def find_import_nodes() -> list:
    """Every refreshable import node in the scene, outermost only."""
    import hou

    matched = {}
    for native in hou.node("/").allSubChildren():
        spec = spec_for(native.type().name(), _category_name(native) or "")
        if spec is not None:
            matched[native.path()] = (native, spec)
    return [matched[path] for path in outermost(matched)]


def refresh_scene_imports() -> tuple[int, int]:
    """Re-execute the loaded scene's import nodes. Returns ``(executed,
    failed)``.

    One bad node (stale HDA, missing export) is logged and counted, not
    raised, so the rest of the scene still refreshes. The caller owns the
    update mode: run this under ``util.update_mode(hou.updateMode.Manual)``
    in a GUI session so the re-imports don't each cook the graph.
    """
    from tumblepipe import resolver

    executed = 0
    failed = 0
    # Each LOP import requests a resolver refresh over every loaded layer;
    # defer them so the batch costs one refresh at the end.
    with resolver.deferred_refresh():
        try:
            nodes = find_import_nodes()
        except Exception:
            logger.exception("Import refresh: listing import nodes failed")
            return (0, 1)
        for native, spec in nodes:
            try:
                execute(native, spec)
                executed += 1
            except Exception:
                logger.exception(
                    "Import refresh: executing %s failed", native.path(),
                )
                failed += 1

    if executed or failed:
        logger.info(
            "Import refresh: re-executed %d import node(s), %d failed",
            executed, failed,
        )
    return (executed, failed)
