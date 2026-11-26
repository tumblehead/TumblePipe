"""Reading the latest workfiles: which they are, and what a plain ``.hip`` says.

The Python twin of ``src/cinedex/cleanup/src/hip.rs``. Clean Up's exports,
renders and caches each keep what a latest workfile names, so they share
one list of the latest workfiles and one read of each (:class:`Workfiles`):
the reading is bound by the wire (840 MB of ``.hip`` on the 100 Mbit/s
share took 74 s), so reading them once per category tripled it.

A ``.hip`` is text: each node's parms sit in one ``{ … }`` block, one
``name [ … ] ( value )`` line each (:func:`node_blocks`). A ``.hiplc`` or
``.hipnc`` holds the same text behind headers of its own and is read the
same way (``archive.hipfile``); one in a form that cannot be read is
counted unreadable: what it names is not protected, and the plans say so.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from tumblepipe import naming
from tumblepipe.archive.hipfile import read_workfile_text
from tumblepipe.archive.longpath import long_path
from tumblepipe.archive.roots import ProjectRoots

#: The roots whose entities export: a workfile folder under one of them is
#: one entity's.
CONTEXTS = ('shots', 'assets')

#: Folders inside a workfile folder that hold no workfiles.
_NOT_WORKFILE_DIRS = frozenset({
    'cache', 'lops_cache', 'backup', 'render', 'galleries', '_context',
    '__pycache__',
})
_WORKFILE = re.compile(r'_(v\d{4})\.(hip|hiplc|hipnc)$', re.IGNORECASE)
_PARM = re.compile(r'^(\w+)\t\[[^\]]*\]\t\(\t(.*)\t\)\s*$')
_ENTITY = re.compile(r'entity:/((?:assets|shots)/[A-Za-z0-9_./-]+?)(?=[?"\s@]|$)')
_PINNED_URI = re.compile(r'entity:/[^\s"@<>]+')


def parm(line: str) -> tuple[str, str] | None:
    """``(name, value)`` of a parm line in a node's block, the value
    without its quotes; None for any other line."""
    match = _PARM.match(line)
    if match is None:
        return None
    return match.group(1), match.group(2).strip().strip('"')


def entities(value: str) -> list[tuple[str, ...]]:
    """The shots and assets a parm value names (``entity:/shots/sq/sh``),
    as their folder parts."""
    return [tuple(entity.strip('/').split('/')) for entity in _ENTITY.findall(value)]


def pinned_uris(value: str) -> list[str]:
    """Every ``entity:/`` URI in a parm value, query included."""
    return _PINNED_URI.findall(value)


def node_blocks(text: str) -> Iterator[list[tuple[str, str]]]:
    """The parms of each node of a plain ``.hip``: one list of
    ``(name, value)`` per ``{ … }`` block, in order. A line starting a
    block or ending one closes the block before it."""
    block: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.startswith('}') or line.startswith('{'):
            yield block
            block = []
            continue
        found = parm(line)
        if found is not None:
            block.append(found)
    yield block


@dataclass(frozen=True)
class LatestWorkfile:
    """The newest workfile of one workfile folder: its root's purpose
    (``assets``, ``shots`` or ``groups``) and the folder's parts under it."""
    purpose: str
    parts: tuple[str, ...]
    path: Path

    @property
    def label(self) -> str:
        return f'{self.purpose}/{"/".join(self.parts)}/{self.path.name}'

    @property
    def entity(self) -> tuple[str, ...] | None:
        """:func:`workfile_entity` of its folder."""
        return workfile_entity(self.purpose, self.parts)


def latest_workfiles(roots: ProjectRoots) -> list[LatestWorkfile]:
    """The newest workfile in every workfile folder of the assets, shots
    and groups roots."""
    found = []
    for purpose in ('assets', 'shots', 'groups'):
        root = roots.get(purpose)
        if not root.path.is_dir():
            continue
        others = {long_path(r.path) for r in roots.roots if r.path != root.path}
        for dirpath, dirs, files in os.walk(long_path(root.path)):
            here = Path(dirpath)
            dirs[:] = [
                d for d in dirs
                if d not in _NOT_WORKFILE_DIRS and not d.startswith('.')
                and here / d not in others
            ]
            newest = None
            for name in files:
                match = _WORKFILE.search(name)
                if match is None:
                    continue
                code = naming.get_version_code(match.group(1).lower())
                if newest is None or code > newest[0]:
                    newest = (code, name)
            if newest is not None:
                parts = here.relative_to(long_path(root.path)).parts
                found.append(LatestWorkfile(purpose, tuple(parts), here / newest[1]))
    return found


def workfile_entity(purpose: str, parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """The entity a workfile folder (``<…entity>/<department>``) is for,
    as export parts; None for a Multi's (it covers several)."""
    if purpose not in CONTEXTS or len(parts) < 2:
        return None
    return (purpose, *parts[:-1])


@dataclass(frozen=True)
class WorkfileRead:
    """A latest workfile and its text: None when it is in a form that
    cannot be read (``archive.hipfile``) or reading it failed (``error``)."""
    workfile: LatestWorkfile
    text: str | None
    error: str | None = None

    @property
    def unreadable(self) -> bool:
        """Read, but in a form whose text cannot be trusted: what it names
        is not protected."""
        return self.text is None and self.error is None


class Workfiles:
    """The latest workfiles of a project, read the first time a category
    asks and shared by the rest (:class:`WorkfileRead`, one each)."""

    def __init__(self, roots: ProjectRoots):
        self._roots = roots
        self._reads: list[WorkfileRead] | None = None

    def reads(self) -> list[WorkfileRead]:
        if self._reads is None:
            self._reads = [_read(workfile) for workfile in latest_workfiles(self._roots)]
        return self._reads


def _read(workfile: LatestWorkfile) -> WorkfileRead:
    try:
        text = read_workfile_text(workfile.path)
    except OSError as error:
        return WorkfileRead(workfile, None, f'{workfile.path}: {error}')
    return WorkfileRead(workfile, text)
