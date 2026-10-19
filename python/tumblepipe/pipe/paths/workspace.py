import json
import logging
import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from tumblepipe.api import api
from tumblepipe import naming
from tumblepipe.config.groups import find_group
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri

from tumblepipe.pipe.paths.naming import entity_stem, is_versioned, version_of

logger = logging.getLogger(__name__)

# All valid Houdini file extensions (commercial, limited commercial, non-commercial)
HIP_EXTENSIONS = ('hip', 'hiplc', 'hipnc')

###############################################################################
# Workspace Paths
###############################################################################
def _version_code(file_path: Path) -> int:
    return naming.get_version_code(version_of(file_path))

def _resolve_workspace(
    entity_uri: Uri,
    department_name: str
    ) -> tuple[Uri, Path]:
    """Resolve an entity's department workspace.

    Returns ``(workfile_uri, workspace_path)``. A grouped entity resolves to
    its group's shared workspace under ``groups:/`` (with the group's URI as
    the workfile URI); an ungrouped entity resolves under ``project:/``.
    """
    if entity_uri.purpose == 'groups':
        workfile_uri = entity_uri
        purpose = 'groups:/'
    else:
        group = find_group(entity_uri.segments[0], entity_uri, department_name)
        workfile_uri = entity_uri if group is None else group.uri
        purpose = 'project:/' if group is None else 'groups:/'
    workspace_uri = (
        Uri.parse(purpose) /
        workfile_uri.segments /
        department_name
    )
    return workfile_uri, api.storage.resolve(workspace_uri)

def department_workspace(
    entity_uri: Uri,
    department_name: str
    ) -> tuple[Uri, Path]:
    """``(workfile_uri, workspace_path)`` of an entity's department.

    The folder its workfiles are saved in, and the URI they belong to: the
    entity's own, or — for a Multi member in a department the Multi covers —
    the Multi's (see :func:`resolve_workfile_uri`). Resolves against the
    active project.
    """
    return _resolve_workspace(entity_uri, department_name)

def entity_root_path(entity_uri: Uri) -> Path:
    """The entity's own folder in the project tree (``project:/<segments>``).

    Its department workspaces sit under it (unless a Multi covers them), as
    do its per-entity sidecars (``description.txt``, ``thumbnail.png``) and
    its master scene. Resolves against the active project.
    """
    return api.storage.resolve(Uri.parse('project:/') / entity_uri.segments)

def resolve_workfile_uri(entity_uri: Uri, department_name: str) -> Uri:
    """The URI a department workfile of ``entity_uri`` belongs to.

    The entity itself, or — for a Multi member in a department the Multi
    covers — the Multi, because that is whose folder the hip lands in. This
    is the URI a workfile's ``context.json`` must record: recording the
    member instead makes the Multi's shared scene claim to be that one shot,
    and Publish then drops every other member's export node.
    """
    workfile_uri, _ = _resolve_workspace(entity_uri, department_name)
    return workfile_uri

