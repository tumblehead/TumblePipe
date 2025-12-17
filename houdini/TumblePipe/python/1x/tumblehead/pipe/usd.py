"""
Shared USD utilities for generating USDA file content.
"""

from pathlib import Path
import os
import re
import logging
from typing import Union

from tumblehead.api import path_str
from tumblehead.util.uri import Uri


def generate_usda_content(
    layer_paths: list[Union[Path, str]],
    output_path: Path,
    fps: float = None,
    start_frame: int = None,
    end_frame: int = None
) -> str:
    """
    Generate USDA file content with sublayers.

    Primarily uses entity:/ URIs for sublayer references. Filesystem paths
    are only supported for static config templates (e.g., root_default_prims.usda)
    that don't require dynamic version resolution.

    Args:
        layer_paths: List of sublayer references - prefer entity:/ URI strings.
                    Path objects are only for static config templates.
        output_path: Final output path (for computing relative paths from filesystem paths)
        fps: Frames per second (optional, omit for simple sublayer files)
        start_frame: Start time code (optional)
        end_frame: End time code (optional)

    Returns:
        USDA file content as string
    """
    lines = ['#usda 1.0', '(']

    # Add optional timing metadata
    if fps is not None:
        lines.append(f'    framesPerSecond = {fps}')
        lines.append(f'    timeCodesPerSecond = {fps}')

    if start_frame is not None:
        lines.append(f'    startTimeCode = {start_frame}')

    if end_frame is not None:
        lines.append(f'    endTimeCode = {end_frame}')

    # Add standard USD metadata
    lines.append('    metersPerUnit = 1')
    lines.append('    upAxis = "Y"')

    # Add sublayers
    lines.append('    subLayers = [')

    for layer_ref in layer_paths:
        if isinstance(layer_ref, str) and layer_ref.startswith('entity:/'):
            # Entity URI - use as-is (resolver handles resolution)
            lines.append(f'        @{layer_ref}@,')
        else:
            # Filesystem path - compute relative path
            layer_path = layer_ref if isinstance(layer_ref, Path) else Path(layer_ref)
            try:
                relative_layer_path = Path(os.path.relpath(layer_path, output_path.parent))
            except ValueError:
                # Different drives (e.g., temp on C:, export on P:) - use absolute path
                relative_layer_path = layer_path
            lines.append(f'        @{path_str(relative_layer_path)}@,')

    lines.append('    ]')
    lines.append(')')

    return '\n'.join(lines)


def generate_simple_usda_content(
    layer_paths: list[Union[Path, str]],
    output_path: Path
) -> str:
    """
    Generate simple USDA file content with just sublayers (no timing metadata).

    Useful for asset staged files and scenes that don't need frame range information.
    Primarily uses entity:/ URIs for sublayer references.

    Args:
        layer_paths: List of sublayer references - prefer entity:/ URI strings.
                    Path objects are only for static config templates.
        output_path: Final output path (for computing relative paths from filesystem paths)

    Returns:
        USDA file content as string
    """
    return generate_usda_content(
        layer_paths=layer_paths,
        output_path=output_path,
        fps=None,
        start_frame=None,
        end_frame=None
    )


