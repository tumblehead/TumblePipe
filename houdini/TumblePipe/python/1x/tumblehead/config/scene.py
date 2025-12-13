"""
Scene description configuration for shots.

Shots reference scenes via the 'scene' property which contains a scene URI.
Scenes are first-class objects stored at scenes:/ that define asset lists.

This module handles:
- Scene reference get/set for entities (shots, sequences)
- Inheritance of scene references from parent entities
- Resolution of scene assets for root department generation
"""

from pathlib import Path

from tumblehead.api import default_client, fix_path
from tumblehead.util.uri import Uri
from tumblehead.util.io import store_text, store_json

api = default_client()


def get_scene_ref(entity_uri: Uri) -> Uri | None:
    """
    Get scene reference for an entity (shot/sequence).

    Args:
        entity_uri: The entity URI (e.g., entity:/shots/010/010)

    Returns:
        Scene URI if set, None otherwise
    """
    properties = api.config.get_properties(entity_uri)
    if properties is None:
        return None
    scene_str = properties.get('scene')
    if scene_str is None:
        return None
    # Handle legacy format (list of assets) - return None to trigger migration
    if isinstance(scene_str, list):
        return None
    return Uri.parse_unsafe(scene_str)


def set_scene_ref(entity_uri: Uri, scene_uri: Uri | None):
    """
    Set scene reference for an entity.

    Args:
        entity_uri: The entity URI (e.g., entity:/shots/010/010)
        scene_uri: Scene URI to reference, or None to clear
    """
    properties = api.config.get_properties(entity_uri)
    if properties is None:
        properties = {}
    if scene_uri is None:
        properties.pop('scene', None)
    else:
        properties['scene'] = str(scene_uri)
    api.config.set_properties(entity_uri, properties)


def get_inherited_scene_ref(entity_uri: Uri) -> tuple[Uri | None, Uri | None]:
    """
    Get inherited scene reference by walking up hierarchy.

    Returns both the resolved scene URI and the entity it was inherited from.

    Args:
        entity_uri: The entity URI to resolve scene for

    Returns:
        Tuple of (scene_uri, inherited_from_uri)
        - scene_uri: The resolved scene URI, or None if no scene assigned
        - inherited_from_uri: The entity URI the scene was inherited from,
          or None if it's set directly on the entity (not inherited)
    """
    # Check the entity itself first
    scene_ref = get_scene_ref(entity_uri)
    if scene_ref is not None:
        return (scene_ref, None)  # Not inherited, set directly

    # Walk up to parents
    for i in range(len(entity_uri.segments) - 1, 0, -1):
        parent_segments = entity_uri.segments[:i]
        uri_str = f"{entity_uri.purpose}:/{'/'.join(parent_segments)}"
        parent_uri = Uri.parse_unsafe(uri_str)
        scene_ref = get_scene_ref(parent_uri)
        if scene_ref is not None:
            return (scene_ref, parent_uri)  # Inherited from parent

    return (None, None)


def get_resolved_scene_assets(entity_uri: Uri) -> list[Uri]:
    """
    Get resolved scene assets for an entity.

    Resolves the scene reference (with inheritance) and returns the assets
    from that scene.

    Args:
        entity_uri: The entity URI to resolve scene for

    Returns:
        List of asset URIs from the resolved scene, or empty list if no scene
    """
    from tumblehead.config.scenes import get_scene as get_scene_by_uri

    scene_ref, _ = get_inherited_scene_ref(entity_uri)
    if scene_ref is None:
        return []

    scene = get_scene_by_uri(scene_ref)
    if scene is None:
        return []

    return list(scene.assets.keys())


def get_scene(entity_uri: Uri):
    """
    Get scene for an entity (shot/sequence).

    Resolves the scene reference and returns the Scene object.
    If no scene is assigned, returns a Scene with the entity as reference
    and empty assets.

    Args:
        entity_uri: The entity URI (e.g., entity:/shots/010/010)

    Returns:
        Scene object with assets
    """
    from tumblehead.config.scenes import (
        get_scene as get_scene_by_uri,
        Scene,
        AssetEntry
    )

    scene_ref, _ = get_inherited_scene_ref(entity_uri)
    if scene_ref is None:
        # No scene assigned - return empty Scene using entity as reference
        return Scene(uri=entity_uri, assets={})

    scene = get_scene_by_uri(scene_ref)
    if scene is None:
        return Scene(uri=scene_ref, assets={})

    return scene


