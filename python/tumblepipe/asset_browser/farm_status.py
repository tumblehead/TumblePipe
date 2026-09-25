"""Read what the Farm Submit grid shows: how up to date each step of each entity is.

Two halves, split by thread:

* :func:`plan_probe` runs on the **main thread**. It does every config read —
  which workspace a department's workfiles live in (a Multi member's live in
  the Multi's), where exports, playblasts and renders land — and packs the
  answers into a :class:`RowProbe` of plain paths.
* :func:`scan` runs on a **worker thread**. It only lists directories and
  stats files, so it never touches the config store, ``hou`` or Qt, and the
  dialog stays responsive while a project's worth of network folders is read.

The result is a :class:`RowStatus` of modification times; turning those into
cell states (``farm_grid.publish_state`` / ``preview_state``) happens back on
the main thread, because a preview's staleness depends on its department
cut, which the artist can change without a rescan of the publish columns.

Only folder listings and one ``stat`` per latest version are read — never a
USD layer or a ``context.json`` — because on a slow share every file
operation counts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

HIP_EXTENSIONS = ('hip', 'hiplc', 'hipnc')


@dataclass(frozen=True)
class PublishProbe:
    department: str
    workspace_dir: Path
    hip_base: str          # '<workfile segments>_<department>' before '_vNNNN'
    export_dir: Path       # export:/<entity>/default/<department>


@dataclass(frozen=True)
class RowProbe:
    uri: str
    publish: tuple[PublishProbe, ...]
    playblast_dir: Path | None     # render:/playblast/<entity>/<department>
    render_dir: Path | None        # render:/render/<entity>/<department>/default
    is_version: Callable[[str], bool] = field(compare=False, repr=False)
    version_code: Callable[[str], int] = field(compare=False, repr=False)


@dataclass
class RowStatus:
    uri: str
    hip_mtimes: dict[str, float | None] = field(default_factory=dict)
    export_mtimes: dict[str, float | None] = field(default_factory=dict)
    playblast_mtime: float | None = None
    render_mtime: float | None = None
    error: str | None = None


# ── main thread ───────────────────────────────────────────

def plan_probe(
    entity_uri,
    departments: Sequence[str],
    *,
    playblast_department: str | None,
    render_department: str | None,
) -> RowProbe:
    """Resolve every folder the status of ``entity_uri`` needs.

    Main thread only: resolving a workspace reads the config (a Multi member
    in a department its Multi covers keeps its workfiles in the Multi's
    folder). Wrap a loop of these in ``config.coherent()``.
    """
    from tumblepipe.api import api
    from tumblepipe.util.uri import Uri
    from tumblepipe.pipe.paths.workspace import resolve_workfile_uri
    from tumblepipe.pipe.paths import get_export_path

    publish = []
    for department in departments:
        workfile_uri = resolve_workfile_uri(entity_uri, department)
        purpose = 'groups:/' if workfile_uri.purpose == 'groups' else 'project:/'
        workspace_dir = api.storage.resolve(
            Uri.parse_unsafe(purpose) / workfile_uri.segments / department
        )
        publish.append(PublishProbe(
            department=department,
            workspace_dir=Path(workspace_dir),
            hip_base='_'.join(workfile_uri.segments[1:] + [department]),
            # get_export_path(..., version) minus the version folder.
            export_dir=Path(get_export_path(entity_uri, 'default', department, 'v0001')).parent,
        ))

    playblast_dir = None
    if playblast_department:
        playblast_dir = Path(api.storage.resolve(
            Uri.parse_unsafe('render:/playblast') / entity_uri.segments
            / playblast_department
        ))
    render_dir = None
    if render_department:
        render_dir = Path(api.storage.resolve(
            Uri.parse_unsafe('render:/render') / entity_uri.segments
            / render_department / 'default'
        ))

    return RowProbe(
        uri=str(entity_uri),
        publish=tuple(publish),
        playblast_dir=playblast_dir,
        render_dir=render_dir,
        is_version=api.naming.is_valid_version_name,
        version_code=api.naming.get_version_code,
    )


# ── worker thread ─────────────────────────────────────────

def _mtime(path: Path) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _entries(directory: Path | None) -> list[os.DirEntry]:
    if directory is None:
        return []
    try:
        with os.scandir(directory) as it:
            return list(it)
    except OSError:
        return []


def _newest(names_and_paths, probe: RowProbe):
    """The path whose version name sorts last, or None."""
    best = None
    best_code = None
    for version_name, path in names_and_paths:
        if not probe.is_version(version_name):
            continue
        code = probe.version_code(version_name)
        if best_code is None or code > best_code:
            best, best_code = path, code
    return best


def _latest_hip(probe: RowProbe, publish: PublishProbe) -> Path | None:
    prefix = publish.hip_base + '_'
    candidates = []
    for entry in _entries(publish.workspace_dir):
        stem, _, extension = entry.name.rpartition('.')
        if extension in HIP_EXTENSIONS and stem.startswith(prefix):
            candidates.append((stem[len(prefix):], Path(entry.path)))
    return _newest(candidates, probe)


def _latest_version_dir(probe: RowProbe, directory: Path | None) -> Path | None:
    return _newest(
        ((entry.name, Path(entry.path)) for entry in _entries(directory) if entry.is_dir()),
        probe,
    )


def _latest_mp4(probe: RowProbe, directory: Path | None) -> Path | None:
    return _newest(
        (
            (entry.name[:-4], Path(entry.path))
            for entry in _entries(directory) if entry.name.endswith('.mp4')
        ),
        probe,
    )


def scan(probe: RowProbe) -> RowStatus:
    """Read the modification times :class:`RowStatus` carries. Filesystem only."""
    status = RowStatus(uri=probe.uri)
    try:
        for publish in probe.publish:
            hip = _latest_hip(probe, publish)
            status.hip_mtimes[publish.department] = None if hip is None else _mtime(hip)
            export = _latest_version_dir(probe, publish.export_dir)
            status.export_mtimes[publish.department] = (
                None if export is None else _mtime(export)
            )
        playblast = _latest_mp4(probe, probe.playblast_dir)
        status.playblast_mtime = None if playblast is None else _mtime(playblast)
        render = _latest_version_dir(probe, probe.render_dir)
        status.render_mtime = None if render is None else _mtime(render)
    except Exception as error:  # a scan failure greys one row, not the grid
        status.error = str(error)
    return status
