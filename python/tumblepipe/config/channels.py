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

``variant`` is what writers still emit
--------------------------------------
The v1.42.0 rename changed vocabulary only. It deliberately changed nothing
already written to disk, so these are still WRITTEN as ``variant`` today
(readers take either spelling — see below):

* the ``?variant=`` URI query parameter and the ``_shared`` sentinel —
  parsed by the Rust resolver (``src/resolver/src/uri.rs``) and embedded in
  every published staged ``.usda``,
* HDA parm *internal* names (``variant``, ``variant#``) — baked into every
  saved scene; only their labels changed. These are the one set that will
  NEVER move: Houdini resolves parms by name, so a tolerant reader cannot
  help, and renaming them would need dual parms plus a scene-load migration
  for no artist-visible gain,
* farm job JSON keys (``variant_name`` / ``variant_names``) — submit↔worker
  wire format, with in-flight jobs spanning an upgrade,
* the ``variant`` key inside published ``export/**/context.json``,
* the entity property key :data:`CHANNEL_PROPERTY` in the config DB, which
  the asset browser also carries as its ``metadata["variants"]`` payload.

Speak *channel* everywhere above those boundaries.
:mod:`tests.test_frozen_tokens` pins what writers emit, so a sweep that flips
one without the sequencing below fails there.

These are not frozen *forever*, and the way out is not a flag day
----------------------------------------------------------------
The intent is still to eliminate ``variant``. What makes that awkward is
that the data sits on the shared project drive while every reader ships
per-machine in the hpm package, so the two update on their own schedules and
will always pass each other somewhere.

So the readers accept BOTH spellings rather than insisting on one:
:func:`read_channel_names` and :func:`read_channel` here, and the Rust
resolver's ``variant``/``channel`` query keys. A project halfway through a
migration, or an artist a release behind, both read correctly.

That turns the changeover from one synchronised jump into four independent
steps, none of which is a flag day:

1. readers accept both (this) — nothing on disk changes;
2. once every machine has that, writers start emitting ``channel``;
3. a migration rewrites old data whenever convenient — interruptible and
   restartable, because mixed data reads fine;
4. much later, drop ``variant`` reading.

Step 2 is the one with a real ordering constraint: a writer that emits
``channel`` before every reader accepts it hands an out-of-date machine a key
it drops, and it resolves the *default* channel silently. Step 3 may never
need doing — departments re-export constantly, so the new spelling spreads
on its own.

Carrying both spellings at once is legitimate (step 2 can emit both for a
release). Carrying both with *different* values is not, and raises
:class:`AmbiguousChannelError` rather than guessing.

Two spellings are NOT this concept and must not be swept:

* USD's own ``variantSet`` — what ``th::create_asset_model`` /
  ``th::create_asset_lookdev`` author on a prim, including their
  ``variant_names`` / ``add_variant`` / ``variants`` multiparm. Identical
  spelling, unrelated mechanism.
* ``{entity}_{variant}_{dept}_{ver}.usd`` — that slot holds the channel
  *value*, not the literal word (see
  :func:`~tumblepipe.pipe.paths.export.get_layer_file_name`), so there is
  nothing to migrate. This docstring previously listed it as frozen; it was
  never a token.
"""

from tumblepipe.api import api
from tumblepipe.config.entities import get_entity_type  # noqa: F401  (re-export)
from tumblepipe.util.uri import Uri

DEFAULT_CHANNEL = 'default'

#: Entity property holding the custom channel names, as writers emit it today.
#: ``variants`` is what every live project's ``_config`` database stores.
CHANNEL_PROPERTY = 'variants'

#: Per-record key naming ONE channel, inside a stored list of assets
#: (``context.json`` ``parameters.assets[]``, the scene ``assets`` property,
#: an import node's asset table), as writers emit it today.
CHANNEL_KEY = 'variant'

#: Farm job-config keys, as the submitter emits them today. These cross a
#: different version boundary from the ones above: submitter to worker, with
#: in-flight jobs spanning an upgrade, and workers that self-bootstrap their
#: own package copy.
CHANNEL_NAME_KEY = 'variant_name'
CHANNEL_NAMES_KEY = 'variant_names'

#: All four, in the spelling the pipeline is moving to. Readers accept these;
#: nothing writes them yet. Flipping the writers is a separate, later release,
#: because every reader has to accept the new spelling *before* anything
#: starts producing it.
CHANNEL_PROPERTY_ALT = 'channels'
CHANNEL_KEY_ALT = 'channel'
CHANNEL_NAME_KEY_ALT = 'channel_name'
CHANNEL_NAMES_KEY_ALT = 'channel_names'


class AmbiguousChannelError(RuntimeError):
    """A record spells its channel both ways, and the two disagree.

    Both spellings mean the same key, so carrying both is fine — and is a
    reasonable thing for a writer to do while the changeover is in flight.
    Carrying both with *different* values is unresolvable: picking either is a
    guess, and the losing half is a wrong channel with a plausible render
    behind it.
    """


class MissingChannelError(KeyError):
    """A config that must name a channel names it under neither spelling.

    A subclass of :class:`KeyError` because that is what the farm readers
    raised before they learned the second spelling — ``config['settings']
    ['variant_name']`` on a config without it. Tolerance must not quietly turn
    a malformed job into a default-channel render.
    """


_MISSING = object()


def _read_either(data, old_key: str, new_key: str, default, *, where: str):
    """The value stored under either spelling of one key.

    The single implementation of the rule, so every boundary answers the same
    way: prefer the new spelling, fall back to the old, and refuse only when
    both are present and disagree. ``default`` of :data:`_MISSING` makes the
    key required.
    """
    if data is None:
        old = new = None
    else:
        old = data.get(old_key)
        new = data.get(new_key)
    if old is not None and new is not None and old != new:
        raise AmbiguousChannelError(
            f'{where} carries both {old_key!r}={old!r} and {new_key!r}={new!r}, '
            f'which name different channels. They are two spellings of one key '
            f'and must agree; fix whichever is wrong.'
        )
    if new is not None:
        return new
    if old is not None:
        return old
    if default is _MISSING:
        raise MissingChannelError(
            f'{where} names no channel: it has neither {old_key!r} nor '
            f'{new_key!r}.'
        )
    return default


def read_channel_names(properties, *, where: str) -> list[str]:
    """The custom channel names stored in an entity's *properties*.

    Accepts either spelling, so a project migrated to ``channels`` and one
    still on ``variants`` both read correctly, on any package. That tolerance
    is what lets the data on the shared drive and the readers that ship
    per-machine cross over independently instead of in one synchronised jump.

    An absent key is not an error: an entity with no custom channels
    legitimately stores nothing and reads as ``default``-only.

    ``where`` names the caller for the error message; make it something an
    artist can act on, like the entity URI.
    """
    old = None if properties is None else properties.get(CHANNEL_PROPERTY)
    new = None if properties is None else properties.get(CHANNEL_PROPERTY_ALT)
    if old is not None and new is not None and list(old) != list(new):
        raise AmbiguousChannelError(
            f'{where} carries both {CHANNEL_PROPERTY!r}={old!r} and '
            f'{CHANNEL_PROPERTY_ALT!r}={new!r}, which name different channels. '
            f'They are two spellings of one key and must agree; fix whichever '
            f'is wrong.'
        )
    return list(new if new is not None else (old or []))


def read_channel(record, *, where: str) -> str:
    """The channel one stored *record* names, defaulting to ``default``.

    As with :func:`read_channel_names`, either spelling is read and an absent
    key is the ordinary encoding of "the default channel".
    """
    return _read_either(
        record, CHANNEL_KEY, CHANNEL_KEY_ALT, DEFAULT_CHANNEL, where=where
    )


def read_channel_name(settings, *, where: str, default=_MISSING) -> str:
    """The channel a farm job config names, under either spelling.

    Required by default, because that is what the farm readers already were:
    ``config['settings']['variant_name']`` raises on a config that does not
    name a channel, and a malformed job must not quietly become a
    default-channel render. Pass ``default`` only where the call site already
    had one.
    """
    return _read_either(
        settings, CHANNEL_NAME_KEY, CHANNEL_NAME_KEY_ALT, default, where=where
    )


def read_channel_name_list(settings, *, where: str, default=_MISSING) -> list[str]:
    """The channel list a farm job config names, under either spelling."""
    value = _read_either(
        settings, CHANNEL_NAMES_KEY, CHANNEL_NAMES_KEY_ALT, default, where=where
    )
    return list(value) if value is not None else []


def has_channel_names_key(settings) -> bool:
    """Whether a farm config names a channel list under either spelling.

    For the job-config validators, which check shape rather than read values.
    """
    if settings is None:
        return False
    return CHANNEL_NAMES_KEY in settings or CHANNEL_NAMES_KEY_ALT in settings


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

    channels = read_channel_names(properties, where=f'entity {entity_uri}')

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

    channels = read_channel_names(properties, where=f'entity {entity_uri}')
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

    channels = read_channel_names(properties, where=f'entity {entity_uri}')
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
