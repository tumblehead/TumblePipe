import logging
from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import shutil
import json

import hou

logger = logging.getLogger(__name__)

from tumblepipe.api import get_user_name, path_str, local_path, api
from tumblepipe.util.errors import TaskSkipped
from tumblepipe.util.progress import report_progress
from tumblepipe.util.uri import Uri
from tumblepipe.config.variants import list_variants
from tumblepipe.config.timeline import FrameRange, get_frame_range, get_fps
from tumblepipe.config.farm import list_pools
from tumblepipe.pipe.houdini import util
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.paths import (
    latest_export_path,
    next_export_path,
    get_workfile_context,
    get_layer_file_name,
    latest_hip_file_path
)
from tumblepipe.pipe.usd import add_sublayer
from tumblepipe.pipe.context import save_layer_context
from tumblepipe.apps.houdini import (
    stitch_usd_directories,
    calculate_chunks,
    flatten_sidecar_directories,
)


class ExportLayerError(Exception):
    """Raised when export_layer encounters a validation or execution error."""
    pass


def _list_expected_asset_uris(native) -> set[str]:
    """Asset URIs that upstream import nodes placed on the stage.

    Walks the export node's input ancestors for import_asset /
    import_assets instances so the export can cross-check the scraped
    metadata against what the graph says should be there. The sibling
    heuristic in util.list_dropped_asset_prims cannot see a drop when a
    category holds no metadata-carrying asset at all (single asset, or
    every asset dropped) - this expected-vs-scraped check can.
    Best-effort: entries it cannot read are skipped, never blocking.
    """
    expected = set()
    for ancestor in native.inputAncestors():
        if ancestor.isBypassed():
            continue
        # Inline-mode imports deliberately bake their assets into the
        # export without pipeline metadata, so they are not expected in
        # the scrape.
        mode_parm = ancestor.parm('import_mode')
        if mode_parm is not None and mode_parm.evalAsString() == 'inline':
            continue
        type_base = ancestor.type().nameComponents()[2]
        entity_raws = []
        if type_base == 'import_asset':
            filepath_parm = ancestor.parm('import_filepath1')
            entity_parm = ancestor.parm('entity')
            if (
                filepath_parm is not None and filepath_parm.eval()
                and entity_parm is not None
            ):
                entity_raws.append(entity_parm.eval())
        elif type_base == 'import_assets':
            count_parm = ancestor.parm('asset_imports')
            count = count_parm.eval() if count_parm is not None else 0
            for index in range(1, count + 1):
                instances_parm = ancestor.parm(f'instances{index}')
                entity_parm = ancestor.parm(f'entity{index}')
                if instances_parm is None or entity_parm is None:
                    continue
                if instances_parm.eval() <= 0:
                    continue
                entity_raws.append(entity_parm.eval())
        elif type_base == 'import_layer':
            # import_layer tags the imported entity's root prim when it
            # brings in another asset's department layer. 'from_context'
            # (and any self-import) is the workfile's own entity, which
            # stays untagged — the caller discards the exporting entity's
            # URI from the expected set.
            entity_parm = ancestor.parm('entity')
            entity_raw = entity_parm.eval() if entity_parm is not None else ''
            loaded = any(
                ancestor.parm(f'import_enable{index}') is not None
                and ancestor.parm(f'import_enable{index}').eval()
                and ancestor.parm(f'import_filepath{index}') is not None
                and ancestor.parm(f'import_filepath{index}').eval()
                for index in (1, 2)
            )
            if entity_raw.startswith('entity:/assets/') and loaded:
                entity_raws.append(entity_raw)
        for entity_raw in entity_raws:
            if not entity_raw:
                continue
            try:
                expected.add(str(Uri.parse_unsafe(entity_raw)))
            except ValueError:
                continue
    return expected


def _validate_export_files(temp_path: Path, expected_filename: str, operation_desc: str) -> None:
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


def _workfile_cache_storage_roots() -> list[Path]:
    """The ``project:``/``proxy:`` storage roots of the active project.

    Any absolute composition arc that lands in a ``cache/`` directory
    below one of these roots is a versioned cache next to some workfile
    (``<...>/<entity>/<dept>/cache/<name>/<version>/...``) and publishes
    by reference — regardless of how it was loaded into the scene (a
    ``th::cache`` node, a raw ``sublayer`` of the ``.bgeo.sc`` files, a
    file SOP …). Unlike ``_versioned_cache_roots`` this needs no scene
    node, so it also covers caches pulled from another workfile's folder.
    Fail-open to no roots.
    """
    roots: list[Path] = []
    for scheme in ('project', 'proxy'):
        try:
            base = api.storage.resolve(Uri.parse_unsafe(f'{scheme}:/'))
        except Exception:
            logger.warning("Could not resolve %s: storage root", scheme, exc_info=True)
            continue
        if base is None:
            continue
        try:
            roots.append(Path(base).resolve())
        except OSError:
            continue
    return roots


