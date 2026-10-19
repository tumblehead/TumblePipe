"""Publish *channel* configuration.

A **channel** is the pipeline's publish-tree fork: the path segment in
``export/<entity>/<channel>/<dept>/v####/`` under which a department's layer
is published, and the axis a shot renders along (bg/fg splits, character
passes). Every entity has an implicit ``default`` channel; custom channels
layer on top of it during resolution — a department with no export under a
channel falls back to its ``default`` export.

This concept used to be called a *variant*, which collided head-on with USD's
own ``variantSet`` — an unrelated mechanism that Solaris nodes (Add Variant /
Set Variant) and our ``th::create_asset_model`` / ``th::create_asset_lookdev``
HDAs author *on a prim* (their ``variants`` multiparm is that USD thing and
keeps its name). "Variant" now means exactly the USD thing everywhere; the
publish-tree fork is a "channel", in the UI, the code and the data:

* the config database stores an entity's custom channels under
  :data:`CHANNEL_PROPERTY` and a scene asset entry's channel under
  :data:`CHANNEL_KEY` — migration v13 (``src/wizard/core/src/migration.rs``)
  rewrote every older project on upgrade, so they are read in that one
  spelling only;
* farm job configs carry ``channel_name`` / ``channel_names``. A job pins the
  exact package version that submitted it (``farm.deadline.job_dependencies``),
  so submitter and worker always agree on the spelling;
* HDA parms are ``channel`` / ``channel#``; scenes saved with the old
  ``variant`` parm are upgraded on load (``pipe.houdini.node_version``).

Published data is the exception, and :func:`read_published_channel` is the one
place it is handled. Every ``export/**/context.json``, every staged ``.usda``
URI (``?variant=``) and every prim's pipeline ``customData`` written before
the rename spells the channel ``variant``, and published versions are
immutable — they are never rewritten, by a migration or anything else. So
everything that reads a channel *out of published data* accepts both
spellings, through that function, while every writer emits only ``channel``.
"""

from tumblepipe.api import api
from tumblepipe.config.entities import get_entity_type  # noqa: F401  (re-export)
from tumblepipe.util.uri import Uri

DEFAULT_CHANNEL = 'default'

#: Entity property holding an entity's custom channel names.
CHANNEL_PROPERTY = 'channels'

#: Key naming ONE channel in a record: a scene asset entry, an entry of
#: ``context.json`` ``parameters.assets[]``, a context output, a prim's
#: pipeline ``customData`` and an entity URI's query (``?channel=``).
CHANNEL_KEY = 'channel'

#: What :data:`CHANNEL_KEY` was spelled in everything published before the
#: rename. Read by :func:`read_published_channel` and nowhere else.
PUBLISHED_LEGACY_CHANNEL_KEY = 'variant'


class AmbiguousChannelError(RuntimeError):
    """A published record spells its channel both ways, and the two disagree.

    No writer ever emitted both, so this is a hand-edited or corrupt record;
    picking either value would be a guess with a wrong render behind it.
    """


def read_published_channel(record, *, where: str, required: bool = False) -> str:
    """The channel a record read from PUBLISHED data names.

    Published data — ``export/**/context.json``, the entity URIs inside staged
    ``.usda`` layers, prim ``customData`` in exported layers and in the
    generated scripts saved scenes carry — exists in every version the
    pipeline ever wrote, and published versions are immutable: nothing
    rewrites them. Everything published before the variant→channel rename
    names the channel under ``variant``; everything after, under ``channel``.
    This is the single Python reader that accepts both (the Rust resolver's
    ``uri::channel_from_query`` is its counterpart), so the old spelling lives
    in exactly one place per language. Writers emit only :data:`CHANNEL_KEY`.

    An absent key is the ordinary encoding of the default channel, unless
    ``required``: a reader whose records always carry one raises
    :class:`KeyError` instead. Both spellings present with different values
    raises :class:`AmbiguousChannelError`. ``where`` names the record for
    either message.

    Not for config-database records (scenes, entity properties): those were
    migrated (v13) and are read under :data:`CHANNEL_KEY` alone.
    """
    new = None if record is None else record.get(CHANNEL_KEY)
    old = None if record is None else record.get(PUBLISHED_LEGACY_CHANNEL_KEY)
    if new is not None and old is not None and new != old:
        raise AmbiguousChannelError(
            f'{where} carries both {PUBLISHED_LEGACY_CHANNEL_KEY!r}={old!r} and '
            f'{CHANNEL_KEY!r}={new!r}, which name different channels. They are '
            f'two spellings of one key and must agree.'
        )
    if new is not None:
        return new
    if old is not None:
        return old
    if required:
        raise KeyError(f'{where} names no channel ({CHANNEL_KEY!r})')
    return DEFAULT_CHANNEL


