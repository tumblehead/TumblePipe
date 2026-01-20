"""
Tumblehead Custom USD Asset Resolver - PythonExpose.py

This module implements the Python-side resolution logic for the Cached Resolver.
It handles entity:/ URIs and resolves them to filesystem paths matching the
Tumblehead pipeline's path structure (as defined in paths.py).

Entity URI Format:
    entity:/TYPE/CATEGORY/NAME?dept=DEPARTMENT&variant=VARIANT&version=VERSION

Path Structure (matching paths.py):
    {EXPORT_PATH}/{entity_type}/{category}/{name}/{variant}/{dept}/{version}/{filename}

Filename Format:
    Variant layers: {entity_name}_{variant}_{dept}_{version}.usd  (.usda for root)
    Shared layers:  {entity_name}_shared_{dept}_{version}.usd

Where entity_name = "type_category_name" (e.g., "assets_SET_Arena")

Examples:
    entity:/assets/SET/Arena?dept=lookdev&variant=default&version=v0013
    -> P:/export/assets/SET/Arena/default/lookdev/v0013/assets_SET_Arena_default_lookdev_v0013.usd

    entity:/assets/SET/Arena?dept=lookdev&variant=_shared&version=v0005
    -> P:/export/assets/SET/Arena/_shared/lookdev/v0005/assets_SET_Arena_shared_lookdev_v0005.usd

Environment Variables:
    TH_EXPORT_PATH: Base path for exports (e.g., "P:/export")
                    Required - resolver will fail without it.

Module State:
    _latest_mode: When True, ignore explicit versions and always resolve to
                  latest. Provides cascade/closure semantics. Controlled via
                  set_latest_mode() / get_latest_mode() functions.
"""

import inspect
import logging
import os
import re
from functools import wraps
from pathlib import Path

from pxr import Ar, Sdf


# Init logger
logging.basicConfig(format="%(asctime)s %(message)s", datefmt="%Y/%m/%d %I:%M:%S%p")
LOG = logging.getLogger("Python | {file_name}".format(file_name=__name__))
LOG.setLevel(level=logging.INFO)


# Environment variable for latest mode (replaces module-level state)
# Using env var instead of Python global to persist across Houdini's cooking model
# where multiple nodes may set different modes during the same cook cycle.
_LATEST_MODE_ENV_VAR = "TH_RESOLVER_LATEST_MODE"


def set_latest_mode(enabled: bool):
    """Enable or disable latest mode for cascade semantics.

    When enabled, all entity:/ URIs resolve to their latest version,
    ignoring explicit versions. This provides full cascade/closure semantics
    where all nested layers also resolve to their latest versions.

    Uses environment variable to persist across Houdini's node cooking model.

    Args:
        enabled: True to always resolve to latest, False to respect explicit versions
    """
    os.environ[_LATEST_MODE_ENV_VAR] = "1" if enabled else "0"
    LOG.debug(f"::: Latest mode {'enabled' if enabled else 'disabled'}")


def get_latest_mode() -> bool:
    """Check if latest mode is currently enabled.

    Returns:
        True if latest mode is enabled, False otherwise
    """
    return os.environ.get(_LATEST_MODE_ENV_VAR, "0") == "1"


def _get_export_base_path() -> Path:
    """Get the base export path from environment.

    Raises:
        RuntimeError: If TH_EXPORT_PATH is not set.
    """
    export_path = os.environ.get("TH_EXPORT_PATH")
    if not export_path:
        raise RuntimeError(
            "TH_EXPORT_PATH environment variable is not set. "
            "This is required for entity:/ URI resolution."
        )
    return Path(export_path)


