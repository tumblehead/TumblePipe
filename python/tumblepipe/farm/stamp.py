"""Which project and entity a farm job is for, stamped on every job TumblePipe submits.

A Deadline job carries the stamp as ``ExtraInfoKeyValue`` pairs (its
``Props.ExDic`` when read back), a local job as its ``batch.json`` entry's
``extra_info``. The Farm Manager reads them to filter by project, and the
clean-ups (``farm.manager.cleanup``) to find an entity's jobs. A job
submitted before the stamp has none: readers fall back to the batch's name,
which starts with the project and names the entity
(``pipelines._batch.batch_title``).

No imports beyond the standard library: the Farm Manager's rows and the
submission runner both read stamps.
"""

from __future__ import annotations

PROJECT = 'TumblePipeProject'
ENTITY = 'TumblePipeEntity'


def stamp(project: str | None, entity=None) -> dict[str, str]:
    """The extra info a job of ``project`` (and ``entity``, a URI, when known) carries."""
    result = {}
    if project:
        result[PROJECT] = str(project)
    if entity:
        result[ENTITY] = str(entity)
    return result


def read(extra_info) -> tuple[str, str]:
    """``(project, entity)`` from a job's extra info; '' for what it does not carry."""
    if not isinstance(extra_info, dict):
        return '', ''
    return str(extra_info.get(PROJECT) or ''), str(extra_info.get(ENTITY) or '')
