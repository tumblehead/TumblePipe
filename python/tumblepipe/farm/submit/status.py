"""Read what the Farm Submit grid shows: how up to date each step of each entity is.

Two halves, split by thread:

* :func:`plan_probe` runs on the **main thread**. It does every config read —
  which workspace a department's workfiles live in (a Multi member's live in
  the Multi's), where exports, playblasts, renders and comps land — and packs the
  answers into a :class:`RowProbe` of plain paths.
* :func:`scan` runs on a **worker thread**. It only lists directories and
  stats files, so it never touches the config store, ``hou`` or Qt, and the
  dialog stays responsive while a project's worth of network folders is read.

The result is a :class:`RowStatus` of modification times; turning those into
cell states (``submit_grid.publish_state`` / ``preview_state``) happens back on
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
    shared: bool = False   # the workfile is a Multi's, not the entity's own


@dataclass(frozen=True)
class CompProbe:
    """Where a shot's composite workfiles and merged comps are."""
    workspace_dir: Path    # the composite workspace (a Multi's, when one covers it)
    hip_base: str          # '<workfile segments>_composite' before '_vNNNN'
    slapcomp_dir: Path     # render:/render/<shot>/composite/slapcomp


@dataclass(frozen=True)
class RowProbe:
    uri: str
    publish: tuple[PublishProbe, ...]
    playblast_dir: Path | None     # render:/playblast/<entity>/<department>
    render_dir: Path | None        # render:/render/<entity>/<department>/default
    is_version: Callable[[str], bool] = field(compare=False, repr=False)
    version_code: Callable[[str], int] = field(compare=False, repr=False)
    comp: CompProbe | None = None  # shots only
    # (channel, export:/<entity>/_staged/<the channel it renders>) per
    # channel the entity defines, for the unstaged-channel warning (#53).
    staged: tuple[tuple[str, Path], ...] = ()


@dataclass
class RowStatus:
    uri: str
    hip_mtimes: dict[str, float | None] = field(default_factory=dict)
    export_mtimes: dict[str, float | None] = field(default_factory=dict)
    shared: set[str] = field(default_factory=set)  # departments on a Multi's workfile
    playblast_mtime: float | None = None
    render_mtime: float | None = None
    comp_workfile: bool = False        # the shot has a composite workfile
    comp_mtime: float | None = None    # newest merged comp version folder
    staged: set[str] = field(default_factory=set)  # channels with a staged build
    error: str | None = None


# ── main thread ───────────────────────────────────────────

