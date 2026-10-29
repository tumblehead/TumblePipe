"""Build an entity's staged USD: the versioned ``_staged/<channel>/vNNNN``
layer that sublayers the latest export of every department (and, for a
shot, its root layer and every asset it tracks) in pipeline order.

The farm's build worker (``farm/workers/build.py``) runs it on the farm;
Build USD runs it in the session. ``pipe.build`` resolves what to compose,
this writes it.
"""

from tempfile import TemporaryDirectory
from pathlib import Path

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.config.channels import DEFAULT_CHANNEL, read_published_channel
from tumblepipe.config.department import list_departments
from tumblepipe.config.entities import get_entity_type as _get_entity_type
from tumblepipe.config.timeline import BlockRange, require_fps
from tumblepipe.pipe import build as pipe_build
from tumblepipe.pipe import graph
from tumblepipe.pipe.paths import current_staged_path
from tumblepipe.pipe.scene_build import generate_root_version, root_layer_is_stale
from tumblepipe.pipe.usd.sublayer_refs import (
    generate_entity_sublayer_uri,
    generate_staged_sublayer_uri
)
from tumblepipe.pipe.usd.usda import generate_usda_content
from tumblepipe.util.io import (
    copy_version_contents,
    store_text,
    store_json,
    load_json_if_exists
)
from tumblepipe.util.uri import Uri


class StagedBuildError(RuntimeError):
    """An entity's staged USD cannot be built."""


def _section(title):
    print(f' {title} '.center(80, '='))


def _staged_closure(
    asset_uri: Uri,
    channel_name: str,
    _visited: set | None = None
) -> set:
    """Assets reachable through an asset's current staged composition.

    Walks the tracked-asset entries of the asset's current staged
    context.json transitively. Used to drop the direct sublayer ref for
    an asset that already arrives through another tracked asset's staged
    file: a second, independently pinned ref can diverge from the pin
    inside that staged file, composing conflicting opinions on the same
    prims with sublayer order silently picking the winner. An unreadable
    or missing staged context yields an empty closure, so the direct ref
    is kept.
    """
    if _visited is None:
        _visited = set()
    key = (str(asset_uri), channel_name)
    if key in _visited:
        return set()
    _visited.add(key)
    closure = set()
    staged_version_path = current_staged_path(asset_uri, channel_name)
    if staged_version_path is None:
        return closure
    # Absent while a concurrent build is still copying its version in: an
    # unknown closure keeps the direct ref (see above), the safe side.
    context_data = load_json_if_exists(staged_version_path / 'context.json')
    if context_data is None:
        return closure
    for entry in context_data.get('parameters', {}).get('assets', []):
        entry_uri_raw = entry.get('asset')
        if not entry_uri_raw:
            continue
        # A shot's asset recorded in a set (seen through a shot layer the
        # set's workfile imported) is not composed by the set: the fixed
        # asset build leaves it out, and trusting an older staged context
        # here dropped the shot's own ref to it (HideAndReek's PoliceCar).
        if pipe_build.sourced_from_shot(entry.get("inputs")):
            continue
        entry_uri = Uri.parse(entry_uri_raw)
        closure.add(entry_uri)
        closure |= _staged_closure(
            entry_uri,
            read_published_channel(entry, where=f'{staged_version_path} context.json'),
            _visited,
        )
    return closure


def _drop_transitive_refs(tracked_uris: list, channel_of) -> list:
    """Filter tracked assets down to the ones needing a direct sublayer ref.

    An asset is dropped when it is reachable through another tracked
    asset's staged closure AND that other asset does not in turn sit in
    its closure (the dominance check keeps both sides of a pathological
    cycle — duplicated refs are safer than losing the composition).
    Context.json entries are NOT filtered by this: import-side tagging
    and instance re-establishment need the full list.
    """
    closures = {
        uri: _staged_closure(uri, channel_of(uri))
        for uri in tracked_uris
    }
    kept = []
    for uri in tracked_uris:
        dominated = any(
            uri in closures[other] and other not in closures[uri]
            for other in tracked_uris
            if other != uri
        )
        if dominated:
            print(
                f'Skipping direct staged ref for {uri}: already composed '
                'through another tracked asset\'s staged file'
            )
            continue
        kept.append(uri)
    return kept


