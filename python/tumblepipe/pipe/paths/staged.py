from typing import Optional
from pathlib import Path

from tumblepipe.api import api
from tumblepipe.util.uri import Uri

from tumblepipe.pipe.paths.version import (
    list_version_paths,
    get_latest_version_path,
    get_next_version_path,
)

###############################################################################
# Staged Paths
###############################################################################
def get_staged_path(
    entity_uri: Uri,
    version_name: str,
    variant_name: str = 'default'
    ) -> Path:
    staged_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_staged' /
        variant_name /
        version_name
    )
    return api.storage.resolve(staged_uri)

def get_staged_file_path(
    entity_uri: Uri,
    version_name: str,
    variant_name: str = 'default'
    ) -> Path:
    version_path = get_staged_path(
        entity_uri,
        version_name,
        variant_name
    )
    usd_file_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            version_name
        ]),
        'usda'
    ])
    return version_path / usd_file_name

def current_staged_path(
    entity_uri: Uri,
    variant_name: str = 'default'
    ) -> Optional[Path]:
    """Get path to the highest numbered staged version directory."""
    staged_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_staged' /
        variant_name
    )
    staged_path = api.storage.resolve(staged_uri)
    version_paths = list_version_paths(staged_path)
    if len(version_paths) == 0: return None
    current_version_path = version_paths[-1]
    return current_version_path

def current_staged_file_path(
    entity_uri: Uri,
    variant_name: str = 'default'
    ) -> Optional[Path]:
    """Get path to the highest numbered staged .usda file."""
    usd_file_name_pattern = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            '*'
        ]),
        'usda'
    ])
    current_version_path = current_staged_path(
        entity_uri,
        variant_name
    )
    if current_version_path is None: return None
    version_name = current_version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return current_version_path / usd_file_name

def next_staged_path(
    entity_uri: Uri,
    variant_name: str = 'default'
    ) -> Path:
    staged_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_staged' /
        variant_name
    )
    staged_path = api.storage.resolve(staged_uri)
    return get_next_version_path(staged_path)

def next_staged_file_path(
    entity_uri: Uri,
    variant_name: str = 'default'
    ) -> Path:
    usd_file_name_pattern = '.'.join([
        '_'.join(entity_uri.segments[1:] + [
            '*'
        ]),
        'usda'
    ])
    version_path = next_staged_path(
        entity_uri,
        variant_name
    )
    version_name = version_path.name
    usd_file_name = usd_file_name_pattern.replace('*', version_name)
    return version_path / usd_file_name

def get_staged_base_path(
    entity_uri: Uri,
    variant_name: str = 'default'
) -> Path:
    """Get base staged path for an entity (without version)."""
    staged_uri = (
        Uri.parse_unsafe('export:/') /
        entity_uri.segments /
        '_staged' /
        variant_name
    )
    return api.storage.resolve(staged_uri)

def get_latest_staged_path(
    entity_uri: Uri,
    variant_name: str = 'default'
) -> Optional[Path]:
    """Get path to the latest staged version directory."""
    base_path = get_staged_base_path(entity_uri, variant_name)
    return get_latest_version_path(base_path)

def get_latest_staged_file_path(
    entity_uri: Uri,
    variant_name: str = 'default'
) -> Optional[Path]:
    """Get path to the latest staged .usda file."""
    version_path = get_latest_staged_path(entity_uri, variant_name)
    if version_path is None:
        return None
    version_name = version_path.name
    usd_file_name = '.'.join([
        '_'.join(entity_uri.segments[1:] + [version_name]),
        'usda'
    ])
    return version_path / usd_file_name

###############################################################################
# Scene Staged Paths
###############################################################################
def get_scene_staged_path(scene_uri: Uri) -> Path:
    """Get base staged path for a scene."""
    staged_uri = (
        Uri.parse_unsafe('export:/') /
        'scenes' /
        scene_uri.segments /
        '_staged'
    )
    return api.storage.resolve(staged_uri)

def next_scene_staged_path(scene_uri: Uri) -> Path:
    """Get next version directory for scene export."""
    staged_path = get_scene_staged_path(scene_uri)
    return get_next_version_path(staged_path)

def get_current_scene_staged_file_path(scene_uri: Uri) -> Optional[Path]:
    """Get path to the highest numbered staged scene .usda file."""
    staged_path = get_scene_staged_path(scene_uri)
    version_path = get_latest_version_path(staged_path)
    if version_path is None:
        return None
    version_name = version_path.name
    scene_name = scene_uri.segments[-1]
    return version_path / f'{scene_name}_{version_name}.usda'

def get_scene_layer_file_name(scene_uri: Uri, version_name: str) -> str:
    """Get filename for scene layer (e.g., 'forest_v0001.usda')."""
    scene_name = scene_uri.segments[-1]
    return f'{scene_name}_{version_name}.usda'

def get_rig_export_path(asset_uri: Uri, variant_name: str = 'default') -> Path:
    export_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / variant_name / 'rig'
    return api.storage.resolve(export_uri)