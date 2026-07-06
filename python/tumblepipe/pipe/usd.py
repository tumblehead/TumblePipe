"""
Shared USD utilities for generating USDA file content.
"""

from pathlib import Path
from collections import defaultdict
import os
import re
import json
import logging
from typing import Union

from tumblepipe.api import path_str
from tumblepipe.util.uri import Uri

# Candidate placement ops in USD XformCommonAPI application order. The
# pivot's inverse is appended last when the pivot itself is present.
PLACEMENT_OPS = (
    'xformOp:translate',
    'xformOp:translate:pivot',
    'xformOp:rotateXYZ',
    'xformOp:rotateXZY',
    'xformOp:rotateYXZ',
    'xformOp:rotateYZX',
    'xformOp:rotateZXY',
    'xformOp:rotateZYX',
    'xformOp:scale',
)


def composed_placement_op_order(prim) -> list[str]:
    """xformOpOrder applying the placement op values composed on ``prim``.

    Exported set layers carry a duplicate's placement op VALUES (they
    survive the layerbreak in the localized import-node sidecar) but not
    the xformOpOrder that applies them — the order lived in the Duplicate
    LOP defs the export stripped. Every point that re-creates instance
    prims derives the order from what actually composed: ops with a value,
    XformCommonAPI ordering, pivot inverted last. Returns [] when no
    placement composed (callers fall back to an identity dup op).

    ``prim`` is a Usd.Prim; this module stays pxr-free at import time, so
    the caller owns the stage.
    """
    order = [
        op_name for op_name in PLACEMENT_OPS
        if prim.GetAttribute(op_name) and prim.GetAttribute(op_name).HasValue()
    ]
    if order and 'xformOp:translate:pivot' in order:
        order.append('!invert!xformOp:translate:pivot')
    return order


