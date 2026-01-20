import datetime as dt
from pathlib import Path
from tumblehead.api import get_user_name
from tumblehead.util.io import store_json
from tumblehead.pipe.paths import Context, get_hip_file_path
from tumblehead.util.uri import Uri


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
    from tumblehead.pipe.paths import latest_export_path
    from tumblehead.util.io import load_json
    from tumblehead.util.uri import Uri
    from tumblehead.config.department import list_departments

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


def save_context(target_path: Path, prev_context, next_context, houdini_version: str = None, file_extension: str = None):
    """Save version context metadata (_context/{version}.json).

    Args:
        target_path: Directory to save context in
        prev_context: Previous Context (or None)
        next_context: Next Context
        houdini_version: Optional Houdini version string. If None, attempts to get from hou module.
        file_extension: Optional file extension (e.g., 'hip', 'hiplc', 'hipnc'). Used to avoid
            unreliable exists() calls on SMB/CIFS network storage when opening workfiles.
    """
    def _get_version_name(context):
        if context is None:
            return "v0000"
        if context.version_name is None:
            return "v0000"
        return context.version_name

    # Get houdini version - try to import hou if not provided
    if houdini_version is None:
        try:
            import hou
            houdini_version = hou.applicationVersionString()
        except ImportError:
            houdini_version = "unknown"

    timestamp = get_timestamp_from_context(next_context)
    prev_version_name = _get_version_name(prev_context)
    next_version_name = _get_version_name(next_context)
    context_path = target_path / "_context" / f"{next_version_name}.json"
    store_json(
        context_path,
        dict(
            user=get_user_name(),
            timestamp="" if timestamp is None else timestamp.isoformat(),
            from_version=prev_version_name,
            to_version=next_version_name,
            houdini_version=houdini_version,
            extension=file_extension,
        ),
    )


def save_entity_context(target_path: Path, context: Context):
    """Save entity context metadata (context.json)."""
    entity_context_path = target_path / "context.json"

    if context is None:
        return

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
