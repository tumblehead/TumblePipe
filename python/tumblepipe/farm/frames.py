"""Frame-numbered file paths, as the farm's configs and tools spell them.

A frame sequence is named ``<name>.<frame>.<ext>`` with the frame written
as a token: ``####`` in every farm config and receipt pattern, ``$F4``
where Houdini or husk expands it. Frames are four digits, zero padded.
"""

from pathlib import Path

FRAME_TOKEN = '####'
HOUDINI_FRAME_TOKEN = '$F4'

# A farm or local-queue playblast renders on its worker's own disk and
# writes only the finished movie to the project. While it renders, the
# frames it has finished are copied into this folder beside the movie it
# will write (``<movie>.mp4.frames``), with what it renders in
# ``frames.json``, so they can be looked at meanwhile; the folder goes when
# the playblast ends, either way (``farm.workers.playblast``).
PLAYBLAST_FRAMES_SUFFIX = '.frames'
PLAYBLAST_FRAMES_INFO = 'frames.json'


def playblast_frames_dir(movie_path: Path) -> Path:
    """Where the frames of the playblast that will write ``movie_path``
    are while it renders."""
    return movie_path.with_name(movie_path.name + PLAYBLAST_FRAMES_SUFFIX)


def frame_file(pattern: Path, frame: int, token: str = FRAME_TOKEN) -> Path:
    """``pattern`` with the frame ``token`` in its file name replaced by
    ``frame``. Raises when the name has no such token."""
    if token not in pattern.name:
        raise ValueError(f'{pattern} has no {token} frame token')
    return pattern.parent / pattern.name.replace(token, f'{frame:04d}')


def with_frame_token(path: Path, token: str) -> Path:
    """``<name>.<frame>.<ext>`` with its frame field set to ``token``.

    The frame field is the second-to-last dot-separated part of the file
    name, whatever it holds (a frame number or another token). Raises on a
    name without one.
    """
    parts = path.name.rsplit('.', 2)
    if len(parts) != 3 or not parts[0]:
        raise ValueError(f'{path} is not named <name>.<frame>.<ext>')
    name, _, ext = parts
    return path.parent / f'{name}.{token}.{ext}'


def frame_of(path: Path) -> int:
    """The frame number of a ``<name>.<frame>.<ext>`` file."""
    return int(path.name.rsplit('.', 2)[1])
