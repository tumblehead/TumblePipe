"""The file-name convention of everything the pipeline versions per entity.

Workfiles, staged builds, renders, dailies and rigs are named
``<entity>_<part>_..._<version>.<ext>``, where ``<entity>`` is the entity
URI's segments *without* the first one (``entity:/shots/010/sh020`` gives
``010_sh020``; the leading kind segment is already the directory tree).

Department export layers are the exception: their names keep every segment
(``shots_010_sh020_default_lighting_v0003.usd``) because the resolver builds
them that way (``src/resolver/src/resolve.rs``), so ``paths.export`` names
them itself. Do not unify the two: both are on disk in every live project.
"""

from pathlib import Path
from typing import Union

from tumblepipe import naming
from tumblepipe.util.uri import Uri


def entity_stem(entity_uri: Uri, *parts: str) -> str:
    """``<entity>_<part>_...`` for ``entity_uri``, the first segment dropped.

    ``entity_stem(Uri('entity:/shots/010/sh020'), 'lighting', 'v0003')`` is
    ``'010_sh020_lighting_v0003'``.
    """
    return '_'.join([*entity_uri.segments[1:], *parts])


def _trailing_version(path: Union[Path, str]) -> str | None:
    _, separator, version_name = Path(path).stem.rpartition('_')
    if not separator or not naming.is_valid_version_name(version_name):
        return None
    return version_name


def is_versioned(path: Union[Path, str]) -> bool:
    """Whether ``path``'s file name ends in ``_<version>`` (any extension)."""
    return _trailing_version(path) is not None


def version_of(path: Union[Path, str]) -> str:
    """The ``<version>`` a versioned file name ends in.

    Raises ValueError when the name does not end in ``_<version>``: a file
    that is not a version has no version to read back, and guessing one is
    how a lineage chain gets re-anchored. Filter with :func:`is_versioned`
    when a listing may hold other files.
    """
    version_name = _trailing_version(path)
    if version_name is None:
        raise ValueError(f"not a versioned file name: {Path(path).name}")
    return version_name
