import datetime as dt
import logging
from pathlib import Path
from tumblepipe.api import get_user_name, api
from tumblepipe.util.io import store_json
from tumblepipe.util.houdini import hou
from tumblepipe.pipe.paths import Context, get_hip_file_path, HIP_EXTENSIONS
from tumblepipe.util.uri import Uri

logger = logging.getLogger(__name__)


def find_input(data, **kwargs):
    for context_input in data['inputs']:
        # Check all provided kwargs match the input
        if all(context_input.get(key) == value for key, value in kwargs.items()):
            return context_input
    return None

def find_output(data, **kwargs):
    for context_output in data['outputs']:
        # Check all provided kwargs match the output
        if all(context_output.get(key) == value for key, value in kwargs.items()):
            return context_output
    return None

def list_inputs(data, **kwargs):
    result = []
    for context_input in data['inputs']:
        # Check all provided kwargs match the input
        if all(context_input.get(key) == value for key, value in kwargs.items()):
            result.append(context_input)
    return result

def list_outputs(data, **kwargs):
    result = []
    for context_output in data['outputs']:
        # Check all provided kwargs match the output
        if all(context_output.get(key) == value for key, value in kwargs.items()):
            result.append(context_output)
    return result

def get_aov_names_from_context(context_data: dict, variant: str = None) -> list[str]:
    """Extract AOV names from context.json outputs.

    Args:
        context_data: The loaded context.json dictionary
        variant: Optional variant name to filter outputs

    Returns:
        List of AOV names, or empty list if not found
    """
    if context_data is None:
        return []

    outputs = context_data.get('outputs', [])
    for output in outputs:
        if variant is not None and output.get('variant') != variant:
            continue
        params = output.get('parameters', {})
        aov_names = params.get('aov_names', [])
        if aov_names:
            return aov_names

    return []


def aggregate_aov_names_from_inputs(
    context_data: dict,
    variant: str = 'default'
) -> list[str]:
    """Aggregate AOV names from asset exports referenced in context.json.

    Looks at outputs[0].parameters.assets to find referenced assets,
    then checks all department exports for each asset to collect aov_names.

    Args:
        context_data: The loaded context.json dictionary
        variant: Variant name to use when looking up asset exports

    Returns:
        List of unique AOV names aggregated from all referenced assets
    """
    if context_data is None:
        return []

    # Import here to avoid circular dependency
    from tumblepipe.pipe.paths import latest_export_path
    from tumblepipe.util.io import load_json
    from tumblepipe.util.uri import Uri
    from tumblepipe.config.department import list_departments

    aov_set = set()

    # Get assets from outputs[0].parameters.assets
    outputs = context_data.get('outputs', [])
    if not outputs:
        return []

    parameters = outputs[0].get('parameters', {})
    assets = parameters.get('assets', [])

    if not assets:
        return []

    # Get all asset departments to check
    asset_departments = [d.name for d in list_departments('assets')]

    for asset_entry in assets:
        # Asset entries have structure: {asset: uri, instance: name, inputs: [...]}
        asset_uri_str = asset_entry.get('asset', '')
        if not asset_uri_str.startswith('entity:/assets/'):
            continue

        try:
            asset_uri = Uri.parse_unsafe(asset_uri_str)

            # Check all departments for this asset's exports
            for department in asset_departments:
                asset_export_path = latest_export_path(asset_uri, variant, department)
                if asset_export_path is None:
                    continue

                # Load the asset's context.json
                asset_context_path = asset_export_path / 'context.json'
                asset_context = load_json(asset_context_path)
                if asset_context is None:
                    continue

                # Extract AOV names from the asset (no variant filter)
                asset_aov_names = get_aov_names_from_context(asset_context)
                aov_set.update(asset_aov_names)

        except Exception:
            # Skip invalid entries gracefully
            continue

    return list(aov_set)


def file_path_from_context(context: Context):
    """Get the hip file path for a context."""
    if context is None:
        return None
    if context.version_name is None:
        return None
    file_path = get_hip_file_path(context.entity_uri, context.department_name, context.version_name)
    if not file_path.exists():
        return None
    return file_path


def get_timestamp_from_context(context: Context):
    """Get file modification timestamp for a context."""
    file_path = file_path_from_context(context)
    if file_path is None:
        return None
    return dt.datetime.fromtimestamp(file_path.stat().st_mtime)


