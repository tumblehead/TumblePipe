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

from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri
from tumblepipe.config.variants import DEFAULT_VARIANT, get_entity_type
from tumblepipe.pipe.paths import latest_export_path


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
