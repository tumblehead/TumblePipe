import json
import logging
import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from tumblepipe.api import api
from tumblepipe.config.groups import find_group
from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri

logger = logging.getLogger(__name__)

# All valid Houdini file extensions (commercial, limited commercial, non-commercial)
HIP_EXTENSIONS = ('hip', 'hiplc', 'hipnc')

###############################################################################
# Workspace Paths
###############################################################################
def _valid_file_path_version_name(file_path: Path) -> bool:
    return api.naming.is_valid_version_name(file_path.stem.rsplit('_', 1)[-1])

def _get_file_path_version_code(file_path: Path) -> bool:
    return api.naming.get_version_code(file_path.stem.rsplit('_', 1)[-1])

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
        Uri.parse_unsafe(purpose) /
        workfile_uri.segments /
        department_name
    )
    return workfile_uri, api.storage.resolve(workspace_uri)

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
    project_root = api.storage.resolve(Uri.parse_unsafe('project:/'))
    try:
        return '/'.join(workspace_path.relative_to(project_root).parts)
    except ValueError:
        return None

def _list_valid_hip_files(
    workspace_path: Path,
    base_pattern: str
    ) -> list[Path]:
    """Glob all hip variants (.hip, .hiplc, .hipnc) matching ``base_pattern``,
    keeping only validly-versioned files, sorted ascending by version code."""
    all_hip_files = []
    for ext in HIP_EXTENSIONS:
        all_hip_files.extend(workspace_path.glob(f'{base_pattern}.{ext}'))
    return list(sorted(
        filter(_valid_file_path_version_name, all_hip_files),
        key=_get_file_path_version_code
    ))

def list_hip_file_paths(
    entity_uri: Uri,
    department_name: str
    ) -> list[Path]:
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)
    base_pattern = '_'.join(workfile_uri.segments[1:] + [department_name, '*'])
    return _list_valid_hip_files(workspace_path, base_pattern)

def get_hip_file_path(
    entity_uri: Uri,
    department_name: str,
    version_name: str
    ) -> Path:
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)
    base_name = '_'.join(workfile_uri.segments[1:] + [
        department_name,
        version_name
    ])

    # Priority 1: Check _context/{version}.json for stored extension
    # This avoids unreliable exists() calls on SMB/CIFS network storage
    version_context_path = workspace_path / "_context" / f"{version_name}.json"
    version_data = load_json(version_context_path)
    if version_data is not None:
        stored_extension = version_data.get('extension')
        if stored_extension is not None:
            return workspace_path / f'{base_name}.{stored_extension}'

    # Priority 2: Search for file by extension (fallback for older workfiles)
    for ext in HIP_EXTENSIONS:
        hip_file_path = workspace_path / f'{base_name}.{ext}'
        if hip_file_path.exists():
            return hip_file_path

    # Fallback: return .hip path (caller handles non-existence)
    return workspace_path / f'{base_name}.hip'