def plan_probe(
    entity_uri,
    departments: Sequence[str],
    *,
    playblast_department: str | None,
    render_department: str | None,
    comp: bool = False,
    channels: Sequence[str] = (),
) -> RowProbe:
    """Resolve every folder the status of ``entity_uri`` needs.

    Main thread only: resolving a workspace reads the config (a Multi member
    in a department its Multi covers keeps its workfiles in the Multi's
    folder). Wrap a loop of these in ``config.coherent()``.

    ``comp`` (shots) adds the Comp column's folders: the composite workspace
    (resolved like a publish department's, so a Multi's workfile counts, as
    ``latest_complete_hip_file_path`` finds it for the re-comp) and the
    merged comp's ``composite/slapcomp`` versions.

    An asset's Render column measures its turntable (#53), where a
    studio's farm render writes: ``turntable:/render/<asset>/render/main``
    (``pipe.turntable``), whatever the render cut. ``channels`` adds each
    one's staged-build folder, for the row's unstaged-channel warning.
    """
    from tumblepipe.api import api
    from tumblepipe import naming
    from tumblepipe.util.uri import Uri
    from tumblepipe.pipe.paths import department_workspace, get_export_path
    from tumblepipe.pipe.paths.naming import entity_stem

    publish = []
    for department in departments:
        workfile_uri, workspace_dir = department_workspace(entity_uri, department)
        publish.append(PublishProbe(
            department=department,
            workspace_dir=Path(workspace_dir),
            hip_base=entity_stem(workfile_uri, department),
            # get_export_path(..., version) minus the version folder.
            export_dir=Path(get_export_path(entity_uri, 'default', department, 'v0001')).parent,
            shared=workfile_uri.purpose == 'groups',
        ))

    playblast_dir = None
    if playblast_department:
        playblast_dir = Path(api.storage.resolve(
            Uri.parse('render:/playblast') / entity_uri.segments
            / playblast_department
        ))
    render_dir = None
    if render_department and list(entity_uri.segments[:1]) == ['assets']:
        from tumblepipe.pipe import turntable
        render_dir = Path(api.storage.resolve(
            Uri.parse(f'{turntable.RENDER_PURPOSE}:/render') / entity_uri.segments
            / turntable.RENDER_DEPARTMENT / turntable.render_layer('default')
        ))
    elif render_department:
        render_dir = Path(api.storage.resolve(
            Uri.parse('render:/render') / entity_uri.segments
            / render_department / 'default'
        ))

    staged = ()
    if channels:
        from tumblepipe.pipe import render_layers
        from tumblepipe.pipe.paths import get_staged_base_path
        layers = render_layers.layer_names(entity_uri)
        staged = tuple(
            (channel, Path(get_staged_base_path(
                entity_uri, render_layers.stage_channel(entity_uri, channel, layers),
            )))
            for channel in channels
        )

    comp_probe = None
    if comp:
        from tumblepipe.pipe.comp import COMPOSITE_DEPARTMENT
        workfile_uri, workspace_dir = department_workspace(entity_uri, COMPOSITE_DEPARTMENT)
        comp_probe = CompProbe(
            workspace_dir=Path(workspace_dir),
            hip_base=entity_stem(workfile_uri, COMPOSITE_DEPARTMENT),
            slapcomp_dir=Path(api.storage.resolve(
                Uri.parse('render:/render') / entity_uri.segments
                / COMPOSITE_DEPARTMENT / 'slapcomp'
            )),
        )

    return RowProbe(
        uri=str(entity_uri),
        publish=tuple(publish),
        playblast_dir=playblast_dir,
        render_dir=render_dir,
        comp=comp_probe,
        staged=staged,
        is_version=naming.is_valid_version_name,
        version_code=naming.get_version_code,
    )


# ── worker thread ─────────────────────────────────────────

def _mtime(path: Path) -> float | None:
    try:
        return os.stat(path).st_mtime
    except FileNotFoundError:
        # Gone since the listing that found it. Any other OSError (a share
        # that will not answer) fails the row's scan, which shows it.
        return None


def _entries(directory: Path | None) -> list[os.DirEntry]:
    if directory is None:
        return []
    try:
        with os.scandir(directory) as it:
            return list(it)
    except FileNotFoundError:
        # Nothing published / rendered yet: the folder is made on first
        # write. Any other OSError fails the row's scan, which shows it.
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


def _latest_hip(probe: RowProbe, publish: PublishProbe | CompProbe) -> Path | None:
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
    # '<entity>_<dept>_v0003.mp4', or bare 'v0003.mp4' up to 1.87: the
    # version is what follows the last '_' (the whole stem when there is none).
    return _newest(
        (
            (entry.name[:-4].rpartition('_')[2], Path(entry.path))
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
            if publish.shared:
                status.shared.add(publish.department)
            export = _latest_version_dir(probe, publish.export_dir)
            status.export_mtimes[publish.department] = (
                None if export is None else _mtime(export)
            )
        playblast = _latest_mp4(probe, probe.playblast_dir)
        status.playblast_mtime = None if playblast is None else _mtime(playblast)
        render = _latest_version_dir(probe, probe.render_dir)
        status.render_mtime = None if render is None else _mtime(render)
        if probe.comp is not None:
            status.comp_workfile = _latest_hip(probe, probe.comp) is not None
            comp = _latest_version_dir(probe, probe.comp.slapcomp_dir)
            status.comp_mtime = None if comp is None else _mtime(comp)
        for channel, staged_dir in probe.staged:
            if _latest_version_dir(probe, staged_dir) is not None:
                status.staged.add(channel)
    except Exception as error:  # a scan failure greys one row, not the grid
        status.error = str(error)
    return status
