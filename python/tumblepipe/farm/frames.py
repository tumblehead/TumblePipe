"""Frame-numbered file paths, as the farm's configs and tools spell them.

A frame sequence is named ``<name>.<frame>.<ext>`` with the frame written
as a token: ``####`` in every farm config and receipt pattern, ``$F4``
where Houdini or husk expands it. Frames are four digits, zero padded.
"""

from pathlib import Path

FRAME_TOKEN = '####'
HOUDINI_FRAME_TOKEN = '$F4'


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
