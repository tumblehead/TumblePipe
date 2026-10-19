"""Flatten a staged build into one resolver-free .usda for the farm.

The collapse walks every ``_staged`` file and ``entity:/`` sublayer down to
the leaf layers, writes them as absolute paths into one root layer, and adds
what the GUI session would have built on top: instance copies, the
RenderSettings husk is to render through, and the submit dialog's render
overrides.
"""

import logging
import os
from collections import defaultdict
from pathlib import Path

from tumblepipe.pipe.context import get_source_department
from tumblepipe.pipe.usd.instances import (
    asset_uri_to_prim_path,
    collect_instance_info_from_context,
    composed_placement_orders,
    generate_instance_prim_definitions,
)
from tumblepipe.pipe.usd.render_settings import (
    PLAYBLAST_SETTINGS_PRIM_PATH,
    find_render_camera_prim_path,
    find_render_settings_prim_path,
    generate_playblast_settings_section,
    generate_render_overrides_section,
)
from tumblepipe.pipe.usd.sublayer_refs import (
    parse_entity_sublayer_uri,
    read_staged_sublayer_refs,
)
from tumblepipe.pipe.usd.usda import (
    generate_usda_content,
    parse_sublayers,
    parse_usda_metadata,
)
from tumblepipe.util.io import load_json


class LayerCollectionError(RuntimeError):
    """A layer the staged build records could not be collected.

    Raised rather than dropping the layer, because a department that goes
    missing from a collapsed stage does not look like a failure downstream:
    husk renders the remaining layers into a complete, plausible image with
    that department's contribution simply absent. An unlit shot reaches
    dailies before anyone reads the warning it left in a submission log.
    """


def _missing_layer_message(
    ref: str,
    resolved_path: str | None,
    referrer: str | None,
) -> str:
    """Why a recorded layer could not be collected, and what to do about it.

    ``resolved_path`` is None when the ref did not resolve at all; a path when
    it resolved to something that is not on disk. The two have the same cause
    in practice — a failed export leaves a version directory with no layer in
    it, and ``latest`` then lands on that empty version until the next good
    export — so they get one message.
    """
    lines = [
        "the staged build records a layer that is not on disk",
        f"  reference:     {ref}",
    ]
    if resolved_path is not None:
        lines.append(f"  resolves to:   {resolved_path}")
    else:
        lines.append("  resolves to:   nothing (the URI does not resolve)")
    if referrer is not None:
        lines.append(f"  referenced by: {referrer}")
    lines.extend([
        "",
        "Most likely a failed export left a version directory with no layer "
        "file in it, which 'latest' then resolves to until the next good "
        "export. Re-export that department, or remove the empty version, "
        "then submit again.",
        "",
        "Collapsing without the layer would submit a render missing that "
        "department entirely: a complete-looking image with its "
        "contribution absent.",
    ])
    return "\n".join(lines)


def _excluded_asset_prim_paths(excluded_refs) -> set[str]:
    """Prim paths of the whole assets among ``excluded_refs``.

    Only staged asset refs count (no ``dept``); a department layer ref is
    not an asset, and its prim is not one either.
    """
    prim_paths = set()
    for ref in excluded_refs or ():
        parsed_ref = parse_entity_sublayer_uri(ref)
        if parsed_ref is None or parsed_ref.department is not None:
            continue
        if not parsed_ref.base.startswith('entity:/assets/'):
            continue
        _, _, prim_path = asset_uri_to_prim_path(parsed_ref.base)
        prim_paths.add(prim_path)
    return prim_paths


