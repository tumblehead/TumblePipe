"""Why the browser's New Category / Sequence / Asset / Shot form would be refused.

The creation dialog used to learn a name was refused only after OK, from a
message box, and the dialog was gone with the typed name (#29). TumbleTrove
asks ``Catalog.validate_creation`` as the artist types; this is the rule it
answers with, the same one :meth:`EntityOps.create` refuses by, so the
dialog and the create cannot disagree. No ``hou``, no Qt: ``entities`` is the
project's entity paths, each a tuple of URI segments
(``("assets", "CHAR", "hero")``).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from tumblepipe.config.flat_entities import name_taken
from tumblepipe.config.shot_batch import name_problem

# option id -> (bucket field, bucket kind, entity kind, URI root)
_ENTITY_OPTIONS = {
    "new_asset": ("category", "category", "asset", "assets"),
    "new_shot": ("sequence", "sequence", "shot", "shots"),
}
_BUCKET_OPTIONS = {
    "new_category": ("category", "assets"),
    "new_sequence": ("sequence", "shots"),
}
#: The creation options this module checks.
OPTIONS = frozenset(_ENTITY_OPTIONS) | frozenset(_BUCKET_OPTIONS)
_OTHER = {"category": ("sequence", "shots"), "sequence": ("category", "assets")}


def buckets(entities: Collection[tuple[str, ...]], root: str) -> set[str]:
    """The categories (``root="assets"``) or sequences (``"shots"``)."""
    return {path[1] for path in entities if len(path) >= 2 and path[0] == root}


def bucket_problem(
    kind: str, name: str, entities: Collection[tuple[str, ...]],
) -> str:
    """Why a new ``kind`` bucket (category, sequence) may not be ``name``."""
    problem = name_problem(kind, name)
    if problem:
        return problem + "."
    other, other_root = _OTHER[kind]
    if name in buckets(entities, other_root):
        return f"'{name}' is already a {other}; a {kind} cannot share its name."
    return ""


def entity_root(option_id: str) -> str | None:
    """``"assets"`` for New Asset, ``"shots"`` for New Shot, else None."""
    entry = _ENTITY_OPTIONS.get(option_id)
    return entry[3] if entry else None


def with_flat_bucket(
    option_id: str, fields: Mapping[str, object], bucket: str | None,
) -> dict[str, object]:
    """``fields`` with the bucket field (category, sequence) set to the
    project's implicit ``bucket`` when its kind is flat: the form of a flat
    kind has no bucket field, and whatever a caller passed for it anyway
    gives way. Unchanged when ``bucket`` is None or the option makes no
    asset or shot."""
    out = dict(fields)
    entry = _ENTITY_OPTIONS.get(option_id)
    if bucket is not None and entry is not None:
        out[entry[0]] = bucket
    return out


def root_problem(name: str, exists: bool) -> str:
    """Why a new Root may not be ``name``: one of that name is there."""
    return f"Root '{name}' already exists." if exists else ""


def creation_problems(
    option_id: str,
    fields: Mapping[str, object],
    entities: Collection[tuple[str, ...]],
    flat: bool = False,
) -> dict[str, str]:
    """``{field key: reason}`` for the values in the creation form.

    A blank name gets no reason here (the form keeps OK disabled while a
    required field is empty; showing "no name" in red on an empty form is
    noise). An existing bucket is fine even if named before the name rule.

    ``flat``: the project lists this kind flat (``config.flat_entities``),
    so the form has no bucket field (the caller fills in the implicit one)
    and a name is taken when any bucket of the kind has it, by casefold.
    """
    def text(key: str) -> str:
        return str(fields.get(key) or "").strip()

    if option_id in _BUCKET_OPTIONS:
        key, root = _BUCKET_OPTIONS[option_id]
        name = text(key)
        if not name:
            return {}
        problem = bucket_problem(key, name, entities)
        if not problem and name in buckets(entities, root):
            problem = f"{key.capitalize()} '{name}' already exists."
        return {key: problem} if problem else {}

    if option_id in _ENTITY_OPTIONS:
        bucket_key, bucket_kind, kind, root = _ENTITY_OPTIONS[option_id]
        problems: dict[str, str] = {}
        bucket, name = text(bucket_key), text("name")
        if bucket and bucket not in buckets(entities, root):
            problem = bucket_problem(bucket_kind, bucket, entities)
            if problem:
                problems[bucket_key] = problem
        if name:
            problem = name_problem(kind, name)
            taken = name_taken(root, name, entities) if flat else None
            if problem:
                problems["name"] = problem + "."
            elif taken is not None:
                problems["name"] = f"{kind.capitalize()} '{name}' already exists."
            elif bucket and (root, bucket, name) in entities:
                problems["name"] = (
                    f"{kind.capitalize()} '{name}' already exists in '{bucket}'."
                )
        return problems

    return {}
