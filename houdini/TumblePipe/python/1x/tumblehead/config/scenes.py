"""
Scene configuration for shots.

Scenes are first-class objects that define which assets compose a scene.
Shots reference scenes via the 'scene' property on their entity.

Scenes support hierarchical organization:
- scenes:/forest         (flat scene)
- scenes:/outdoor/forest (categorized scene)
- scenes:/outdoor/day/forest (nested categories)
"""

from dataclasses import dataclass
from pathlib import Path

from tumblehead.api import default_client, fix_path
from tumblehead.util.uri import Uri
from tumblehead.util.io import store_text, store_json

api = default_client()

SCENES_URI = Uri.parse_unsafe('scenes:/')

DEFAULT_VARIANT = 'default'


@dataclass(frozen=True)
class AssetEntry:
    """An asset entry in a scene with instance count and variant."""
    asset: str  # URI string
    instances: int
    variant: str = DEFAULT_VARIANT


@dataclass(frozen=True)
class Scene:
    """A scene definition containing assets with instance counts and variants."""
    uri: Uri
    assets: list[AssetEntry]  # List allows multiple entries per asset

    @property
    def name(self) -> str:
        """Get the scene name (last segment)."""
        return self.uri.segments[-1] if self.uri.segments else ''

    @property
    def display_name(self) -> str:
        """Get the full display path (e.g., 'outdoor/forest')."""
        return '/'.join(self.uri.segments) if self.uri.segments else ''


@dataclass(frozen=True)
class SceneTreeNode:
    """A node in the scene hierarchy tree."""
    name: str
    uri: Uri
    is_scene: bool  # True if this is a scene (has assets), False if category
    children: list['SceneTreeNode']


def is_scene_uri(uri: Uri) -> bool:
    """Check if a URI is a valid scene URI (any depth >= 1)."""
    if uri.purpose != 'scenes':
        return False
    if len(uri.segments) < 1:
        return False
    return True


def _get_scene_schema_uri(depth: int) -> Uri:
    """
    Get the schema URI for a scene at a given depth.

    depth=1: scenes:/scene -> schemas:/scenes/scene
    depth=2: scenes:/cat/scene -> schemas:/scenes/category/scene
    depth=3: scenes:/cat/subcat/scene -> schemas:/scenes/category/category/scene
    """
    if depth == 1:
        return Uri.parse_unsafe('schemas:/scenes/scene')
    elif depth == 2:
        return Uri.parse_unsafe('schemas:/scenes/category/scene')
    else:
        # For depth >= 3, use nested category path
        return Uri.parse_unsafe('schemas:/scenes/category/category/scene')


def add_scene(path: str, assets: list[AssetEntry] | None = None) -> Uri:
    """
    Create a new scene.

    Creates parent scenes automatically if they don't exist (with empty assets).
    This ensures all nodes in the scene hierarchy are editable scenes.

    Args:
        path: The scene path (e.g., "forest" or "outdoor/forest")
        assets: Optional list of AssetEntry objects

    Returns:
        The URI of the created scene

    Raises:
        ValueError: If scene already exists
    """
    if assets is None:
        assets = []

    # Build URI from path
    segments = [s.strip() for s in path.split('/') if s.strip()]
    if not segments:
        raise ValueError('Scene path cannot be empty')

    # Create parent scenes first (if they don't exist)
    # This ensures "scenes all the way down" - every node is editable
    for i in range(1, len(segments)):
        parent_uri = SCENES_URI
        for seg in segments[:i]:
            parent_uri = parent_uri / seg

        parent_props = api.config.get_properties(parent_uri)
        if parent_props is None:
            # Create parent as scene with empty assets
            parent_schema = _get_scene_schema_uri(i)
            api.config.add_entity(parent_uri, dict(assets=[]), parent_schema)

    # Build final scene URI
    scene_uri = SCENES_URI
    for segment in segments:
        scene_uri = scene_uri / segment

    properties = api.config.get_properties(scene_uri)
    if properties is not None:
        raise ValueError('Scene already exists')

    schema_uri = _get_scene_schema_uri(len(segments))
    api.config.add_entity(scene_uri, dict(
        assets=[
            {'asset': entry.asset, 'instances': entry.instances, 'variant': entry.variant}
            for entry in assets
        ]
    ), schema_uri)
    return scene_uri


def remove_scene(scene_uri: Uri):
    """
    Delete a scene.

    Args:
        scene_uri: The scene URI to delete
    """
    api.config.remove_entity(scene_uri)


def get_scene(scene_uri: Uri) -> Scene | None:
    """
    Get a scene by URI.

    Args:
        scene_uri: The scene URI

    Returns:
        Scene object or None if not found
    """
    properties = api.config.get_properties(scene_uri)
    if properties is None:
        return None

    raw_assets = properties.get('assets', [])

    assets = [
        AssetEntry(
            asset=item['asset'],
            instances=item.get('instances', 1),
            variant=item.get('variant', DEFAULT_VARIANT)
        )
        for item in raw_assets
        if isinstance(item, dict)
    ]

    return Scene(
        uri=scene_uri,
        assets=assets
    )


def list_scenes() -> list[Scene]:
    """
    List all scenes (flat list, any depth).

    Uses tree traversal to ensure all scenes are included, even parent scenes
    that might not be returned by list_entities when children exist.

    Returns:
        List of all Scene objects, including parent scenes
    """
    tree_nodes = list_scene_tree()
    scenes = []

    def collect_scenes(node: SceneTreeNode):
        # Fetch scene directly to ensure we get all scenes including parents
        scene = get_scene(node.uri)
        if scene is not None:
            scenes.append(scene)
        for child in node.children:
            collect_scenes(child)

    for node in tree_nodes:
        collect_scenes(node)

    return scenes


