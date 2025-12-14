"""
Shared USD utilities for generating USDA file content.
"""

from pathlib import Path
import os
import re
import logging

from tumblehead.api import path_str


def generate_usda_content(
    layer_paths: list[Path],
    output_path: Path,
    fps: float = None,
    start_frame: int = None,
    end_frame: int = None
) -> str:
    """
    Generate USDA file content with sublayers.

    Args:
        layer_paths: List of absolute paths to sublayer files
        output_path: Final output path (for computing relative paths)
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

    for layer_path in layer_paths:
        # Try to compute relative path, fall back to absolute if drives differ (Windows)
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
    layer_paths: list[Path],
    output_path: Path
) -> str:
    """
    Generate simple USDA file content with just sublayers (no timing metadata).

    Useful for asset staged files that don't need frame range information.

    Args:
        layer_paths: List of absolute paths to sublayer files
        output_path: Final output path (for computing relative paths)

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
    Collapse a staged USDA file by resolving all 'latest' references to versioned paths.

    Reads the staged file, finds all sublayer references containing 'latest',
    resolves each to its actual versioned path, and generates new USDA content.

    Args:
        staged_file_path: Path to the source _latest.usda file
        output_path: Output path (for computing relative paths)

    Returns:
        USDA file content as string with resolved version references

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

    # Resolve each sublayer path
    source_dir = staged_file_path.parent
    resolved_paths = []

    for sublayer_str in metadata['sublayers']:
        sublayer_path = Path(sublayer_str)
        resolved_path = _resolve_latest_to_version(sublayer_path, source_dir)
        resolved_paths.append(resolved_path)

    # Generate new USDA content with resolved paths
    return generate_usda_content(
        layer_paths=resolved_paths,
        output_path=output_path,
        fps=metadata['fps'],
        start_frame=metadata['start_frame'],
        end_frame=metadata['end_frame']
    )


def add_sublayer(layer_path: Path, sublayer_path: Path) -> bool:
    """
    Add a sublayer to an existing USD layer file.

    Args:
        layer_path: Path to the USD layer to modify
        sublayer_path: Path to the sublayer to add (will be stored as relative path)

    Returns:
        True if sublayer was added successfully, False on error
    """
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if not layer:
        logging.error(f"Failed to open layer: {layer_path}")
        return False

    # Compute relative path from layer to sublayer
    rel_path = os.path.relpath(sublayer_path, layer_path.parent)

    # Append to sublayers (last = weakest in USD composition)
    layer.subLayerPaths.append(rel_path.replace('\\', '/'))
    layer.Save()
    return True