def _list_present_version_codes(workspace_path: Path) -> set[int]:
    """Version codes present in a department workspace.

    Unions the evidence from both the hip files and the ``_context/*.json``
    lineage entries — either is proof a version exists — so a version is not
    lost from lineage just because one of the two was cleaned up.
    """
    codes: set[int] = set()
    context_dir = workspace_path / "_context"
    if context_dir.is_dir():
        for entry in context_dir.glob("*.json"):
            name = entry.stem
            if api.naming.is_valid_version_name(name):
                codes.add(api.naming.get_version_code(name))
    for ext in HIP_EXTENSIONS:
        for hip_path in workspace_path.glob(f"*.{ext}"):
            name = hip_path.stem.rsplit("_", 1)[-1]
            if api.naming.is_valid_version_name(name):
                codes.add(api.naming.get_version_code(name))
    return codes


def _predecessor_version_name(workspace_path: Path, version_name: str) -> str:
    """The real on-disk predecessor of ``version_name``.

    Returns the highest existing version strictly below it, or ``'v0000'`` when
    none exists (a genuine first save). Grounds a version's ``from_version`` in
    what is actually on disk instead of a possibly-missing ``context.json``
    pointer — so an unresolved predecessor can never silently re-anchor the
    lineage chain to ``v0000`` mid-history.
    """
    target_code = api.naming.get_version_code(version_name)
    prior = [code for code in _list_present_version_codes(workspace_path) if code < target_code]
    if not prior:
        return "v0000"
    return api.naming.get_version_name(max(prior))


def save_context(target_path: Path, prev_context, next_context, houdini_version: str = None, file_extension: str = None):
    """Save version context metadata (_context/{version}.json).

    Args:
        target_path: Directory to save context in
        prev_context: Previous Context (or None). When None (or its version is
            None) the predecessor is derived from the versions on disk rather
            than being coerced to ``v0000``.
        next_context: Next Context. Its version must be concrete — a None
            version is a caller bug (it would clobber ``_context/v0000.json``
            and poison the chain), so it raises rather than writing garbage.
        houdini_version: Optional Houdini version string. If None, attempts to get from hou module.
        file_extension: Optional file extension (e.g., 'hip', 'hiplc', 'hipnc'). Used to avoid
            unreliable exists() calls on SMB/CIFS network storage when opening workfiles.
    """
    if next_context is None or next_context.version_name is None:
        raise ValueError(
            f"save_context requires a concrete next version (target={target_path}); "
            f"got {next_context!r}"
        )
    next_version_name = next_context.version_name

    # Get houdini version from hou if available and not provided
    if houdini_version is None:
        houdini_version = (
            hou.applicationVersionString() if hou is not None else "unknown"
        )

    # from_version records provenance. Trust the caller's explicit predecessor;
    # otherwise ground it in the real on-disk predecessor rather than silently
    # re-anchoring the chain to 'v0000' (which snaps the linked list — the farm
    # publish path passes prev_context=None yet clearly continues a lineage).
    if prev_context is not None and prev_context.version_name is not None:
        from_version_name = prev_context.version_name
    else:
        from_version_name = _predecessor_version_name(target_path, next_version_name)

    # Timestamp from the saved hip's mtime, falling back to now() so the chain
    # entry never records an empty timestamp — a network stat() can miss a
    # just-written file on SMB/CIFS (the same reason exists() is distrusted
    # elsewhere in the path layer).
    timestamp = get_timestamp_from_context(next_context)
    timestamp_str = (
        timestamp.isoformat() if timestamp is not None else dt.datetime.now().isoformat()
    )

    context_path = target_path / "_context" / f"{next_version_name}.json"

    logger.info(
        f"Saving version context: {from_version_name} -> {next_version_name} "
        f"at {target_path}"
    )

    store_json(
        context_path,
        dict(
            user=get_user_name(),
            timestamp=timestamp_str,
            from_version=from_version_name,
            to_version=next_version_name,
            houdini_version=houdini_version,
            extension=file_extension,
        ),
    )


