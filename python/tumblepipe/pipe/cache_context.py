"""The ``context.json`` a ``th::cache`` version writes when it finishes.

A cache version used to be just files: nothing said who cached it, from which
workfile version, over which frames, or whether it finished. A crashed cache
left a ``vNNNN`` folder indistinguishable from a good one, and an archive
could not tell a cache some workfile can write again from a one-off sim.

``save_cache_context`` is called after the cache's files are on disk, so the
sidecar doubles as the completeness marker: a version folder without one is
either unfinished or predates the sidecar (``is_complete`` cannot tell those
apart, and says so by returning False for both).

Shape (``kind`` lets a reader tell it from an export's ``context.json``)::

    {
      "kind": "cache",
      "uri": "entity:/shots/seq/010", "department": "fx",
      "cache": "smoke", "version": "v0003", "format": "sop",
      "frames": {"first": 1001, "last": 1100, "step": 1},
      "timestamp": "...", "user": "...",
      "producer": {"workfile": {...}, "node": "/obj/...", "tools": {...}}
    }
"""

from pathlib import Path

from tumblepipe.util.io import load_json_if_exists, store_json
from tumblepipe.util.uri import Uri

CONTEXT_FILE = 'context.json'
KIND = 'cache'


def cache_context(
    *,
    entity_uri: Uri,
    department_name: str,
    cache_name: str,
    version_name: str,
    cache_format: str,
    frames: tuple[int, int, int] | None,
    timestamp: str,
    user_name: str | None,
    producer: dict,
) -> dict:
    return dict(
        kind=KIND,
        uri=str(entity_uri),
        department=department_name,
        cache=cache_name,
        version=version_name,
        format=cache_format,
        frames=(
            dict(first=frames[0], last=frames[1], step=frames[2])
            if frames is not None else None
        ),
        timestamp=timestamp,
        user=user_name,
        producer=producer,
    )


def save_cache_context(version_path: Path, data: dict) -> Path:
    """Write ``data`` as the version's sidecar; returns its path."""
    if data.get('kind') != KIND:
        raise ValueError(f"not a cache context: kind={data.get('kind')!r}")
    path = Path(version_path) / CONTEXT_FILE
    store_json(path, data)
    return path


def load_cache_context(version_path: Path) -> dict | None:
    """The version's sidecar, or None when it has none (or is not a cache's)."""
    data = load_json_if_exists(Path(version_path) / CONTEXT_FILE)
    if not isinstance(data, dict) or data.get('kind') != KIND:
        return None
    return data


def is_complete(version_path: Path) -> bool:
    """Whether the version finished: its sidecar is there."""
    return load_cache_context(version_path) is not None