def set_scene(entity_uri: Uri, assets: dict[Uri, 'AssetEntry']):
    """
    Set scene assets for an entity.

    Creates or updates the scene for the entity. If no scene reference exists,
    creates a new scene based on the entity path.

    Args:
        entity_uri: The entity URI (e.g., entity:/shots/010/010)
        assets: Dict of asset URI -> AssetEntry (instances, variant)
    """
    from tumblehead.config.scenes import (
        get_scene as get_scene_by_uri,
        set_scene_assets,
        add_scene,
        AssetEntry
    )

    scene_ref, _ = get_inherited_scene_ref(entity_uri)

    # If no scene exists, create one based on entity path
    if scene_ref is None:
        # Create scene with path structure based on entity
        # entity:/shots/010/010 -> scenes:/shots_010_010
        scene_path = '_'.join(entity_uri.segments)
        scene_ref = add_scene(scene_path)
        set_scene_ref(entity_uri, scene_ref)

    # Save assets to the scene
    set_scene_assets(scene_ref, assets)


def list_available_assets() -> list[Uri]:
    """
    List all available assets for scene assignment.

    Returns:
        List of all asset URIs from the database
    """
    asset_entities = api.config.list_entities(
        filter=Uri.parse_unsafe('entity:/assets'),
        closure=True
    )
    return [entity.uri for entity in asset_entities]


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
    from tumblehead.config.timeline import get_frame_range, get_fps
    from tumblehead.pipe.paths import (
        get_next_version_path,
        get_scene_latest_path,
        get_layer_file_name
    )
    from tumblehead.pipe.usd import generate_usda_content

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

    # Collect sublayer paths
    layer_paths = []

    # Scene layer (if assigned) - ALWAYS include path even if not exported yet
    # This allows the "latest" reference chain to work when scene is exported later
    if scene_ref is not None:
        scene_path = get_scene_latest_path(scene_ref)
        layer_paths.append(scene_path)
        # USD will resolve the reference at load time

    # Root defaults template (weakest - provides camera, render settings, render vars)
    root_defaults_uri = Uri.parse_unsafe('config:/usd/root_default_prims.usda')
    root_defaults_path = fix_path(api.storage.resolve(root_defaults_uri))
    if root_defaults_path.exists():
        layer_paths.append(root_defaults_path)

    # Get next version path for root department (using 'default' variant)
    export_uri = Uri.parse_unsafe('export:/') / shot_uri.segments / 'default' / 'root'
    export_path = fix_path(api.storage.resolve(export_uri))
    version_path = get_next_version_path(export_path)
    version_name = version_path.name

    # Generate output path
    layer_file_name = get_layer_file_name(shot_uri, 'default', 'root', version_name)
    output_path = version_path / layer_file_name

    # Get full frame range (including roll)
    full_range = frame_range.full_range()

    # Generate USDA content with sublayers and timing metadata
    usda_content = generate_usda_content(
        layer_paths=layer_paths,
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


def get_root_layer_path(shot_uri: Uri) -> Path | None:
    """
    Get the latest root department layer path for a shot.

    Args:
        shot_uri: The shot entity URI

    Returns:
        Path to the latest root layer .usd file, or None if no root exports exist
    """
    from tumblehead.pipe.paths import latest_export_path, get_layer_file_name

    # Use 'default' variant for root department
    version_path = latest_export_path(shot_uri, 'default', 'root')
    if version_path is None:
        return None

    version_name = version_path.name
    layer_file_name = get_layer_file_name(shot_uri, 'default', 'root', version_name)
    layer_path = version_path / layer_file_name

    if not fix_path(layer_path).exists():
        return None

    return layer_path