def read_channel_names(properties) -> list[str]:
    """The custom channel names stored in an entity's *properties*.

    An absent key is not an error: an entity with no custom channels
    legitimately stores nothing and reads as ``default``-only.
    """
    if properties is None:
        return []
    return list(properties.get(CHANNEL_PROPERTY) or [])


def list_channels(entity_uri: Uri) -> list[str]:
    """Return channel names for an entity (asset or shot).

    The 'default' channel is always included as the first element,
    even if not explicitly defined in properties.

    Args:
        entity_uri: The entity URI (e.g., entity:/assets/CHAR/Hero
                    or entity:/shots/010/010)

    Returns:
        List of channel names, always starting with 'default'
    """
    properties = api.config.get_properties(entity_uri)
    if properties is None:
        return [DEFAULT_CHANNEL]

    channels = read_channel_names(properties)

    # Ensure 'default' is always first
    if DEFAULT_CHANNEL in channels:
        channels = [c for c in channels if c != DEFAULT_CHANNEL]

    return [DEFAULT_CHANNEL] + channels


def add_channel(entity_uri: Uri, channel_name: str) -> None:
    """Add a channel to an entity.

    Args:
        entity_uri: The entity URI
        channel_name: Name of the channel to add

    Raises:
        ValueError: If channel_name is 'default' (always exists implicitly)
        ValueError: If entity does not exist
        ValueError: If channel already exists
    """
    if channel_name == DEFAULT_CHANNEL:
        raise ValueError(f"Cannot add '{DEFAULT_CHANNEL}' channel - it exists implicitly")

    properties = api.config.get_properties(entity_uri)
    if properties is None:
        raise ValueError(f'Entity not found: {entity_uri}')

    channels = read_channel_names(properties)
    if channel_name in channels:
        raise ValueError(f'Channel already exists: {channel_name}')

    channels.append(channel_name)
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a channel layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(entity_uri) or {}
    own[CHANNEL_PROPERTY] = channels
    api.config.set_own_properties(entity_uri, own)


def remove_channel(entity_uri: Uri, channel_name: str) -> None:
    """Remove a channel from an entity.

    Args:
        entity_uri: The entity URI
        channel_name: Name of the channel to remove

    Raises:
        ValueError: If channel_name is 'default' (cannot be removed)
        ValueError: If entity does not exist
        ValueError: If channel does not exist
    """
    if channel_name == DEFAULT_CHANNEL:
        raise ValueError(f"Cannot remove '{DEFAULT_CHANNEL}' channel")

    properties = api.config.get_properties(entity_uri)
    if properties is None:
        raise ValueError(f'Entity not found: {entity_uri}')

    channels = read_channel_names(properties)
    if channel_name not in channels:
        raise ValueError(f'Channel not found: {channel_name}')

    channels.remove(channel_name)
    # Pin just this key onto the entity's OWN overrides. The resolved value
    # computed above is the right basis (a channel layers on top of what is
    # inherited), but writing the whole resolved dict back would pin every
    # inherited key here and detach the entity from its parents.
    own = api.config.get_own_properties(entity_uri) or {}
    own[CHANNEL_PROPERTY] = channels
    api.config.set_own_properties(entity_uri, own)


def has_channel(entity_uri: Uri, channel_name: str) -> bool:
    """Check if an entity has a specific channel.

    Args:
        entity_uri: The entity URI
        channel_name: Name of the channel to check

    Returns:
        True if the channel exists (including 'default'), False otherwise
    """
    return channel_name in list_channels(entity_uri)