def _check_no_dangling_composition_paths(
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

    Fail-open on analysis errors: a problem opening / walking the layer
    only skips the check (preserving the prior behaviour), never blocks a
    legitimate export.
    """
    from tumblepipe.pipe.usd import (
        collect_layer_composition_paths,
        find_dangling_layer_paths,
        find_escaping_layer_paths,
    )

    try:
        asset_paths = collect_layer_composition_paths(layer_path)
    except Exception:
        logger.warning(
            "Dangling-path check skipped; could not analyse %s",
            layer_path, exc_info=True,
        )
        return

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


def _resolve_under_any(path: Path, roots: list[Path]) -> bool:
    """True if ``path`` resolves inside one of ``roots`` (already resolved)."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(resolved.is_relative_to(root) for root in roots)


def _versioned_cache_roots() -> list[Path]:
    """The th::cache locations whose files may stay referenced by a publish.

    Versioned caches — both the LOP ``th::cache`` (``lops_cache``, USD) and the
    SOP ``th::cache`` (``cache``, ``.bgeo.sc``) — live on shared storage
    (``project:``/``proxy:``) and are immutable per version, so — unlike other
    external files — they are safe (and, given their size, necessary) to
    publish by reference instead of copying into every version folder. Both
    node types carry entity/department parms, so a node may address another
    workfile's cache; walking the actual nodes keeps the exemption in agreement
    with each node's resolved path wherever it points. Fail-open to no
    exemptions.
    """
    from tumblepipe.pipe.houdini.lops.cache import (
        list_cache_locations as list_lop_cache_locations,
    )
    from tumblepipe.pipe.houdini.sops.cache import (
        list_cache_locations as list_sop_cache_locations,
    )
    roots = []
    for list_locations in (list_lop_cache_locations, list_sop_cache_locations):
        try:
            locations = list_locations()
        except Exception:
            logger.warning("Could not determine th::cache locations", exc_info=True)
            continue
        for root in locations:
            try:
                roots.append(Path(root).resolve())
            except OSError:
                continue
    return roots


def _absolutize_cache_arcs(layer_path: Path, cache_roots: list[Path]) -> None:
    """Pin arcs into versioned cache locations to absolute paths.

    th::cache files publish by reference, but the layer is exported into a
    temp folder that is then copied to the version location — so an arc the
    ROP's "Use Relative Paths" processor relativised against the temp folder
    would re-anchor wrongly after the copy. Rewrite every arc that resolves
    into a cache location to its absolute form so it survives the move (and
    machine hops on the shared mount).

    Fail-open: any analysis error leaves the layer untouched for the
    escaping-path guard to catch.
    """
    if not cache_roots:
        return
    try:
        from pxr import Sdf, UsdUtils
    except Exception:
        logger.warning("Cache-arc pinning skipped; USD unavailable", exc_info=True)
        return

    from tumblepipe.pipe.usd import _looks_like_uri, collect_layer_composition_paths

    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        return

    layer_dir = layer_path.parent
    remap: dict[str, str] = {}
    for raw in collect_layer_composition_paths(layer_path):
        if not raw or raw in remap or _looks_like_uri(raw):
            continue
        src = Path(raw)
        abs_src = src if src.is_absolute() else (layer_dir / src)
        if not _resolve_under_any(abs_src, cache_roots):
            continue
        pinned = path_str(abs_src.resolve())
        if pinned != raw:
            remap[raw] = pinned

    if not remap:
        return

    UsdUtils.ModifyAssetPaths(layer, lambda p: remap.get(p, p))
    layer.Save()
    logger.info("Pinned %d cache arc(s) absolute in %s", len(remap), layer_path)


def _localize_external_sidecars(layer_path: Path, skip_roots: list[Path] = ()) -> None:
    """Pull externally-anchored composition sidecars into the layer's folder.

    The ``asset_payload`` HDA writes its geometry into a ``payload.usd``
    sidecar whose *relative* save path anchors to ``$HIP`` — next to the
    workfile, or a machine-local desktop dir for an unsaved scene — not the
    export directory. The ROP's "Use Relative Paths" processor can't
    relativise an arc to a file outside the output tree (and never across
    drives on Windows), so the published layer keeps a payload arc pointing
    outside the version folder, where it dangles on import.

    Right after export the sidecar still exists at its anchored location, so
    we copy every external, on-disk payload/reference file the layer points
    at into the layer's own directory and rewrite the arc to the bare sibling
    filename. The sidecar then travels into the version folder with the layer
    and resolves portably — fixing both the saved-workfile and the
    unsaved-desktop cases.

    Files under ``skip_roots`` (versioned th::cache locations) are left
    alone: they publish by reference and may be far too large to copy.

    Fail-open: any analysis/copy error is logged and skipped, leaving the
    layer untouched for the downstream dangling-path guard to catch.
    """
    try:
        from pxr import Sdf, UsdUtils
    except Exception:
        logger.warning("Sidecar localisation skipped; USD unavailable", exc_info=True)
        return

    from tumblepipe.pipe.usd import _looks_like_uri

    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        return

    layer_dir = layer_path.parent
    try:
        used_names = {p.name for p in layer_dir.iterdir()}
    except OSError:
        used_names = set()

    # Map each raw authored asset path we relocate -> its new sibling name.
    remap: dict[str, str] = {}
    stack = list(layer.rootPrims)
    while stack:
        prim = stack.pop()
        for arc_list in (prim.referenceList, prim.payloadList):
            for item in arc_list.GetAddedOrExplicitItems():
                raw = str(getattr(item, 'assetPath', '') or '')
                if not raw or raw in remap or _looks_like_uri(raw):
                    continue
                src = Path(raw)
                abs_src = src if src.is_absolute() else (layer_dir / src)
                try:
                    if abs_src.resolve().parent == layer_dir.resolve():
                        continue  # already a sibling — nothing to do
                except OSError:
                    pass
                if _resolve_under_any(abs_src, skip_roots):
                    continue  # versioned cache — published by reference
                if not abs_src.is_file():
                    continue  # missing — leave for the dangling-path guard
                dest_name = abs_src.name
                if dest_name in used_names:
                    stem, suffix = abs_src.stem, abs_src.suffix
                    n = 1
                    while f"{stem}_{n}{suffix}" in used_names:
                        n += 1
                    dest_name = f"{stem}_{n}{suffix}"
                try:
                    shutil.copy(abs_src, layer_dir / dest_name)
                except OSError:
                    logger.warning("Could not localise sidecar %s", abs_src, exc_info=True)
                    continue
                used_names.add(dest_name)
                remap[raw] = dest_name
        stack.extend(prim.nameChildren.values())

    if not remap:
        return

    UsdUtils.ModifyAssetPaths(layer, lambda p: remap.get(p, p))
    layer.Save()
    logger.info(
        "Localised %d payload/reference sidecar(s) into %s", len(remap), layer_dir
    )


class ExportLayer(EntityNode):

    def __init__(self, native):
        super().__init__(native)

    def list_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return ['from_context']

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        # Exclude generated departments and filter to publishable only.
        # scoped_departments narrows the pool to the departments the target
        # entity actually has.
        names = [
            d.name for d in
            self.scoped_departments(context_name, include_generated=False)
            if d.publishable
        ]
        return ['from_context'] + names

    def list_downstream_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return []

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        departments = self.scoped_departments(context_name)
        if len(departments) == 0:
            return []

        department_names = [dept.name for dept in departments]
        department_name = self.get_department_name()
        if department_name is None:
            return []
        if department_name not in department_names:
            return []

        department_index = department_names.index(department_name)
        return department_names[department_index + 1:]

    def list_pool_names(self) -> list[str]:
        return [pool.name for pool in list_pools()]

    def get_department_name(self) -> str | None:
        department_name = self.parm('department').eval()
        if department_name == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            return context.department_name
        # From settings
        department_names = self.list_department_names()
        if len(department_names) <= 1:  # Only 'from_context' means no real names
            return None
        if len(department_name) == 0:
            return department_names[1]  # Skip 'from_context'
        if department_name not in department_names:
            return None
        return department_name

    def get_downstream_department_names(self) -> list[str]:
        department_names = self.list_downstream_department_names()
        if len(department_names) == 0:
            return []
        selected = list(filter(len, self.parm('export_departments').eval().split(' ')))
        # A stored selection can name a department that is no longer
        # downstream — the pool was reordered, the department retired, or the
        # entity scoped away from it. Drop those instead of raising out of
        # the sort key.
        selected = [name for name in selected if name in department_names]
        if len(selected) == 0:
            return []
        selected.sort(key=department_names.index)
        return selected

    def get_pool_name(self) -> str | None:
        pool_names = self.list_pool_names()
        if len(pool_names) == 0:
            return None
        pool_name = self.parm('export_pool').eval()
        if pool_name == '':
            return pool_names[0]
        return pool_name

    def get_priority(self) -> int:
        return self.parm('export_priority').eval()

    def get_frame_range_source(self) -> str:
        return self.parm('frame_range').eval()

    def get_frame_range(self) -> tuple[FrameRange, int] | None:
        # The node's 'frame_range' menu only offers 'from_context' and
        # 'from_settings'. ('single_frame'/'playback_range' are options on the
        # cache/archive nodes, not here.)
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_context':
                entity_uri = self.get_entity_uri()
                if entity_uri is None:
                    return None
                frame_range = get_frame_range(entity_uri)
                if frame_range is None:
                    return None
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range source: {frame_range_source}'

    def get_export_type(self) -> str:
        """Get export type ('local' or 'farm')."""
        return self.parm('export_type').eval()

    def get_batch_size(self) -> int:
        """Get batch size for chunked export.

        Returns:
            Batch size (0 means no batching, export all frames at once).
        """
        return self.parm('batch_size').eval()

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        entity_raw = self.parm('entity').eval()
        if entity_raw == 'from_context':
            entity_uri = self.get_entity_uri()
            if entity_uri:
                self.parm('entity_label').set(f'from_context: {entity_uri}')
            else:
                self.parm('entity_label').set('from_context: none')
        else:
            # Specific entity URI selected
            self.parm('entity_label').set(entity_raw)

        department_raw = self.parm('department').eval()
        if department_raw == 'from_context':
            department_name = self.get_department_name()
            if department_name:
                self.parm('department_label').set(f'from_context: {department_name}')
            else:
                self.parm('department_label').set('from_context: none')
        else:
            # Specific department selected
            self.parm('department_label').set(department_raw)

    def _initialize(self):
        """Initialize node and update labels to show resolved values."""
        self._update_labels()

    def execute(self, force_local: bool = False):
        """
        Execute export.

        If force_local=True, executes directly (used by ProcessDialog callbacks).
        Otherwise, opens the ProcessDialog for task selection and execution.
        """
        if force_local:
            return self._execute()
        # Open ProcessDialog
        from tumblepipe.pipe.houdini.ui.dialog_launcher import (
            open_process_dialog_for_node
        )
        open_process_dialog_for_node(self, dialog_title="Export Layer")

    def _execute(self):
        """Internal execution - called by ProcessDialog callbacks.

        The config cache is refreshed once up front in
        open_process_dialog_for_node (the single entry point for every
        interactive export/publish flow), so entity-derived values resolved
        here are read from fresh on-disk config.
        """
        export_type = self.get_export_type()
        match export_type:
            case 'local':
                return self._export_local()
            case 'farm':
                return self._export_farm()
            case _:
                assert False, f'Unknown export type: {export_type}'

    def _check_variant_parm_listed(self, entity_uri):
        """Refuse to export when the variant parm names an unlisted variant.

        get_variant_name() silently falls back to 'default' for unknown
        names, which is harmless on imports but on an export would quietly
        publish under the wrong variant path (the casing/typo trap).
        An empty parm legitimately means 'default'.
        """
        raw_variant = self.parm('variant').eval().strip()
        variant_names = self.list_variant_names()
        if raw_variant and raw_variant not in variant_names:
            listed = ', '.join(variant_names)
            raise ExportLayerError(
                f"Export aborted: variant '{raw_variant}' is not a variant "
                f"of {entity_uri} (listed: {listed}), so the export would "
                "silently publish under 'default'. Fix the node's variant "
                "parm, or register the variant on the entity."
            )

    def _export_local(self):
        native = self.native()
        stage_node = native.node('IN_stage')

        # Get parameters
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        frame_range_result = self.get_frame_range()

        logger.info(
            f"Starting local export: uri={entity_uri}, dept={department_name}, "
            f"variant={variant_name}"
        )

        if entity_uri is None:
            raise ExportLayerError("Entity URI is not set. Check 'Entity Source' setting or workfile context.")
        if department_name is None:
            raise ExportLayerError(f"Department name is not set for entity: {entity_uri}")
        if frame_range_result is None:
            raise ExportLayerError(
                f"Frame range could not be determined for entity: {entity_uri}. "
                "Its config resolves no frame range (is the entity registered?) - "
                "set one on the entity, or switch the node's frame range source to "
                "'From settings' and enter the range manually."
            )

        self._check_variant_parm_listed(entity_uri)

        # The render step is deliberately dropped: a published stage cache
        # must carry every frame so downstream renders can sample any frame
        # (including a later switch to 1s). Sub-sampling the export on the
        # render step also broke batched export - integer chunk boundaries
        # do not align to a step>1 grid, so the stitched samples came out
        # off-grid whenever batch_size was not a multiple of step. Exporting
        # on 1s (export_f3 below) matches the farm exporter, which has always
        # discarded step (see the farm submit path and export_houdini).
        frame_range, _step = frame_range_result
        render_range = frame_range.full_range()
        batch_size = self.get_batch_size()
        user_name = get_user_name()
        timestamp = dt.datetime.now()
        fps = get_fps()

        # Determine version path
        version_path = next_export_path(entity_uri, variant_name, department_name)
        version_name = version_path.name

        # Prepare for stage scrape
        stage = stage_node.stage() if stage_node is not None else None
        if stage is None:
            # Usually a node left disconnected in the network. There is
            # nothing to publish, so skip this one and let the other exports
            # in the group run - it used to crash on None.GetPseudoRoot().
            raise TaskSkipped(
                f"Nothing exported for variant '{variant_name}': the node "
                f"{self.path()} has no stage input connected."
            )
        root = stage.GetPseudoRoot()

        # Check if we're exporting a shot (to add shot dept entry to inputs)
        is_shot_export = str(entity_uri).startswith('entity:/shots/')

        # Scrape stage for assets - group by asset URI to count instances
        assets_by_uri = dict()  # {asset_uri: [list of instance info]}
        asset_inputs = set()
        for asset_info in util.list_assets(root):
            prim_path = asset_info['prim_path']
            asset_metadata = asset_info['metadata']
            asset_uri_str = asset_metadata['uri']

            # Add current shot department entry to inputs if exporting a shot
            if is_shot_export:
                shot_dept_entry = {
                    'uri': str(entity_uri),
                    'department': department_name,
                    'version': version_name
                }
                # Add to inputs if not already present
                existing_inputs = asset_metadata.get('inputs', [])
                if shot_dept_entry not in existing_inputs:
                    asset_metadata['inputs'] = existing_inputs + [shot_dept_entry]

            # Group by asset URI to count instances
            if asset_uri_str not in assets_by_uri:
                assets_by_uri[asset_uri_str] = []
            assets_by_uri[asset_uri_str].append({
                'prim_path': prim_path,
                'instance': asset_metadata['instance'],
                'variant': asset_metadata.get('variant', 'default'),
                'inputs': asset_metadata.get('inputs', [])
            })
            asset_inputs.update(set(map(json.dumps, asset_metadata['inputs'])))

        # Refuse to publish if an asset sitting on the stage lost its pipeline
        # metadata: list_assets() (and every downstream consumer) only sees
        # prims with customData, so such an asset would silently drop out of
        # the export and out of every import that follows.
        # The exporting entity's own root prim legitimately carries no
        # metadata (it only gets tagged when another workfile imports it),
        # so exclude it from the sibling check.
        # Only a metadata-less prim that still composes from the *asset*
        # export tree (export/assets/...) is a dropped asset; geometry the
        # artist authored directly, or department-authored shot content
        # imported from another department's shot export (an FX sim or a
        # set-dress cache under export/shots/.../<dept>/...), never carried
        # per-asset metadata and is a supported addition that passes.
        # If the asset export root can't be resolved we fall back to
        # flagging every metadata-less sibling (over-block, never silently
        # drop).
        asset_export_roots = []
        try:
            asset_export_roots.append(
                local_path(api.storage.resolve(Uri.parse_unsafe('export:/assets')))
            )
        except Exception:
            logger.warning(
                "Could not resolve the asset export root for the drop guard; "
                "falling back to flagging every metadata-less sibling."
            )
        dropped_prims = util.list_dropped_asset_prims(
            root,
            ignore_prim_paths={util.uri_to_prim_path(entity_uri)},
            asset_export_roots=asset_export_roots,
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

        # The sibling heuristic above is blind when a category holds no
        # metadata-carrying asset at all (a shot's only asset dropped, or
        # every asset dropped). Cross-check the scrape against what the
        # upstream import nodes say they placed on the stage.
        expected_asset_uris = _list_expected_asset_uris(native)
        # A self-import (import_layer pulling the exporting entity's own
        # layer) is deliberately untagged — never expect it in the scrape.
        expected_asset_uris.discard(str(entity_uri))
        missing_asset_uris = sorted(expected_asset_uris - set(assets_by_uri))
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

        # Set fps
        self.parm('set_metadata_fps').set(fps)

        # Export the stage
        root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Collect asset parameters with instance counts
            parameter_assets = []
            for asset_uri_str, instances in assets_by_uri.items():
                # Use first instance for metadata, count all instances
                first_instance = instances[0]
                # 'instance' names the asset's BASE prim, so derive it from
                # the URI. instances[0] is whichever copy the scrape walked
                # first — and since the prototype is deactivated, list_assets
                # skips it and the first LIVE copy wins, having tagged itself
                # with its own name (Haybale9, not Haybale). Consumers read
                # this as the base name and regenerate <base>0..<base>N-1
                # from it, so an arbitrary copy's name spawned a phantom set
                # at the origin.
                try:
                    base_name = Uri.parse_unsafe(asset_uri_str).segments[-1]
                except (ValueError, IndexError):
                    base_name = first_instance['instance']
                parameter_assets.append(dict(
                    asset=asset_uri_str,
                    instance=base_name,
                    instances=len(instances),  # Count of instances
                    variant=first_instance['variant'],
                    inputs=first_instance['inputs']
                ))

            # Export the stage
            layer_file_name = get_layer_file_name(entity_uri, variant_name, department_name, version_name)
            self.parm('export_f3').set(1)

            if batch_size > 0:
                # Batched export: export chunks to separate directories then stitch
                chunks = calculate_chunks(render_range.first_frame, render_range.last_frame, batch_size)

                # Create chunks directory
                chunks_dir = temp_path / 'chunks'
                chunks_dir.mkdir(exist_ok=True)
                chunk_dirs = []

                for chunk_start, chunk_end in chunks:
                    # Each chunk exports to its own subdirectory
                    chunk_name = f"{chunk_start:04d}-{chunk_end:04d}"
                    report_progress(f"cooking frames {chunk_start}-{chunk_end} of {render_range.last_frame}")
                    chunk_dir = chunks_dir / chunk_name
                    chunk_dir.mkdir(exist_ok=True)

                    # Use 'stage.usd' as temp filename so sidecar directory is 'stage/'
                    temp_export_name = 'stage.usd'
                    chunk_temp_path = chunk_dir / temp_export_name
                    chunk_main_path = chunk_dir / layer_file_name

                    self.parm('export_f1').deleteAllKeyframes()
                    self.parm('export_f2').deleteAllKeyframes()
                    self.parm('export_f1').set(chunk_start)
                    self.parm('export_f2').set(chunk_end)
                    self.parm('export_lopoutput').set(path_str(chunk_temp_path))
                    self.parm('export_execute').pressButton()

                    # Validate files were created
                    _validate_export_files(chunk_dir, temp_export_name, f"batch export chunk {chunk_name}")

                    # Rename to final name
                    try:
                        chunk_temp_path.rename(chunk_main_path)
                    except Exception as e:
                        logger.error(f"Failed to rename chunk file: {e}")
                        raise ExportLayerError(
                            f"Failed to rename chunk file from {temp_export_name} to {layer_file_name}: {e}"
                        )

                    # Flatten any .usd.textures sidecar directories
                    flatten_sidecar_directories(chunk_dir)

                    logger.info(f"Chunk {chunk_name} exported successfully")

                    chunk_dirs.append(chunk_dir)

                # Stitch all chunks (main file + sidecar directories)
                try:
                    report_progress(f"stitching {len(chunk_dirs)} chunks")
                    logger.info(f"Stitching {len(chunk_dirs)} chunks into final USD")
                    stitch_usd_directories(chunk_dirs, layer_file_name, temp_path)
                    logger.info("Chunk stitching completed successfully")
                except Exception as e:
                    logger.error(f"USD stitching failed: {e}")
                    raise ExportLayerError(
                        f"Failed to stitch USD chunks:\n"
                        f"Chunks: {len(chunk_dirs)}\n"
                        f"Output: {temp_path / layer_file_name}\n"
                        f"Error: {str(e)}"
                    )

                # Clean up chunks directory
                shutil.rmtree(chunks_dir)
            else:
                # Standard export: export all frames at once
                # Use 'stage.usd' as temp filename so sidecar directory is 'stage/'
                temp_export_name = 'stage.usd'
                report_progress(f"cooking frames {render_range.first_frame}-{render_range.last_frame}")
                logger.info(f"Exporting frames {render_range.first_frame}-{render_range.last_frame}")
                self.parm('export_f1').deleteAllKeyframes()
                self.parm('export_f2').deleteAllKeyframes()
                self.parm('export_f1').set(render_range.first_frame)
                self.parm('export_f2').set(render_range.last_frame)
                self.parm('export_lopoutput').set(path_str(temp_path / temp_export_name))
                self.parm('export_execute').pressButton()

                # Validate files were created
                _validate_export_files(
                    temp_path,
                    temp_export_name,
                    f"standard export (frames {render_range.first_frame}-{render_range.last_frame})"
                )

                # Rename the exported file to the final layer name
                try:
                    (temp_path / temp_export_name).rename(temp_path / layer_file_name)
                except Exception as e:
                    logger.error(f"Failed to rename exported file: {e}")
                    raise ExportLayerError(
                        f"Failed to rename exported file from {temp_export_name} to {layer_file_name}: {e}"
                    )

                # Flatten any .usd.textures sidecar directories
                flatten_sidecar_directories(temp_path)

                logger.info("Export to temp completed successfully")

            # Versioned th::cache files stay referenced (they can be huge
            # and live on shared storage); pin their arcs absolute so they
            # survive the temp -> version copy. Everything else external
            # is pulled into the version folder so the published layer is
            # self-contained and travels portably.
            report_progress("localizing sidecars")
            cache_roots = _versioned_cache_roots()
            _absolutize_cache_arcs(temp_path / layer_file_name, cache_roots)
            _localize_external_sidecars(
                temp_path / layer_file_name, skip_roots=cache_roots,
            )

            # Refuse to publish a layer that composes geometry from a
            # missing file (e.g. a payload anchored to a machine-local
            # scratch path) — otherwise the asset silently imports empty.
            report_progress("checking composition arcs")
            _check_no_dangling_composition_paths(
                temp_path / layer_file_name, allowed_roots=cache_roots,
                cache_storage_roots=_workfile_cache_storage_roots(),
            )

            # Re-fetch root prim after export (stage may have been modified)
            root = stage_node.stage().GetPseudoRoot()

            # Extract AOV names from the stage
            aov_names = [
                aov_path.rsplit('/', 1)[-1].lower()
                for aov_path in util.list_render_vars(root)
            ]

            # Write layer context
            save_layer_context(
                target_path=temp_path,
                entity_uri=entity_uri,
                department_name=department_name,
                version_name=version_name,
                timestamp=timestamp.isoformat(),
                user_name=user_name,
                variant_name=variant_name,
                parameters=dict(assets=parameter_assets, aov_names=aov_names),
                inputs=list(map(json.loads, asset_inputs))
            )

            # Copy all files to output path
            report_progress(f"copying {version_name} to server")
            version_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Copying exported files to: {version_path}")

            try:
                for temp_item_path in temp_path.iterdir():
                    output_item_path = version_path / temp_item_path.name
                    if temp_item_path.is_file():
                        shutil.copy(temp_item_path, output_item_path)
                    elif temp_item_path.is_dir():
                        shutil.copytree(temp_item_path, output_item_path)
            except Exception as e:
                logger.error(f"Failed to copy files to output: {e}")
                raise ExportLayerError(
                    f"Failed to copy exported files to final location:\n"
                    f"Source: {temp_path}\n"
                    f"Destination: {version_path}\n"
                    f"Error: {str(e)}"
                )

            logger.info("Files copied to output path successfully")

        # Add shared layer as sublayer only if entity has multiple variants
        from tumblepipe.pipe.paths import latest_shared_export_path

        if len(list_variants(entity_uri)) > 1:
            shared_version_path = latest_shared_export_path(entity_uri, department_name)
            if shared_version_path is not None:
                exported_layer_path = version_path / layer_file_name
                shared_uri = f"{entity_uri}?dept={department_name}&variant=_shared"
                try:
                    add_sublayer(exported_layer_path, shared_uri)
                    logger.info(f"Added shared layer sublayer: {shared_uri}")
                except Exception as e:
                    # Non-fatal error - log warning but don't fail the export
                    logger.warning(f"Failed to add shared layer sublayer (non-fatal): {e}")

        # Update node comment
        native.setComment(
            f'last export: {version_name}\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

        logger.info(
            f"Export completed: uri={entity_uri}, dept={department_name}, "
            f"version={version_name}, output={version_path}"
        )

        return version_name

    def _export_farm(self):
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        frame_range_result = self.get_frame_range()

        if entity_uri is None:
            raise ExportLayerError("Entity URI is not set. Check 'Entity Source' setting or workfile context.")
        if department_name is None:
            raise ExportLayerError(f"Department name is not set for entity: {entity_uri}")
        if frame_range_result is None:
            raise ExportLayerError(
                f"Frame range could not be determined for entity: {entity_uri}. "
                "Its config resolves no frame range (is the entity registered?) - "
                "set one on the entity, or switch the node's frame range source to "
                "'From settings' and enter the range manually."
            )

        self._check_variant_parm_listed(entity_uri)

        frame_range, _step = frame_range_result

        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()

        if pool_name is None:
            raise ExportLayerError("No render pool available. Check Deadline configuration.")
        if priority is None:
            raise ExportLayerError("Priority is not set for farm export.")

        # Get workfile path for bundling
        workfile_path = latest_hip_file_path(entity_uri, department_name)
        if not workfile_path.exists():
            raise ExportLayerError(f"No workfile found for {entity_uri} {department_name}")
        workfile_dest = Path('workfiles') / workfile_path.name
        paths = {workfile_path: workfile_dest}

        config = {
            'entity': {
                'uri': str(entity_uri),
                'department': department_name,
                'variant': variant_name
            },
            'settings': {
                'priority': priority,
                'pool_name': pool_name,
                'first_frame': frame_range.full_range().first_frame,
                'last_frame': frame_range.full_range().last_frame
            },
            'tasks': {
                'publish': {
                    'downstream_departments': downstream_deps
                }
            },
            'workfile_path': path_str(workfile_dest)
        }

        from tumblepipe.farm.jobs.houdini.publish import job as publish_job
        try:
            publish_job.submit(config, paths)
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to submit farm job: {str(e)}",
                severity=hou.severityType.Error
            )
            return

        native = self.native()
        timestamp = dt.datetime.now()
        user_name = get_user_name()
        native.setComment(
            f'farm export submitted:\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}\n'
            f'downstream: {", ".join(downstream_deps) if downstream_deps else "None"}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

        downstream_msg = f"\nDownstream: {', '.join(downstream_deps)}" if downstream_deps else ""
        hou.ui.displayMessage(
            f"Export job submitted to farm\n"
            f"Department: {department_name}"
            f"{downstream_msg}",
            title="Farm Export Submitted"
        )
    
    def open_location(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            hou.ui.displayMessage("No entity selected.", severity=hou.severityType.Warning)
            return
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        if department_name is None:
            hou.ui.displayMessage("No department selected.", severity=hou.severityType.Warning)
            return

        export_path = latest_export_path(entity_uri, variant_name, department_name)
        if export_path is None:
            hou.ui.displayMessage(f"No exports found for {department_name}.", severity=hou.severityType.Warning)
            return
        if not export_path.exists():
            hou.ui.displayMessage(f"Export path does not exist: {export_path}", severity=hou.severityType.Warning)
            return
        hou.ui.showInFileBrowser(path_str(export_path))

def create(scene, name):
    return ns.create_node(scene, name, ExportLayer, 'export_layer')


def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):
    # Set node style
    set_style(raw_node)

    node = ExportLayer(raw_node)
    node._initialize()

def execute():
    raw_node = hou.pwd()
    node = ExportLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ExportLayer(raw_node)
    node.open_location()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ExportLayer(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='both',
        include_from_context=True,
        current_selection=node.parm('entity').eval(),
        title="Select Entity",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node._update_labels()


def validate():
    """HDA button callback to run stage validation."""
    from tumblepipe.pipe.houdini.validators import validate_stage_for_department

    raw_node = hou.pwd()
    stage_node = raw_node.node('IN_stage')

    if stage_node is None:
        hou.ui.displayMessage(
            "No stage input connected.",
            severity=hou.severityType.Warning
        )
        return

    stage = stage_node.stage()
    if stage is None:
        hou.ui.displayMessage(
            "No stage available for validation.",
            severity=hou.severityType.Warning
        )
        return

    node = ExportLayer(raw_node)
    entity_uri = node.get_entity_uri()
    department = node.get_department_name()

    if entity_uri is None or department is None:
        hou.ui.displayMessage(
            "Entity or department not set on this export node - cannot pick validators.",
            severity=hou.severityType.Warning
        )
        return

    uri_str = str(entity_uri)
    entity_context = 'shots' if uri_str.startswith('entity:/shots/') else 'assets'

    root = stage.GetPseudoRoot()
    result = validate_stage_for_department(
        root, entity_context, department, {'entity_uri': uri_str}
    )

    if result.passed:
        hou.ui.displayMessage(
            "Validation passed - no issues found.",
            severity=hou.severityType.Message,
            title="Validation Passed"
        )
        return

    from tumblepipe.pipe.houdini.ui.validation_dialog import (
        ValidationConfirmDialog,
    )
    entity_name = uri_str.rsplit('/', 1)[-1] if uri_str else 'unknown'
    dialog = ValidationConfirmDialog(
        validation_result=result,
        department=department,
        entity_name=entity_name,
        parent=hou.qt.mainWindow(),
        read_only=True,
    )
    dialog.exec_()


def output_modified_prims(raw_node) -> str:
    """Return the prim path this HDA wrote, for the output's modifiedprims."""
    entity = raw_node.parm('entity').eval()
    if not entity:
        return ''
    try:
        return util.uri_to_prim_path(Uri.parse_unsafe(entity))
    except ValueError:
        return ''
