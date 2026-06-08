"""Typed factories for tumblepipe URIs.

Centralises URI construction so the catalog code reads as
``uris.entity_asset(category, name)`` instead of
``Uri.parse_unsafe(f"entity:/assets/{category}/{name}")``.

Every callsite that built a URI via inline f-string interpolation
goes through one of these helpers. New URI schemes get one new
function here, not a new f-string sprinkled across the catalog.

Imports of ``tumblepipe.util.uri`` are kept lazy: this module is
imported at catalog-load time (when tumblepipe may not yet be fully
available — see the warm-up dance in :meth:`PipelineCatalog.initialize`).
Each factory does its own one-line import so the cost is paid only
when a URI is actually constructed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tumblepipe.util.uri import Uri


def _Uri():
    """Return the ``Uri`` class, importing lazily on first use."""
    from tumblepipe.util.uri import Uri
    return Uri


# ── Entity URIs (assets and shots) ───────────────────────────


def entity(kind: str, second: str, third: str) -> "Uri":
    """``entity:/<kind>/<second>/<third>`` — used when ``kind`` is
    already known to be ``"assets"`` or ``"shots"``."""
    return _Uri().parse_unsafe(f"entity:/{kind}/{second}/{third}")


def entity_asset(category: str, name: str) -> "Uri":
    """``entity:/assets/<category>/<name>``"""
    return _Uri().parse_unsafe(f"entity:/assets/{category}/{name}")


def entity_shot(sequence: str, name: str) -> "Uri":
    """``entity:/shots/<sequence>/<name>``"""
    return _Uri().parse_unsafe(f"entity:/shots/{sequence}/{name}")


def entity_category(category: str) -> "Uri":
    """``entity:/assets/<category>`` — parent-only entity for an empty
    category. tumblepipe supports 2-segment parent entities natively
    (see ``ensure_sequence`` in tools/csv_shot_import.py)."""
    return _Uri().parse_unsafe(f"entity:/assets/{category}")


def entity_sequence(sequence: str) -> "Uri":
    """``entity:/shots/<sequence>`` — parent-only entity for an empty
    sequence."""
    return _Uri().parse_unsafe(f"entity:/shots/{sequence}")


# ── Entity schema URIs ───────────────────────────────────────
# ``ProjectConfigConvention.add_entity(uri, properties, schema_uri)``
# wants the schema for the entity's position. The pipeline declares
# these four:
#
#     schemas:/entity/assets/category          (entity:/assets/<X>)
#     schemas:/entity/assets/category/asset    (entity:/assets/<X>/<Y>)
#     schemas:/entity/shots/sequence           (entity:/shots/<X>)
#     schemas:/entity/shots/sequence/shot      (entity:/shots/<X>/<Y>)


def schema_asset() -> "Uri":
    """``schemas:/entity/assets/category/asset`` — for ``entity:/assets/<cat>/<asset>``."""
    return _Uri().parse_unsafe("schemas:/entity/assets/category/asset")


def schema_shot() -> "Uri":
    """``schemas:/entity/shots/sequence/shot`` — for ``entity:/shots/<seq>/<shot>``."""
    return _Uri().parse_unsafe("schemas:/entity/shots/sequence/shot")


def schema_category() -> "Uri":
    """``schemas:/entity/assets/category`` — for the 2-segment category parent."""
    return _Uri().parse_unsafe("schemas:/entity/assets/category")


def schema_sequence() -> "Uri":
    """``schemas:/entity/shots/sequence`` — for the 2-segment sequence parent."""
    return _Uri().parse_unsafe("schemas:/entity/shots/sequence")


def entity_from_suffix(suffix: str) -> "Uri":
    """``entity:/<suffix>`` — for callers that already have the
    post-scheme tail (e.g. extracted from another URI's segments)."""
    return _Uri().parse_unsafe(f"entity:/{suffix}")


# ── Group URIs ───────────────────────────────────────────────


def group(path: str) -> "Uri":
    """``groups:/<path>`` (e.g. ``groups:/shots/foo``)."""
    return _Uri().parse_unsafe(f"groups:/{path}")


def group_in_context(context: str, name: str) -> "Uri":
    """``groups:/<context>/<name>`` — for new-group creation where
    the path hasn't been joined yet."""
    return _Uri().parse_unsafe(f"groups:/{context}/{name}")


def groups_root() -> "Uri":
    """``groups:/`` — base for building member-relative paths via
    ``groups_root() / segments``."""
    return _Uri().parse_unsafe("groups:/")


# ── Scene URIs ───────────────────────────────────────────────


def scene(path_or_name: str) -> "Uri":
    """``scenes:/<path_or_name>``"""
    return _Uri().parse_unsafe(f"scenes:/{path_or_name}")


def scenes_root() -> "Uri":
    """``scenes:/`` — base for building member-relative paths."""
    return _Uri().parse_unsafe("scenes:/")


# ── Project / export / config URIs ───────────────────────────


def project_root() -> "Uri":
    """``project:/`` — workfile-side mirror of the entity layout."""
    return _Uri().parse_unsafe("project:/")


def export_scenes_root() -> "Uri":
    """``export:/scenes/`` — base for resolving Root export folders."""
    return _Uri().parse_unsafe("export:/scenes/")


def export_for_entity(uri: "Uri") -> "Uri":
    """``export:/<segments-of-entity-uri>`` — the export-side mirror
    of an ``entity:/`` URI. Used by the latest-export resolver."""
    return _Uri().parse_unsafe(f"export:/{'/'.join(uri.segments)}")


def dept_template(context: str, dept: str) -> "Uri":
    """``config:/templates/<context>/<dept>/template.py`` — points at
    the .py template file for new-from-template workfile creation."""
    return _Uri().parse_unsafe(
        f"config:/templates/{context}/{dept}/template.py"
    )


# ── Reparse pass-through ─────────────────────────────────────


def parse(text: str) -> "Uri":
    """Parse an arbitrary URI string.

    Use only for strings that came from outside the catalog (drop
    payloads, on-disk JSON, external callers). For typed
    constructions, prefer the named factories above so the URI scheme
    is documented at the callsite.
    """
    return _Uri().parse_unsafe(text)