def get_workspace_relpath(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[str]:
    """The entity/department workspace as a path relative to the project root.

    This is the ``<context>/<group>/<entity>/<department>`` sub-path that
    per-workfile sidecar areas (caches, ``_context``, ...) anchor under. It is
    purpose-independent: the same relpath re-anchors under ``project:`` for the
    live tree or ``proxy:`` for the shared proxy mirror. Grouped entities
    resolve under ``project:/groups/...`` (``groups:`` lives inside the project
    tree), so the relpath stays valid for both purposes.

    Returns None if the workspace resolves outside the project root.
    """
    _, workspace_path = _resolve_workspace(entity_uri, department_name)
    project_root = api.storage.resolve(Uri.parse('project:/'))
    try:
        return '/'.join(workspace_path.relative_to(project_root).parts)
    except ValueError:
        return None

def _list_valid_hip_files(
    workspace_path: Path,
    base_pattern: str
    ) -> list[Path]:
    """Glob all hip channels (.hip, .hiplc, .hipnc) matching ``base_pattern``,
    keeping only validly-versioned files, sorted ascending by version code."""
    all_hip_files = []
    for ext in HIP_EXTENSIONS:
        all_hip_files.extend(workspace_path.glob(f'{base_pattern}.{ext}'))
    return sorted(filter(is_versioned, all_hip_files), key=_version_code)


###############################################################################
# What a workspace holds
###############################################################################
def is_reservation_stub(context_entry: dict) -> bool:
    """Whether a ``_context/{version}.json`` entry is only a claim, not a saved version.

    :func:`reserve_next_hip_file_path` writes exactly ``{"reserved": "<version>"}``
    before the hip is saved, and ``save_context`` *replaces* the file with the
    real lineage entry once the save is done — nothing merges into a claim,
    so a claim is that one key and nothing else. Until it is replaced the hip
    may be missing or half-written.
    """
    return set(context_entry) == {'reserved'}


@dataclass(frozen=True)
class WorkspaceScan:
    """The versions a department workspace holds, read once.

    ``hips`` maps each version with a hip file on disk to that file's
    extension; ``entries`` maps each version with a ``_context`` entry to
    the entry (finalized lineage or a reservation claim). The two are
    independent evidence: either proves the version number is taken.
    """
    hips: dict[str, str]
    entries: dict[str, dict]

    def version_names(self) -> set[str]:
        """Every version the workspace has evidence of."""
        return set(self.hips) | set(self.entries)


def scan_workspace(workspace_path: Path) -> WorkspaceScan:
    """Read the hips and ``_context`` entries of a department workspace.

    Files whose names are not versions are not versions and are skipped.
    A ``_context`` entry that is not a JSON object raises ValueError: it is
    neither a claim nor a lineage entry, and reading it as either would let
    a second saver take its number or re-anchor the chain.
    """
    workspace_path = Path(workspace_path)
    hips: dict[str, str] = {}
    for ext in HIP_EXTENSIONS:
        for hip_path in workspace_path.glob(f'*.{ext}'):
            if is_versioned(hip_path):
                hips[version_of(hip_path)] = ext
    entries: dict[str, dict] = {}
    context_dir = workspace_path / '_context'
    if context_dir.is_dir():
        for entry_path in context_dir.glob('*.json'):
            if not naming.is_valid_version_name(entry_path.stem):
                continue
            data = load_json(entry_path)
            if not isinstance(data, dict):
                raise ValueError(
                    f"_context entry is not a JSON object: {entry_path}"
                )
            entries[entry_path.stem] = data
    return WorkspaceScan(hips=hips, entries=entries)


###############################################################################
# Workfile paths
###############################################################################
def get_hip_file_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str
    ) -> Path:
    """The hip file of one workfile version.

    Raises FileNotFoundError when the version has neither an extension hint
    nor a hip on disk: there is no file to name, and inventing ``.hip``
    pointed every caller at a workfile that never existed.
    """
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)
    base_name = entity_stem(workfile_uri, department_name, version_name)

    # Priority 1: Check _context/{version}.json for stored extension
    # This avoids unreliable exists() calls on SMB/CIFS network storage.
    # A version saved before _context entries existed has none.
    version_context_path = workspace_path / "_context" / f"{version_name}.json"
    stored_path = None
    if version_context_path.exists():
        stored_extension = load_json(version_context_path).get('extension')
        if stored_extension is not None:
            stored_path = workspace_path / f'{base_name}.{stored_extension}'
            if stored_path.exists():
                return stored_path

    # Priority 2: Search for file by extension. Covers older workfiles with no
    # extension hint AND a hint that is wrong — sidecars written before
    # ``save_hip_file`` recorded the *requested* extension, so an Education
    # session's ``.hipnc`` was filed as ``hip``. Only a sibling that is
    # actually there overrides the hint; a network stat that misses every
    # channel falls through to the stored path (exists() is distrusted on
    # SMB/CIFS, but a false negative here costs nothing beyond the old answer).
    for ext in HIP_EXTENSIONS:
        hip_file_path = workspace_path / f'{base_name}.{ext}'
        if hip_file_path.exists():
            return hip_file_path

    if stored_path is not None:
        return stored_path
    raise FileNotFoundError(
        f"no workfile {base_name} in {workspace_path}: no hip on disk and no "
        f"extension recorded in its _context entry"
    )

def latest_hip_file_path(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[Path]:
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)
    base_pattern = entity_stem(workfile_uri, department_name, '*')
    hip_file_paths = _list_valid_hip_files(workspace_path, base_pattern)
    if len(hip_file_paths) == 0: return None
    return hip_file_paths[-1]


def latest_complete_hip_file_path(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[Path]:
    """The newest workfile whose save has finished, for copying elsewhere.

    :func:`latest_hip_file_path` returns the highest version on disk, which may
    be one another process is still writing: its ``_context`` entry is still the
    reservation stub. A farm submission bundled such a file mid-save — cut off
    a megabyte short — and the job failed with "Invalid .hip file header". A
    version with no ``_context`` entry at all (saved before entries existed)
    counts as finished.
    """
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)
    base_pattern = entity_stem(workfile_uri, department_name, '*')
    hip_file_paths = _list_valid_hip_files(workspace_path, base_pattern)
    for hip_file_path in reversed(hip_file_paths):
        entry_path = workspace_path / '_context' / f'{version_of(hip_file_path)}.json'
        if not entry_path.exists() or not is_reservation_stub(load_json(entry_path)):
            return hip_file_path
    return None


