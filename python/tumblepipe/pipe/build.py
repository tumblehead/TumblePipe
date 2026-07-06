"""Build resolution: which exported versions compose a shot or staged asset.

Split out of pipe.graph — the dependency Graph answers "who uses whom",
while this module answers "which latest layer paths build this entity".
The resolvers take a scanned Graph only to assert scan-freshness; all
path lookups go through pipe.paths.
"""

from pathlib import Path
from typing import Optional

from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri
from tumblepipe.config.variants import DEFAULT_VARIANT
from tumblepipe.config.department import list_departments
from tumblepipe.pipe.paths import (
    latest_export_path,
    get_root_layer_file_name,
    get_current_scene_staged_file_path,
    get_latest_version_path
)
from tumblepipe.pipe.graph import Graph
import tumblepipe.pipe.context as ctx


def get_source_department(inputs: list[dict], department_order: list[str]) -> str | None:
    """
    Find the source department (first shot department) from inputs array.

    Extracts all shot department entries from inputs and returns the one
    that appears earliest in the pipeline department order.

    Args:
        inputs: List of input dicts with 'uri' and 'department' keys
        department_order: List of department names in pipeline order

    Returns:
        The source department name, or None if no shot entries found
    """
    # Extract shot department entries (URIs starting with entity:/shots/)
    shot_depts = [
        inp['department'] for inp in inputs
        if inp.get('uri', '').startswith('entity:/shots/')
    ]
    if not shot_depts:
        return None

    # Return the one earliest in pipeline order
    for dept in department_order:
        if dept in shot_depts:
            return dept

    # Fallback to first shot department found
    return shot_depts[0]


def _load_shot_layer(
    shot_uri: Uri,
    department_name: str,
    variant_name: str
) -> Optional[tuple[Path, str, list[tuple[Uri, int, list]]]]:
    """
    Load the latest export of one shot department.

    Returns: (version_path, timestamp, [(asset_uri, instances, inputs), ...])
    or None. The timestamp is the layer's export time ('' for very old
    exports without one) — ISO strings compare lexicographically.
    """
    latest_version_path = latest_export_path(shot_uri, variant_name, department_name)
    if latest_version_path is None:
        return None

    context_data = load_json(latest_version_path / 'context.json')
    if context_data is None:
        return None

    layer_info = ctx.find_output(
        context_data,
        uri=str(shot_uri),
        department=department_name
    )
    if layer_info is None:
        return None

    asset_entries = [
        (
            Uri.parse_unsafe(asset_datum['asset']),
            asset_datum.get('instances', 1),
            asset_datum.get('inputs', [])
        )
        for asset_datum in layer_info['parameters'].get('assets', [])
    ]
    return latest_version_path, layer_info.get('timestamp', ''), asset_entries


def _resolve_source_department(
    inputs: list[dict],
    asset_uri: Uri,
    all_shot_departments: list[str],
    source_dept_assets: dict[str, set[Uri]]
) -> Optional[str]:
    """Determine which shot department an asset entry originates from."""
    source_dept = get_source_department(inputs, all_shot_departments)
    if source_dept is not None:
        return source_dept

    # Backwards compatibility: use first department in order that has this asset
    for dept in all_shot_departments:
        if asset_uri in source_dept_assets.get(dept, ()):
            return dept
    return None


def _latest_shot_layer_paths(
    shot_uri: Uri,
    shot_departments: list[str],
    variant_name: str,
    all_shot_departments: list[str]
) -> tuple[dict, dict, dict]:
    """
    Find latest shot layer paths and extract their assets.

    Returns: (
        {dept: (version_path, {asset_uri: instances})},
        {asset_uri: instances},
        {asset_uri: inputs}
    )
    """
    # First pass: load each department's latest layer and its asset entries
    dept_layers = {}  # {dept: (version_path, timestamp, [(asset_uri, instances, inputs), ...])}
    for department_name in shot_departments:
        layer = _load_shot_layer(shot_uri, department_name, variant_name)
        if layer is None:
            continue
        dept_layers[department_name] = layer

    # Determine which assets exist in each source department (for validation)
    source_dept_assets = {
        dept: {asset_uri for asset_uri, _, _ in asset_entries}
        for dept, (_, _, asset_entries) in dept_layers.items()
    }

    # Second pass: keep only assets whose source department is in the
    # resolution list. Per asset, the MOST RECENTLY EXPORTED layer that
    # records it wins (instances + inputs as one snapshot) — mirroring
    # resolve_asset_build. The previous union of instance names across
    # layers had the same stale-layer ratchet as the asset flow's max()
    # (an old downstream layer pinned instances forever), and it also
    # collapsed real counts: each layer records one representative
    # instance name, so the union's size never reflected 'instances'.
    layer_data = dict()
    shot_assets = dict()
    asset_inputs = dict()
    asset_stamps = dict()
    for department_name, (version_path, stamp, asset_entries) in dept_layers.items():
        layer_assets = dict()
        for asset_uri, instances, inputs in asset_entries:
            source_dept = _resolve_source_department(
                inputs, asset_uri, all_shot_departments, source_dept_assets
            )
            if source_dept not in shot_departments:
                continue
            # Verify source department still has this asset
            if asset_uri not in source_dept_assets.get(source_dept, ()):
                continue

            layer_assets[asset_uri] = instances
            if asset_uri in asset_stamps and stamp <= asset_stamps[asset_uri]:
                continue
            asset_stamps[asset_uri] = stamp
            shot_assets[asset_uri] = instances
            asset_inputs[asset_uri] = inputs

        layer_data[department_name] = (version_path, layer_assets)

    return layer_data, shot_assets, asset_inputs