def _collect_leaf_layers_and_instances(
    layer_path: str,
    visited: set = None,
    instances_by_asset: dict = None,
    excluded_refs: set = None,
    referrer: str = None
) -> tuple[list[str], dict[str, list[str]]]:
    """
    Recursively collect all leaf USD layers and instance info from a sublayer hierarchy.

    Traverses _staged files and entity URIs, resolving to filesystem paths,
    and returns the actual USD layer files plus instance information from context.json files.

    Args:
        layer_path: Filesystem path or entity URI to start from
        visited: Set of already-visited paths to prevent cycles
        instances_by_asset: Dict to accumulate instance info (modified in place)
        excluded_refs: Refs to skip at *this* level only (not propagated into
                       the recursion) — a partial-department render drops the
                       shot's own layers past the cut, never an asset's.
                       An excluded asset ref also gets no instance copies
        referrer: The layer that sublayered this one, for error messages;
                  None at the top of the traversal

    Returns:
        Tuple of (leaf_layers, instances_by_asset) where:
        - leaf_layers: List of filesystem paths to leaf USD layers
        - instances_by_asset: Dict mapping asset prim path to list of instance names

    Raises:
        LayerCollectionError: If a recorded ref does not resolve, or resolves
                              to a file that is not on disk. Both used to
                              return an empty list, which collapsed the stage
                              without that department.
    """
    if visited is None:
        visited = set()
    if instances_by_asset is None:
        instances_by_asset = defaultdict(list)

    # The ref as the staged file wrote it, kept for error messages: once
    # resolved, layer_path is a filesystem path and no longer says which
    # department or version the build actually asked for.
    original_ref = layer_path

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
        except resolver.ResolveError as error:
            # Translated rather than propagated: a bare "entity URI did not
            # resolve" says nothing about which build referenced it or what
            # the artist should do about it.
            raise LayerCollectionError(
                _missing_layer_message(original_ref, None, referrer)
            ) from error
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
        raise LayerCollectionError(
            _missing_layer_message(original_ref, layer_path, referrer)
        )

    # Collect instance info from context.json alongside this layer. An asset
    # whose staged ref is excluded here gets no copies: later departments'
    # overs still make its prim exist, so copies of it would render as
    # empty prims (import_shot's /SET/cloud0, cloud1 ghosts, 2026-10-12).
    excluded_asset_prims = _excluded_asset_prim_paths(excluded_refs)
    layer_instances = collect_instance_info_from_context(layer_path)
    for prim_path, instance_names in layer_instances.items():
        if prim_path in excluded_asset_prims:
            continue
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
    sublayer_refs = parse_sublayers(content)

    if not sublayer_refs:
        # This is a leaf layer - return it
        return ([layer_path], instances_by_asset)

    # This has sublayers - recursively collect from each
    leaf_layers = []
    for ref in sublayer_refs:
        # Dropped by a partial-department render. Checked before path
        # normalisation so it matches the ref as the staged file wrote it,
        # which is the form excluded_staged_refs() reports.
        if excluded_refs and ref in excluded_refs:
            logging.info(f"Excluding department layer from render: {ref}")
            continue
        # Handle relative paths (not entity URIs)
        if not ref.startswith('entity:') and not Path(ref).is_absolute():
            ref = os.path.normpath(str(path.parent / ref)).replace('\\', '/')
        sub_layers, _ = _collect_leaf_layers_and_instances(
            ref, visited, instances_by_asset, referrer=layer_path
        )
        leaf_layers.extend(sub_layers)

    return (leaf_layers, instances_by_asset)


def excluded_staged_refs(
    staged_file_path: Path,
    included_department_names: list[str],
    department_order: list[str],
) -> set[str]:
    """Top-level refs to drop when rendering only part of the department stack.

    A staged build composes every department that exported, so "render up
    to lighting" cannot be expressed by picking layers to add — it has to
    drop the ones past the cut. Two kinds of ref go:

    * the shot's own department layers past the cut, recognised by the
      ``dept`` query param that ``generate_entity_sublayer_uri`` writes;
    * assets *introduced by* those departments, which are staged refs with
      no ``dept`` of their own — their provenance lives in the staged
      ``context.json`` ``inputs``, the same source ``import_shot`` uses.

    A ref whose ``dept`` is not in ``department_order`` at all is left
    alone: the cut slices the pool, so a name outside the pool is not
    "past" it. That is what keeps the root layer (``dept='root'``).

    Only the shot's own staged file is filtered. Departments nested deeper
    (an asset's lookdev, say) come from the assets pool and are unrelated
    to the shot's cut, even where the two pools share a name.
    """
    excluded: set[str] = set()
    included = set(included_department_names)

    refs = read_staged_sublayer_refs(staged_file_path)
    parsed = {ref: parse_entity_sublayer_uri(ref) for ref in refs}

    # The shot's own department layers past the cut
    order = set(department_order)
    for ref, parsed_ref in parsed.items():
        if parsed_ref is None:
            continue
        if parsed_ref.department is None:
            continue
        # Outside the pool entirely, so outside the cut's domain. The
        # staged build sublayers the shot's root layer as dept='root',
        # one of config.department.RESERVED_NAMES — pseudo-departments
        # that are never registered in a pool. It carries the render
        # settings, RenderVars and camera, so cutting it left husk with a
        # stage that had nothing to render into. Testing pool membership
        # rather than the reserved list keeps every such name safe.
        if parsed_ref.department not in order:
            continue
        if parsed_ref.department not in included:
            excluded.add(ref)

    # Assets introduced by those departments, as the staged context.json
    # beside every staged build records them
    context_data = load_json(staged_file_path.parent / 'context.json')

    assets = context_data.get('parameters', {}).get('assets', [])
    excluded_asset_uris = set()
    for asset_info in assets:
        source_dept = get_source_department(
            asset_info.get('inputs', []), department_order
        )
        if source_dept is not None and source_dept not in included:
            excluded_asset_uris.add(asset_info['asset'])

    if not excluded_asset_uris:
        return excluded

    for ref, parsed_ref in parsed.items():
        if parsed_ref is None:
            continue
        if parsed_ref.department is not None:
            continue
        if parsed_ref.base in excluded_asset_uris:
            excluded.add(ref)

    return excluded