def _store_shot_stage(
    shot_uri: Uri,
    render_range: BlockRange,
    resolved_context: dict,
    output_path: Path,
    final_output_path: Path
):
    """
    Store shot staged .usda file.

    References shot layer .usd files and root department layer.
    Scene assets arrive via the root department's scene reference;
    shot-flow assets (imported in department workfiles) get their staged
    files sublayered directly. Uses entity URIs for sublayer references
    (resolver-based).

    Args:
        shot_uri: Shot entity URI
        render_range: Frame range for the shot
        resolved_context: Resolved dependency context from graph
        output_path: Temporary output path
        final_output_path: Final network output path
    """
    layer_refs = []  # Entity URI strings
    shot_channel = resolved_context['shot_channel']
    shot_layer_channels = resolved_context['shot_layer_channels']

    # Add shot layer references (reverse config order: later pipeline stages = stronger in USD sublayers).
    # Each URI names the channel the layer was resolved from — departments
    # without exports under the build's channel fall back to default, and a
    # URI claiming the build's channel for them would resolve to nothing.
    for department_name, version_path in reversed(resolved_context['shot_layers'].items()):
        version_name = version_path.name

        # Use entity URI - resolver handles path lookup
        layer_ref = generate_entity_sublayer_uri(
            shot_uri,
            shot_layer_channels[department_name],
            department_name,
            version_name
        )
        layer_refs.append(layer_ref)

    # Add sublayer references for shot-flow assets (assets added via import_asset in department workfiles)
    # These are weaker than department layers (so departments can modify them)
    # but stronger than root/scene assets
    scene_asset_uris = resolved_context['scene_asset_uris']
    asset_channels = resolved_context['asset_channels']
    shot_flow_uris = [
        asset_uri for asset_uri in resolved_context['assets']
        if asset_uri not in scene_asset_uris
        # Scene assets are loaded via root → scene → asset staged files
    ]
    for asset_uri in _drop_transitive_refs(
        shot_flow_uris,
        lambda uri: asset_channels.get(uri, 'default')
    ):
        # This is a shot-flow asset - add its staged file as a sublayer,
        # pinned to its current staged version so the build stays frozen
        # (latest-mode imports strip/ignore the pin; a version-less ref
        # would float to whatever is newest at resolve time — the farm
        # divergence bug class). Unstaged assets fall back to version-less.
        channel = asset_channels.get(asset_uri, 'default')
        staged_version_path = current_staged_path(asset_uri, channel)
        staged_version = (
            None if staged_version_path is None
            else staged_version_path.name
        )
        layer_refs.append(generate_staged_sublayer_uri(
            asset_uri, channel, staged_version
        ))

    # Add root department layer as base (weakest - added last in sublayer order)
    # Note: Scene assets are included in root department via scene reference
    # Root is shot-level at _root/ (channel is ignored by resolver for root)
    root_layer = resolved_context['root_layer']
    if root_layer is not None:
        root_version = root_layer.parent.name
        # Use any channel - resolver handles root specially and ignores channel
        layer_ref = generate_entity_sublayer_uri(
            shot_uri, shot_channel, 'root', root_version
        )
        layer_refs.append(layer_ref)

    # The shot's own fps (an entity may override the project's)
    fps = require_fps(shot_uri)

    # Generate USDA content using shared helper
    usda_content = generate_usda_content(
        layer_paths=layer_refs,
        fps=fps,
        start_frame=render_range.first_frame,
        end_frame=render_range.last_frame
    )
    store_text(output_path, usda_content)

    # Create the context json file
    # Include inputs per asset to preserve composition history for dependency resolution
    asset_inputs = resolved_context['asset_inputs']
    context_path = output_path.parent / 'context.json'
    store_json(context_path, dict(
        uri = str(shot_uri),
        version = final_output_path.parent.name,
        parameters = dict(
            assets = [
                dict(
                    asset = str(asset_uri),
                    instances = instances,
                    channel = asset_channels.get(asset_uri, DEFAULT_CHANNEL),
                    inputs = asset_inputs.get(asset_uri, [])  # Scene assets have none
                )
                for asset_uri, instances in resolved_context['assets'].items()
            ]
        )
    ))


