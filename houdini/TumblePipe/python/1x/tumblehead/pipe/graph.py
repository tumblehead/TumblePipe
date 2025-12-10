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

from tumblehead.util.io import load_json
from tumblehead.util.uri import Uri
from tumblehead.config.variants import DEFAULT_VARIANT
from tumblehead.config.department import list_departments
from tumblehead.pipe.paths import (
    latest_export_path,
    get_layer_file_name
)
import tumblehead.pipe.context as ctx


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


def _get_entity_type(entity_uri: Uri) -> Optional[str]:
    """Get entity type from URI."""
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
    return f'{entity_uri.path}{dept_suffix}'


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
        department = data.get('department')
        return entity_uri, department
    except:
        return None


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
        path_segments = [s for s in entity.uri.path.split('/') if s]
        if len(path_segments) >= 4:
            department_name = path_segments[3]
            base_uri = Uri.parse_unsafe(f'entity:/assets/{path_segments[1]}/{path_segments[2]}')
            yield base_uri, department_name
        elif len(path_segments) == 3:
            yield entity.uri, None

    # Iterate all shot entities (sequence/shot/department)
    for entity in api.config.list_entities(Uri.parse_unsafe('entity:/shots/*/*/*'), closure=True):
        # Extract department from URI path
        path_segments = [s for s in entity.uri.path.split('/') if s]
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

    # Check all nodes for shots that reference this asset
    for node in graph.nodes.values():
        # Check if this node is a shot
        if _get_entity_type(node.entity_uri) == 'shot':
            # Check if any dependencies are the target asset
            for dep in node.dependencies:
                if _get_entity_type(dep.entity_uri) == 'asset':
                    # Compare URIs directly
                    if dep.entity_uri == asset_uri:
                        shot_uris.add(node.entity_uri)
                        break

    return sorted(list(shot_uris), key=str)




# === Resolution ===

def _get_latest_export_path(entity_uri: Uri, variant_name: str, department_name: str) -> Optional[Path]:
    """Get latest export path for entity/variant/department."""
    return latest_export_path(entity_uri, variant_name, department_name)


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

    # Helper: Find latest shot layer paths and extract assets
    def _latest_shot_layer_paths(shot_departments, variant_name):
        layer_data = dict()
        shot_assets = dict()
        asset_inputs = dict()  # Track inputs per asset for staged output

        # First pass: collect all assets and their sources from each department
        dept_asset_data = {}  # {dept: [(asset_uri, instance, inputs), ...]}
        for department_name in shot_departments:
            # Find latest layer version (with variant support)
            latest_version_path = _get_latest_export_path(shot_uri, variant_name, department_name)
            if latest_version_path is None:
                continue

            # Find shot info
            context_file_path = latest_version_path / 'context.json'
            context_data = load_json(context_file_path)
            if context_data is None:
                continue

            layer_info = ctx.find_output(
                context_data,
                uri=str(shot_uri),
                department=department_name
            )

            if layer_info is None:
                continue

            # Collect assets with their inputs
            dept_asset_data[department_name] = []
            for asset_datum in layer_info['parameters'].get('assets', []):
                asset_uri = Uri.parse_unsafe(asset_datum['asset'])
                instance_name = asset_datum['instance']
                inputs = asset_datum.get('inputs', [])
                dept_asset_data[department_name].append((asset_uri, instance_name, inputs, latest_version_path))

        # Determine which assets exist in each source department (for validation)
        source_dept_assets = {}  # {dept: set(asset_uri)}
        for dept, assets in dept_asset_data.items():
            source_dept_assets[dept] = {asset_uri for asset_uri, _, _, _ in assets}

        # Second pass: filter assets by source department
        for department_name, assets in dept_asset_data.items():
            layer_assets = dict()
            latest_version_path = None

            for asset_uri, instance_name, inputs, version_path in assets:
                latest_version_path = version_path

                # Determine source department from inputs
                source_dept = get_source_department(inputs, all_shot_departments)

                # Backwards compatibility: if no source, use first dept that has it
                if source_dept is None:
                    # Find first department in order that has this asset
                    for dept in all_shot_departments:
                        if dept in source_dept_assets and asset_uri in source_dept_assets[dept]:
                            source_dept = dept
                            break

                # Skip if source department is not in our resolution list
                if source_dept not in shot_departments:
                    continue

                # Verify source department still has this asset
                if source_dept not in source_dept_assets:
                    continue
                if asset_uri not in source_dept_assets[source_dept]:
                    continue

                # Include the asset
                if asset_uri not in shot_assets:
                    shot_assets[asset_uri] = set()
                if asset_uri not in layer_assets:
                    layer_assets[asset_uri] = set()

                shot_assets[asset_uri].add(instance_name)
                layer_assets[asset_uri].add(instance_name)

                # Track inputs for this asset (use first occurrence)
                if asset_uri not in asset_inputs:
                    asset_inputs[asset_uri] = inputs

            # Store layer data
            if latest_version_path is not None:
                layer_data[department_name] = (latest_version_path, layer_assets)

        return layer_data, shot_assets, asset_inputs


    # Helper: Find latest asset layer paths
    def _latest_asset_layer_paths(assets, asset_departments, asset_variants):
        layer_data = dict()

        for asset_uri in assets.keys():
            # Get variant for this asset (default if not specified)
            asset_variant = asset_variants.get(asset_uri, DEFAULT_VARIANT)

            for department_name in asset_departments:
                # Find latest layer version (with variant support)
                latest_version_path = _get_latest_export_path(asset_uri, asset_variant, department_name)
                if latest_version_path is None:
                    continue

                # Store layer data
                if department_name not in layer_data:
                    layer_data[department_name] = dict()
                layer_data[department_name][asset_uri] = latest_version_path

        return layer_data

    # Find latest paths
    shot_layer_paths, assets, asset_inputs = _latest_shot_layer_paths(shot_departments, shot_variant)
    asset_layer_paths = _latest_asset_layer_paths(assets, asset_departments, asset_variants)

    # Find root department layer (if it exists)
    root_layer = None
    root_version_path = latest_export_path(shot_uri, shot_variant, 'root')
    if root_version_path is not None:
        version_name = root_version_path.name
        layer_file_name = get_layer_file_name(shot_uri, shot_variant, 'root', version_name)
        root_layer_path = root_version_path / layer_file_name
        if root_layer_path.exists():
            root_layer = root_layer_path

    # Done
    return dict(
        assets=assets,
        asset_inputs=asset_inputs,  # Track inputs per asset for staged output
        shot_layers=shot_layer_paths,
        asset_layers=asset_layer_paths,
        root_layer=root_layer,  # Root department layer (base sublayer)
        shot_variant=shot_variant,
        asset_variants=asset_variants
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
        'department_layers': {dept_name: version_path}
    }
    """
    if not graph.scanned:
        raise ValueError("Graph not scanned")

    # Find latest version for each department (with variant support)
    department_layers = {}
    for department_name in asset_departments:
        latest_version_path = _get_latest_export_path(asset_uri, variant_name, department_name)
        if latest_version_path is None:
            continue
        department_layers[department_name] = latest_version_path

    return dict(
        asset_uri=asset_uri,
        variant=variant_name,
        department_layers=department_layers
    )
