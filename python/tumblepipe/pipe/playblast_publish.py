"""Writing a finished playblast: the same for the SOP and LOP nodes and the
farm worker.

A playblast takes its version before it renders and writes the movie when
it is done, minutes later. The version is held with a reservation
(``pipe.paths.review.reserve_playblast``) for that whole time, so a farm
submit or another session in between takes the version after it instead of
the same one. :func:`reserved_next_playblast` holds it for an in-session
playblast (a farm submit reserves in ``farm.pipelines.playblast``), and
:func:`publish_playblast` copies the movie out, checks it landed, lets the
reservation go and announces it.
"""

import shutil
from contextlib import contextmanager
from pathlib import Path

from tumblepipe.pipe import review_events
from tumblepipe.pipe.paths.review import (
    get_next_playblast_path,
    release_playblast,
    reserve_playblast,
)
from tumblepipe.util.uri import Uri


class PlayblastPublishError(Exception):
    """A finished playblast's movie could not be written to the network.
    Its version stays reserved: some of its files may be there."""


@contextmanager
def reserved_next_playblast(entity_uri: Uri, department_name: str):
    """The department's next playblast version, reserved for the block.

    A render that fails or is cancelled in the block gives the version back,
    so no version is burned for it. A publish that fails half way
    (PlayblastPublishError) keeps it: its partial files are there, as a
    farm job that dies while copying leaves them.
    """
    movie_path = get_next_playblast_path(entity_uri, department_name)
    reserve_playblast(movie_path)
    try:
        yield movie_path
    except PlayblastPublishError:
        raise
    except BaseException:
        release_playblast(movie_path)
        raise


def publish_playblast(
    movie: Path,
    outputs: list[Path],
    layer_movies: list[tuple[Path, Path]] = (),
) -> None:
    """Copy the encoded ``movie`` to ``outputs`` (the versioned movie first,
    then the daily) and each ``(movie, destination)`` of ``layer_movies``
    (a render layer's movie of the same version), check every copy landed,
    release the versioned movie's reservation and announce the change.
    Raises PlayblastPublishError, leaving the reservation, when a copy
    fails."""
    copies = [*layer_movies, *((movie, output) for output in outputs)]
    try:
        for source, destination in copies:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    except OSError as error:
        raise PlayblastPublishError(f'copying the playblast failed: {error}') from error
    missing = [destination for _source, destination in copies if not destination.exists()]
    if missing:
        raise PlayblastPublishError(
            'playblast not copied: ' + ', '.join(str(path) for path in missing)
        )
    release_playblast(outputs[0])
    review_events.announce_written(outputs)
