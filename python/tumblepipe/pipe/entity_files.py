"""Where an entity's files live on disk, and moving them out of the way.

An entity is a row in the config database; its work is folders named after
it in several storage roots (workfiles, exports, renders...). Removing the
row left the folders behind, so an entity created later under the same name
picked all of that work back up, and renaming the row left its folders under
the old name, so the renamed entity came up empty (both reported by an
external user who renamed assets in the Database Editor).

Deleting now archives the folders under ``_deleted/<stamp>/`` in each
storage root, and renaming an entity that has files is refused.
"""

import datetime as dt
import logging
import shutil
from pathlib import Path

from tumblepipe.api import api
from tumblepipe.util.uri import Uri

logger = logging.getLogger(__name__)

ARCHIVE_DIR = '_deleted'

# Storage locations keyed by an entity's URI segments. Each is (storage
# root, sub-path below the root that the segments go under).
ENTITY_STORAGE = (
    ('project:/', ()),
    ('proxy:/', ()),
    ('export:/', ()),
    ('render:/', ('render',)),
    ('render:/', ('playblast',)),
    ('render:/', ('daily',)),
)


class ArchiveError(RuntimeError):
    """The entity's folders could not all be moved; none were."""


def _root(storage: str) -> Path | None:
    try:
        root = api.storage.resolve(Uri.parse_unsafe(storage))
    except Exception:
        logger.warning('Could not resolve %s', storage, exc_info=True)
        return None
    return None if root is None else Path(root)


def entity_folders(entity_uri: Uri) -> list[tuple[Path, Path]]:
    """``(storage root, folder)`` for every folder holding ``entity_uri``'s files.

    Only folders that exist are listed. A category or sequence lists the
    folders its children live in, since theirs sit below it.
    """
    if entity_uri.purpose != 'entity' or not entity_uri.segments:
        return []
    folders = []
    seen = set()
    for storage, prefix in ENTITY_STORAGE:
        root = _root(storage)
        if root is None:
            continue
        folder = root.joinpath(*prefix, *entity_uri.segments)
        key = str(folder).lower()
        if key in seen or not folder.is_dir():
            continue
        seen.add(key)
        folders.append((root, folder))
    return folders


def has_entity_files(entity_uri: Uri) -> bool:
    """True if any folder named after ``entity_uri`` holds a file."""
    for _root_path, folder in entity_folders(entity_uri):
        if any(p.is_file() for p in folder.rglob('*')):
            return True
    return False


def archive_entity_folders(
    entity_uri: Uri, stamp: str | None = None,
) -> list[tuple[Path, Path]]:
    """Move ``entity_uri``'s folders to ``<root>/_deleted/<stamp>/<same path>``.

    All or nothing: if a folder cannot be moved (a file held open on
    Windows), the ones already moved are put back and ``ArchiveError`` is
    raised naming it. Returns the ``(from, to)`` pairs moved.
    """
    if stamp is None:
        stamp = dt.datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')
    moved: list[tuple[Path, Path]] = []
    for root, folder in entity_folders(entity_uri):
        target = root / ARCHIVE_DIR / stamp / folder.relative_to(root)
        n = 1
        while target.exists():
            target = target.with_name(f'{folder.name}_{n}')
            n += 1
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(target))
        except OSError as exc:
            for source, dest in reversed(moved):
                try:
                    shutil.move(str(dest), str(source))
                except OSError:
                    logger.exception('Could not move %s back to %s', dest, source)
            raise ArchiveError(
                f'Could not move {folder} to the {ARCHIVE_DIR} folder: {exc}. '
                'Close any scene or viewer using its files and try again.'
            ) from exc
        moved.append((folder, target))
    for source, dest in moved:
        logger.info('Archived %s to %s', source, dest)
    return moved
