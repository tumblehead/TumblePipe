"""
Scene configuration for shots.

Scenes are first-class objects stored at ``scenes:/`` that define which assets
compose a scene. Shots reference scenes via the ``scene`` property on their
entity; that reference is inherited from parent entities when not set directly.

This module handles both halves of scene description, which are mutually
recursive and therefore live together:

- Scene objects: create/read/update/delete, the hierarchy tree, inherited
  assets (the ``scenes:/`` storage side).
- Scene references on entities (shots, sequences): get/set, inheritance
  resolution, and resolving a shot's assets through its scene reference.

Scenes support hierarchical organization:
- ``scenes:/forest``              (flat scene)
- ``scenes:/outdoor/forest``      (categorized scene)
- ``scenes:/outdoor/day/forest``  (nested categories)
"""

from dataclasses import dataclass

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.config.entities import is_terminal_entity

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


# ---------------------------------------------------------------------------
# Scene objects (the scenes:/ storage side)
# ---------------------------------------------------------------------------

def is_scene_uri(uri: Uri) -> bool:
    """Check if a URI is a valid scene URI (any depth >= 1)."""
    if uri.purpose != 'scenes':
        return False
    if len(uri.segments) < 1:
        return False
    return True


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
            api.config.add_entity(parent_uri, dict(assets=[]))

    # Build final scene URI
    scene_uri = SCENES_URI
    for segment in segments:
        scene_uri = scene_uri / segment

    properties = api.config.get_properties(scene_uri)
    if properties is not None:
        raise ValueError('Scene already exists')

    api.config.add_entity(scene_uri, dict(
        assets=[
            {'asset': entry.asset, 'instances': entry.instances, 'variant': entry.variant}
            for entry in assets
        ]
    ))
    return scene_uri


def remove_scene(scene_uri: Uri):
    """
    Delete a scene.

    Args:
        scene_uri: The scene URI to delete
    """
    api.config.remove_entity(scene_uri)


def get_scene_by_uri(scene_uri: Uri) -> Scene | None:
    """
    Get a scene by its scene URI (``scenes:/...``).

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
        scene = get_scene_by_uri(node.uri)
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

        parent_scene = get_scene_by_uri(parent_uri)
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
    shot_uris = api.config.list_entity_uris(
        Uri.parse_unsafe('entity:/shots'),
        closure=True
    )

    affected = []
    scene_uri_str = str(scene_uri)

    for shot_uri in shot_uris:
        scene_ref = get_scene_ref(shot_uri)
        if scene_ref and str(scene_ref) == scene_uri_str:
            affected.append(shot_uri)

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
    shot_uris = api.config.list_entity_uris(
        Uri.parse_unsafe('entity:/shots'),
        closure=True
    )

    affected = []
    scene_uri_str = str(scene_uri)

    for shot_uri in shot_uris:
        # Get resolved scene (with inheritance)
        resolved_scene, _ = get_inherited_scene_ref(shot_uri)
        if resolved_scene and str(resolved_scene) == scene_uri_str:
            affected.append(shot_uri)

    return affected


# ---------------------------------------------------------------------------
# Scene references on entities (shots, sequences)
# ---------------------------------------------------------------------------

def get_scene_ref(entity_uri: Uri) -> Uri | None:
    """
    Get scene reference for an entity (shot/sequence).

    Args:
        entity_uri: The entity URI (e.g., entity:/shots/010/010)

    Returns:
        Scene URI if set *directly on this entity*, None otherwise. Use
        get_inherited_scene_ref to resolve refs inherited from ancestors —
        get_properties merges those down, so reading it here would wrongly
        report an inherited ref as direct.
    """
    properties = api.config.get_own_properties(entity_uri)
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
    scene_ref, _ = get_inherited_scene_ref(entity_uri)
    if scene_ref is None:
        return []

    scene = get_scene_by_uri(scene_ref)
    if scene is None:
        return []

    # Scene.assets is a list[AssetEntry]; return unique asset URIs in order.
    seen = set()
    result = []
    for entry in scene.assets:
        if entry.asset in seen:
            continue
        seen.add(entry.asset)
        result.append(Uri.parse_unsafe(entry.asset))
    return result


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
    scene_ref, _ = get_inherited_scene_ref(entity_uri)
    if scene_ref is None:
        # No scene assigned - return empty Scene using entity as reference
        return Scene(uri=entity_uri, assets=[])

    scene = get_scene_by_uri(scene_ref)
    if scene is None:
        return Scene(uri=scene_ref, assets=[])

    return scene


def set_scene(entity_uri: Uri, assets: "list['AssetEntry'] | dict[Uri, 'AssetEntry']"):
    """
    Set scene assets for an entity.

    Creates or updates the scene for the entity. If no scene reference exists,
    creates a new scene based on the entity path.

    Args:
        entity_uri: The entity URI (e.g., entity:/shots/010/010)
        assets: A list[AssetEntry] (canonical). A legacy dict[Uri, AssetEntry]
            is also accepted and normalised to its values — the dict keys are
            redundant with AssetEntry.asset.
    """
    if isinstance(assets, dict):
        assets = list(assets.values())

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
    with api.config.coherent():
        asset_uris = api.config.list_entity_uris(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        return [
            uri for uri in asset_uris
            if is_terminal_entity(api.config, uri)
        ]
