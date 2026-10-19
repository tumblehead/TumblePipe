"""The text of sublayer-only .usda files: writing it and reading it back.

Staged builds, scene layers, shot root layers and the farm's collapsed stage
are all a metadata block plus a ``subLayers`` list, so they are written as
text rather than through Sdf. Reading them back is text too: the callers want
what a file *recorded*, without composing it or needing a USD runtime.
"""

import os
import re
from pathlib import Path
from typing import Union

from tumblepipe.api import path_str

# The @…@ asset paths inside a ``subLayers = [...]`` list.
_SUBLAYERS_RE = re.compile(r'subLayers\s*=\s*\[(.*?)\]', re.DOTALL)
_ASSET_PATH_RE = re.compile(r'@([^@]+)@')


def generate_usda_content(
    layer_paths: list[Union[Path, str]],
    relative_to: Path | None = None,
    fps: float | None = None,
    start_frame: int | None = None,
    end_frame: int | None = None,
    render_settings_prim_path: str | None = None
) -> str:
    """
    Generate USDA file content with sublayers.

    ``entity:/`` URIs are written verbatim; the resolver resolves them when
    the layer composes. Anything else is a filesystem path: written relative
    to ``relative_to`` (the directory the file will live in) when given, and
    absolute otherwise — the collapsed farm stage wants absolute paths, a
    shot root layer sublayering the project's static
    ``root_default_prims.usda`` wants a relative one.

    Args:
        layer_paths: Sublayer references, strongest first.
        relative_to: Directory filesystem paths are made relative to.
        fps: Frames per second (omit for files without timing).
        start_frame: Start time code (optional)
        end_frame: End time code (optional)
        render_settings_prim_path: Prim path of the stage's RenderSettings, stamped
                    as `renderSettingsPrimPath` layer metadata (optional). See
                    the note below for why this has to be on the ROOT layer.

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

    # Which RenderSettings husk renders with. It has to be stamped on THIS
    # layer: husk reads the metadatum off the root layer only, and its own
    # search for a settings prim covers `/Render` and nothing else. A project
    # whose settings live at `/scene/Render/rendersettings` (7 of 13 live
    # projects) declares that path in root_default_prims.usda, which reaches a
    # collapsed stage as a *sublayer* — husk never sees it, logs "No camera in
    # render settings, defaulting to <first camera in the stage>" and renders
    # the whole shot from the project template's origin camera. Verified
    # against husk 22.0.429: with the metadatum here it logs "Using stage
    # default settings: <path>" instead.
    if render_settings_prim_path:
        lines.append(
            f'    renderSettingsPrimPath = "{render_settings_prim_path}"'
        )

    # Add standard USD metadata
    lines.append('    metersPerUnit = 1')
    lines.append('    upAxis = "Y"')

    # Add sublayers
    lines.append('    subLayers = [')

    for layer_ref in layer_paths:
        if isinstance(layer_ref, str) and layer_ref.startswith('entity:/'):
            lines.append(f'        @{layer_ref}@,')
            continue
        layer_path = Path(layer_ref)
        if relative_to is not None:
            try:
                layer_path = Path(os.path.relpath(layer_path, relative_to))
            except ValueError:
                # Windows only: no relative path exists between two drives
                # (temp on C:, export on P:), so the absolute one is the
                # only correct reference.
                pass
        lines.append(f'        @{path_str(layer_path)}@,')

    lines.append('    ]')
    lines.append(')')

    return '\n'.join(lines)


def parse_usda_metadata(content: str) -> dict:
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
        'sublayers': parse_sublayers(content)
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

    return metadata


def parse_sublayers(content: str) -> list[str]:
    """The ``subLayers`` asset paths in USDA ``content``, in file order.

    Entity URIs and filesystem paths alike, verbatim; [] when the file has
    no ``subLayers`` list.
    """
    sublayers_match = _SUBLAYERS_RE.search(content)
    if not sublayers_match:
        return []
    return _ASSET_PATH_RE.findall(sublayers_match.group(1))
