from typing import Optional
from pathlib import Path

from tumblepipe.api import api
from tumblepipe.util.uri import Uri

from tumblepipe.pipe.paths.naming import entity_stem
from tumblepipe.pipe.paths.version import (
    get_latest_version_path,
    get_next_version_path,
)

###############################################################################
# Staged Paths
###############################################################################
def _staged_file_name(entity_uri: Uri, version_name: str) -> str:
    """``<entity>_<version>.usda`` -- the resolver's staged-file rule."""
    return f'{entity_stem(entity_uri, version_name)}.usda'

def get_staged_path(
    entity_uri: Uri,
    version_name: str,
    channel_name: str = 'default'
    ) -> Path:
    staged_uri = (
        Uri.parse('export:/') /
        entity_uri.segments /
        '_staged' /
        channel_name /
        version_name
    )
    return api.storage.resolve(staged_uri)

def get_staged_file_path(
    entity_uri: Uri,
    version_name: str,
    channel_name: str = 'default'
    ) -> Path:
    version_path = get_staged_path(
        entity_uri,
        version_name,
        channel_name
    )
    return version_path / _staged_file_name(entity_uri, version_name)

def current_staged_path(
    entity_uri: Uri,
    channel_name: str = 'default'
    ) -> Optional[Path]:
    """Get path to the newest finished staged version directory
    (``get_latest_version_path``: a failed build is read past)."""
    staged_uri = (
        Uri.parse('export:/') /
        entity_uri.segments /
        '_staged' /
        channel_name
    )
    return get_latest_version_path(api.storage.resolve(staged_uri))

def current_staged_file_path(
    entity_uri: Uri,
    channel_name: str = 'default'
    ) -> Optional[Path]:
    """Get path to the highest numbered staged .usda file."""
    current_version_path = current_staged_path(
        entity_uri,
        channel_name
    )
    if current_version_path is None: return None
    return current_version_path / _staged_file_name(
        entity_uri, current_version_path.name
    )

def next_staged_path(
    entity_uri: Uri,
    channel_name: str = 'default'
    ) -> Path:
    staged_uri = (
        Uri.parse('export:/') /
        entity_uri.segments /
        '_staged' /
        channel_name
    )
    staged_path = api.storage.resolve(staged_uri)
    return get_next_version_path(staged_path)

def next_staged_file_path(
    entity_uri: Uri,
    channel_name: str = 'default'
    ) -> Path:
    version_path = next_staged_path(
        entity_uri,
        channel_name
    )
    return version_path / _staged_file_name(entity_uri, version_path.name)

def get_staged_base_path(
    entity_uri: Uri,
    channel_name: str = 'default'
) -> Path:
    """Get base staged path for an entity (without version)."""
    staged_uri = (
        Uri.parse('export:/') /
        entity_uri.segments /
        '_staged' /
        channel_name
    )
    return api.storage.resolve(staged_uri)

def get_latest_staged_path(
    entity_uri: Uri,
    channel_name: str = 'default'
) -> Optional[Path]:
    """Get path to the latest staged version directory."""
    base_path = get_staged_base_path(entity_uri, channel_name)
    return get_latest_version_path(base_path)

def get_latest_staged_file_path(
    entity_uri: Uri,
    channel_name: str = 'default'
) -> Optional[Path]:
    """Get path to the latest staged .usda file."""
    version_path = get_latest_staged_path(entity_uri, channel_name)
    if version_path is None:
        return None
    return version_path / _staged_file_name(entity_uri, version_path.name)

###############################################################################
# Scene Staged Paths
###############################################################################
def get_scene_staged_path(scene_uri: Uri) -> Path:
    """Get base staged path for a scene."""
    staged_uri = (
        Uri.parse('export:/') /
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
