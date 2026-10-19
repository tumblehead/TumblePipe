"""What th::export_layer checks and fixes up before a layer is published.

The export writes into a temp folder that is then copied to the version
location, so everything here is about the layer travelling as a folder:

- the stage must not have lost assets on the way (a prim that dropped its
  pipeline metadata, an import node whose asset never reached the stage);
- every composition arc must land inside the folder, in a versioned cache
  on shared storage (published by reference, pinned absolute), or be
  pulled into the folder; none may dangle.

Each check raises ``ExportLayerError`` with what to do about it.
"""

import logging
from pathlib import Path

import hou

from tumblepipe.api import path_str, api
from tumblepipe.util.uri import Uri

logger = logging.getLogger(__name__)


class ExportLayerError(Exception):
    """Raised when export_layer encounters a validation or execution error."""
    pass


# The cook errors quoted in an export failure are cut to this many characters:
# an APEX rig that fails to compile repeats its whole chain per sample.
_COOK_ERROR_LIMIT = 1500


def cook_errors(node) -> str:
    """The cook errors of ``node`` (and so of what feeds it), for a failure message."""
    try:
        errors = [e.strip() for e in node.errors() if e.strip()]
    except hou.Error:
        errors = []
    if not errors:
        return "No cook error was reported; look for the red node upstream."
    text = "\n".join(errors)
    if len(text) > _COOK_ERROR_LIMIT:
        text = text[:_COOK_ERROR_LIMIT] + " …"
    return "Cook error:\n" + text


def list_expected_asset_uris(native) -> set[Uri]:
    """Asset URIs that upstream import nodes placed on the stage.

    Walks the export node's input ancestors for the th:: import nodes so the
    export can cross-check the scraped metadata against what the graph says
    should be there. The sibling heuristic in
    prim_metadata.list_dropped_asset_prims cannot see a drop when a category
    holds no metadata-carrying asset at all (a single asset, or every asset
    dropped) - this expected-vs-scraped check can.

    Bypassed nodes and inline-mode imports (which deliberately bake their
    assets in without pipeline metadata) expect nothing. An import node
    whose entity does not resolve raises, as it would on its own Import.
    """
    from tumblepipe.pipe.houdini.lops import import_asset, import_assets, import_layer

    expected = set()
    for ancestor in native.inputAncestors():
        if ancestor.isBypassed():
            continue
        if ancestor.type().nameComponents()[1] != 'th':
            continue
        match ancestor.type().nameComponents()[2]:
            case 'import_asset':
                node = import_asset.ImportAsset(ancestor)
                if node.get_import_mode() == 'inline':
                    continue
                # Imported: the node resolved a staged file
                if not ancestor.parm('import_filepath1').eval():
                    continue
                entity_uri = node.get_entity_uri()
                if entity_uri is not None:
                    expected.add(entity_uri)
            case 'import_assets':
                node = import_assets.ImportAssets(ancestor)
                if node.get_import_mode() == 'inline':
                    continue
                for index in range(1, ancestor.parm('asset_imports').eval() + 1):
                    if ancestor.parm(f'instances{index}').eval() <= 0:
                        continue
                    entity_uri = node.get_entity_uri(index)
                    if entity_uri is not None:
                        expected.add(entity_uri)
            case 'import_layer':
                # import_layer tags the imported entity's root prim when it
                # brings in another asset's department layer. A self-import
                # is the workfile's own entity, which stays untagged — the
                # caller discards the exporting entity from the expected set.
                node = import_layer.ImportLayer(ancestor)
                if node.get_import_mode() == 'inline':
                    continue
                loaded = any(
                    ancestor.parm(f'import_enable{index}').eval()
                    and ancestor.parm(f'import_filepath{index}').eval()
                    for index in (1, 2)
                )
                entity_uri = node.get_entity_uri()
                if (loaded and entity_uri is not None
                        and entity_uri.segments[0] == 'assets'):
                    expected.add(entity_uri)
    return expected


