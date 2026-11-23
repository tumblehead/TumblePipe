"""The project settings *Flat asset list* and *Flat shot list*.

Entities stay two levels deep everywhere: ``entity:/assets/<category>/<asset>``
and ``entity:/shots/<sequence>/<shot>`` name paths on disk, export layers,
farm jobs, Houdini's ``$CATEGORY`` / ``$SEQUENCE``, Cinedex and the MCP
server. A tiny project that wants no CHAR / PROP / SET and no sequences
gets one *implicit bucket* per kind instead: every new asset goes into one
category, every new shot into one sequence, and the Asset Browser stops
showing that level (no category / sequence rows in the sidebar, no Category /
Sequence field in New Asset / New Shot, no New Category / New Sequence).

Stored as ``flat_assets`` / ``flat_shots`` on ``config:/project``, each the
name of its implicit bucket. A project that never set them is not flat, so
they need no migration and no config layout bump. Turning one off leaves the
bucket as an ordinary category or sequence; nothing moves on disk.

Which bucket: :func:`choose_bucket`. A project that already keeps all its
shots in one sequence (``000``) goes on using it; otherwise the bucket is
:data:`DEFAULT_BUCKETS` (``ASSETS`` / ``SHOTS``), spelled as the project
already spells it if it has one, and never a name the other kind uses (a
browser card id is ``PROJECT/<bucket>/<name>``, and the kind is read back
from whether the bucket is a category).

Turning a kind flat then offers, with a confirmation, to remove the
buckets that are still empty (a new project's CHAR / PROP / SET):
:func:`removable_buckets`. The bucket is chosen first, so it is never
offered, and removing the offered ones leaves it the one
:func:`choose_bucket` would pick again.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from tumblepipe.util.uri import Uri

PROJECT_URI = Uri.parse('config:/project')

#: The two entity kinds that have a bucket level.
KINDS = ('assets', 'shots')

#: The ``config:/project`` property of each kind.
KEYS = {'assets': 'flat_assets', 'shots': 'flat_shots'}

#: The implicit bucket a project gets when it has no single bucket to adopt.
DEFAULT_BUCKETS = {'assets': 'ASSETS', 'shots': 'SHOTS'}

_OTHER = {'assets': 'shots', 'shots': 'assets'}


def _check_kind(kind: str) -> None:
    if kind not in KEYS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")


def bucket_from(properties: Mapping | None, kind: str) -> str | None:
    """The implicit bucket ``config:/project``'s ``properties`` name for
    ``kind``, or None when that kind is not flat.

    A hand-written ``true`` means the default bucket; anything that is not
    a non-blank string or ``True`` (``false``, ``""``, ``1``) means off.
    """
    _check_kind(kind)
    if not properties:
        return None
    value = properties.get(KEYS[kind])
    if value is True:
        return DEFAULT_BUCKETS[kind]
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def buckets(entities: Collection[tuple[str, ...]], kind: str) -> set[str]:
    """The categories (``kind="assets"``) or sequences (``"shots"``) among
    ``entities``, each a tuple of entity URI segments."""
    return {path[1] for path in entities if len(path) >= 2 and path[0] == kind}


def choose_bucket(kind: str, entities: Collection[tuple[str, ...]]) -> str:
    """The implicit bucket ``kind`` gets when the project turns flat.

    - The project's only bucket of that kind, when it has exactly one: its
      entities are already all in it.
    - Else :data:`DEFAULT_BUCKETS`, in the spelling of an existing bucket
      that differs from it only by case (the store refuses case variants).
    - Never a name the other kind uses, by casefold: ``SHOTS2``, ``SHOTS3``
      ... then.
    """
    _check_kind(kind)
    own = buckets(entities, kind)
    other = {name.casefold() for name in buckets(entities, _OTHER[kind])}
    if len(own) == 1:
        (only,) = own
        if only.casefold() not in other:
            return only
    by_fold = {name.casefold(): name for name in own}
    base = DEFAULT_BUCKETS[kind]
    candidate, number = base, 1
    while candidate.casefold() in other:
        number += 1
        candidate = f'{base}{number}'
    return by_fold.get(candidate.casefold(), candidate)


def name_taken(
    kind: str, name: str, entities: Collection[tuple[str, ...]],
) -> str | None:
    """The bucket already holding an entity of ``kind`` named ``name`` by
    casefold, or None. A flat list shows no bucket, so two shots ``010`` in
    two sequences would be two identical rows: a flat kind refuses a name
    any of its buckets has."""
    _check_kind(kind)
    fold = name.casefold()
    for path in sorted(entities):
        if len(path) == 3 and path[0] == kind and path[2].casefold() == fold:
            return path[1]
    return None


def removable_buckets(
    kind: str,
    entities: Collection[tuple[str, ...]],
    bucket: str,
    *,
    listed: Collection[tuple[str, ...]] = (),
    configured: Collection[str] = (),
) -> list[str]:
    """The buckets of ``kind`` that turning flat may offer to remove,
    sorted: the empty ones other than the implicit ``bucket``.

    Empty means nothing would be lost or left pointing at it:

    - no entity under it (``entities``, URI segment tuples). Only assets
      live under a category and only shots under a sequence (the entity
      schema), so this is every asset or shot it holds;
    - no Multi lists it or anything under it, or has it on its shared
      timeline (``listed``, entity paths): a Multi that lists a category or
      sequence takes in what is made there later;
    - no settings of its own (``configured``, bucket names): channels,
      departments, frame range or render settings set on the category or
      sequence row would go with it.

    Names are compared by casefold (the store refuses case variants), so
    a differently spelled bucket, Multi member or setting keeps a bucket
    too.
    """
    _check_kind(kind)
    keep = {bucket.casefold()}
    keep.update(path[1].casefold() for path in entities
                if len(path) >= 3 and path[0] == kind)
    keep.update(path[1].casefold() for path in listed
                if len(path) >= 2 and path[0] == kind)
    keep.update(name.casefold() for name in configured)
    return sorted(
        (name for name in buckets(entities, kind)
         if name.casefold() not in keep),
        key=lambda name: (name.casefold(), name),
    )


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #


def _entity_paths(store) -> set[tuple[str, ...]]:
    return {tuple(uri.segments) for uri in store.list_entity_uris(None, closure=True)}


def flat_bucket_in(store, kind: str) -> str | None:
    """The implicit bucket of ``kind`` in the project of ``store``, or None
    when that kind is not flat."""
    return bucket_from(store.get_properties(PROJECT_URI), kind)


def is_flat_in(store, kind: str) -> bool:
    return flat_bucket_in(store, kind) is not None


def ensure_bucket_in(store, kind: str, bucket: str) -> None:
    """Make the bucket entity (``entity:/<kind>/<bucket>``) when the project
    has none, so a flat project whose bucket was deleted still creates."""
    if bucket not in buckets(_entity_paths(store), kind):
        store.add_entity(Uri.parse(f'entity:/{kind}/{bucket}'), {})


def set_flat_in(store, kind: str, on: bool) -> str | None:
    """Turn ``kind`` flat (or not) for the project of ``store``; the
    implicit bucket, or None when turned off.

    On: the bucket is the one already set, else :func:`choose_bucket`'s,
    made when missing. Off: the key is dropped, the bucket stays an
    ordinary category or sequence. Only the key is written: the rest of
    ``config:/project`` (fps, the master edit) is kept.
    """
    _check_kind(kind)
    own = dict(store.get_own_properties(PROJECT_URI) or {})
    if not on:
        if KEYS[kind] in own:
            del own[KEYS[kind]]
            store.set_own_properties(PROJECT_URI, own)
        return None
    bucket = bucket_from(own, kind) or choose_bucket(kind, _entity_paths(store))
    ensure_bucket_in(store, kind, bucket)
    own[KEYS[kind]] = bucket
    store.set_own_properties(PROJECT_URI, own)
    return bucket


def _listed_paths(store) -> set[tuple[str, ...]]:
    """The entity paths a Multi of either kind lists or has on its shared
    timeline, as stored (a listed sequence stays a sequence)."""
    from tumblepipe.config.groups import list_groups_in
    out: set[tuple[str, ...]] = set()
    for context in KINDS:
        for group in list_groups_in(store, context):
            for uri in group.listed():
                out.add(tuple(uri.segments))
            for raw in group.timeline_starts:
                out.add(tuple(Uri.parse(raw).segments))
    return out


def removable_buckets_in(store, kind: str) -> list[str]:
    """:func:`removable_buckets` for the project of ``store``: the empty
    categories / sequences a flat ``kind`` may offer to remove, sorted.
    ``[]`` when ``kind`` is not flat."""
    _check_kind(kind)
    bucket = flat_bucket_in(store, kind)
    if bucket is None:
        return []
    entities = _entity_paths(store)
    configured = {
        name for name in buckets(entities, kind)
        if store.get_own_properties(Uri.parse(f'entity:/{kind}/{name}'))
    }
    return removable_buckets(
        kind, entities, bucket,
        listed=_listed_paths(store), configured=configured,
    )