def _parse_usda_metadata(content: str) -> dict:
    """
    Parse USDA file content to extract metadata.

    Args:
        content: USDA file content as string

    Returns:
        Dict with keys: fps, start_frame, end_frame, sublayers
    """
    metadata = {
        'fps': None,
        'start_frame': None,
        'end_frame': None,
        'sublayers': []
    }

    # Parse framesPerSecond
    fps_match = re.search(r'framesPerSecond\s*=\s*(\d+(?:\.\d+)?)', content)
    if fps_match:
        metadata['fps'] = float(fps_match.group(1))

    # Parse startTimeCode
    start_match = re.search(r'startTimeCode\s*=\s*(\d+(?:\.\d+)?)', content)
    if start_match:
        metadata['start_frame'] = int(float(start_match.group(1)))

    # Parse endTimeCode
    end_match = re.search(r'endTimeCode\s*=\s*(\d+(?:\.\d+)?)', content)
    if end_match:
        metadata['end_frame'] = int(float(end_match.group(1)))

    # Parse sublayers - match @path@ patterns within subLayers = [...]
    sublayers_match = re.search(r'subLayers\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if sublayers_match:
        sublayers_block = sublayers_match.group(1)
        # Find all @path@ patterns
        path_matches = re.findall(r'@([^@]+)@', sublayers_block)
        metadata['sublayers'] = path_matches

    return metadata


def _resolve_latest_to_version(layer_path: Path, source_dir: Path) -> Path:
    """
    Resolve a 'latest' path to its actual versioned path.

    Args:
        layer_path: Path that may contain 'latest' segment
        source_dir: Directory of the source file (for resolving relative paths)

    Returns:
        Resolved path with actual version, or original if not 'latest' or can't resolve
    """
    # Check if the ORIGINAL path contains 'latest' BEFORE converting to absolute
    # This avoids false positives when source_dir itself contains 'latest'
    path_str_value = str(layer_path)
    if '/latest/' not in path_str_value and '\\latest\\' not in path_str_value:
        # Not a "latest" reference - just return absolute path
        if not layer_path.is_absolute():
            return source_dir / layer_path
        return layer_path

    # Convert to absolute path if relative (only for paths that need resolution)
    if not layer_path.is_absolute():
        layer_path = source_dir / layer_path

    # Find the 'latest' directory and look for versioned siblings
    parts = layer_path.parts
    try:
        latest_idx = parts.index('latest')
    except ValueError:
        return layer_path

    # Get the parent directory of 'latest'
    parent_parts = parts[:latest_idx]
    parent_dir = Path(*parent_parts) if parent_parts else Path('.')

    # Get the filename pattern (replace 'latest' with version pattern)
    filename = layer_path.name
    if '_latest' in filename:
        # Pattern: entity_latest.usda -> entity_v####.usda
        file_pattern = filename.replace('_latest', '_v*')
    else:
        file_pattern = filename

    # List versioned directories (v0001, v0002, etc.)
    if parent_dir.exists():
        version_dirs = sorted([
            d for d in parent_dir.iterdir()
            if d.is_dir() and re.match(r'^v\d+$', d.name)
        ], key=lambda d: int(d.name[1:]))

        if version_dirs:
            # Use the latest version directory
            current_version_dir = version_dirs[-1]
            version_name = current_version_dir.name

            # Construct the versioned filename
            if '_latest' in filename:
                versioned_filename = filename.replace('_latest', f'_{version_name}')
            else:
                versioned_filename = filename

            versioned_path = current_version_dir / versioned_filename

            if versioned_path.exists():
                logging.debug(f"Resolved {layer_path} -> {versioned_path}")
                return versioned_path

    # Couldn't resolve, return original
    logging.warning(f"Could not resolve latest path: {layer_path}")
    return layer_path


def collapse_latest_references(
    staged_file_path: Path,
    output_path: Path
) -> str:
    """
    Copy a staged USDA file's content for use in a collapsed render stage.

    Reads the staged file and generates USDA content with the same metadata
    and sublayer references. All sublayer references should be entity:/ URIs
    which are resolved by the custom USD asset resolver at runtime.

    Args:
        staged_file_path: Path to the source staged .usda file
        output_path: Output path (not used for entity URIs, kept for API compatibility)

    Returns:
        USDA file content as string

    Raises:
        ValueError: If staged file doesn't exist or has invalid format
    """
    if not staged_file_path.exists():
        raise ValueError(f"Staged file does not exist: {staged_file_path}")

    # Read the source file
    with open(staged_file_path, 'r') as f:
        content = f.read()

    # Parse metadata
    metadata = _parse_usda_metadata(content)

    if not metadata['sublayers']:
        logging.warning(f"No sublayers found in {staged_file_path}")

    # All sublayers should be entity:/ URIs - pass through unchanged
    # The custom USD resolver handles version resolution at runtime
    sublayer_refs = metadata['sublayers']

    # Generate new USDA content
    return generate_usda_content(
        layer_paths=sublayer_refs,
        output_path=output_path,
        fps=metadata['fps'],
        start_frame=metadata['start_frame'],
        end_frame=metadata['end_frame']
    )


def add_sublayer(layer_path: Path, sublayer_ref: Union[Path, str]) -> bool:
    """
    Add a sublayer to an existing USD layer file.

    Primarily uses entity:/ URIs for sublayer references. The resolver handles
    dynamic version resolution at runtime.

    Args:
        layer_path: Path to the USD layer to modify
        sublayer_ref: Prefer entity:/ URI string. Path objects are only for
                     static config templates that don't need version resolution.

    Returns:
        True if sublayer was added successfully, False on error
    """
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if not layer:
        logging.error(f"Failed to open layer: {layer_path}")
        return False

    if isinstance(sublayer_ref, str) and sublayer_ref.startswith('entity:/'):
        # Entity URI - use as-is (resolver handles resolution)
        layer.subLayerPaths.append(sublayer_ref)
    else:
        # Filesystem path - compute relative path
        rel_path = os.path.relpath(sublayer_ref, layer_path.parent)
        layer.subLayerPaths.append(rel_path.replace('\\', '/'))

    layer.Save()
    return True


def generate_entity_sublayer_uri(
    entity_uri: Uri,
    variant_name: str,
    department_name: str,
    version_name: str | None = None
) -> str:
    """
    Generate an entity:/ URI for use as a USD sublayer reference.

    The generated URI can be used in USD sublayer paths and will be
    resolved by the custom asset resolver at runtime.

    Args:
        entity_uri: Entity URI (e.g., entity:/assets/SET/Arena)
        variant_name: Variant name (default, _shared, etc.)
        department_name: Department name (lookdev, model, etc.)
        version_name: Optional specific version (None = latest)

    Returns:
        Entity URI string for USD sublayer reference

    Example:
        >>> uri = Uri.parse_unsafe('entity:/assets/SET/Arena')
        >>> generate_entity_sublayer_uri(uri, 'default', 'lookdev')
        'entity:/assets/SET/Arena?dept=lookdev&variant=default'
        >>> generate_entity_sublayer_uri(uri, '_shared', 'lookdev', 'v0013')
        'entity:/assets/SET/Arena?dept=lookdev&variant=_shared&version=v0013'
    """
    uri_str = f"{entity_uri}?dept={department_name}&variant={variant_name}"
    if version_name:
        uri_str += f"&version={version_name}"
    return uri_str


def generate_scene_sublayer_uri(
    scene_uri: Uri,
    version_name: str | None = None
) -> str:
    """
    Generate an entity:/ URI for a scene sublayer reference.

    The generated URI can be used in USD sublayer paths and will be
    resolved by the custom asset resolver at runtime.

    Args:
        scene_uri: Scene URI (e.g., scenes:/outdoor/forest)
        version_name: Optional specific version (None = latest)

    Returns:
        Entity URI string for USD sublayer reference

    Example:
        >>> uri = Uri.parse_unsafe('scenes:/outdoor/forest')
        >>> generate_scene_sublayer_uri(uri)
        'entity:/scenes/outdoor/forest'
        >>> generate_scene_sublayer_uri(uri, 'v0013')
        'entity:/scenes/outdoor/forest?version=v0013'
    """
    # Convert scenes:/ URI to entity:/scenes/ format
    segments = '/'.join(str(s) for s in scene_uri.segments)
    uri_str = f"entity:/scenes/{segments}"
    if version_name:
        uri_str += f"?version={version_name}"
    return uri_str


def generate_staged_sublayer_uri(
    entity_uri: Uri,
    variant_name: str,
    version_name: str | None = None
) -> str:
    """
    Generate an entity:/ URI for a staged asset/shot sublayer reference.

    Unlike generate_entity_sublayer_uri which includes a department,
    this generates URIs for staged files (composed from all departments).

    Args:
        entity_uri: Entity URI (e.g., entity:/assets/SET/Arena)
        variant_name: Variant name (default, _shared, etc.)
        version_name: Optional specific version (None = latest)

    Returns:
        Entity URI string for USD sublayer reference

    Example:
        >>> uri = Uri.parse_unsafe('entity:/assets/SET/Arena')
        >>> generate_staged_sublayer_uri(uri, 'default')
        'entity:/assets/SET/Arena?variant=default'
        >>> generate_staged_sublayer_uri(uri, 'default', 'v0013')
        'entity:/assets/SET/Arena?variant=default&version=v0013'
    """
    uri_str = f"{entity_uri}?variant={variant_name}"
    if version_name:
        uri_str += f"&version={version_name}"
    return uri_str
