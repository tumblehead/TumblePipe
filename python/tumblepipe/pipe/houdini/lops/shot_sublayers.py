"""The sublayers of a shot's staged build, as th::import_shot reads them.

A staged build is a .usda whose sublayers are entity URIs — the root layer,
the scene's assets, and one per shot department and channel
(``entity:/shots/010/0010?dept=animation&channel=default&version=v0006``;
builds staged before the variant→channel rename say ``variant=``).
The URIs stay strings: the Sublayer LOP hands them to the USD resolver,
whose latest mode decides which version actually composes.
"""

from pathlib import Path

from tumblepipe.config.channels import CHANNEL_KEY, read_published_channel
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.paths import version_name_from_path
from tumblepipe.pipe.usd.sublayer_refs import read_staged_sublayer_refs
from tumblepipe import resolver


def parse_sublayer(ref: str) -> dict:
    """One staged sublayer ref as ``{path, type, department, channel}``.

    ``type`` is 'asset' for an asset's staged build, 'root' for the shot's
    root layer and 'shot_department' for a department layer.
    """
    uri = Uri.parse(ref)
    department = uri.query.get('dept')
    if uri.segments[0] == 'assets':
        layer_type = 'asset'
    elif department == 'root':
        layer_type = 'root'
    else:
        layer_type = 'shot_department'
    return {
        'path': ref,
        'type': layer_type,
        'department': department,
        CHANNEL_KEY: read_published_channel(uri.query, where=f'sublayer ref {ref!r}'),
    }


def parse_staged_sublayers(staged_file_path: Path) -> list[dict]:
    """Every sublayer of a staged build, parsed (see :func:`parse_sublayer`).

    A filesystem-path sublayer (a build staged before URIs) is refused:
    rebuild the shot.
    """
    sublayers = []
    for ref in read_staged_sublayer_refs(staged_file_path):
        if not ref.startswith('entity:/'):
            raise ValueError(
                f'{staged_file_path} sublayers {ref!r}, a file path: staged '
                'builds sublayer entity:/ URIs. Rebuild the shot.'
            )
        sublayers.append(parse_sublayer(ref))
    return sublayers


def asset_uri_of(layer_ref: str) -> Uri | None:
    """The asset a sublayer ref composes; None for a shot layer."""
    uri = Uri.parse(layer_ref)
    if uri.segments[0] != 'assets':
        return None
    return Uri(uri.purpose, uri.segments, {})


def layer_display_name(layer_info: dict) -> str:
    """The Layer Stack row label of a parsed sublayer."""
    match layer_info['type']:
        case 'root':
            return 'Root'
        case 'shot_department':
            name = layer_info['department'].capitalize()
            return f'{name} (downstream)' if layer_info.get('downstream') else name
        case 'asset':
            return f"Asset: {asset_uri_of(layer_info['path']).segments[-1]}"


def recorded_layer_version(layer_info: dict) -> str:
    """The version *recorded* in the layer ref — the staged build's pin, or
    'latest' for a ref without one.

    In 'latest' mode this is not what loads: ``execute`` strips the pin so
    the resolver floats to the newest export. Use
    :func:`layer_display_version` for anything artist-facing.
    """
    return Uri.parse(layer_info['path']).query.get('version', 'latest')


def effective_layer_version(layer_ref: str) -> str | None:
    """The version the resolver actually lands on for ``layer_ref``; None
    when the ref resolves to nothing (never exported, or gone).

    ``layer_ref`` must be the exact string handed to the Sublayer LOP — the
    point is to report what composed, so anything re-derived here instead of
    asked of the resolver can disagree with the loaded stage.
    """
    resolved = resolver.try_resolve_entity_uri(layer_ref)
    if resolved is None:
        return None
    return version_name_from_path(resolved)


def layer_display_version(layer_info: dict) -> str:
    """The version to show an artist: what composed, not what was pinned.

    ``effective_version`` is stamped by ``execute`` when it stripped a pin
    and asked the resolver; without it the recorded pin is what composes.
    """
    effective = layer_info.get('effective_version')
    if effective:
        return effective
    return recorded_layer_version(layer_info)
