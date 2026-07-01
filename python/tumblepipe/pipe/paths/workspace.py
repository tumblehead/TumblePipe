import logging
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
    """Get latest hip file path, preferring context.json tracked version.

    More reliable than glob() on network filesystems (SMB/CIFS) where
    directory listings may be cached. Falls back to glob-based discovery
    if context unavailable or tracked file doesn't exist.
    """
    workfile_uri, workspace_path = _resolve_workspace(entity_uri, department_name)

    # Priority 1: Check context.json for tracked version
    context_path = workspace_path / 'context.json'
    context_data = load_json(context_path)
    if context_data is not None:
        tracked_version = context_data.get('version')
        # Validate version exists and has valid format before using
        if tracked_version and api.naming.is_valid_version_name(tracked_version):
            base_name = '_'.join(workfile_uri.segments[1:] + [
                department_name,
                tracked_version
            ])
            # Check all hip file extensions (.hip, .hiplc, .hipnc)
            for ext in HIP_EXTENSIONS:
                tracked_file_path = workspace_path / f'{base_name}.{ext}'
                if tracked_file_path.exists():
                    logger.info(
                        f"Resolved latest workfile from context: {tracked_file_path}"
                    )
                    return tracked_file_path
            # Context points to non-existent file
            logger.warning(
                f"Context version {tracked_version} does not exist on disk, "
                f"falling back to glob: {entity_uri}"
            )
        elif tracked_version:
            logger.warning(
                f"Context has invalid version format '{tracked_version}', "
                f"falling back to glob: {entity_uri}"
            )

    # Priority 2: Fall back to glob-based discovery
    result = latest_hip_file_path(entity_uri, department_name)
    if result is not None:
        logger.info(f"Resolved latest workfile via glob: {result}")
    return result

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
