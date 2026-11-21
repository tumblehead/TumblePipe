"""A Houdini workfile's text: ``.hip``, ``.hiplc`` and ``.hipnc``.

A ``.hip`` is a cpio archive of text sections (``070707…<name>\\0<text>``).
A ``.hiplc`` (Houdini Indie) or ``.hipnc`` (Apprentice) holds the same text
sections, each behind a header of its own in place of cpio's::

    HouLC\\x1a<hex key><section name>\\0<section text>

(``HouNC`` for ``.hipnc``). The node parms in them are plain text, so the
readers of a ``.hip`` (``prune_exports.hip_pins``, ``cleanup.caches.
hip_names``, ``cleanup.renders.hip_pins``, ``archive.references``) read
them the same way: those go by parm lines and ``{ … }`` node blocks, never
by the section header.

Only that layout is trusted. A ``.hiplc``/``.hipnc`` that does not start
with such a header, or whose first section is not plain text, is not read
(:func:`workfile_text` returns None) and its caller counts it as unreadable:
reading it wrong would protect nothing it pins.
"""

from __future__ import annotations

from pathlib import Path
import re

from tumblepipe.archive.longpath import long_path

PLAIN = 'hip'
LICENSED = ('hiplc', 'hipnc')
SUFFIXES = (PLAIN, *LICENSED)

#: A licensed section header: magic, 0x1a, hex key, the section name, NUL.
_HEADER = re.compile(rb'Hou(?:LC|NC)\x1a[0-9a-f]{8,64}[^\x00\n]{1,256}\x00')
_MAGICS = (b'HouLC\x1a', b'HouNC\x1a')
#: How much of the first section must be plain text (all of a shorter one).
_SAMPLE = 64


def suffix(name: str) -> str:
    return name.rsplit('.', 1)[-1].lower() if '.' in name else ''


def _plain(data: bytes) -> bool:
    return all(byte in (9, 10, 13) or 32 <= byte < 127 or byte >= 128 for byte in data)


def readable(data: bytes, name: str) -> bool:
    """Whether :func:`workfile_text` reads ``data`` (the file ``name``)."""
    kind = suffix(name)
    if kind == PLAIN:
        return True
    if kind not in LICENSED:
        return False
    header = _HEADER.match(data)
    if header is None:
        return False
    first = data[header.end():header.end() + _SAMPLE]
    # A short first section ends at the next section's header.
    for magic in _MAGICS:
        at = first.find(magic)
        if at >= 0:
            first = first[:at]
    return bool(first) and _plain(first)


def workfile_text(data: bytes, name: str) -> str | None:
    """The text of the workfile ``name`` holding ``data``; None for one that
    cannot be read (see the module docstring)."""
    if not readable(data, name):
        return None
    return data.decode('utf-8', 'replace')


def read_workfile_text(path: Path) -> str | None:
    """:func:`workfile_text` of the file at ``path``. Raises OSError."""
    if suffix(path.name) not in SUFFIXES:
        return None
    return workfile_text(long_path(path).read_bytes(), path.name)