def list_scene_tree() -> list[SceneTreeNode]:
    """
    List scenes as a hierarchical tree.

    Returns:
        List of root SceneTreeNode objects
    """
    entities = api.config.list_entities(SCENES_URI, closure=True)

    # Build a dict of uri_str -> entity for quick lookup
    entity_map = {str(e.uri): e for e in entities}

    # Build tree structure
    # First, collect all unique paths
    all_paths: dict[str, dict] = {}  # uri_str -> {entity, children: {}}

    for entity in entities:
        uri = entity.uri
        # Add this entity and all its parent paths
        for i in range(1, len(uri.segments) + 1):
            path_segments = uri.segments[:i]
            path_uri_str = f"scenes:/{'/'.join(path_segments)}"

            if path_uri_str not in all_paths:
                # Check if this path has an entity
                path_entity = entity_map.get(path_uri_str)
                all_paths[path_uri_str] = {
                    'uri_str': path_uri_str,
                    'segments': path_segments,
                    'entity': path_entity,
                    'children': {}
                }

    # Build parent-child relationships
    for path_uri_str, path_info in all_paths.items():
        segments = path_info['segments']
        if len(segments) > 1:
            # Find parent
            parent_segments = segments[:-1]
            parent_uri_str = f"scenes:/{'/'.join(parent_segments)}"
            if parent_uri_str in all_paths:
                all_paths[parent_uri_str]['children'][path_uri_str] = path_info

    def build_node(path_info: dict) -> SceneTreeNode:
        entity = path_info['entity']
        uri = Uri.parse_unsafe(path_info['uri_str'])
        name = path_info['segments'][-1]

        # It's a scene if it has 'assets' property
        is_scene = entity is not None and 'assets' in entity.properties

        children = [
            build_node(child_info)
            for child_info in sorted(
                path_info['children'].values(),
                key=lambda x: x['segments'][-1]
            )
        ]

        return SceneTreeNode(
            name=name,
            uri=uri,
            is_scene=is_scene,
            children=children
        )

    # Build root nodes (depth 1)
    root_nodes = []
    for path_uri_str, path_info in sorted(all_paths.items()):
        if len(path_info['segments']) == 1:
            root_nodes.append(build_node(path_info))

    return root_nodes


def set_scene_assets(scene_uri: Uri, assets: list[AssetEntry]):
    """
    Update a scene's assets with instance counts and variants.

    Args:
        scene_uri: The scene URI
        assets: List of AssetEntry objects

    Raises:
        ValueError: If scene does not exist
    """
    properties = api.config.get_properties(scene_uri)
    if properties is None:
        raise ValueError('Scene does not exist')
    properties['assets'] = [
        {'asset': entry.asset, 'instances': entry.instances, 'variant': entry.variant}
        for entry in assets
    ]
    api.config.set_properties(scene_uri, properties)


def get_inherited_assets(scene_uri: Uri) -> list[tuple[AssetEntry, Uri]]:
    """
    Get all assets inherited from parent scenes.

    Walks up the scene hierarchy and collects assets from each parent.
    Assets from closer parents appear first in the list.

    Args:
        scene_uri: The scene URI (e.g., scenes:/outdoor/forest)

    Returns:
        List of (AssetEntry, inherited_from_scene_uri) tuples
    """
    inherited = []
    segments = list(scene_uri.segments)

    # Walk up: scenes:/outdoor/forest -> scenes:/outdoor
    while len(segments) > 1:
        segments = segments[:-1]
        parent_uri = SCENES_URI
        for seg in segments:
            parent_uri = parent_uri / seg

        parent_scene = get_scene(parent_uri)
        if parent_scene and parent_scene.assets:
            for entry in parent_scene.assets:
                inherited.append((entry, parent_uri))

    return inherited


def find_shots_with_scene_ref(scene_uri: Uri) -> list[Uri]:
    """
    Find all shots that reference a specific scene (directly, not inherited).

    Args:
        scene_uri: The scene URI to search for

    Returns:
        List of entity URIs that directly reference this scene
    """
    from tumblehead.config.scene import get_scene_ref

    shot_entities = api.config.list_entities(
        Uri.parse_unsafe('entity:/shots'),
        closure=True
    )

    affected = []
    scene_uri_str = str(scene_uri)

    for entity in shot_entities:
        scene_ref = get_scene_ref(entity.uri)
        if scene_ref and str(scene_ref) == scene_uri_str:
            affected.append(entity.uri)

    return affected


def find_all_shots_using_scene(scene_uri: Uri) -> list[Uri]:
    """
    Find all shots that use a scene (directly OR via inheritance).

    Unlike find_shots_with_scene_ref() which only finds direct refs,
    this finds all shots that would be affected if the scene changes.

    Args:
        scene_uri: The scene URI to search for

    Returns:
        List of entity URIs that use this scene (directly or inherited)
    """
    from tumblehead.config.scene import get_inherited_scene_ref

    shot_entities = api.config.list_entities(
        Uri.parse_unsafe('entity:/shots'),
        closure=True
    )

    affected = []
    scene_uri_str = str(scene_uri)

    for entity in shot_entities:
        # Get resolved scene (with inheritance)
        resolved_scene, _ = get_inherited_scene_ref(entity.uri)
        if resolved_scene and str(resolved_scene) == scene_uri_str:
            affected.append(entity.uri)

    return affected


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
    from tumblehead.pipe.paths import (
        next_scene_staged_path,
        get_scene_layer_file_name
    )
    from tumblehead.pipe.usd import (
        generate_simple_usda_content,
        generate_staged_sublayer_uri,
        generate_scene_sublayer_uri
    )

    # Get scene
    scene = get_scene(scene_uri)
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