def commit_next_workfile(
    entity_uri: Uri,
    department_name: str,
    prev_context: Context = None,
    nc_type: str = None,
) -> Path:
    """Reserve, save the loaded hip into, and record the next workfile version.

    The single implementation every workfile-save path shares, so version
    allocation and the three-step commit are done one correct way:

    1. atomically reserve the next version (:func:`reserve_next_hip_file_path`),
       so two concurrent saves never pick the same number;
    2. ``hou.hipFile.save`` the loaded scene into it;
    3. write the ``_context`` lineage entry, then the ``context.json`` pointer
       **last** — so a crash always leaves the pointer at or below a
       fully-committed version, never ahead of a missing one.

    Rollback is deliberately narrow: if the *hip save* fails the reservation is
    released (the number is reusable). If the hip saved but the bookkeeping
    afterward fails, the file is **kept** — the artist's work is real — and the
    reader reconciliation plus ``verify_context_chain`` recover the pointer and
    chain rather than discarding a genuine save.

    ``prev_context`` records provenance; pass the loaded scene's context on an
    incremental save, or None (a fresh create / farm publish) to let
    :func:`save_context` derive the predecessor from disk. Returns the saved path.
    """
    from tumblepipe.pipe.paths import (
        reserve_next_hip_file_path, release_reserved_version,
    )

    next_path = reserve_next_hip_file_path(entity_uri, department_name, nc_type=nc_type)
    version_name = next_path.stem.rsplit("_", 1)[-1]
    try:
        hou.hipFile.save(str(next_path))
    except BaseException:
        release_reserved_version(next_path)
        raise

    next_context = Context(
        entity_uri=entity_uri,
        department_name=department_name,
        version_name=version_name,
    )
    save_context(
        next_path.parent, prev_context, next_context,
        file_extension=next_path.suffix.lstrip("."),
    )
    save_entity_context(next_path.parent, next_context)
    return next_path


def save_entity_context(target_path: Path, context: Context):
    """Save entity context metadata (context.json)."""
    entity_context_path = target_path / "context.json"

    if context is None:
        return

    # Warn if saving with None version - helps identify code paths that create invalid context
    if context.version_name is None:
        logger.warning(
            f"Saving context.json with None version at {target_path} "
            f"(entity: {context.entity_uri}, department: {context.department_name})"
        )

    logger.info(
        f"Saving entity context: uri={context.entity_uri}, "
        f"dept={context.department_name}, version={context.version_name}"
    )

    context_data = dict(
        uri=str(context.entity_uri),
        department=context.department_name,
        version=context.version_name,
        timestamp=dt.datetime.now().isoformat(),
        user=get_user_name(),
    )

    store_json(entity_context_path, context_data)


def save_export_context(
    target_path: Path,
    entity_uri: Uri,
    department_name: str,
    version_name: str,
    variant_name: str = 'default'
):
    """Save simple export context metadata (context.json).

    Used by layer_split and similar export operations.

    Args:
        target_path: Directory to save context.json in
        entity_uri: Entity URI
        department_name: Department name
        version_name: Version name
        variant_name: Variant name (default: 'default')
    """
    logger.info(
        f"Saving export context: uri={entity_uri}, dept={department_name}, "
        f"version={version_name}, variant={variant_name}"
    )

    context_path = target_path / 'context.json'
    context_data = dict(
        uri=str(entity_uri),
        department=department_name,
        variant=variant_name,
        version=version_name,
        timestamp=dt.datetime.now().isoformat(),
        user=get_user_name()
    )
    store_json(context_path, context_data)


def save_layer_context(
    target_path: Path,
    entity_uri: Uri,
    department_name: str,
    version_name: str,
    timestamp: str,
    user_name: str,
    variant_name: str = 'default',
    parameters: dict = None,
    inputs: list = None
):
    """Save layer context with inputs/outputs arrays (context.json).

    Used by export_layer, export_rig, and similar layer export operations.

    Args:
        target_path: Directory to save context.json in
        entity_uri: Entity URI
        department_name: Department name
        version_name: Version name
        timestamp: Timestamp string (ISO format)
        user_name: User name
        variant_name: Variant name (default: 'default')
        parameters: Optional parameters dict
        inputs: Optional list of input references
    """
    input_count = len(inputs) if inputs else 0
    logger.info(
        f"Saving layer context: uri={entity_uri}, dept={department_name}, "
        f"version={version_name}, variant={variant_name}, inputs={input_count}"
    )

    context_path = target_path / 'context.json'
    context_data = dict(
        inputs=inputs or [],
        outputs=[dict(
            uri=str(entity_uri),
            department=department_name,
            variant=variant_name,
            version=version_name,
            timestamp=timestamp,
            user=user_name,
            parameters=parameters or {}
        )]
    )
    store_json(context_path, context_data)
