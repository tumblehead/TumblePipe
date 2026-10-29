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
import os
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


def _root(storage: str) -> Path:
    """The storage root ``storage`` resolves to."""
    return api.storage.resolve(Uri.parse(storage))


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

    Each folder moves with one rename, never ``shutil.move``: when the rename
    fails, that falls back to copy-then-delete, which deletes the source file
    by file up to whatever is held open. An external user's shot was left as
    a lone undeletable (delete-pending, "access denied") ``layout`` folder
    that way, with the full copy already in ``_deleted/``. The archive sits
    in the same storage root, so a rename always suffices.
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
            os.rename(folder, target)
        except OSError as exc:
            restore_entity_folders(moved)
            raise ArchiveError(
                f'Could not move {folder} to the {ARCHIVE_DIR} folder: {exc}. '
                'Close any scene or viewer using its files and try again.'
            ) from exc
        moved.append((folder, target))
    for source, dest in moved:
        logger.info('Archived %s to %s', source, dest)
    return moved


def restore_entity_folders(moved: list[tuple[Path, Path]]) -> None:
    """Undo :func:`archive_entity_folders`: move each ``(from, to)`` back.

    Best effort by necessity — it runs while another failure is already on
    its way to the caller — so a folder that will not move back is logged
    with both paths, for a person to put back, rather than raised over the
    failure that made the rollback necessary.
    """
    for source, dest in reversed(moved):
        try:
            os.rename(dest, source)
        except OSError:
            logger.exception('Could not move %s back to %s', dest, source)


def delete_entity(entity_uri: Uri) -> None:
    """Remove ``entity_uri`` from the config, archiving its folders first.

    Left in place, the folders would be picked up by a new entity of the same
    name. It also leaves every Multi it (or anything under it) was a member
    of: left listed, the Multi's template would lay out a graph for an
    entity that is gone. All or nothing: the folders move first
    (:class:`ArchiveError` when one cannot, with none moved), and if the
    config writes then fail the folders and the Multis' member lists are
    put back before the error propagates. Works on the active project.
    """
    from tumblepipe.config.groups import drop_member_everywhere, restore_members
    moved = archive_entity_folders(entity_uri)
    memberships = []
    try:
        memberships = drop_member_everywhere(entity_uri)
        api.config.remove_entity(entity_uri)
    except BaseException:
        restore_members(memberships)
        restore_entity_folders(moved)
        raise


class EntityHasFiles(ValueError):
    """A rename refused because the entity's files would stay under the old name."""


def rename_entity(entity_uri: Uri, new_name: str) -> Uri:
    """Rename ``entity_uri`` to ``new_name`` in the config; the new URI.

    Workfiles, exports and renders sit in folders named after the entity,
    and published layers name it inside, so renaming one that has files
    would leave all of that under the old name and the renamed entity empty:
    refused with :class:`EntityHasFiles`. Duplicate it under the new name
    instead. Every Multi listing the entity lists it under its new name.
    Works on the active project.
    """
    if has_entity_files(entity_uri):
        raise EntityHasFiles(
            f"'{entity_uri.segments[-1]}' has workfiles or exports on disk, so "
            "it can't be renamed: they would stay under the old name and the "
            "renamed entity would come up empty. Use Duplicate… on its card "
            "in the Asset Browser to make a copy under the new name, then "
            "delete the old one."
        )
    from tumblepipe.config.groups import rename_member_everywhere
    new_uri = Uri(entity_uri.purpose, [*entity_uri.segments[:-1], new_name])
    api.config.rename_entity(entity_uri, new_name)
    rename_member_everywhere(entity_uri, new_uri)
    return new_uri
