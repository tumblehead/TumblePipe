"""The ``entity:/`` sublayer refs a staged build records.

The ``generate_*_sublayer_uri`` family writes them, ``parse_entity_sublayer_uri``
and ``read_staged_sublayer_refs`` read them back. A staged .usda is read as
text rather than through Sdf: these callers want to know what a staged build
*recorded* without composing the stage, resolving anything, or needing a USD
runtime — so this stays importable (and testable) outside Houdini.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from tumblepipe.config.channels import read_published_channel
from tumblepipe.pipe.usd.arcs import open_layer
from tumblepipe.util.uri import Uri


def add_sublayer(layer_path: Path, entity_ref: str) -> None:
    """Append an ``entity:/`` sublayer to the USD layer at ``layer_path``.

    The resolver resolves the URI (and its version) when the layer composes.
    """
    if not entity_ref.startswith('entity:/'):
        raise ValueError(f"not an entity:/ URI: {entity_ref}")
    layer = open_layer(layer_path)
    layer.subLayerPaths.append(entity_ref)
    layer.Save()


def generate_entity_sublayer_uri(
    entity_uri: Uri,
    channel_name: str,
    department_name: str,
    version_name: str | None = None
) -> str:
    """
    Generate an entity:/ URI for use as a USD sublayer reference.

    The generated URI can be used in USD sublayer paths and will be
    resolved by the custom asset resolver at runtime.

    Args:
        entity_uri: Entity URI (e.g., entity:/assets/SET/Arena)
        channel_name: Channel name (default, _shared, etc.)
        department_name: Department name (lookdev, model, etc.)
        version_name: Optional specific version (None = latest)

    Returns:
        Entity URI string for USD sublayer reference

    Example:
        >>> uri = Uri.parse('entity:/assets/SET/Arena')
        >>> generate_entity_sublayer_uri(uri, 'default', 'lookdev')
        'entity:/assets/SET/Arena?dept=lookdev&channel=default'
        >>> generate_entity_sublayer_uri(uri, '_shared', 'lookdev', 'v0013')
        'entity:/assets/SET/Arena?dept=lookdev&channel=_shared&version=v0013'
    """
    uri_str = f"{entity_uri}?dept={department_name}&channel={channel_name}"
    if version_name:
        uri_str += f"&version={version_name}"
    return uri_str


def generate_scene_sublayer_uri(
    scene_uri: Uri,
    version_name: str | None = None
) -> str:
    """
    Generate an entity:/ URI for a scene sublayer reference.

    The generated URI can be used in USD sublayer paths and will be
    resolved by the custom asset resolver at runtime.

    Args:
        scene_uri: Scene URI (e.g., scenes:/outdoor/forest)
        version_name: Optional specific version (None = latest)

    Returns:
        Entity URI string for USD sublayer reference

    Example:
        >>> uri = Uri.parse('scenes:/outdoor/forest')
        >>> generate_scene_sublayer_uri(uri)
        'entity:/scenes/outdoor/forest'
        >>> generate_scene_sublayer_uri(uri, 'v0013')
        'entity:/scenes/outdoor/forest?version=v0013'
    """
    # Convert scenes:/ URI to entity:/scenes/ format
    segments = '/'.join(str(s) for s in scene_uri.segments)
    uri_str = f"entity:/scenes/{segments}"
    if version_name:
        uri_str += f"?version={version_name}"
    return uri_str


def generate_staged_sublayer_uri(
    entity_uri: Uri,
    channel_name: str,
    version_name: str | None = None
) -> str:
    """
    Generate an entity:/ URI for a staged asset/shot sublayer reference.

    Unlike generate_entity_sublayer_uri which includes a department,
    this generates URIs for staged files (composed from all departments).

    Args:
        entity_uri: Entity URI (e.g., entity:/assets/SET/Arena)
        channel_name: Channel name (default, _shared, etc.)
        version_name: Optional specific version (None = latest)

    Returns:
        Entity URI string for USD sublayer reference

    Example:
        >>> uri = Uri.parse('entity:/assets/SET/Arena')
        >>> generate_staged_sublayer_uri(uri, 'default')
        'entity:/assets/SET/Arena?channel=default'
        >>> generate_staged_sublayer_uri(uri, 'default', 'v0013')
        'entity:/assets/SET/Arena?channel=default&version=v0013'
    """
    uri_str = f"{entity_uri}?channel={channel_name}"
    if version_name:
        uri_str += f"&version={version_name}"
    return uri_str


# Sublayer/reference asset paths in USDA text: @…@
_ASSET_REF_RE = re.compile(r'@([^@]+)@')


@dataclass(frozen=True)
class SublayerRef:
    """One ``@…@`` reference scraped from a staged .usda.

    ``department`` is set exactly when the ref was written by
    :func:`generate_entity_sublayer_uri` — an entity's own department layer.
    It is None when the ref came from :func:`generate_staged_sublayer_uri`,
    which points at a whole staged entity (from an asset's staged file, a
    nested sub-asset). That is the only structural difference between the
    two, and it is what separates "SET's model layer" from "an asset SET
    imports".
    """
    uri: str                    # the ref verbatim, query and all
    base: str                   # the ref with the query stripped
    department: str | None
    channel: str
    version: str | None


def parse_entity_sublayer_uri(uri_string: str) -> SublayerRef | None:
    """Parse an ``entity:/`` sublayer ref written by this module.

    Returns None for anything that is not an entity URI — a staged file may
    also carry plain relative paths (older builds), which are the caller's
    problem, not this function's.

    A ref with no query at all is still a valid entity ref (version-less,
    channel-less); it parses with ``channel='default'`` to match what the
    resolver assumes. Staged files are published data, so a ref written
    before the variant→channel rename (``?variant=``) parses the same way
    (see :func:`~tumblepipe.config.channels.read_published_channel`).

    Example:
        >>> ref = parse_entity_sublayer_uri(
        ...     'entity:/assets/SET/Arena?dept=lookdev&channel=_shared&version=v0013')
        >>> ref.department, ref.channel, ref.version
        ('lookdev', '_shared', 'v0013')
    """
    if not uri_string.startswith('entity:/'):
        return None

    base, _, query_string = uri_string.partition('?')

    params: dict[str, str] = {}
    if query_string:
        for param in query_string.split('&'):
            key, sep, value = param.partition('=')
            if sep:
                params[key] = value

    return SublayerRef(
        uri=uri_string,
        base=base,
        department=params.get('dept'),
        channel=read_published_channel(params, where=f"sublayer ref {uri_string!r}"),
        version=params.get('version'),
    )


def read_staged_sublayer_refs(staged_file_path: Path) -> list[str]:
    """Every ``@…@`` asset ref in a staged .usda, in file order.

    Raw strings: entity URIs and (for older builds) relative filesystem
    paths alike. Callers classify.
    """
    with open(staged_file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return [match.group(1) for match in _ASSET_REF_RE.finditer(content)]


def read_asset_department_layers(
    asset_uri: Uri,
    staged_file_path: Path
) -> tuple[list[SublayerRef], list[SublayerRef]]:
    """Split an asset's staged file into its own department layers and the
    sub-assets it imports.

    ``_store_asset_stage`` writes both into one sublayer list: the asset's
    department exports (``?dept=…``, same entity as the asset) and, for a
    set-style asset, a staged ref per tracked sub-asset (no ``dept``, a
    different entity). Telling them apart is the whole job here.

    Returns ``(department_layers, nested_assets)``, each in file order —
    which is *reverse* pipeline order, since the build emits sublayers
    strongest-first. Callers that want pipeline order must not rely on this
    ordering; order departments through the config pool instead.

    Entity bases are compared exactly. Casing is significant in entity URIs
    (a ``Clash``/``clash`` schism is a real failure mode in live projects),
    so a case-different base is a different asset here — not silently folded
    into this one.
    """
    asset_base = str(asset_uri)

    department_layers: list[SublayerRef] = []
    nested_assets: list[SublayerRef] = []

    for raw in read_staged_sublayer_refs(staged_file_path):
        ref = parse_entity_sublayer_uri(raw)
        if ref is None:
            continue
        if ref.department is not None and ref.base == asset_base:
            department_layers.append(ref)
        elif ref.department is None and ref.base != asset_base:
            nested_assets.append(ref)
        # Anything else — a department ref for a *different* entity, or a
        # dept-less ref to this same asset — is not something
        # _store_asset_stage writes. Skipping keeps a hand-edited or
        # future-format staged file from being reported as fact.

    return department_layers, nested_assets


def nested_asset_department_refs(
    refs: list[str],
    department_names: list[str],
    resolve,
) -> set[str]:
    """Asset department layer refs reachable from ``refs``, verbatim.

    A shot composes its assets several sublayers deep — shot staged file,
    root layer, scene staged file, asset staged file, and only then the
    asset's department exports — so an asset department cannot be left out
    by picking which files to sublayer. It has to be *muted*, and USD mutes
    by exact layer identifier: no patterns (a Configure Stage LOP takes
    ``*`` literally). For an ``entity:/`` sublayer the identifier is the
    ref exactly as authored in its parent layer, pin and all, so this
    returns the strings the staged files actually hold.

    ``refs`` are the layers the stage loads, as handed to the Sublayer LOP.
    ``resolve`` maps a ref to a filesystem path or None — the caller's
    resolver, in the same latest/pinned mode the stage resolves in, so the
    staged files read here are the ones that compose.

    Only ``.usda`` layers are read: staged files, scene files and the shot
    root. Department exports are crate files and never sublayer assets, so
    refs carrying a department (other than the shot's ``root``
    pseudo-department) are matched but never opened. Matching is limited
    to ``entity:/assets/`` refs — a shot department sharing a name with an
    asset one is not the asset's layer.
    """
    wanted = set(department_names)
    found: set[str] = set()
    if not wanted:
        return found

    visited: set[str] = set()
    pending = list(refs)
    while pending:
        ref = pending.pop()
        parsed = parse_entity_sublayer_uri(ref)
        if parsed is None:
            continue
        if parsed.department is not None:
            if parsed.base.startswith('entity:/assets/'):
                if parsed.department in wanted:
                    found.add(ref)
                continue
            if parsed.department != 'root':
                continue
        resolved = resolve(ref)
        if not resolved:
            continue
        path = Path(resolved)
        if path.suffix.lower() != '.usda':
            continue
        key = os.path.normcase(os.path.normpath(str(path)))
        if key in visited:
            continue
        visited.add(key)
        try:
            pending.extend(read_staged_sublayer_refs(path))
        except OSError:
            continue
    return found