def _store_asset_stage(
    asset_uri: Uri,
    resolved_context: dict,
    output_path: Path,
    final_output_path: Path,
    render_range: BlockRange | None = None
):
    """
    Store asset staged .usda file.

    Creates a layer file referencing all renderable department exports
    (e.g., lookdev > model in that priority order), then the staged files
    of any assets tracked in those department layers (nested assets, e.g.
    a set that imports other assets) as weaker sublayers.
    Uses entity URIs for sublayer references (resolver-based).

    If render_range is provided (animated asset), includes timing metadata.
    Otherwise generates simple USDA without timing.

    Args:
        asset_uri: Asset entity URI
        resolved_context: Resolved dependency context from graph
        output_path: Temporary output path
        final_output_path: Final network output path
        render_range: Optional frame range for animated assets
    """
    # Collect layer references (reverse config order: later pipeline stages = stronger in USD sublayers)
    layer_refs = []  # Entity URI strings
    asset_channel = resolved_context['channel']
    for department_name, version_path in reversed(resolved_context['department_layers'].items()):
        version_name = version_path.name

        # Use entity URI - resolver handles path lookup
        layer_ref = generate_entity_sublayer_uri(
            asset_uri, asset_channel, department_name, version_name
        )
        layer_refs.append(layer_ref)

    # Sublayer the staged file of every asset tracked in the department
    # layers (weaker than this asset's own layers, so its placement overs
    # win). Without this, a set-style asset's imported assets exist only
    # as typeless overs and compose empty everywhere downstream. Mirrors
    # _store_shot_stage's shot-flow asset sublayers.
    # Pin each ref to the sub-asset's current staged version so a pinned
    # build stays frozen (latest-mode imports strip/ignore the pin, so
    # floating still cascades). An unstaged sub-asset falls back to a
    # version-less ref rather than dropping out of the composition.
    # Transitively-reachable assets get no direct ref of their own — they
    # compose through the tracked asset that nests them.
    asset_channels = resolved_context['asset_channels']
    for tracked_uri in _drop_transitive_refs(
        list(resolved_context['assets']),
        lambda uri: asset_channels[uri]
    ):
        tracked_channel = asset_channels[tracked_uri]
        staged_version_path = current_staged_path(tracked_uri, tracked_channel)
        staged_version = (
            None if staged_version_path is None
            else staged_version_path.name
        )
        layer_refs.append(generate_staged_sublayer_uri(
            tracked_uri, tracked_channel, staged_version
        ))

    # Generate USDA content based on whether asset is animated
    if render_range is not None:
        # Animated asset - include timing metadata
        fps = require_fps(asset_uri)
        usda_content = generate_usda_content(
            layer_paths=layer_refs,
            fps=fps,
            start_frame=render_range.first_frame,
            end_frame=render_range.last_frame
        )
    else:
        # Static asset - no timing metadata
        usda_content = generate_usda_content(layer_paths=layer_refs)
    store_text(output_path, usda_content)

    # Create the context json file
    asset_inputs = resolved_context['asset_inputs']
    context_path = output_path.parent / 'context.json'
    store_json(context_path, dict(
        uri = str(asset_uri),
        version = final_output_path.parent.name,
        parameters = dict(
            departments = list(resolved_context['department_layers'].keys()),
            assets = [
                dict(
                    asset = str(tracked_uri),
                    instances = instances,
                    channel = asset_channels[tracked_uri],
                    inputs = asset_inputs[tracked_uri]
                )
                for tracked_uri, instances in resolved_context['assets'].items()
            ]
        )
    ))

def _get_renderable_asset_departments() -> list[str]:
    """
    Get list of renderable asset departments in config order.

    Config defines departments in pipeline order (weaker → stronger).
    The sublayer generation uses reversed() to get correct USD composition order.
    """
    all_departments = list_departments('assets')
    return [d.name for d in all_departments if d.renderable]


def _build_shot(
    shot_uri: Uri,
    output_path: Path,
    render_range: BlockRange,
    channel_name: str,
    dependency_graph=None,
) -> None:
    """Build staged .usda for a shot; ``dependency_graph`` is a scan to
    reuse (staging several shots scans once), else one is made."""
    _section('Shot Build')
    print(f'Shot URI: {shot_uri}')
    print(f'Output path: {output_path}')
    print(f'Render range: {render_range}')
    print(f'Channel: {channel_name}')

    # Check if output already exists
    if output_path.exists():
        print('Output already exists')
        return

    # Get configured departments
    shot_departments = [d.name for d in list_departments('shots')]
    asset_departments = [d.name for d in list_departments('assets')]
    print(f'Shot departments configured: {shot_departments}')
    print(f'Asset departments configured: {asset_departments}')

    # A root layer that is missing, or that names another Root than the
    # shot now points at (the Entity Editor gave it its own Root), is
    # regenerated: otherwise the build keeps composing the old Root. A shot
    # whose root cannot be generated (no frame range) fails the task: a
    # build without its root has no Root assets.
    if root_layer_is_stale(shot_uri):
        print('Root layer missing or out of date, generating...')
        root_layer_path = generate_root_version(shot_uri)
        print(f'Generated root layer: {root_layer_path}')

    # Scan dependency graph and resolve all needed layers for shot
    if dependency_graph is None:
        print('Scanning dependency graph...')
        dependency_graph = graph.scan()
    try:
        resolved_context = pipe_build.resolve_shot_build(
            dependency_graph,
            api,
            shot_uri,
            shot_departments=shot_departments,
            asset_departments=asset_departments,
            shot_channel=channel_name,
        )
    except pipe_build.ChannelNotExportedError as e:
        # Fail the build: an all-default "channel" stage would render the
        # default scene again under the channel's name.
        raise StagedBuildError(str(e)) from e

    # Print resolved context summary
    found_shot_layers = list(resolved_context['shot_layers'].keys())
    found_assets = list(resolved_context['assets'].keys())
    print(f'Found shot layers: {found_shot_layers}')
    print(f'Found assets: {[str(a) for a in found_assets]}')
    print(f'Root layer: {resolved_context["root_layer"]}')

    # Export
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths
        temp_stage_path = temp_path / output_path.name

        # Build the USD stage
        _section('Building USD stage')
        _store_shot_stage(
            shot_uri=shot_uri,
            render_range=render_range,
            resolved_context=resolved_context,
            output_path=temp_stage_path,
            final_output_path=output_path
        )

        # Copy stage to network
        _section('Copying files to output')
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        # context.json last: it marks the staged version finished.
        copy_version_contents(temp_path, output_dir)

        # Note: _staged/latest/ is no longer created here.
        # Dynamic latest resolution is handled at import time by stripping
        # version parameters from entity URIs. This eliminates network file
        # conflicts and enables true closure semantics.

    # Done
    print('Success')