def validate_export_files(temp_path: Path, expected_filename: str, operation_desc: str) -> None:
    """Validate that export operation created expected files.

    Args:
        temp_path: Directory where files should be created
        expected_filename: Name of main USD file expected
        operation_desc: Description for error message (e.g., "batch export chunk 1001-1010")

    Raises:
        ExportLayerError: If expected files don't exist with detailed diagnostic info
    """
    expected_file = temp_path / expected_filename

    if not expected_file.exists():
        # Check if temp directory itself exists
        if not temp_path.exists():
            raise ExportLayerError(
                f"Export failed during {operation_desc}: "
                f"Temp directory not found: {temp_path}\n"
                f"This may indicate a disk I/O error or permission issue."
            )

        # List what files DO exist for diagnostics
        existing_files = [f.name for f in temp_path.iterdir()] if temp_path.exists() else []

        raise ExportLayerError(
            f"Export failed during {operation_desc}: "
            f"Expected file not created: {expected_filename}\n"
            f"Export path: {temp_path}\n"
            f"Files found: {existing_files if existing_files else '(none)'}\n\n"
            f"Possible causes:\n"
            f"- Disk full or quota exceeded\n"
            f"- Network drive disconnected\n"
            f"- File permissions issue\n"
            f"- Houdini export node errors (check Houdini console)"
        )


def workfile_cache_storage_roots() -> list[Path]:
    """The ``project:``/``proxy:`` storage roots of the active project.

    Any absolute composition arc that lands in a ``cache/`` directory
    below one of these roots is a versioned cache next to some workfile
    (``<...>/<entity>/<dept>/cache/<name>/<version>/...``) and publishes
    by reference — regardless of how it was loaded into the scene (a
    ``th::cache`` node, a raw ``sublayer`` of the ``.bgeo.sc`` files, a
    file SOP …). Unlike ``versioned_cache_roots`` this needs no scene
    node, so it also covers caches pulled from another workfile's folder.
    """
    return _storage_roots(('project', 'proxy'))


def project_storage_roots() -> list[Path]:
    """Shared project storage: asset paths under it are pinned, not copied."""
    return _storage_roots(('project', 'proxy', 'export'))


def _storage_roots(schemes) -> list[Path]:
    return [
        Path(api.storage.resolve(Uri.parse(f'{scheme}:/'))).resolve()
        for scheme in schemes
    ]


def check_no_dangling_composition_paths(
    layer_path: Path, allowed_roots: list[Path] = (),
    cache_storage_roots: list[Path] = (),
) -> None:
    """Abort the export if the layer composes geometry it can't carry.

    Two confirmed failure classes raise:

    - *Escaping* paths: sublayer/reference/payload arcs that resolve
      outside the layer's own folder (absolute, or relative ``..``
      climbs). The layer travels as a folder into the version location,
      so such an arc dangles — or silently reads another machine's /
      workfile's state — after publish even when the target exists right
      now. The classic source is H22's sopcreate/sopimport 'Layer Save
      Path' default (``$HIP/usd/$OS.usd``), which writes the node's
      layer next to the workfile and sublayers it by an escaping path,
      publishing a hollow layer.
    - *Dangling* paths: arcs whose target doesn't exist at all, so every
      consumer imports an empty asset (e.g. a payload anchored to a
      machine-local scratch file).

    Absolute arcs into a versioned cache on shared storage are exempt
    from the escaping check — they publish by reference — whether the
    location comes from a ``th::cache`` node (``allowed_roots``) or is any
    ``cache/`` directory below a ``project:``/``proxy:`` storage root
    (``cache_storage_roots``), which also covers a cache loaded straight
    off another workfile's folder via a raw sublayer/reference. Either
    way the dangling check still aborts when the cached file is missing.

    Arcs authored inside variants count like any other. A layer that can't
    be opened aborts the export too: an unchecked layer is not published.
    """
    from tumblepipe.pipe.usd.arcs import (
        arc_paths,
        find_dangling_layer_paths,
        find_escaping_layer_paths,
        open_layer,
    )

    asset_paths = arc_paths(open_layer(layer_path))

    escaping = find_escaping_layer_paths(
        asset_paths, layer_path.parent, allowed_roots=allowed_roots,
        cache_storage_roots=cache_storage_roots,
    )
    if escaping:
        bullets = "\n  - ".join(escaping)
        raise ExportLayerError(
            "Export aborted: the exported layer composes geometry from "
            "path(s) outside the export folder, so the published layer "
            f"would break (or go stale) on every other machine:\n  - {bullets}\n\n"
            "This usually means a LOP in the scene has 'Layer Save Path' "
            "enabled — H22 enables it by default on new SOP Create / SOP "
            "Import nodes, pointing at $HIP/usd/. Disable 'Enable Layer "
            "Save Path' on the offending node(s) and re-export so the "
            "geometry flattens into the published layer. For caches, use "
            "a th::cache node — its versioned cache locations publish by "
            "reference and are allowed."
        )

    dangling = find_dangling_layer_paths(asset_paths, layer_path.parent)
    if not dangling:
        return

    bullets = "\n  - ".join(dangling)
    raise ExportLayerError(
        "Export aborted: the exported layer composes geometry from "
        "path(s) that do not exist, so the published asset would import "
        f"empty:\n  - {bullets}\n\n"
        "This usually means the asset's geometry/payload was not written "
        "into the version folder — for example a payload anchored to a "
        "machine-local scratch path rather than the export directory. "
        "Check the asset_payload / export setup and re-export so the "
        "geometry travels with the layer."
    )


