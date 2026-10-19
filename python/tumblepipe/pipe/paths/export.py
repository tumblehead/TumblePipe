from typing import Optional
from pathlib import Path

from tumblepipe.api import api
from tumblepipe.util.uri import Uri

from tumblepipe.pipe.paths.version import (
    list_version_paths,
    get_next_version_path,
)

###############################################################################
# Export Paths
###############################################################################
# export:/{entity}/{channel}/{dept}/{version}/ — every export has a channel,
# "default" being the standard one.
###############################################################################

#: The reserved channel ``layer_split`` publishes a department's
#: channel-independent part to: export:/{entity}/_shared/{dept}/{version}/
SHARED_CHANNEL = '_shared'


def get_export_uri(
    entity_uri: Uri,
    channel_name: str,
    department_name: str
    ) -> Uri:
    """export:/{entity}/{channel}/{dept} — the directory holding its versions."""
    return (
        Uri.parse('export:/') /
        entity_uri.segments /
        channel_name /
        department_name
    )

def get_rig_export_path(asset_uri: Uri, channel_name: str = 'default') -> Path:
    """export:/{asset}/{channel}/rig — the directory holding a rig's versions."""
    return api.storage.resolve(get_export_uri(asset_uri, channel_name, 'rig'))

def get_export_path(
    entity_uri: Uri,
    channel_name: str,
    department_name: str,
    version_name: str
    ) -> Path:
    """The export directory of one version."""
    return api.storage.resolve(
        get_export_uri(entity_uri, channel_name, department_name) / version_name)

def latest_export_path(
    entity_uri: Uri,
    channel_name: str,
    department_name: str
    ) -> Optional[Path]:
    """The newest version's export directory, or None when there is none."""
    version_paths = list_version_paths(api.storage.resolve(
        get_export_uri(entity_uri, channel_name, department_name)))
    return version_paths[-1] if version_paths else None

def next_export_path(
    entity_uri: Uri,
    channel_name: str,
    department_name: str
    ) -> Path:
    """The export directory the next version will be written to."""
    return get_next_version_path(api.storage.resolve(
        get_export_uri(entity_uri, channel_name, department_name)))

def get_layer_file_name(
    entity_uri: Uri,
    channel_name: str,
    department_name: str,
    version_name: str
    ) -> str:
    """Get layer filename for a department export.

    Filename format: {entity}_{channel}_{dept}_{version}.usd

    Must match the resolver's department-flavor rule (resolve.rs:62-66).
    For the root department, use ``get_root_layer_file_name`` instead — the
    root layer is shot-level and does not include a channel in its name.
    For the shared channel, use ``get_shared_layer_file_name``.
    """
    assert department_name != 'root', (
        "use get_root_layer_file_name() for the root department"
    )
    entity_name = '_'.join(entity_uri.segments)
    return f'{entity_name}_{channel_name}_{department_name}_{version_name}.usd'

def get_root_layer_file_name(
    entity_uri: Uri,
    version_name: str
    ) -> str:
    """Get root-department layer filename.

    Filename format: {entity}_root_{version}.usda

    Must match the resolver's root-flavor rule (resolve.rs:113). The root
    layer is shot-level and has no channel axis, so unlike ordinary
    department layers the channel is not part of the filename.
    """
    entity_name = '_'.join(entity_uri.segments)
    return f'{entity_name}_root_{version_name}.usda'

def get_shared_layer_file_name(
    entity_uri: Uri,
    department_name: str,
    version_name: str
    ) -> str:
    """Layer filename of a ``SHARED_CHANNEL`` export.

    Filename format: {entity}_shared_{dept}_{version}.usd — not
    ``get_layer_file_name`` with the shared channel, which would double the
    underscore; the resolver has the same rule.
    """
    entity_name = '_'.join(entity_uri.segments)
    return f'{entity_name}_shared_{department_name}_{version_name}.usd'