def _latest_asset_layer_paths(
    assets: dict,
    asset_departments: list[str],
    asset_variants: dict
) -> dict:
    """Find latest asset layer paths: {dept: {asset_uri: version_path}}."""
    layer_data = dict()
    for asset_uri in assets.keys():
        asset_variant = asset_variants.get(asset_uri, DEFAULT_VARIANT)
        for department_name in asset_departments:
            latest_version_path = latest_export_path(asset_uri, asset_variant, department_name)
            if latest_version_path is None:
                continue
            layer_data.setdefault(department_name, dict())[asset_uri] = latest_version_path
    return layer_data


def _get_root_scene_uri(root_version_path: Path) -> Optional[Uri]:
    """Read the scene reference from a root layer's context.json."""
    root_context_data = load_json(root_version_path / 'context.json')
    if root_context_data is None:
        return None
    scene_ref = root_context_data.get('parameters', {}).get('scene')
    if scene_ref is None:
        return None
    return Uri.parse_unsafe(scene_ref)


def _iter_scene_assets(scene_uri: Uri):
    """
    Yield (asset_uri, variant_name, instances) for every asset composed
    by a scene.

    Direct scene assets come first so they take precedence over assets
    inherited from parent scenes.
    """
    scene_path = get_current_scene_staged_file_path(scene_uri)
    if scene_path is not None:
        scene_context_data = load_json(scene_path.parent / 'context.json')
        if scene_context_data is not None:
            for asset_datum in scene_context_data.get('parameters', {}).get('assets', []):
                asset_uri = Uri.parse_unsafe(asset_datum['asset'])
                yield (
                    asset_uri,
                    asset_datum.get('variant', DEFAULT_VARIANT),
                    asset_datum.get('instances', 1)
                )

    from tumblepipe.config.scene import get_inherited_assets
    for entry, _parent_uri in get_inherited_assets(scene_uri):
        yield Uri.parse_unsafe(entry.asset), entry.variant, entry.instances


def resolve_shot_build(
    graph: Graph,
    api,
    shot_uri: Uri,
    shot_departments: list[str],
    asset_departments: list[str],
    shot_variant: str = DEFAULT_VARIANT,
    asset_variants: Optional[dict] = None
) -> dict:
    """
    Resolve all versions needed to build a shot.

    Uses graph to find entities, then looks up latest versions.

    Args:
        graph: Scanned dependency graph
        api: API client
        shot_uri: Shot URI to build
        shot_departments: List of shot department names
        asset_departments: List of asset department names
        shot_variant: Variant name for shot layers (default: 'default')
        asset_variants: Optional dict mapping asset_uri to variant_name for per-asset variants

    Returns: {
        'assets': {asset_uri: instances},
        'shot_layers': {dept: (version_path, {asset_uri: instances})},
        'asset_layers': {dept: {asset_uri: version_path}},
        'shot_variant': str,
        'asset_variants': {asset_uri: variant_name}
    }
    """
    if not graph.scanned:
        raise ValueError("Graph not scanned")

    if asset_variants is None:
        asset_variants = {}

    # Get department order for determining source
    all_shot_departments = [d.name for d in list_departments('shots')]

    # Find latest paths
    shot_layer_paths, assets, asset_inputs = _latest_shot_layer_paths(
        shot_uri, shot_departments, shot_variant, all_shot_departments
    )
    asset_layer_paths = _latest_asset_layer_paths(assets, asset_departments, asset_variants)

    # Find root department layer (shot-level, stored at _root/)
    root_layer = None
    export_uri = Uri.parse_unsafe('export:/') / shot_uri.segments / '_root'
    export_path = api.storage.resolve(export_uri)
    root_version_path = get_latest_version_path(export_path)
    if root_version_path is not None:
        layer_file_name = get_root_layer_file_name(shot_uri, root_version_path.name)
        root_layer_path = root_version_path / layer_file_name
        if root_layer_path.exists():
            root_layer = root_layer_path

    # Extract assets by following the scene reference in the root layer's
    # context.json ({parameters: {scene: "scenes:/..."}}), so scene changes
    # don't require root regeneration. Track which assets come from the scene
    # (vs. shot-flow assets from department exports).
    scene_asset_uris = set()
    scene_uri = None
    if root_version_path is not None:
        scene_uri = _get_root_scene_uri(root_version_path)
    if scene_uri is not None:
        for asset_uri, variant, instances in _iter_scene_assets(scene_uri):
            scene_asset_uris.add(asset_uri)
            # Shot-flow entries win over the scene's count (setdefault):
            # a department that re-imported the asset is the fresher
            # authority, matching the pre-count behaviour.
            assets.setdefault(asset_uri, instances)
            asset_variants.setdefault(asset_uri, variant)

    # Done
    return dict(
        assets=assets,
        asset_inputs=asset_inputs,  # Track inputs per asset for staged output
        shot_layers=shot_layer_paths,
        asset_layers=asset_layer_paths,
        root_layer=root_layer,  # Root department layer (shot-level, stored at _root/)
        shot_variant=shot_variant,
        asset_variants=asset_variants,
        scene_asset_uris=scene_asset_uris  # Track which assets are from scene (vs. shot-flow)
    )


