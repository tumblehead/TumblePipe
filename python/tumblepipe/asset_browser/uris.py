"""Typed factories for tumblepipe URIs, and the URI -> browser id mapping.

Centralises URI construction so the catalog code reads as
``uris.entity_asset(category, name)`` instead of
``Uri.parse(f"entity:/assets/{category}/{name}")``. New URI schemes
get one new function here, not a new f-string sprinkled across the catalog.

:func:`asset_id_for` is the one place an entity URI becomes a browser asset
id (``PROJECT/<bucket>/<name>``); the reverse is
:meth:`AssetResolver.uri_for`.
"""

from __future__ import annotations

from tumblepipe.util.uri import Uri

#: The entity kinds the browser shows as asset / shot cards.
ENTITY_KINDS = ("assets", "shots")


# ── Browser ids ──────────────────────────────────────────────


def asset_id_for(project: str, uri: Uri) -> str:
    """The browser id of an asset or shot: ``PROJECT/<bucket>/<name>``.

    ``uri`` is an ``entity:/assets|shots/<bucket>/<name>`` URI — a Multi's
    member, a Root's asset entry, a loaded workfile's entity. Anything else
    (a bucket-only entity, a Multi, a scene) has no card, and asking for its
    id is a bug: it raises ValueError rather than minting an id that names
    nothing.
    """
    segments = uri.segments
    if (
        uri.purpose != "entity"
        or len(segments) != 3
        or segments[0] not in ENTITY_KINDS
    ):
        raise ValueError(f"{uri} is not an asset or shot URI")
    return f"{project}/{segments[1]}/{segments[2]}"


# ── Entity URIs (assets and shots) ───────────────────────────


def entity(kind: str, second: str, third: str) -> Uri:
    """``entity:/<kind>/<second>/<third>`` — used when ``kind`` is
    already known to be ``"assets"`` or ``"shots"``."""
    return Uri.parse(f"entity:/{kind}/{second}/{third}")


def entity_asset(category: str, name: str) -> Uri:
    """``entity:/assets/<category>/<name>``"""
    return Uri.parse(f"entity:/assets/{category}/{name}")


def entity_shot(sequence: str, name: str) -> Uri:
    """``entity:/shots/<sequence>/<name>``"""
    return Uri.parse(f"entity:/shots/{sequence}/{name}")


def entity_category(category: str) -> Uri:
    """``entity:/assets/<category>`` — parent-only entity for an empty
    category. tumblepipe supports 2-segment parent entities natively
    (see ``ensure_sequence`` in tools/csv_shot_import.py)."""
    return Uri.parse(f"entity:/assets/{category}")


def entity_sequence(sequence: str) -> Uri:
    """``entity:/shots/<sequence>`` — parent-only entity for an empty
    sequence."""
    return Uri.parse(f"entity:/shots/{sequence}")


# ── Group URIs ───────────────────────────────────────────────


def group(path: str) -> Uri:
    """``groups:/<path>`` (e.g. ``groups:/shots/foo``)."""
    return Uri.parse(f"groups:/{path}")


def group_in_context(context: str, name: str) -> Uri:
    """``groups:/<context>/<name>`` — for new-group creation where
    the path hasn't been joined yet."""
    return Uri.parse(f"groups:/{context}/{name}")


def groups_root() -> Uri:
    """``groups:/`` — base for building member-relative paths via
    ``groups_root() / segments``."""
    return Uri.parse("groups:/")


# ── Scene URIs ───────────────────────────────────────────────


def scene(path_or_name: str) -> Uri:
    """``scenes:/<path_or_name>``"""
    return Uri.parse(f"scenes:/{path_or_name}")


# ── Project / export / config URIs ───────────────────────────


def project_root() -> Uri:
    """``project:/`` — workfile-side mirror of the entity layout."""
    return Uri.parse("project:/")


def export_scenes_root() -> Uri:
    """``export:/scenes`` — base for resolving Root export folders."""
    return Uri.parse("export:/scenes")


def export_for_entity(uri: Uri) -> Uri:
    """``export:/<segments-of-entity-uri>`` — the export-side mirror
    of an ``entity:/`` URI. Used by the latest-export resolver."""
    return Uri.parse(f"export:/{'/'.join(uri.segments)}")


def dept_template(context: str, dept: str) -> Uri:
    """``config:/templates/<context>/<dept>/template.py`` — points at
    the .py template file for new-from-template workfile creation."""
    return Uri.parse(
        f"config:/templates/{context}/{dept}/template.py"
    )


# ── Reparse pass-through ─────────────────────────────────────


def parse(text: str) -> Uri:
    """Parse an arbitrary URI string.

    Use only for strings that came from outside the catalog (drop
    payloads, on-disk JSON, external callers). For typed
    constructions, prefer the named factories above so the URI scheme
    is documented at the callsite.
    """
    return Uri.parse(text)