def generate_usda_content(
    layer_paths: list[Union[Path, str]],
    output_path: Path,
    fps: float = None,
    start_frame: int = None,
    end_frame: int = None,
    use_absolute_paths: bool = False
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
        use_absolute_paths: If True, output absolute filesystem paths (for baked/collapsed stages)

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
        if use_absolute_paths:
            # Force absolute filesystem path format (for collapsed/baked stages)
            layer_str = layer_ref if isinstance(layer_ref, str) else str(layer_ref)
            # Normalize to forward slashes for USD
            layer_str = layer_str.replace('\\', '/')
            lines.append(f'        @{layer_str}@,')
        elif isinstance(layer_ref, str) and layer_ref.startswith('entity:/'):
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


def _parse_sublayers_from_content(content: str) -> list[str]:
    """
    Extract sublayer references from USDA file content.

    Args:
        content: USDA file content as string

    Returns:
        List of sublayer reference strings (entity URIs or paths)
    """
    sublayers_match = re.search(r'subLayers\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if not sublayers_match:
        return []

    sublayers_block = sublayers_match.group(1)
    return re.findall(r'@([^@]+)@', sublayers_block)


def _asset_uri_to_prim_path(asset_uri: str) -> tuple[str, str, str]:
    """
    Convert an asset URI to its USD prim path components.

    Args:
        asset_uri: Entity URI string (e.g., 'entity:/assets/CHAR/cupAndBall')

    Returns:
        Tuple of (category, asset_name, prim_path) where prim_path is like '/CHAR/cupAndBall'
    """
    # Parse: entity:/assets/CATEGORY/ASSET_NAME
    parts = asset_uri.replace('entity:/assets/', '').split('/')
    if len(parts) >= 2:
        category = parts[0]
        asset_name = parts[1]
        prim_path = f'/{category}/{asset_name}'
        return (category, asset_name, prim_path)
    return (None, None, None)


def _collect_instance_info_from_context(layer_path: str) -> dict[str, list[str]]:
    """
    Collect asset instance information from a context.json file alongside a layer.

    Args:
        layer_path: Path to a USD layer file

    Returns:
        Dict mapping asset prim path (e.g., '/CHAR/cupAndBall') to list of instance names
    """
    instances_by_asset = defaultdict(list)

    # Find context.json alongside the layer
    layer_dir = Path(layer_path).parent
    context_path = layer_dir / 'context.json'

    if not context_path.exists():
        return instances_by_asset

    try:
        with open(context_path, 'r', encoding='utf-8') as f:
            context_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logging.warning(f"Failed to parse context.json at {context_path}: {e}")
        return instances_by_asset

    # Check outputs[].parameters.assets[] structure
    outputs = context_data.get('outputs', [])
    for output in outputs:
        parameters = output.get('parameters', {})
        assets = parameters.get('assets', [])

        for asset_entry in assets:
            asset_uri = asset_entry.get('asset')
            instance_name = asset_entry.get('instance')
            instance_count = asset_entry.get('instances', 1)

            if not asset_uri or not instance_name:
                continue

            category, asset_name, prim_path = _asset_uri_to_prim_path(asset_uri)
            if prim_path:
                # Generate instance names based on count
                # All instances get numeric suffix: MiniFig0, MiniFig1, MiniFig2
                # The prototype (MiniFig) is the original asset prim
                for i in range(instance_count):
                    instances_by_asset[prim_path].append(f"{instance_name}{i}")

    return instances_by_asset


def _generate_render_overrides_section(overrides: dict) -> str:
    """
    Generate USDA text for render setting overrides.

    Args:
        overrides: Dict mapping Karma attribute names to values
                   e.g. {'karma:global:pathtracedsamples': 128}

    Returns:
        USDA text defining render settings overrides, or empty string if no overrides
    """
    if not overrides:
        return ''

    lines = []
    lines.append('')
    lines.append('over "Render"')
    lines.append('{')
    lines.append('    over "rendersettings"')
    lines.append('    {')

    for attr_name, value in overrides.items():
        if isinstance(value, bool):
            val_str = 'true' if value else 'false'
            lines.append(f'        bool {attr_name} = {val_str}')
        elif isinstance(value, int):
            lines.append(f'        int {attr_name} = {value}')
        elif isinstance(value, float):
            lines.append(f'        float {attr_name} = {value}')

    lines.append('    }')
    lines.append('}')

    return '\n'.join(lines)


def _generate_instance_prim_definitions(
    instances_by_asset: dict[str, list[str]],
    placement_orders: dict[str, list[str]] | None = None
) -> str:
    """
    Generate USDA prim definitions for asset instances.

    Only generates definitions for assets with 2+ instances.
    Each instance references the base asset prim and the original is deactivated.

    Args:
        instances_by_asset: Dict mapping asset prim path (e.g., '/CHAR/cupAndBall')
                           to list of instance names
        placement_orders: Optional {instance_prim_path: [op names]} from
                          composing the staged stage — instances present
                          here author that xformOpOrder (applying the
                          placement values composed from the authoring
                          department's sidecar) instead of the identity
                          dup op.

    Returns:
        USDA text defining instance prims, or empty string if no multi-instance assets
    """
    if placement_orders is None:
        placement_orders = {}
    # Filter to only assets with 2+ instances
    multi_instance_assets = {
        prim_path: instances
        for prim_path, instances in instances_by_asset.items()
        if len(instances) >= 2
    }

    if not multi_instance_assets:
        return ''

    # Group by category
    assets_by_category = defaultdict(list)
    for prim_path, instances in multi_instance_assets.items():
        # Extract category from prim path like '/CHAR/cupAndBall'
        parts = prim_path.strip('/').split('/')
        if len(parts) >= 2:
            category = parts[0]
            asset_name = parts[1]
            assets_by_category[category].append((asset_name, instances))

    lines = []

    for category, assets in sorted(assets_by_category.items()):
        lines.append('')
        lines.append(f'def Xform "{category}"')
        lines.append('{')

        for asset_name, instances in assets:
            base_prim_path = f'/{category}/{asset_name}'

            # Generate instance prims (MiniFig0, MiniFig1, MiniFig2, etc.)
            # Each instance references the base prim; the op order applies
            # the composed placement values when known, else an identity
            # transform gives the instance a transform surface.
            for instance_name in instances:
                instance_path = f'/{category}/{instance_name}'
                order = placement_orders.get(instance_path)
                lines.append(f'    def "{instance_name}" (')
                lines.append('        active = true')
                lines.append(f'        append references = <{base_prim_path}>')
                lines.append('    )')
                lines.append('    {')
                if order:
                    order_str = ', '.join(f'"{op_name}"' for op_name in order)
                    lines.append(f'        uniform token[] xformOpOrder = [{order_str}]')
                else:
                    lines.append(f'        matrix4d xformOp:transform:{asset_name}_dup = ( (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1) )')
                    lines.append(f'        uniform token[] xformOpOrder = ["xformOp:transform:{asset_name}_dup"]')
                lines.append('    }')
                lines.append('')

            # Deactivate original prototype so it doesn't render
            lines.append(f'    over "{asset_name}" (')
            lines.append('        active = false')
            lines.append('    )')
            lines.append('    {')
            lines.append('    }')
            lines.append('')

        lines.append('}')

    return '\n'.join(lines)


def _collect_leaf_layers_and_instances(
    layer_path: str,
    visited: set = None,
    instances_by_asset: dict = None
) -> tuple[list[str], dict[str, list[str]]]:
    """
    Recursively collect all leaf USD layers and instance info from a sublayer hierarchy.

    Traverses _staged files and entity URIs, resolving to filesystem paths,
    and returns the actual USD layer files plus instance information from context.json files.

    Args:
        layer_path: Filesystem path or entity URI to start from
        visited: Set of already-visited paths to prevent cycles
        instances_by_asset: Dict to accumulate instance info (modified in place)

    Returns:
        Tuple of (leaf_layers, instances_by_asset) where:
        - leaf_layers: List of filesystem paths to leaf USD layers
        - instances_by_asset: Dict mapping asset prim path to list of instance names
    """
    if visited is None:
        visited = set()
    if instances_by_asset is None:
        instances_by_asset = defaultdict(list)

    # Resolve entity URI to filesystem path if needed
    if layer_path.startswith('entity:'):
        from tumblepipe import resolver

        # Enable latest mode to resolve to newest version
        previous_mode = resolver.get_latest_mode()
        resolver.set_latest_mode(True)
        try:
            # Raises ResolveError if unresolvable — silently returning an
            # empty layer list here produced incomplete scene builds.
            resolved_path = resolver.resolve_entity_uri(layer_path)
        finally:
            resolver.set_latest_mode(previous_mode)

        layer_path = resolved_path

    # Normalize path separators
    layer_path = layer_path.replace('\\', '/')

    # Prevent cycles
    if layer_path in visited:
        return ([], instances_by_asset)
    visited.add(layer_path)

    # Check if file exists
    path = Path(layer_path)
    if not path.exists():
        logging.warning(f"Layer file does not exist: {layer_path}")
        return ([], instances_by_asset)

    # Collect instance info from context.json alongside this layer
    layer_instances = _collect_instance_info_from_context(layer_path)
    for prim_path, instance_names in layer_instances.items():
        for instance_name in instance_names:
            if instance_name not in instances_by_asset[prim_path]:
                instances_by_asset[prim_path].append(instance_name)

    # Only parse .usda files for sublayers - other formats are leaf layers
    if path.suffix.lower() != '.usda':
        return ([layer_path], instances_by_asset)

    # Read the .usda file to check for sublayers
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse sublayers from text content
    sublayer_refs = _parse_sublayers_from_content(content)

    if not sublayer_refs:
        # This is a leaf layer - return it
        return ([layer_path], instances_by_asset)

    # This has sublayers - recursively collect from each
    leaf_layers = []
    for ref in sublayer_refs:
        # Handle relative paths (not entity URIs)
        if not ref.startswith('entity:') and not Path(ref).is_absolute():
            ref = os.path.normpath(str(path.parent / ref)).replace('\\', '/')
        sub_layers, _ = _collect_leaf_layers_and_instances(ref, visited, instances_by_asset)
        leaf_layers.extend(sub_layers)

    return (leaf_layers, instances_by_asset)


def _collect_leaf_layers(
    layer_path: str,
    visited: set = None
) -> list[str]:
    """
    Recursively collect all leaf USD layers from a sublayer hierarchy.

    Traverses _staged files and entity URIs, resolving to filesystem paths,
    and returns only the actual USD layer files (not intermediate _staged files).

    Args:
        layer_path: Filesystem path or entity URI to start from
        visited: Set of already-visited paths to prevent cycles

    Returns:
        List of filesystem paths to leaf USD layers
    """
    leaf_layers, _ = _collect_leaf_layers_and_instances(layer_path, visited)
    return leaf_layers


def _composed_placement_orders(
    staged_file_path: Path,
    instances_by_asset: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Per-instance placement op order, read from the composed staged stage.

    Opens the staged file with USD and derives each instance prim's
    xformOpOrder from the placement op values that actually composed
    (composed_placement_op_order). Runs at submission time inside a
    Houdini/hython session, so pxr and the entity resolver are available;
    any failure returns {} and the static defs fall back to the identity
    dup op. The instance prims exist on the staged stage only as typeless
    overs, which is enough — their attributes compose and are queryable
    via GetPrimAtPath.
    """
    try:
        from pxr import Usd
    except ImportError:
        return {}
    try:
        stage = Usd.Stage.Open(str(staged_file_path))
    except Exception:
        logging.warning(
            f'Could not compose {staged_file_path} for placement op '
            'orders; instance defs fall back to identity transforms'
        )
        return {}
    if stage is None:
        return {}

    orders = {}
    for prim_path, instance_names in instances_by_asset.items():
        parent_path = prim_path.rsplit('/', 1)[0]
        for instance_name in instance_names:
            instance_path = f'{parent_path}/{instance_name}'
            prim = stage.GetPrimAtPath(instance_path)
            if not prim:
                continue
            order = composed_placement_op_order(prim)
            if order:
                orders[instance_path] = order
    return orders


def collapse_latest_references(
    staged_file_path: Path,
    output_path: Path,
    render_overrides: dict = None
) -> str:
    """
    Create a fully baked USDA with all sublayers resolved to filesystem paths.

    Recursively traverses all _staged files and entity URIs, resolving them
    to filesystem paths and collecting all leaf layers. The output is a
    completely static USDA file that requires no resolver at render time.

    For assets with multiple instances, generates instance prim definitions
    that reference the base asset and deactivate the original.

    This is used for farm rendering where we want deterministic, pre-resolved
    layer references without any dynamic URI resolution.

    Args:
        staged_file_path: Path to the source staged .usda file
        output_path: Output path (kept for API compatibility)
        render_overrides: Optional dict mapping Karma attribute names to values
                         e.g. {'karma:global:pathtracedsamples': 128}

    Returns:
        USDA file content as string with all leaf layers as absolute paths,
        instance prim definitions for multi-instance assets, and render overrides

    Raises:
        ValueError: If staged file doesn't exist or has invalid format
    """
    if not staged_file_path.exists():
        raise ValueError(f"Staged file does not exist: {staged_file_path}")

    # Read the source file for metadata
    with open(staged_file_path, 'r') as f:
        content = f.read()

    # Parse metadata (fps, frame range)
    metadata = _parse_usda_metadata(content)

    # Recursively collect all leaf layers and instance information
    # This traverses all _staged files and entity URIs, flattening the hierarchy
    leaf_layers, instances_by_asset = _collect_leaf_layers_and_instances(str(staged_file_path))

    if not leaf_layers:
        logging.warning(f"No leaf layers found in {staged_file_path}")

    # Generate USDA with absolute filesystem paths (no entity URIs)
    usda_content = generate_usda_content(
        layer_paths=leaf_layers,
        output_path=output_path,
        fps=metadata['fps'],
        start_frame=metadata['start_frame'],
        end_frame=metadata['end_frame'],
        use_absolute_paths=True
    )

    # Generate instance prim definitions for multi-instance assets, with
    # each instance's xformOpOrder derived from the composed staged stage
    # (the placement values ride in department sidecars without an order;
    # an identity dup op would leave every copy stacked at the prototype).
    placement_orders = _composed_placement_orders(
        staged_file_path, instances_by_asset
    )
    instance_prims = _generate_instance_prim_definitions(
        instances_by_asset, placement_orders
    )
    if instance_prims:
        usda_content = usda_content + '\n' + instance_prims

    # Generate render settings overrides
    if render_overrides:
        overrides_section = _generate_render_overrides_section(render_overrides)
        if overrides_section:
            usda_content = usda_content + '\n' + overrides_section

    return usda_content


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


def _looks_like_uri(asset_path: str) -> bool:
    """True if ``asset_path`` carries a URI scheme the resolver handles
    (``entity:/``, ``op:/`` …) rather than a bare filesystem path.

    A scheme is two or more leading characters before ``:`` — this
    deliberately excludes single-letter Windows drive prefixes (``C:``)
    so absolute paths are treated as filesystem paths, not URIs.
    """
    return bool(re.match(r'^[A-Za-z][A-Za-z0-9+.\-]+:', asset_path))


def find_dangling_layer_paths(
    asset_paths, layer_dir: Union[Path, str, None] = None,
) -> list:
    """Return composition asset paths that won't resolve at load time.

    Flags only filesystem paths whose target does not exist — the
    unambiguous "this layer composes geometry from a file that isn't
    there, so consumers import nothing" case (e.g. a payload left
    pointing at a machine-local scratch file). Conservative by design:

    - URI-scheme arcs (``entity:/``, ``op:/`` …) are left to the
      resolver and never flagged.
    - Absolute paths are checked directly.
    - Relative paths are resolved against ``layer_dir`` (the layer's own
      directory) when given; with no ``layer_dir`` they're assumed to
      travel with the layer and skipped.

    Returns the offending raw path strings, in input order, de-duped.
    """
    dangling = []
    seen = set()
    for raw in asset_paths:
        p = (raw or '').strip()
        if not p or p in seen:
            continue
        if _looks_like_uri(p):
            continue
        path = Path(p)
        if path.is_absolute():
            exists = path.exists()
        elif layer_dir is not None:
            exists = (Path(layer_dir) / path).exists()
        else:
            continue
        if not exists:
            seen.add(p)
            dangling.append(p)
    return dangling


def collect_layer_composition_paths(layer_path: Union[Path, str]) -> list:
    """Collect the sublayer / reference / payload asset paths declared
    directly in ``layer_path``.

    Shallow: inspects this layer's own composition arcs, not the
    contents of layers it points at. Requires USD; returns ``[]`` if the
    layer can't be opened.
    """
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        return []
    paths = [str(p) for p in layer.subLayerPaths]
    stack = list(layer.rootPrims)
    while stack:
        prim = stack.pop()
        for arc_list in (prim.referenceList, prim.payloadList):
            for item in arc_list.GetAddedOrExplicitItems():
                asset_path = getattr(item, 'assetPath', '')
                if asset_path:
                    paths.append(str(asset_path))
        stack.extend(prim.nameChildren.values())
    return paths


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