def resolve_asset_build(
    graph: Graph,
    api,
    asset_uri: Uri,
    asset_departments: list[str],
    variant_name: str = DEFAULT_VARIANT
) -> dict:
    """
    Resolve all versions needed to build a staged asset.

    Finds latest versions for each stageable asset department
    (e.g., lookdev, model) in the specified order.

    Args:
        graph: Scanned dependency graph
        api: API client
        asset_uri: Asset URI to build (e.g., entity:/assets/CHAR/Steen)
        asset_departments: List of department names in priority order
                          (stronger layers first, e.g., ['lookdev', 'model'])
        variant_name: Variant name to use (default: 'default')

    Returns: {
        'asset_uri': asset_uri,
        'variant': variant_name,
        'department_layers': {dept_name: version_path},
        'assets': {tracked_asset_uri: instances},
        'asset_inputs': {tracked_asset_uri: inputs},
        'asset_variants': {tracked_asset_uri: variant_name}
    }
    """
    if not graph.scanned:
        raise ValueError("Graph not scanned")

    # Find latest version for each department (with variant support)
    department_layers = {}
    for department_name in asset_departments:
        latest_version_path = latest_export_path(asset_uri, variant_name, department_name)
        if latest_version_path is None:
            continue
        department_layers[department_name] = latest_version_path

    # Collect the assets tracked in each department layer's context.json:
    # a set-style asset imports other assets into its workfile, and the
    # department export carries only overs for them (placement + metadata) —
    # the staged build must re-reference each tracked asset's own staged
    # file or those prims compose empty downstream. Mirrors the shot flow's
    # _latest_shot_layer_paths scrape.
    #
    # Per tracked asset, the MOST RECENTLY EXPORTED layer that records it
    # wins (instances, variant, inputs as one consistent snapshot) — never
    # a max() across layers. A stale department (e.g. lookdev exported
    # before a model rework halved the copies) would otherwise pin the old
    # instance count forever: max() can only go up, and any workfile that
    # imports the staged asset re-composes the inflated count onto its
    # stage and scrapes it back into its next export — a self-reinforcing
    # loop that survives every re-stage (the paleindia six-towers bug).
    assets = {}
    asset_inputs = {}
    asset_variants = {}
    asset_stamps = {}
    for department_name, version_path in department_layers.items():
        context_data = load_json(version_path / 'context.json')
        if context_data is None:
            continue
        layer_info = ctx.find_output(
            context_data,
            uri=str(asset_uri),
            department=department_name
        )
        if layer_info is None:
            continue
        # ISO timestamps compare lexicographically; layers without one
        # (very old exports) rank oldest.
        stamp = layer_info.get('timestamp', '')
        for asset_datum in layer_info['parameters'].get('assets', []):
            tracked_uri = Uri.parse_unsafe(asset_datum['asset'])
            if tracked_uri == asset_uri:
                continue  # self-import — never sublayer an asset into itself
            if tracked_uri in asset_stamps and stamp <= asset_stamps[tracked_uri]:
                continue
            asset_stamps[tracked_uri] = stamp
            assets[tracked_uri] = asset_datum.get('instances', 1)
            asset_inputs[tracked_uri] = asset_datum.get('inputs', [])
            asset_variants[tracked_uri] = asset_datum.get(
                'variant', DEFAULT_VARIANT
            )

    return dict(
        asset_uri=asset_uri,
        variant=variant_name,
        department_layers=department_layers,
        assets=assets,
        asset_inputs=asset_inputs,
        asset_variants=asset_variants
    )
