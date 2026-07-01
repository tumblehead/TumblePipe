"""
Pipeline-layer build operations for scenes and a shot's root department.

This is the pipe-layer home for scene-driven USD layer generation: it composes
scene references and scene contents (config) with versioned export paths and USD
content generation (pipe). It deliberately lives above ``config`` — config
describes scenes, the pipe builds USD from them — so the dependency runs
downward (pipe -> config), never the inversion that previously forced these
imports to be deferred inside config.
"""

from pathlib import Path

from tumblepipe.api import api, local_path
from tumblepipe.util.uri import Uri
from tumblepipe.util.io import store_text, store_json
from tumblepipe.config.scene import (
    SCENES_URI,
    get_scene_by_uri,
    get_inherited_scene_ref,
)
from tumblepipe.config.timeline import get_frame_range, get_fps
from tumblepipe.pipe.paths import (
    get_next_version_path,
    get_root_layer_file_name,
    next_scene_staged_path,
    get_scene_layer_file_name,
)
from tumblepipe.pipe.usd import (
    generate_usda_content,
    generate_simple_usda_content,
    generate_scene_sublayer_uri,
    generate_staged_sublayer_uri,
)


def export_scene_version(scene_uri: Uri) -> Path:
    """
    Export a new scene layer version.

    Creates versioned .usda at:
    export:/scenes/{path}/_staged/v####/{scene}_v####.usda

    The file sublayers:
    1. Direct asset staged files (strongest in USD composition)
    2. Parent scene layers (for inheritance - weaker)

    Parent scene inheritance allows changes to parent scenes to propagate
    automatically to child scenes without re-exporting the child.

    Returns:
        Path to the generated .usda file

    Raises:
        ValueError: If scene not found or asset builds missing
    """
    # Get scene
    scene = get_scene_by_uri(scene_uri)
    if scene is None:
        raise ValueError(f"Scene not found: {scene_uri}")

    # Collect sublayer URIs
    layer_uris = []

    # 1. Direct assets FIRST (strongest in USD composition)
    for entry in scene.assets:
        asset_uri = Uri.parse_unsafe(entry.asset)
        staged_uri = generate_staged_sublayer_uri(asset_uri, entry.variant)
        layer_uris.append(staged_uri)

    # 2. Parent scene sublayers AFTER (weaker, inherited)
    #    Walk up: scenes:/outdoor/forest -> scenes:/outdoor
    segments = list(scene_uri.segments)
    while len(segments) > 1:
        segments = segments[:-1]
        parent_uri = SCENES_URI
        for seg in segments:
            parent_uri = parent_uri / seg
        parent_scene_uri = generate_scene_sublayer_uri(parent_uri)
        layer_uris.append(parent_scene_uri)

    # Get next version path
    version_path = next_scene_staged_path(scene_uri)
    version_name = version_path.name

    # Generate output path
    layer_file_name = get_scene_layer_file_name(scene_uri, version_name)
    output_path = version_path / layer_file_name

    # Generate USDA content (no timing metadata for scenes)
    usda_content = generate_simple_usda_content(
        layer_paths=layer_uris,
        output_path=output_path
    )

    # Write files
    version_path.mkdir(parents=True, exist_ok=True)
    store_text(output_path, usda_content)

    # Write context.json
    context_path = version_path / 'context.json'
    store_json(context_path, {
        'uri': str(scene_uri),
        'version': version_name,
        'parameters': {
            'assets': [
                {'asset': entry.asset, 'instances': entry.instances, 'variant': entry.variant}
                for entry in scene.assets
            ]
        }
    })

    return output_path


def generate_root_version(shot_uri: Uri) -> Path:
    """
    Generate a new root department version for a shot.

    This creates a versioned .usda file at:
    export:/shots/{seq}/{shot}/root/v####/{shot}_root_v####.usda

    The file sublayers:
    1. Scene .usda (if scene assigned) - contains asset sublayers
    2. Root defaults template - camera, render settings, render vars

    Shots without a scene assigned will only have the root defaults template.

    Args:
        shot_uri: The shot entity URI (e.g., entity:/shots/010/010)

    Returns:
        Path to the generated USD file

    Raises:
        ValueError: If frame range not set
    """
    # Get scene reference (may be None if no scene assigned)
    scene_ref, _ = get_inherited_scene_ref(shot_uri)

    # Get frame range
    frame_range = get_frame_range(shot_uri)
    if frame_range is None:
        raise ValueError(f"No frame range defined for {shot_uri}")

    # Get fps (default to 24 if not set)
    fps = get_fps(shot_uri)
    if fps is None:
        fps = 24

    # Collect sublayer references
    layer_refs = []

    # Scene layer (if assigned) - use entity URI for dynamic resolution
    if scene_ref is not None:
        scene_uri = generate_scene_sublayer_uri(scene_ref)
        layer_refs.append(scene_uri)

    # Root defaults template (weakest - provides camera, render settings, render vars)
    # Note: This is the only exception - config templates use filesystem paths
    # since they are static and don't need dynamic version resolution
    root_defaults_uri = Uri.parse_unsafe('config:/usd/root_default_prims.usda')
    root_defaults_path = local_path(api.storage.resolve(root_defaults_uri))
    if root_defaults_path.exists():
        layer_refs.append(root_defaults_path)

    # Get next version path for root (shot-level, not variant-specific)
    export_uri = Uri.parse_unsafe('export:/') / shot_uri.segments / '_root'
    export_path = local_path(api.storage.resolve(export_uri))
    version_path = get_next_version_path(export_path)
    version_name = version_path.name

    # Generate output path (no variant in filename for shot-level root)
    layer_file_name = get_root_layer_file_name(shot_uri, version_name)
    output_path = version_path / layer_file_name

    # Get full frame range (including roll)
    full_range = frame_range.full_range()

    # Generate USDA content with sublayers and timing metadata
    usda_content = generate_usda_content(
        layer_paths=layer_refs,
        output_path=output_path,
        fps=fps,
        start_frame=full_range.first_frame,
        end_frame=full_range.last_frame
    )

    # Write files
    version_path.mkdir(parents=True, exist_ok=True)
    store_text(output_path, usda_content)

    # Write context.json
    context_path = version_path / 'context.json'
    store_json(context_path, {
        'uri': str(shot_uri),
        'department': 'root',
        'version': version_name,
        'parameters': {
            'scene': str(scene_ref) if scene_ref else None
        }
    })

    return output_path