def versioned_cache_roots() -> list[Path]:
    """The th::cache locations whose files may stay referenced by a publish.

    Versioned caches — both the LOP ``th::cache`` (``lops_cache``, USD) and the
    SOP ``th::cache`` (``cache``, ``.bgeo.sc``) — live on shared storage
    (``project:``/``proxy:``) and are immutable per version, so — unlike other
    external files — they are safe (and, given their size, necessary) to
    publish by reference instead of copying into every version folder. Both
    node types carry entity/department parms, so a node may address another
    workfile's cache; walking the actual nodes keeps the exemption in agreement
    with each node's resolved path wherever it points.
    """
    from tumblepipe.pipe.houdini.cache import list_cache_locations
    return [Path(root).resolve() for root in list_cache_locations()]


def absolutize_cache_arcs(layer_path: Path, cache_roots: list[Path]) -> None:
    """Pin arcs into versioned cache locations to absolute paths.

    th::cache files publish by reference, but the layer is exported into a
    temp folder that is then copied to the version location — so an arc the
    ROP's "Use Relative Paths" processor relativised against the temp folder
    would re-anchor wrongly after the copy. Rewrite every arc that resolves
    into a cache location to its absolute form so it survives the move (and
    machine hops on the shared mount).
    """
    if not cache_roots:
        return
    from pxr import UsdUtils
    from tumblepipe.pipe.usd.arcs import arc_paths, looks_like_uri, open_layer

    layer = open_layer(layer_path)
    layer_dir = layer_path.parent
    remap: dict[str, str] = {}
    for raw in arc_paths(layer):
        if looks_like_uri(raw):
            continue
        src = Path(raw)
        resolved = (src if src.is_absolute() else layer_dir / src).resolve()
        if not any(resolved.is_relative_to(root) for root in cache_roots):
            continue
        pinned = path_str(resolved)
        if pinned != raw:
            remap[raw] = pinned

    if not remap:
        return

    UsdUtils.ModifyAssetPaths(layer, lambda p: remap.get(p, p))
    layer.Save()
    logger.info("Pinned %d cache arc(s) absolute in %s", len(remap), layer_path)


def localize_external_sidecars(layer_path: Path, skip_roots: list[Path] = ()):
    """Pull externally-anchored composition files into the layer's folder.

    The ``asset_payload`` HDA writes its geometry into a ``payload.usd``
    sidecar anchored to ``$HIP``, and a Reference LOP can point at a library
    file anywhere on disk; the ROP can't relativise either into the export
    folder, so the arc would dangle after publish. See
    ``pipe.localize.localize_external_arcs``: self-contained files land next
    to the layer, files with dependencies come in with all of them.
    """
    from tumblepipe.pipe.localize import localize_external_arcs
    return localize_external_arcs(layer_path, skip_roots=skip_roots)