def _build_asset(
    asset_uri: Uri,
    output_path: Path,
    render_range: BlockRange | None,
    channel_name: str
) -> None:
    """Build staged .usda for an asset.

    If render_range is provided (animated asset), includes timing metadata
    in the USD file. Otherwise generates simple USDA without timing.
    """
    _section('Asset Build')
    print(f'Asset URI: {asset_uri}')
    print(f'Channel: {channel_name}')
    print(f'Output path: {output_path}')
    print(f'Render range: {render_range}')

    # Check if output already exists
    if output_path.exists():
        print('Output already exists')
        return

    # Get renderable departments in priority order
    renderable_departments = _get_renderable_asset_departments()
    print(f'Renderable departments configured: {renderable_departments}')
    if not renderable_departments:
        raise StagedBuildError('No renderable asset departments configured')

    # Scan dependency graph and resolve all needed layers for asset
    print('Scanning dependency graph...')
    dependency_graph = graph.scan()
    resolved_context = pipe_build.resolve_asset_build(
        dependency_graph,
        api,
        asset_uri,
        asset_departments=renderable_departments,
        channel_name=channel_name
    )

    # Log found layers (build proceeds even if empty - creates valid USDA with no sublayers)
    found_layers = list(resolved_context['department_layers'].keys())
    print(f'Found department layers: {found_layers}')
    for dept, path in resolved_context['department_layers'].items():
        print(f'  {dept}: {path.name}')

    # Export
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths
        temp_stage_path = temp_path / output_path.name

        # Build the USD stage
        _section('Building USD stage')
        _store_asset_stage(
            asset_uri=asset_uri,
            resolved_context=resolved_context,
            output_path=temp_stage_path,
            final_output_path=output_path,
            render_range=render_range
        )

        # Copy stage to network
        _section('Copying files to output')
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        # context.json last: it marks the staged version finished.
        copy_version_contents(temp_path, output_dir)

        # Note: _staged/latest/ is no longer created here.
        # Dynamic latest resolution is handled at import time by stripping
        # version parameters from entity URIs. This eliminates network file
        # conflicts and enables true closure semantics.

    # Done
    print('Success')


def build_staged(
    entity_uri: Uri,
    output_path: Path,
    render_range: BlockRange | None = None,
    channel_name: str = DEFAULT_CHANNEL,
    dependency_graph=None,
) -> None:
    """Build the staged .usda of a shot or an asset at ``output_path``.

    A shot needs ``render_range``; an asset takes one only when it is
    animated (its timing goes into the layer). Nothing is written when
    ``output_path`` already exists. A shot build reuses
    ``dependency_graph`` when given a scan. Raises :class:`StagedBuildError`.
    """
    entity_type = _get_entity_type(entity_uri)
    if entity_type == 'shot':
        if render_range is None:
            raise StagedBuildError(f'A frame range is required to build shot {entity_uri}')
        _build_shot(entity_uri, output_path, render_range, channel_name, dependency_graph)
    elif entity_type == 'asset':
        _build_asset(entity_uri, output_path, render_range, channel_name)
    else:
        raise StagedBuildError(f'Unknown entity type: {entity_uri}')
