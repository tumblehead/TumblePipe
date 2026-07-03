"""
Centralized dependency graph for pipeline entities.

This module provides a functional interface for querying and resolving
dependencies between assets and shots. It scans context.json files
to build a graph of entity relationships.

Terminology:
- Dependencies: What an entity depends on (forward: "what I use")
- References: What depends on an entity (reverse: "what uses me")
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

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


def get_entity_type(entity_uri: Uri) -> Optional[str]:
    """Get entity type ('asset' or 'shot') from URI."""
    if entity_uri.purpose != 'entity': return None
    if len(entity_uri.segments) < 1: return None
    context = entity_uri.segments[0]
    if context == 'assets': return 'asset'
    if context == 'shots': return 'shot'
    return None


@dataclass
class Node:
    """Represents an entity in the dependency graph (version-agnostic)."""
    entity_uri: Uri
    department_name: Optional[str]
    dependencies: list['Node'] = field(default_factory=list)
    references: list['Node'] = field(default_factory=list)


@dataclass
class Graph:
    """Dependency graph (version-agnostic)."""
    nodes: dict[str, Node] = field(default_factory=dict)
    scanned: bool = False


# === Utilities ===

def entity_key(entity_uri: Uri, department_name: Optional[str]) -> str:
    """Create unique key for entity URI + department combination."""
    dept_suffix = f'/{department_name}' if department_name else ''
    return f'{entity_uri}{dept_suffix}'


def get_latest_version(api, entity_uri: Uri, variant_name: str, department_name: Optional[str]) -> Optional[Path]:
    """
    Look up latest version path for entity.

    Uses latest_export_path() with Uri.
    """
    return latest_export_path(entity_uri, variant_name, department_name)


def entity_from_dict(data: dict) -> Optional[tuple[Uri, Optional[str]]]:
    """Create (entity_uri, department_name) tuple from context.json input/output dict."""
    entity_str = data.get('uri')
    if not entity_str:
        return None

    try:
        entity_uri = Uri.parse_unsafe(entity_str)
    except ValueError:
        return None
    department = data.get('department')
    return entity_uri, department


# === Scanning ===

def _iter_all_entities(api):
    """
    Iterate all possible entities in the project.

    Yields: (entity_uri, department_name) tuples for all combinations
    """
    # Iterate all asset entities (category/asset/department)
    for entity in api.config.list_entities(Uri.parse_unsafe('entity:/assets/*/*/*'), closure=True):
        # entity is Entity(uri, properties) from new API
        # Extract department from URI path
        path_segments = list(entity.uri.segments)
        if len(path_segments) >= 4:
            department_name = path_segments[3]
            base_uri = Uri.parse_unsafe(f'entity:/assets/{path_segments[1]}/{path_segments[2]}')
            yield base_uri, department_name
        elif len(path_segments) == 3:
            yield entity.uri, None

    # Iterate all shot entities (sequence/shot/department)
    for entity in api.config.list_entities(Uri.parse_unsafe('entity:/shots/*/*/*'), closure=True):
        # Extract department from URI path
        path_segments = list(entity.uri.segments)
        if len(path_segments) >= 4:
            department_name = path_segments[3]
            base_uri = Uri.parse_unsafe(f'entity:/shots/{path_segments[1]}/{path_segments[2]}')
            yield base_uri, department_name
        elif len(path_segments) == 3:
            yield entity.uri, None


def scan(api) -> Graph:
    """
    Scan all latest context.json files and build dependency graph.

    Returns: new Graph instance with populated nodes
    """
    nodes = {}

    # Scan all entities
    for entity_uri, department_name in _iter_all_entities(api):
        # Get latest version path (scan with default variant)
        latest_path = get_latest_version(api, entity_uri, DEFAULT_VARIANT, department_name)
        if latest_path is None:
            continue

        # Load context.json
        context_path = latest_path / 'context.json'
        context_data = load_json(context_path)
        if context_data is None:
            continue

        # Create node for this entity
        key = entity_key(entity_uri, department_name)
        if key not in nodes:
            nodes[key] = Node(entity_uri=entity_uri, department_name=department_name)

        # Add dependencies from inputs
        inputs = context_data.get('inputs', [])
        for input_data in inputs:
            dep_result = entity_from_dict(input_data)
            if dep_result is None:
                continue

            dep_entity_uri, dep_department = dep_result
            dep_key = entity_key(dep_entity_uri, dep_department)
            if dep_key not in nodes:
                nodes[dep_key] = Node(entity_uri=dep_entity_uri, department_name=dep_department)

            # Add bidirectional edges
            if nodes[dep_key] not in nodes[key].dependencies:
                nodes[key].dependencies.append(nodes[dep_key])
            if nodes[key] not in nodes[dep_key].references:
                nodes[dep_key].references.append(nodes[key])

    return Graph(nodes=nodes, scanned=True)


def invalidate(graph: Graph) -> Graph:
    """
    Clear cache - return empty graph.

    Returns: new empty Graph
    """
    return Graph()


def invalidate_entity(graph: Graph, entity_uri: Uri, department_name: Optional[str]) -> Graph:
    """
    Remove specific entity from graph.

    Returns: new Graph with entity removed
    """
    key = entity_key(entity_uri, department_name)
    new_nodes = {k: v for k, v in graph.nodes.items() if k != key}

    # Remove references to this entity from other nodes
    for node in new_nodes.values():
        node.dependencies = [n for n in node.dependencies if entity_key(n.entity_uri, n.department_name) != key]
        node.references = [n for n in node.references if entity_key(n.entity_uri, n.department_name) != key]

    return Graph(nodes=new_nodes, scanned=graph.scanned)


# === Forward Queries ===

def get_dependencies(graph: Graph, entity_uri: Uri, department_name: Optional[str], recursive: bool = False) -> list[tuple[Uri, Optional[str]]]:
    """
    Get what entity depends on (what it uses).

    Returns: list of (entity_uri, department_name) tuples
    """
    if not graph.scanned:
        raise ValueError("Graph not scanned")

    key = entity_key(entity_uri, department_name)
    if key not in graph.nodes:
        return []

    node = graph.nodes[key]
    deps = [(dep.entity_uri, dep.department_name) for dep in node.dependencies]

    if recursive:
        seen = set([key])
        for dep in node.dependencies:
            dep_key = entity_key(dep.entity_uri, dep.department_name)
            if dep_key not in seen:
                seen.add(dep_key)
                deps.extend(get_dependencies(graph, dep.entity_uri, dep.department_name, recursive=True))

    return deps


# === Reverse Queries ===

def get_references(graph: Graph, entity_uri: Uri, department_name: Optional[str], recursive: bool = False) -> list[tuple[Uri, Optional[str]]]:
    """
    Get what depends on entity (what uses it).

    Returns: list of (entity_uri, department_name) tuples
    """
    if not graph.scanned:
        raise ValueError("Graph not scanned")

    key = entity_key(entity_uri, department_name)
    if key not in graph.nodes:
        return []

    node = graph.nodes[key]
    refs = [(ref.entity_uri, ref.department_name) for ref in node.references]

    if recursive:
        seen = set([key])
        for ref in node.references:
            ref_key = entity_key(ref.entity_uri, ref.department_name)
            if ref_key not in seen:
                seen.add(ref_key)
                refs.extend(get_references(graph, ref.entity_uri, ref.department_name, recursive=True))

    return refs


def find_shots_referencing_asset(graph: Graph, asset_uri: Uri) -> list[Uri]:
    """
    Find all shots that reference an asset (any department).

    Returns: list of shot URIs
    """
    if not graph.scanned:
        raise ValueError("Graph not scanned")

    shot_uris = set()

    # Check all nodes for shots that depend on this asset
    for node in graph.nodes.values():
        if get_entity_type(node.entity_uri) != 'shot':
            continue
        for dep in node.dependencies:
            if get_entity_type(dep.entity_uri) != 'asset':
                continue
            if dep.entity_uri != asset_uri:
                continue
            shot_uris.add(node.entity_uri)
            break

    return sorted(list(shot_uris), key=str)




# === Resolution ===

def _load_shot_layer(
    shot_uri: Uri,
    department_name: str,
    variant_name: str
) -> Optional[tuple[Path, list[tuple[Uri, str, list]]]]:
    """
    Load the latest export of one shot department.

    Returns: (version_path, [(asset_uri, instance_name, inputs), ...]) or None
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
            asset_datum['instance'],
            asset_datum.get('inputs', [])
        )
        for asset_datum in layer_info['parameters'].get('assets', [])
    ]
    return latest_version_path, asset_entries


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
        {dept: (version_path, {asset_uri: set(instances)})},
        {asset_uri: set(instances)},
        {asset_uri: inputs}
    )
    """
    # First pass: load each department's latest layer and its asset entries
    dept_layers = {}  # {dept: (version_path, [(asset_uri, instance, inputs), ...])}
    for department_name in shot_departments:
        layer = _load_shot_layer(shot_uri, department_name, variant_name)
        if layer is None:
            continue
        dept_layers[department_name] = layer

    # Determine which assets exist in each source department (for validation)
    source_dept_assets = {
        dept: {asset_uri for asset_uri, _, _ in asset_entries}
        for dept, (_, asset_entries) in dept_layers.items()
    }

    # Second pass: keep only assets whose source department is in the resolution list
    layer_data = dict()
    shot_assets = dict()
    asset_inputs = dict()  # Track inputs per asset for staged output (first occurrence)
    for department_name, (version_path, asset_entries) in dept_layers.items():
        layer_assets = dict()
        for asset_uri, instance_name, inputs in asset_entries:
            source_dept = _resolve_source_department(
                inputs, asset_uri, all_shot_departments, source_dept_assets
            )
            if source_dept not in shot_departments:
                continue
            # Verify source department still has this asset
            if asset_uri not in source_dept_assets.get(source_dept, ()):
                continue

            shot_assets.setdefault(asset_uri, set()).add(instance_name)
            layer_assets.setdefault(asset_uri, set()).add(instance_name)
            asset_inputs.setdefault(asset_uri, inputs)

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
    Yield (asset_uri, variant_name) for every asset composed by a scene.

    Direct scene assets come first so they take precedence over assets
    inherited from parent scenes.
    """
    scene_path = get_current_scene_staged_file_path(scene_uri)
    if scene_path is not None:
        scene_context_data = load_json(scene_path.parent / 'context.json')
        if scene_context_data is not None:
            for asset_datum in scene_context_data.get('parameters', {}).get('assets', []):
                asset_uri = Uri.parse_unsafe(asset_datum['asset'])
                yield asset_uri, asset_datum.get('variant', DEFAULT_VARIANT)

    from tumblepipe.config.scene import get_inherited_assets
    for entry, _parent_uri in get_inherited_assets(scene_uri):
        yield Uri.parse_unsafe(entry.asset), entry.variant


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

    Replaces _resolve_versions_latest() in build.py/build_shot.py.
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
        'assets': {asset_uri: set(instance_names)},
        'shot_layers': {dept: (version_path, {asset_uri: set(instances)})},
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
        for asset_uri, variant in _iter_scene_assets(scene_uri):
            asset_name = asset_uri.segments[-1]  # Use asset name as instance
            scene_asset_uris.add(asset_uri)
            assets.setdefault(asset_uri, set()).add(asset_name)
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
        'asset_inputs': {tracked_asset_uri: inputs}
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
    assets = {}
    asset_inputs = {}
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
        for asset_datum in layer_info['parameters'].get('assets', []):
            tracked_uri = Uri.parse_unsafe(asset_datum['asset'])
            if tracked_uri == asset_uri:
                continue  # self-import — never sublayer an asset into itself
            instances = asset_datum.get('instances', 1)
            assets[tracked_uri] = max(assets.get(tracked_uri, 0), instances)
            asset_inputs.setdefault(tracked_uri, asset_datum.get('inputs', []))

    return dict(
        asset_uri=asset_uri,
        variant=variant_name,
        department_layers=department_layers,
        assets=assets,
        asset_inputs=asset_inputs
    )