def _open_staged(staged_file_path: Path):
    """The composed stage of *staged_file_path*, with every payload loaded.

    LoadAll explicitly, not by default: an unloaded payload's contents are
    not composed onto the stage, so the settings prim or a copy's placement
    could sit in one and be invisible to any traversal. Raises
    LayerCollectionError when the build does not compose: every answer the
    collapse reads off the stage would otherwise be a guess.
    """
    from pxr import Usd

    try:
        stage = Usd.Stage.Open(str(staged_file_path), Usd.Stage.LoadAll)
    except Exception as error:
        # pxr raises its own Tf.ErrorException for a layer that will not
        # open; translated so the farm reports which build it was.
        raise LayerCollectionError(
            f'could not compose the staged build {staged_file_path}: {error}'
        ) from error
    if stage is None:
        raise LayerCollectionError(
            f'could not compose the staged build {staged_file_path}'
        )
    return stage


def collapse_latest_references(
    staged_file_path: Path,
    render_overrides: dict | None = None,
    excluded_refs: set[str] | None = None,
    playblast: bool = False
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
        render_overrides: Optional dict mapping Karma attribute names to values
                         e.g. {'karma:global:pathtracedsamples': 128}
        excluded_refs: Optional set of top-level sublayer refs to drop, from
                       excluded_staged_refs(). Used to render only part of
                       the department stack; None composes everything.
        playblast: Collapse for a playblast rather than a Karma render —
                   authors a preview RenderSettings (one raw colour var) and
                   points the stage at it. See
                   generate_playblast_settings_section.

    Returns:
        USDA file content as string with all leaf layers as absolute paths,
        a renderSettingsPrimPath naming the RenderSettings husk is to render
        through, instance prim definitions for multi-instance assets, and
        render overrides

    Raises:
        ValueError: If staged file doesn't exist or has invalid format
        LayerCollectionError: If a layer the build records is missing from
                              disk, the collapse yields no layers at all, or
                              the staged build does not compose
        RenderSettingsError: If the stage's RenderSettings prim (or, for a
                             playblast, the camera it names) cannot be
                             identified
    """
    if not staged_file_path.exists():
        raise ValueError(f"Staged file does not exist: {staged_file_path}")

    # Read the source file for metadata
    with open(staged_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse metadata (fps, frame range)
    metadata = parse_usda_metadata(content)

    # Recursively collect all leaf layers and instance information
    # This traverses all _staged files and entity URIs, flattening the hierarchy
    leaf_layers, instances_by_asset = _collect_leaf_layers_and_instances(
        str(staged_file_path), excluded_refs=excluded_refs
    )

    if not leaf_layers:
        # Nothing to render. The shot's root layer (dept='root') sits outside
        # the department pool and so survives every cut, which means an empty
        # result is a broken staged build rather than an aggressive cut.
        raise LayerCollectionError(
            f"staged build collapsed to no layers at all: {staged_file_path}\n"
            f"There is nothing to render. Check that the shot's root layer is "
            f"sublayered into the staged build."
        )

    # One compose answers everything the collapse reads off the stage.
    stage = _open_staged(staged_file_path)

    # Where this stage's RenderSettings lives, asked of the composed staged
    # build. Stamped onto the collapsed root layer below so husk renders
    # through the project's own settings prim (and therefore its camera)
    # instead of falling back to the first camera it happens to traverse --
    # see generate_usda_content. Shared with the overrides section, which
    # needs the same answer. A stage with no (or several) settings prims is
    # refused: husk would render it from whatever camera it lands on.
    settings_prim_path = find_render_settings_prim_path(stage)
    playblast_section = None
    if playblast:
        playblast_section = generate_playblast_settings_section(
            find_render_camera_prim_path(stage, settings_prim_path)
        )
        # The playblast renders through the settings authored above, not
        # the project's Karma ones.
        settings_prim_path = PLAYBLAST_SETTINGS_PRIM_PATH

    # Generate USDA with absolute filesystem paths (no entity URIs)
    usda_content = generate_usda_content(
        layer_paths=leaf_layers,
        fps=metadata['fps'],
        start_frame=metadata['start_frame'],
        end_frame=metadata['end_frame'],
        render_settings_prim_path=settings_prim_path
    )

    # Generate instance prim definitions for multi-instance assets, with
    # each instance's xformOpOrder derived from the composed staged stage
    # (the placement values ride in department sidecars without an order).
    # A copy nothing placed composes the prototype's placement instead.
    instance_prims = generate_instance_prim_definitions(
        instances_by_asset,
        composed_placement_orders(stage, instances_by_asset),
    )
    if instance_prims:
        usda_content = usda_content + '\n' + instance_prims

    if playblast_section:
        usda_content = usda_content + '\n' + playblast_section

    # Generate render settings overrides. The prim path comes from the
    # composed stage, never a constant: the project decides where its
    # RenderSettings lives, and an `over` chain aimed at the wrong path drops
    # every override in silence. Only the submit dialog's own overrides go
    # here: settings the render department's nodes author (per-AOV EXR
    # compression on th::render_vars, ...) reach the farm through its publish
    # and are never rewritten on the way.
    if render_overrides:
        usda_content = usda_content + '\n' + generate_render_overrides_section(
            render_overrides, settings_prim_path
        )

    return usda_content
