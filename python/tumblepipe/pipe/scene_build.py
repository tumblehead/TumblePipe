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

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.util.io import load_json, store_text, store_json
from tumblepipe.config.channels import read_published_channel
from tumblepipe.config.scene import (
    SCENES_URI,
    AssetEntry,
    get_scene_by_uri,
    get_inherited_scene_ref,
)
from tumblepipe.config.timeline import get_frame_range, require_fps
from tumblepipe.pipe.context import root_input
from tumblepipe.pipe.paths import (
    get_next_version_path,
    get_root_export_path,
    get_root_layer_file_name,
    get_scene_staged_path,
    is_finished_version,
    latest_root_version_path,
    list_version_paths,
    next_scene_staged_path,
    get_scene_layer_file_name,
)
from tumblepipe.pipe.usd.sublayer_refs import (
    generate_scene_sublayer_uri,
    generate_staged_sublayer_uri,
)
from tumblepipe.pipe.usd.usda import generate_usda_content


def root_chain(scene_uri: Uri) -> list[Uri]:
    """``scenes:/a/b/c``, then its parent Roots, closest first."""
    segments = list(scene_uri.segments)
    chain = []
    while segments:
        uri = SCENES_URI
        for segment in segments:
            uri = uri / segment
        chain.append(uri)
        segments = segments[:-1]
    return chain


def _latest_root_export(scene_uri: Uri) -> Path | None:
    """A Root's newest finished export directory, or None: its assets are
    read from that export's context.json."""
    for version_path in reversed(list_version_paths(get_scene_staged_path(scene_uri))):
        if is_finished_version(version_path):
            return version_path
    return None


def root_assets(scene_uri: Uri) -> list[AssetEntry]:
    """The assets a Root composes, as its exported layers say.

    A Root's layer sublayers its own assets, then its parent Roots' layers
    (each at their latest export), so this reads the same: the Root's own
    exported list, then each parent's, closest first. One entry per asset,
    the first met: a Root's own entry beats a parent's, and of two own
    entries the first. Everything that asks which assets a shot's Root
    gives it (import_shot, the build, the export check) asks this.
    """
    found: dict[str, AssetEntry] = {}
    for uri in root_chain(scene_uri):
        version_path = _latest_root_export(uri)
        if version_path is None:
            continue
        data = load_json(version_path / 'context.json')
        for item in data.get('parameters', {}).get('assets', []):
            entry = AssetEntry(
                asset=item['asset'],
                instances=item.get('instances', 1),
                channel=read_published_channel(
                    item, where=f'{version_path}/context.json'
                ),
            )
            found.setdefault(entry.asset, entry)
    return list(found.values())


def root_marker(scene_uri: Uri) -> list[dict]:
    """The inputs a Root's assets carry: the Root and its exported version."""
    version_path = _latest_root_export(scene_uri)
    return [root_input(scene_uri, version_path.name if version_path else None)]


def shot_root(shot_uri: Uri) -> Uri | None:
    """The Root the shot's newest root layer composes, or None."""
    version_path = latest_root_version_path(shot_uri)
    if version_path is None:
        return None
    scene_ref = load_json(version_path / 'context.json').get('parameters', {}).get('scene')
    return None if scene_ref is None else Uri.parse(scene_ref)


def root_layer_is_stale(shot_uri: Uri) -> bool:
    """True when the shot has no root layer, or its newest one names
    another Root than the shot (or its sequence) now points at."""
    if latest_root_version_path(shot_uri) is None:
        return True
    scene_ref, _ = get_inherited_scene_ref(shot_uri)
    return shot_root(shot_uri) != scene_ref


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
        asset_uri = Uri.parse(entry.asset)
        staged_uri = generate_staged_sublayer_uri(asset_uri, entry.channel)
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
    usda_content = generate_usda_content(layer_paths=layer_uris)

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
                {'asset': entry.asset, 'instances': entry.instances, 'channel': entry.channel}
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

    # The root layer's timing is the shot's: its own fps, else the project's
    fps = require_fps(shot_uri)

    # Collect sublayer references
    layer_refs = []

    # Scene layer (if assigned) - use entity URI for dynamic resolution
    if scene_ref is not None:
        scene_uri = generate_scene_sublayer_uri(scene_ref)
        layer_refs.append(scene_uri)

    # Root defaults template (weakest - provides camera, render settings, render vars)
    # Note: This is the only exception - config templates use filesystem paths
    # since they are static and don't need dynamic version resolution
    root_defaults_uri = Uri.parse('config:/usd/root_default_prims.usda')
    root_defaults_path = api.storage.resolve(root_defaults_uri)
    if root_defaults_path.exists():
        layer_refs.append(root_defaults_path)

    # Get next version path for root (shot-level, not channel-specific)
    version_path = get_next_version_path(get_root_export_path(shot_uri))
    version_name = version_path.name

    # Generate output path (no channel in filename for shot-level root)
    layer_file_name = get_root_layer_file_name(shot_uri, version_name)
    output_path = version_path / layer_file_name

    # Get full frame range (including roll)
    full_range = frame_range.full_range()

    # Generate USDA content with sublayers and timing metadata
    usda_content = generate_usda_content(
        layer_paths=layer_refs,
        relative_to=output_path.parent,
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