def latest_hip_file_path_with_context(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[Path]:
    """Get the latest hip file path, reconciling the context.json pointer with disk.

    The context.json ``version`` pointer is a fast, network-reliable hint: a
    directory glob on SMB/CIFS can return a stale-low listing that misses a
    just-saved file, and the pointer catches that. But the pointer can also
    *lag* reality — a crash between the hip write and the pointer write, or a
    concurrent save that regressed it — and it must never suppress a
    physically-newer version that exists on disk.

    So we take the higher of the two: the pointer wins when a glob is stale-low,
    the glob wins when the pointer lags. They should agree; a disagreement is
    logged and healed out-of-band by ``verify_context_chain``.
    """
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)

    # The pointer: context.json 'version', if valid and its file exists on disk.
    # A workspace nobody has saved into yet has no pointer.
    tracked_path: Optional[Path] = None
    tracked_code = -1
    context_path = workspace_path / 'context.json'
    if context_path.exists():
        tracked_version = load_json(context_path).get('version')
        if tracked_version and naming.is_valid_version_name(tracked_version):
            base_name = entity_stem(workfile_uri, department_name, tracked_version)
            for ext in HIP_EXTENSIONS:
                candidate = workspace_path / f'{base_name}.{ext}'
                if candidate.exists():
                    tracked_path = candidate
                    tracked_code = naming.get_version_code(tracked_version)
                    break
            if tracked_path is None:
                logger.warning(
                    f"Context version {tracked_version} does not exist on disk, "
                    f"reconciling against glob: {entity_uri}"
                )
        elif tracked_version:
            logger.warning(
                f"Context has invalid version format '{tracked_version}', "
                f"reconciling against glob: {entity_uri}"
            )

    # Disk truth: the highest valid hip actually present (exists by construction
    # — latest_hip_file_path globs real files).
    glob_path = latest_hip_file_path(entity_uri, department_name)
    glob_code = _version_code(glob_path) if glob_path is not None else -1

    # Reconcile: the higher version wins in each direction.
    if glob_path is not None and glob_code > tracked_code:
        if tracked_code >= 0:
            logger.warning(
                f"context.json pointer ({naming.get_version_name(tracked_code)}) "
                f"lags on-disk latest ({version_of(glob_path)}); using disk. "
                f"Run verify_context_chain to heal: {entity_uri}/{department_name}"
            )
        logger.info(f"Resolved latest workfile via disk: {glob_path}")
        return glob_path
    if tracked_path is not None:
        logger.info(f"Resolved latest workfile from context: {tracked_path}")
        return tracked_path
    return glob_path

def next_hip_file_path(
    entity_uri: Uri,
    department_name: str,
    nc_type: str | None = None
    ) -> Path:
    """Get path for the next version of a workfile.

    Args:
        entity_uri: Entity URI
        department_name: Department name
        nc_type: 'nc' for .hipnc, 'lc' for .hiplc, None for .hip
    """
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)
    base_pattern = entity_stem(workfile_uri, department_name, '*')

    hip_file_paths = _list_valid_hip_files(workspace_path, base_pattern)
    latest_version_code = (
        0 if len(hip_file_paths) == 0
        else _version_code(hip_file_paths[-1])
    )
    next_version_name = naming.get_version_name(latest_version_code + 1)
    result_path = _hip_path_for_version(
        workspace_path, workfile_uri, department_name, next_version_name,
        _hip_extension(nc_type),
    )

    logger.info(
        f"Generated next workfile path: {result_path} "
        f"(entity={entity_uri}, dept={department_name})"
    )
    return result_path

def _hip_extension(nc_type: str | None) -> str:
    """The hip extension a session of license ``nc_type`` writes."""
    return {'nc': 'hipnc', 'lc': 'hiplc', None: 'hip'}[nc_type]

def _hip_path_for_version(
    workspace_path: Path,
    workfile_uri: Uri,
    department_name: str,
    version_name: str,
    ext: str,
    ) -> Path:
    base_name = entity_stem(workfile_uri, department_name, version_name)
    return workspace_path / f'{base_name}.{ext}'

