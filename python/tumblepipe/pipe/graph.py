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
from typing import Optional

from tumblepipe.api import api
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import DEFAULT_CHANNEL
from tumblepipe.config.department import list_entity_department_names
from tumblepipe.config.entities import get_entity_type, is_terminal_entity
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


def entity_from_dict(data: dict) -> Optional[tuple[Uri, Optional[str]]]:
    """Create (entity_uri, department_name) tuple from context.json input/output dict."""
    entity_str = data.get('uri')
    if not entity_str:
        return None

    try:
        entity_uri = Uri.parse(entity_str)
    except ValueError:
        return None
    department = data.get('department')
    return entity_uri, department


# === Scanning ===

def _iter_all_entities():
    """Every (entity, department) pair whose latest export ``scan`` reads.

    The entities are the terminal assets and shots, decided by the schema
    (``is_terminal_entity``) rather than by URI depth: an asset is
    ``entity:/assets/<category>/<asset>``, and departments are not entities
    at all, so the ``entity:/assets/*/*/*`` filter this used to list matched
    nothing and every scan came back empty. Each entity contributes the
    departments assigned to it, disabled ones included, since a disabled
    department's exports are still on disk and still referenced.
    """
    with api.config.coherent():
        for context in ('assets', 'shots'):
            for entity_uri in api.config.list_entity_uris(
                Uri.parse(f'entity:/{context}'), closure=True
            ):
                if not is_terminal_entity(api.config, entity_uri):
                    continue
                for department_name in list_entity_department_names(
                    entity_uri, include_disabled=True
                ):
                    yield entity_uri, department_name


def _asset_dependencies(context_data: dict) -> list[Uri]:
    """The assets an export composes, from ``outputs[].parameters.assets``.

    That is where an export records the assets on its stage (one entry per
    asset, ``asset`` its URI). Its ``inputs`` only ever name shot
    departments, so reading ``inputs`` alone left every shot without an
    asset edge and ``find_shots_referencing_asset`` always answered ``[]``.
    """
    uris = []
    for output in context_data.get('outputs', []) or []:
        if not isinstance(output, dict):
            continue
        parameters = output.get('parameters') or {}
        for entry in parameters.get('assets', []) or []:
            raw = entry.get('asset') if isinstance(entry, dict) else None
            if not raw:
                continue
            try:
                uri = Uri.parse(raw)
            except ValueError:
                continue
            if uri not in uris:
                uris.append(uri)
    return uris


def _link(nodes: dict, key: str, dep_uri: Uri, dep_department) -> None:
    dep_key = entity_key(dep_uri, dep_department)
    if dep_key not in nodes:
        nodes[dep_key] = Node(entity_uri=dep_uri, department_name=dep_department)
    # Add bidirectional edges
    if nodes[dep_key] not in nodes[key].dependencies:
        nodes[key].dependencies.append(nodes[dep_key])
    if nodes[key] not in nodes[dep_key].references:
        nodes[dep_key].references.append(nodes[key])


def scan() -> Graph:
    """
    Scan all latest context.json files and build dependency graph.

    Returns: new Graph instance with populated nodes
    """
    nodes = {}

    # Scan all entities
    for entity_uri, department_name in _iter_all_entities():
        # Every edge is recorded in the default channel's latest export
        latest_path = latest_export_path(entity_uri, DEFAULT_CHANNEL, department_name)
        if latest_path is None:
            continue

        # Every export writes its context.json; one missing is a broken export
        context_data = load_json(latest_path / 'context.json')

        # Create node for this entity
        key = entity_key(entity_uri, department_name)
        if key not in nodes:
            nodes[key] = Node(entity_uri=entity_uri, department_name=department_name)

        # Add dependencies from inputs
        for input_data in context_data.get('inputs', []):
            dep_result = entity_from_dict(input_data)
            if dep_result is None:
                continue
            _link(nodes, key, *dep_result)

        # And from the assets the export composes (department unknown: the
        # asset as a whole)
        for asset_uri in _asset_dependencies(context_data):
            _link(nodes, key, asset_uri, None)

    return Graph(nodes=nodes, scanned=True)



# === Reverse Queries ===


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
