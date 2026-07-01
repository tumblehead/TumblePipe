import logging
from typing import Optional
from pathlib import Path

from tumblepipe.api import api
from tumblepipe.util.uri import Uri

from tumblepipe.pipe.paths.version import (
    list_version_paths,
    get_next_version_path,
)

logger = logging.getLogger(__name__)

###############################################################################
# Export Paths
###############################################################################
# Unified path structure: export:/{entity}/{variant}/{dept}/{version}/
# All exports include a variant, with "default" as the standard variant name.
###############################################################################

def get_export_path(
    entity_uri: Uri,
    variant_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    """Get export path for entity/variant/department/version.

    Path structure: export:/{entity}/{variant}/{dept}/{version}/
    """
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        variant_name /
        department_name /
        version_name
    )
    return api.storage.resolve(export_uri)

def latest_export_path(
    entity_uri: Uri,
    variant_name: str,
    department_name: str
    ) -> Optional[Path]:
    """Get the latest version path for an export."""
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        variant_name /
        department_name
    )
    export_path = api.storage.resolve(export_uri)
    version_paths = list_version_paths(export_path)
    if len(version_paths) == 0:
        logger.debug(
            f"No export versions found: uri={entity_uri}, "
            f"variant={variant_name}, dept={department_name}"
        )
        return None
    latest_version_path = version_paths[-1]
    logger.debug(
        f"Resolved latest export: {latest_version_path}"
    )
    return latest_version_path

def next_export_path(
    entity_uri: Uri,
    variant_name: str,
    department_name: str
    ) -> Path:
    """Get the next version path for an export."""
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        variant_name /
        department_name
    )
    export_path = api.storage.resolve(export_uri)
    return get_next_version_path(export_path)

def get_export_uri(
    entity_uri: Uri,
    variant_name: str,
    department_name: str
    ) -> Uri:
    """Get export URI for entity/variant/department.

    Returns the URI (not resolved path) for use in import modules.
    """
    return (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        variant_name /
        department_name
    )

def get_layer_file_name(
    entity_uri: Uri,
    variant_name: str,
    department_name: str,
    version_name: str
    ) -> str:
    """Get layer filename for a department export.

    Filename format: {entity}_{variant}_{dept}_{version}.usd

    Must match the resolver's department-flavor rule (resolve.rs:62-66).
    For the root department, use ``get_root_layer_file_name`` instead — the
    root layer is shot-level and does not include a variant in its name.
    """
    assert department_name != 'root', (
        "use get_root_layer_file_name() for the root department"
    )
    entity_name = '_'.join(entity_uri.segments)
    return f'{entity_name}_{variant_name}_{department_name}_{version_name}.usd'

def get_root_layer_file_name(
    entity_uri: Uri,
    version_name: str
    ) -> str:
    """Get root-department layer filename.

    Filename format: {entity}_root_{version}.usda

    Must match the resolver's root-flavor rule (resolve.rs:113). The root
    layer is shot-level and has no variant axis, so unlike ordinary
    department layers the variant is not part of the filename.
    """
    entity_name = '_'.join(entity_uri.segments)
    return f'{entity_name}_root_{version_name}.usda'

###############################################################################
# Shared Export Paths (for layer_split)
###############################################################################
# Shared exports use '_shared' as a reserved variant name
# Path structure: export:/{entity}/_shared/{dept}/{version}/
###############################################################################

def get_shared_export_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str
    ) -> Path:
    """Get shared export path (_shared variant)."""
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_shared' /
        department_name /
        version_name
    )
    return api.storage.resolve(export_uri)

def latest_shared_export_path(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[Path]:
    """Get latest shared export version path."""
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_shared' /
        department_name
    )
    export_path = api.storage.resolve(export_uri)
    version_paths = list_version_paths(export_path)
    if len(version_paths) == 0: return None
    return version_paths[-1]

def next_shared_export_path(
    entity_uri: Uri,
    department_name: str
    ) -> Path:
    """Get next shared export version path."""
    export_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_shared' /
        department_name
    )
    export_path = api.storage.resolve(export_uri)
    return get_next_version_path(export_path)

def get_shared_layer_file_name(
    entity_uri: Uri,
    department_name: str,
    version_name: str
    ) -> str:
    """Get shared layer filename.

    Filename format: {entity}_shared_{dept}_{version}.usd
    """
    entity_name = '_'.join(entity_uri.segments)
    return f'{entity_name}_shared_{department_name}_{version_name}.usd'
