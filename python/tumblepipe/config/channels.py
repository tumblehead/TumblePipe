"""Publish *channel* configuration (formerly "variant").

A **channel** is the pipeline's publish-tree fork: the path segment in
``export/<entity>/<channel>/<dept>/v####/`` under which a department's layer
is published, and the axis a shot renders along (bg/fg splits, character
passes). Every entity has an implicit ``default`` channel; custom channels
layer on top of it during resolution — a department with no export under a
channel falls back to its ``default`` export.

Why the name changed
--------------------
This concept used to be called a *variant*, which collided head-on with USD's
own ``variantSet`` — an unrelated, genuinely different mechanism that Solaris
nodes (Add Variant / Set Variant) and our ``th::create_asset_model`` /
``th::create_asset_lookdev`` HDAs author *on a prim*. Artists read one word
and got the other concept. "Variant" now means exactly the USD thing
everywhere in the UI; the publish-tree fork is a "channel".

``variant`` is still the frozen wire token
------------------------------------------
The rename is a vocabulary change, not a data migration. Every already
published staged file embeds sublayer URIs carrying ``?variant=``, and the
Rust resolver (``src/resolver/src/uri.rs``) parses that key. So these stay
spelled ``variant`` forever, like a protocol field name:

* the ``?variant=`` URI query parameter and the ``_shared`` sentinel,
* the ``<channel>`` path segment and the ``{entity}_{variant}_{dept}_{ver}.usd``
  filename token,
* HDA parm *internal* names (``variant``, ``variant#``) — baked into every
  saved scene; only their labels changed,
* farm job JSON keys (``variant_name`` / ``variant_names``) — submit↔worker
  wire format with in-flight jobs,
* the entity property key :data:`CHANNEL_PROPERTY` in the config DB.

Translate at those boundaries; speak *channel* everywhere above them.
"""

from tumblepipe.api import api
from tumblepipe.config.entities import get_entity_type  # noqa: F401  (re-export)
from tumblepipe.util.uri import Uri

DEFAULT_CHANNEL = 'default'

#: Entity property holding the custom channel names. Frozen as ``variants``:
#: it is what every live project's ``_config`` database already stores.
CHANNEL_PROPERTY = 'variants'


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

    channels = properties.get(CHANNEL_PROPERTY, [])

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

    channels = properties.get(CHANNEL_PROPERTY, [])
    if channel_name in channels:
        raise ValueError(f'Channel already exists: {channel_name}')

    channels.append(channel_name)
    properties[CHANNEL_PROPERTY] = channels
    api.config.set_properties(entity_uri, properties)


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

    channels = properties.get(CHANNEL_PROPERTY, [])
    if channel_name not in channels:
        raise ValueError(f'Channel not found: {channel_name}')

    channels.remove(channel_name)
    properties[CHANNEL_PROPERTY] = channels
    api.config.set_properties(entity_uri, properties)


def has_channel(entity_uri: Uri, channel_name: str) -> bool:
    """Check if an entity has a specific channel.

    Args:
        entity_uri: The entity URI
        channel_name: Name of the channel to check

    Returns:
        True if the channel exists (including 'default'), False otherwise
    """
    return channel_name in list_channels(entity_uri)
