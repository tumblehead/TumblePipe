"""Whose workfile is the open scene: the one resolver every node uses.

A th:: node's 'from_context' sentinel, an HDA's on-created entity pick and
the create_asset nodes all ask the same question. They used to answer it
four ways — ``context.json`` beside any hip, the versioned workfile check,
with and without a group filter — so the same scene could resolve an entity
on one node and none on the next.

``scene_context()`` is the open workfile's ``Context`` (None for a scene that
is not a pipeline workfile). ``scene_entity_uri(contexts=...)`` narrows it to
an entity a caller can address: never a Multi's group URI, and only an
entity under one of ``contexts`` ('assets', 'shots'), which every caller
names explicitly — None means either.
"""

from pathlib import Path

import hou

from tumblepipe.util.uri import Uri
from tumblepipe.pipe.paths import Context, get_workfile_context


def scene_context() -> Context | None:
    """The open workfile's context; None for a non-pipeline scene."""
    return get_workfile_context(Path(hou.hipFile.path()))


def entity_in_contexts(
    context: Context | None, *, contexts: tuple[str, ...] | None,
    ) -> Uri | None:
    """``context``'s entity when it is an entity under one of ``contexts``."""
    if context is None:
        return None
    entity_uri = context.entity_uri
    # A Multi's workfile names its group, which is not an entity.
    if entity_uri.purpose != 'entity' or not entity_uri.segments:
        return None
    if contexts is not None and entity_uri.segments[0] not in contexts:
        return None
    return entity_uri


def scene_entity_uri(*, contexts: tuple[str, ...] | None) -> Uri | None:
    """The open workfile's entity, when it is one under ``contexts``."""
    return entity_in_contexts(scene_context(), contexts=contexts)
