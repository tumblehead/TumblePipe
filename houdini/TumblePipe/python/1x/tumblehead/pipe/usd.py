"""
Shared USD utilities for generating USDA file content.
"""

from pathlib import Path
import os

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