def latest_hip_file_path(
    entity_uri: Uri,
    department_name: str
    ) -> Optional[Path]:
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)
    base_pattern = '_'.join(workfile_uri.segments[1:] + [department_name, '*'])
    hip_file_paths = _list_valid_hip_files(workspace_path, base_pattern)
    if len(hip_file_paths) == 0: return None
    return hip_file_paths[-1]

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
    tracked_path: Optional[Path] = None
    tracked_code = -1
    context_path = workspace_path / 'context.json'
    context_data = load_json(context_path)
    if context_data is not None:
        tracked_version = context_data.get('version')
        if tracked_version and api.naming.is_valid_version_name(tracked_version):
            base_name = '_'.join(workfile_uri.segments[1:] + [
                department_name,
                tracked_version
            ])
            for ext in HIP_EXTENSIONS:
                candidate = workspace_path / f'{base_name}.{ext}'
                if candidate.exists():
                    tracked_path = candidate
                    tracked_code = api.naming.get_version_code(tracked_version)
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
    glob_code = _get_file_path_version_code(glob_path) if glob_path is not None else -1

    # Reconcile: the higher version wins in each direction.
    if glob_path is not None and glob_code > tracked_code:
        if tracked_code >= 0:
            logger.warning(
                f"context.json pointer ({api.naming.get_version_name(tracked_code)}) "
                f"lags on-disk latest ({glob_path.stem.rsplit('_', 1)[-1]}); using disk. "
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
    base_pattern = '_'.join(workfile_uri.segments[1:] + [department_name, '*'])

    hip_file_paths = _list_valid_hip_files(workspace_path, base_pattern)
    latest_version_code = (
        0 if len(hip_file_paths) == 0
        else _get_file_path_version_code(hip_file_paths[-1])
    )
    next_version_code = latest_version_code + 1
    next_version_name = api.naming.get_version_name(next_version_code)
    # Use appropriate extension based on NC type
    ext = {'nc': 'hipnc', 'lc': 'hiplc'}.get(nc_type, 'hip')
    hip_file_name = f'{base_pattern.replace("*", next_version_name)}.{ext}'
    result_path = workspace_path / hip_file_name

    logger.info(
        f"Generated next workfile path: {result_path} "
        f"(entity={entity_uri}, dept={department_name})"
    )
    return result_path

def _hip_path_for_version(
    workspace_path: Path,
    workfile_uri: Uri,
    department_name: str,
    version_name: str,
    ext: str,
    ) -> Path:
    base_name = '_'.join(workfile_uri.segments[1:] + [department_name, version_name])
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
    ext = {'nc': 'hipnc', 'lc': 'hiplc'}.get(nc_type, 'hip')
    context_dir = workspace_path / '_context'
    context_dir.mkdir(parents=True, exist_ok=True)
    base_pattern = '_'.join(workfile_uri.segments[1:] + [department_name, '*'])

    # `floor` lets a lost race skip ahead without waiting for the winner's hip
    # to surface in the (possibly cache-stale) glob — otherwise we would spin on
    # the same number until the other save finished writing its file.
    floor = 1
    for _ in range(attempts):
        hip_file_paths = _list_valid_hip_files(workspace_path, base_pattern)
        latest_code = (
            0 if len(hip_file_paths) == 0
            else _get_file_path_version_code(hip_file_paths[-1])
        )
        code = max(latest_code + 1, floor)
        version_name = api.naming.get_version_name(code)
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
            try:
                os.unlink(str(claim_path))
            except OSError:
                pass
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
    burned version number becomes reusable. Best-effort — missing files are
    ignored. Only call this when the hip save itself failed: once a hip is
    persisted it is a real version and must not be deleted for a mere
    bookkeeping failure. Orphaned claims that slip through are also cleaned up
    by ``verify_context_chain``.
    """
    version_name = hip_file_path.stem.rsplit('_', 1)[-1]
    claim_path = hip_file_path.parent / '_context' / f'{version_name}.json'
    for path in (hip_file_path, claim_path):
        try:
            path.unlink()
        except OSError:
            pass

@dataclass(frozen=True)
class Context:
    entity_uri: Uri
    department_name: str
    version_name: str | None = None

def load_entity_context(context_json_path: Path) -> Optional[Context]:
    """
    Load entity context from a context.json file.

    Returns Context with entity_uri from the 'uri' field,
    department_name from 'department' field, and version_name
    from 'version' field (or None if not present/empty).
    """
    context_data = load_json(context_json_path)
    if context_data is None:
        logger.debug(f"No context data found at {context_json_path}")
        return None

    uri_str = context_data.get('uri')
    if not uri_str:
        logger.warning(f"Context missing 'uri' field: {context_json_path}")
        return None

    try:
        entity_uri = Uri.parse_unsafe(uri_str)
        department_name = context_data.get('department', '')
        # Normalize empty string and None to None for consistent handling
        raw_version = context_data.get('version')
        version_name = raw_version if raw_version else None

        if version_name is None:
            logger.warning(f"Context has missing/empty version: {context_json_path}")
        elif not api.naming.is_valid_version_name(version_name):
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
    except ValueError:
        logger.error(f"Invalid entity URI in context: {context_json_path}", exc_info=True)
        return None

def get_workfile_context(hip_file_path: Path) -> Optional[Context]:

    # Parse the file name
    hip_file_name = hip_file_path.stem
    if '_' not in hip_file_name: return None
    version_name = hip_file_name.rsplit('_', 1)[-1]
    if not api.naming.is_valid_version_name(version_name): return None

    # Read the workfile context
    context_path = hip_file_path.parent / 'context.json'
    context_data = load_json(context_path)
    if context_data is None: return None

    # Degrade to None on a partial/legacy context.json rather than raising,
    # matching load_entity_context's contract.
    uri_str = context_data.get('uri')
    department_name = context_data.get('department')
    if uri_str is None or department_name is None: return None
    try:
        entity_uri = Uri.parse_unsafe(uri_str)
    except ValueError:
        return None

    # Return context
    return Context(
        entity_uri = entity_uri,
        department_name = department_name,
        version_name = version_name
    )