def check_no_dropped_assets(root, entity_uri, expected_asset_uris, tracked_asset_uris):
    """Refuse to publish a stage that lost an asset's pipeline metadata.

    list_assets() (and every downstream consumer) only sees prims with
    customData, so such an asset would silently drop out of the export and
    out of every import that follows. Two checks:

    - the sibling scan: a metadata-less prim that still composes from the
      *asset* export tree (export/assets/...) is a dropped asset. The
      exporting entity's own root legitimately carries no metadata, and
      geometry the artist authored, or shot content imported from another
      department's shot export, never carried per-asset metadata;
    - the expected set: the sibling scan is blind when a category holds no
      metadata-carrying asset at all, so the scrape is also checked against
      what the upstream import nodes say they placed.
    """
    from tumblepipe.pipe.houdini import prim_metadata, prim_queries

    dropped_prims = prim_metadata.list_dropped_asset_prims(
        root,
        ignore_prim_paths={prim_queries.uri_to_prim_path(entity_uri)},
        asset_export_roots=[
            api.storage.resolve(Uri.parse('export:/assets'))
        ],
    )
    if dropped_prims:
        bullets = "\n  - ".join(dropped_prims)
        raise ExportLayerError(
            "Export aborted: asset(s) on the stage carry no pipeline "
            "metadata, so they would silently drop out of the published "
            f"layer and every downstream import:\n  - {bullets}\n\n"
            "This usually means the import node's metadata step didn't "
            "reach these prims - e.g. a layerbreak stripped the customData, "
            "or multi-instance duplicates didn't inherit it. Re-run the "
            "import node (Import button) and re-export. If you meant to "
            "bake these assets into the export, set the import node's "
            "Import Mode to Inline instead."
        )

    missing_asset_uris = sorted(set(expected_asset_uris) - set(tracked_asset_uris))
    if missing_asset_uris:
        bullets = "\n  - ".join(missing_asset_uris)
        raise ExportLayerError(
            "Export aborted: asset(s) configured on upstream import "
            "nodes carry no pipeline metadata on the stage, so they "
            "would silently drop out of the published layer and every "
            f"downstream import:\n  - {bullets}\n\n"
            "Re-run the import node (Import button) so its metadata "
            "step cooks, then re-export. If it persists, recreate the "
            "import node or update the pipeline package."
        )


def make_portable(layer_path: Path) -> None:
    """Make an exported layer travel as its folder, or raise.

    Versioned th::cache files stay referenced (they can be huge and live on
    shared storage); their arcs are pinned absolute so they survive the
    temp -> version copy. Everything else external is pulled into the folder.
    Then no arc may escape the folder or dangle.
    """
    from tumblepipe.util.progress import report_progress
    from tumblepipe.pipe.localize import localize_external_assets

    report_progress("localizing sidecars")
    cache_roots = versioned_cache_roots()
    absolutize_cache_arcs(layer_path, cache_roots)
    arcs = localize_external_sidecars(layer_path, skip_roots=cache_roots)
    if arcs.missing_layers:
        bullets = "\n  - ".join(arcs.missing_layers)
        raise ExportLayerError(
            "Export aborted: a file referenced from outside the "
            "project composes layers that do not exist, so it would "
            f"publish empty:\n  - {bullets}\n\n"
            "Re-download or repair the referenced asset and re-export."
        )
    # Textures and other asset paths on the exported prims: the ROP made
    # them relative to the temp folder.
    assets = localize_external_assets(
        layer_path,
        project_roots=project_storage_roots(),
        skip_roots=cache_roots,
    )
    for missing in arcs.missing_assets + assets.missing_assets:
        logger.warning("Exported layer points at a missing file: %s", missing)

    # A layer that composes geometry from a missing file (a payload anchored
    # to a machine-local scratch path) would silently import empty.
    report_progress("checking composition arcs")
    check_no_dangling_composition_paths(
        layer_path, allowed_roots=cache_roots,
        cache_storage_roots=workfile_cache_storage_roots(),
    )