def log_function_args(func):
    """Decorator to print function call details."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        func_args = inspect.signature(func).bind(*args, **kwargs).arguments
        func_args_str = ", ".join(map("{0[0]} = {0[1]!r}".format, func_args.items()))
        # To enable logging on all methods, uncomment this:
        # LOG.info(f"{func.__module__}.{func.__qualname__} ({func_args_str})")
        return func(*args, **kwargs)

    return wrapper


def _parse_entity_uri(uri_string: str) -> dict:
    """
    Parse an entity URI into its components.

    Args:
        uri_string: URI like "entity:/assets/SET/Arena?dept=lookdev&variant=default"

    Returns:
        Dict with keys: segments (list), department, variant, version
        segments = ['assets', 'SET', 'Arena']
    """
    # Handle both "entity:/path" and "entity:path" formats
    if uri_string.startswith("entity:/"):
        uri_string = uri_string[7:]  # Remove "entity:" prefix, keep leading /
    elif uri_string.startswith("entity:"):
        uri_string = uri_string[7:]

    # Parse the path and query string
    if "?" in uri_string:
        path_part, query_part = uri_string.split("?", 1)
    else:
        path_part = uri_string
        query_part = ""

    # Parse path components
    # Assets: /assets/TYPE/NAME (3+ segments)
    # Scenes: /scenes/NAME or /scenes/PATH/NAME (2+ segments)
    segments = [p for p in path_part.split("/") if p]
    if len(segments) < 2:
        LOG.warning(f"Invalid entity URI path (need at least 2 segments): {path_part}")
        return None

    # Parse query parameters
    params = {}
    if query_part:
        for param in query_part.split("&"):
            if "=" in param:
                key, value = param.split("=", 1)
                params[key] = value

    return {
        "segments": segments,  # e.g., ['assets', 'SET', 'Arena']
        "department": params.get("dept"),
        "variant": params.get("variant", "default"),
        "version": params.get("version"),  # None means "latest"
    }


def _find_latest_version(version_root: Path) -> str:
    """
    Find the highest version number in a directory.

    Args:
        version_root: Path containing version directories (v0001, v0002, etc.)

    Returns:
        Version string like "v0013" or None if no versions found
    """
    if not version_root.exists():
        return None

    version_dirs = []
    for item in version_root.iterdir():
        if item.is_dir() and re.match(r"^v\d+$", item.name):
            version_dirs.append(item.name)

    if not version_dirs:
        return None

    # Sort by version number and return highest
    version_dirs.sort(key=lambda v: int(v[1:]))
    return version_dirs[-1]


def _get_layer_file_name(
    segments: list,
    variant_name: str,
    department_name: str,
    version_name: str
) -> str:
    """
    Get layer filename matching paths.py format.

    Filename format: {entity_name}_{variant}_{dept}_{version}.usd
    Root department uses .usda extension.

    Args:
        segments: Entity path segments (e.g., ['assets', 'SET', 'Arena'])
        variant_name: Variant name (e.g., 'default')
        department_name: Department name (e.g., 'lookdev')
        version_name: Version name (e.g., 'v0013')

    Returns:
        Filename string (e.g., 'assets_SET_Arena_default_lookdev_v0013.usd')
    """
    entity_name = "_".join(segments)
    ext = ".usda" if department_name == "root" else ".usd"
    return f"{entity_name}_{variant_name}_{department_name}_{version_name}{ext}"


def _get_shared_layer_file_name(
    segments: list,
    department_name: str,
    version_name: str
) -> str:
    """
    Get shared layer filename matching paths.py format.

    Filename format: {entity_name}_shared_{dept}_{version}.usd

    Args:
        segments: Entity path segments (e.g., ['assets', 'SET', 'Arena'])
        department_name: Department name (e.g., 'lookdev')
        version_name: Version name (e.g., 'v0005')

    Returns:
        Filename string (e.g., 'assets_SET_Arena_shared_lookdev_v0005.usd')
    """
    entity_name = "_".join(segments)
    return f"{entity_name}_shared_{department_name}_{version_name}.usd"


def _get_export_base() -> Path | None:
    """Get the base export path from environment.

    Returns:
        Path object or None if TH_EXPORT_PATH is not set.
    """
    export_path = os.environ.get("TH_EXPORT_PATH")
    if not export_path:
        LOG.warning("TH_EXPORT_PATH not set, cannot resolve entity URI")
        return None
    return Path(export_path)


def _use_latest_mode() -> bool:
    """Check if resolver should ignore explicit versions and use latest.

    Uses TH_RESOLVER_LATEST_MODE environment variable, controlled via set_latest_mode().
    When enabled, all entity:/ URIs resolve to their latest version,
    providing full cascade/closure semantics through all nested layers.

    Returns:
        True if latest mode is enabled, False otherwise.
    """
    return os.environ.get(_LATEST_MODE_ENV_VAR, "0") == "1"


def _resolve_root_uri(
    segments: list,
    version: str | None
) -> str:
    """
    Resolve a root layer entity URI.

    Root is stored at shot-level: {export}/{segments}/_root/{version}/{filename}
    Example: P:/export/shots/sq050/sh446/_root/v0006/shots_sq050_sh446_root_v0006.usda

    Args:
        segments: Entity path segments (e.g., ['shots', 'sq050', 'sh446'])
        version: Version name or None for latest

    Returns:
        Resolved filesystem path or empty string if resolution fails
    """
    export_base = _get_export_base()
    if not export_base:
        return ""

    # Build path: {export_base}/{segments}/_root/{version}/
    entity_path = export_base
    for segment in segments:
        entity_path = entity_path / segment

    root_path = entity_path / "_root"

    # Resolve version
    use_latest = _use_latest_mode()
    if version and not use_latest:
        version_name = version
    else:
        version_name = _find_latest_version(root_path)
        if not version_name:
            LOG.warning(f"No versions found at: {root_path}")
            return ""

    version_path = root_path / version_name

    # Filename: {entity_name}_root_{version}.usda (no variant in filename)
    entity_name = "_".join(segments)
    filename = f"{entity_name}_root_{version_name}.usda"

    resolved_path = version_path / filename
    return str(resolved_path).replace("\\", "/")


def _resolve_department_uri(
    segments: list,
    department: str,
    variant: str,
    version: str | None
) -> str:
    """
    Resolve a department-specific entity URI.

    Path structure: {export}/{segments}/{variant}/{dept}/{version}/{filename}
    Example: P:/export/assets/SET/Arena/default/lookdev/v0013/assets_SET_Arena_default_lookdev_v0013.usd

    Special case: Root department is stored at shot-level _root/ directory
    (no variant subfolder). Delegates to _resolve_root_uri.

    Args:
        segments: Entity path segments (e.g., ['assets', 'SET', 'Arena'])
        department: Department name (e.g., 'lookdev')
        variant: Variant name (e.g., 'default')
        version: Version name or None for latest

    Returns:
        Resolved filesystem path or empty string if resolution fails
    """
    # Root department has special handling - stored at shot-level _root/
    if department == "root":
        return _resolve_root_uri(segments, version)

    export_base = _get_export_base()
    if not export_base:
        return ""

    # Build path: {export_base}/{segments}/{variant}/{dept}/{version}/
    entity_path = export_base
    for segment in segments:
        entity_path = entity_path / segment

    variant_path = entity_path / variant / department

    # Resolve version
    # When latest mode is enabled, ignore explicit versions for cascade semantics
    use_latest = _use_latest_mode()
    if version and not use_latest:
        version_name = version
    else:
        version_name = _find_latest_version(variant_path)
        if not version_name:
            # Silently skip _shared variants that don't exist (cleaned up for single-variant entities)
            if variant != "_shared":
                LOG.warning(f"No versions found at: {variant_path}")
            return ""

    version_path = variant_path / version_name

    # Construct filename based on variant type
    if variant == "_shared":
        filename = _get_shared_layer_file_name(segments, department, version_name)
    else:
        filename = _get_layer_file_name(segments, variant, department, version_name)

    resolved_path = version_path / filename
    return str(resolved_path).replace("\\", "/")


def _resolve_staged_uri(
    segments: list,
    variant: str,
    version: str | None
) -> str:
    """
    Resolve a staged entity URI (no department - composed from all departments).

    Path structure: {export}/{segments}/_staged/{variant}/{version}/{filename}
    Example: P:/export/assets/CHAR/Crowd/_staged/cheering/v0013/CHAR_Crowd_v0013.usda

    Args:
        segments: Entity path segments (e.g., ['assets', 'CHAR', 'Crowd'])
        variant: Variant name (e.g., 'default', 'cheering')
        version: Version name or None for latest

    Returns:
        Resolved filesystem path or empty string if resolution fails
    """
    export_base = _get_export_base()
    if not export_base:
        return ""

    # Build path: {export_base}/{segments}/_staged/{variant}/{version}/
    entity_path = export_base
    for segment in segments:
        entity_path = entity_path / segment

    staged_path = entity_path / "_staged" / variant

    # Resolve version
    # When latest mode is enabled, ignore explicit versions for cascade semantics
    use_latest = _use_latest_mode()
    if version and not use_latest:
        version_name = version
    else:
        version_name = _find_latest_version(staged_path)
        if not version_name:
            LOG.warning(f"No versions found at: {staged_path}")
            return ""

    version_path = staged_path / version_name

    # Filename: {category}_{name}_{version}.usda (using segments[1:] to skip 'assets')
    entity_name = "_".join(segments[1:])
    filename = f"{entity_name}_{version_name}.usda"

    resolved_path = version_path / filename
    return str(resolved_path).replace("\\", "/")


def _resolve_scene_uri(
    scene_segments: list,
    version: str | None
) -> str:
    """
    Resolve a scene entity URI.

    Path structure: {export}/scenes/{scene_segments}/_staged/{version}/{filename}
    Example: P:/export/scenes/arena/_staged/v0001/arena_v0001.usda

    Args:
        scene_segments: Scene path segments after 'scenes' (e.g., ['arena'] or ['outdoor', 'forest'])
        version: Version name or None for latest

    Returns:
        Resolved filesystem path or empty string if resolution fails
    """
    export_base = _get_export_base()
    if not export_base:
        return ""

    # Build path: {export_base}/scenes/{scene_segments}/_staged/{version}/
    scene_path = export_base / "scenes"
    for segment in scene_segments:
        scene_path = scene_path / segment

    staged_path = scene_path / "_staged"

    # Resolve version
    # When latest mode is enabled, ignore explicit versions for cascade semantics
    use_latest = _use_latest_mode()
    if version and not use_latest:
        version_name = version
    else:
        version_name = _find_latest_version(staged_path)
        if not version_name:
            LOG.warning(f"No versions found at: {staged_path}")
            return ""

    version_path = staged_path / version_name

    # Filename: {scene_name}_{version}.usda (last segment is scene name)
    scene_name = scene_segments[-1]
    filename = f"{scene_name}_{version_name}.usda"

    resolved_path = version_path / filename
    return str(resolved_path).replace("\\", "/")


def _resolve_entity_uri(uri_string: str) -> str:
    """
    Resolve an entity URI to a filesystem path.

    Handles three URI formats:
    1. Department layers: entity:/assets/SET/Arena?dept=lookdev&variant=default&version=v0013
    2. Staged layers: entity:/assets/CHAR/Crowd?variant=cheering
    3. Scene layers: entity:/scenes/arena or entity:/scenes/outdoor/forest?version=v0001

    Args:
        uri_string: Entity URI string

    Returns:
        Resolved filesystem path or empty string if resolution fails
    """
    parsed = _parse_entity_uri(uri_string)
    if not parsed:
        LOG.warning(f"Failed to parse entity URI: {uri_string}")
        return ""

    segments = parsed["segments"]
    department = parsed["department"]
    variant = parsed["variant"]
    version = parsed["version"]

    # Route to appropriate resolution function based on URI type
    if segments[0] == "scenes":
        # Scene URIs: entity:/scenes/arena or entity:/scenes/outdoor/forest
        scene_segments = segments[1:]  # Remove 'scenes' prefix
        if not scene_segments:
            LOG.warning(f"Scene URI missing scene path: {uri_string}")
            return ""
        resolved = _resolve_scene_uri(scene_segments, version)
    elif department:
        # Department-specific URIs: entity:/assets/SET/Arena?dept=lookdev&variant=default
        resolved = _resolve_department_uri(segments, department, variant, version)
    else:
        # Staged URIs (no dept): entity:/assets/CHAR/Crowd?variant=cheering
        resolved = _resolve_staged_uri(segments, variant, version)

    if resolved:
        LOG.debug(f"Resolved {uri_string} -> {resolved}")
    return resolved


class Resolver:

    @staticmethod
    @log_function_args
    def CreateRelativePathIdentifier(resolver, anchoredAssetPath, assetPath, anchorAssetPath):
        """Returns an identifier for the asset specified by assetPath and anchor asset path.

        Args:
            resolver (CachedResolver): The resolver
            anchoredAssetPath (str): The anchored asset path, this has to be used as the cached key.
            assetPath (str): An unresolved asset path.
            anchorAssetPath (Ar.ResolvedPath): A resolved anchor path.

        Returns:
            str: The identifier.
        """
        LOG.debug(f"::: Resolver.CreateRelativePathIdentifier | {anchoredAssetPath} | {assetPath} | {anchorAssetPath}")

        # For entity URIs, pass through as-is (they're not relative paths)
        if assetPath.startswith("entity:"):
            resolver.AddCachedRelativePathIdentifierPair(anchoredAssetPath, assetPath)
            return assetPath

        # For regular relative paths, create a remapped identifier
        remappedRelativePathIdentifier = f"relativePath|{assetPath}?{anchorAssetPath}".replace("\\", "/")
        resolver.AddCachedRelativePathIdentifierPair(anchoredAssetPath, remappedRelativePathIdentifier)
        return remappedRelativePathIdentifier


class ResolverContext:

    @staticmethod
    @log_function_args
    def Initialize(context):
        """Initialize the context.

        This gets called on default and post mapping file path context creation.
        Here you can inject data by batch calling context.AddCachingPair(assetPath, resolvePath).

        Args:
            context (CachedResolverContext): The active context.
        """
        LOG.debug("::: ResolverContext.Initialize")
        # Log the export path being used for debugging (if available)
        export_path = os.environ.get("TH_EXPORT_PATH")
        if export_path:
            LOG.debug(f"::: Export base path: {export_path}")
        return

    @staticmethod
    @log_function_args
    def ResolveAndCache(context, assetPath):
        """Return the resolved path for the given assetPath.

        Args:
            context (CachedResolverContext): The active context.
            assetPath (str): An unresolved asset path.

        Returns:
            str: The resolved path string.
        """
        LOG.debug(f"::: ResolverContext.ResolveAndCache | {assetPath}")

        # Handle anonymous layers
        if Sdf.Layer.IsAnonymousLayerIdentifier(assetPath):
            context.AddCachingPair(assetPath, assetPath)
            return assetPath

        # Handle entity URIs
        if assetPath.startswith("entity:"):
            resolved_path = _resolve_entity_uri(assetPath)
            # Don't cache entity URIs - always resolve fresh to pick up new versions
            return resolved_path if resolved_path else ""

        # Handle relative path identifiers (from CreateRelativePathIdentifier)
        relative_path_prefix = "relativePath|"
        if assetPath.startswith(relative_path_prefix):
            relative_path, anchor_path = assetPath[len(relative_path_prefix):].split("?")
            # Remove trailing separator or get parent directory
            if anchor_path.endswith(os.path.sep) or anchor_path.endswith("/"):
                anchor_path = anchor_path[:-1]
            else:
                # Get directory part of anchor path
                last_sep = max(anchor_path.rfind("/"), anchor_path.rfind("\\"))
                if last_sep > 0:
                    anchor_path = anchor_path[:last_sep]
            resolved_path = os.path.normpath(os.path.join(anchor_path, relative_path))
            resolved_path = resolved_path.replace("\\", "/")
            context.AddCachingPair(assetPath, resolved_path)
            return resolved_path

        # For other paths, return as-is (let default resolver handle it)
        # Log all non-entity paths for debugging texture resolution issues
        LOG.info(f"::: Passthrough path: {assetPath}")
        context.AddCachingPair(assetPath, assetPath)
        return assetPath