def reserve_next_hip_file_path(
    entity_uri: Uri,
    department_name: str,
    nc_type: str | None = None,
    attempts: int = 64,
    ) -> Path:
    """Atomically allocate the next workfile version path.

    Unlike :func:`next_hip_file_path`, which merely reports ``glob_max + 1`` and
    leaves a window in which two concurrent saves pick the same number, this
    *claims* the version by exclusively creating its ``_context/{version}.json``
    entry (``O_CREAT | O_EXCL``). If another process already claimed that number
    the create fails and we bump and retry, so concurrent saves — two artists,
    or an artist and a farm publish, on the same department — each get a
    distinct version instead of silently overwriting one another.

    The reservation is network-safe: an exclusive create is atomic on local disk
    and on the SMB/CIFS shares the pipeline runs on, and it needs no lock daemon.
    The claim is a small valid-JSON placeholder that :func:`save_context`
    overwrites with the real lineage entry. Cleanup is the caller's: on a failed
    save it must unlink the returned hip path and its ``_context`` claim so a
    version number is not permanently burned (see ``commit_next_workfile``).

    Returns the path the caller should ``hou.hipFile.save`` into.
    """
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)
    ext = _hip_extension(nc_type)
    context_dir = workspace_path / '_context'
    context_dir.mkdir(parents=True, exist_ok=True)
    base_pattern = entity_stem(workfile_uri, department_name, '*')

    # `floor` lets a lost race skip ahead without waiting for the winner's hip
    # to surface in the (possibly cache-stale) glob — otherwise we would spin on
    # the same number until the other save finished writing its file.
    floor = 1
    for _ in range(attempts):
        hip_file_paths = _list_valid_hip_files(workspace_path, base_pattern)
        latest_code = (
            0 if len(hip_file_paths) == 0
            else _version_code(hip_file_paths[-1])
        )
        code = max(latest_code + 1, floor)
        version_name = naming.get_version_name(code)
        claim_path = context_dir / f'{version_name}.json'
        try:
            fd = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            floor = code + 1
            continue
        try:
            with os.fdopen(fd, 'w') as claim_file:
                # A valid-JSON stub, not an empty file: get_hip_file_path loads
                # this entry for its extension hint and json.load would raise on
                # an empty file. No extension yet — the reader falls through to
                # probing until save_context finalizes the entry.
                json.dump({'reserved': version_name}, claim_file)
        except BaseException:
            claim_path.unlink(missing_ok=True)
            raise
        result_path = _hip_path_for_version(
            workspace_path, workfile_uri, department_name, version_name, ext
        )
        logger.info(
            f"Reserved workfile version {version_name}: {result_path} "
            f"(entity={entity_uri}, dept={department_name})"
        )
        return result_path

    raise RuntimeError(
        f"could not reserve a workfile version for {entity_uri}/{department_name} "
        f"after {attempts} attempts"
    )

def release_reserved_version(hip_file_path: Path) -> None:
    """Undo a :func:`reserve_next_hip_file_path` claim after a *failed* save.

    Removes the (possibly partial) hip file and its ``_context`` claim so the
    burned version number becomes reusable; either may not exist yet,
    depending on where the save failed. Only call this when the hip save
    itself failed: once a hip is persisted it is a real version and must not
    be deleted for a mere bookkeeping failure. Orphaned claims that slip
    through are also cleaned up by ``verify_context_chain`` — but only once
    they have aged past ``context_repair.RESERVATION_GRACE``, since until then
    the claim is indistinguishable from a save still in flight and removing
    it would let a second saver take this same version number.
    """
    claim_path = hip_file_path.parent / '_context' / f'{version_of(hip_file_path)}.json'
    for path in (hip_file_path, claim_path):
        path.unlink(missing_ok=True)

@dataclass(frozen=True)
class Context:
    entity_uri: Uri
    department_name: str
    version_name: str | None = None

def load_entity_context(context_json_path: Path) -> Context:
    """The entity context a workfile folder's ``context.json`` records.

    Raises when the file is missing or lacks its ``uri`` / ``department``:
    a context that does not say whose workfile this is cannot be answered
    for, and every reader used to get None and carry on as if the scene
    belonged to nobody. ``version`` may be empty (the pointer of a folder
    being created); that reads as None.
    """
    context_data = load_json(context_json_path)
    entity_uri = Uri.parse(context_data['uri'])
    department_name = context_data['department']
    version_name = context_data.get('version') or None
    if version_name is None:
        logger.warning(f"Context has missing/empty version: {context_json_path}")
    elif not naming.is_valid_version_name(version_name):
        logger.warning(f"Context has invalid version '{version_name}': {context_json_path}")
    logger.debug(
        f"Loaded context: uri={entity_uri}, dept={department_name}, "
        f"version={version_name}"
    )
    return Context(
        entity_uri=entity_uri,
        department_name=department_name,
        version_name=version_name
    )

def get_workfile_context(hip_file_path: Path) -> Optional[Context]:
    """The context of the pipeline workfile at ``hip_file_path``.

    None when the file is not a pipeline workfile at all: its name does not
    end in a version, or no ``context.json`` sits beside it (an untitled
    scene, a hip opened from anywhere else). A ``context.json`` that is
    there but does not name the ``uri`` and ``department`` raises.
    """
    if not is_versioned(hip_file_path): return None
    context_path = hip_file_path.parent / 'context.json'
    if not context_path.exists(): return None
    context_data = load_json(context_path)
    return Context(
        entity_uri = Uri.parse(context_data['uri']),
        department_name = context_data['department'],
        version_name = version_of(hip_file_path)
    )
