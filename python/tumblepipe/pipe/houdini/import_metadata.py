"""The Python the import nodes write into their metadata Python Script LOPs.

Every import node stamps pipeline metadata (``prim_metadata``) onto the
prims it imported, by a script it generates and stores in a parm:
import_asset (``metadata_python``), import_assets (``set_metadata_python``
and its dive's ``metadata_update``), import_layer (``metadata_python``),
import_shot (its ``set_metadata`` node and the duplicates subnet's
``placement_op_order``) and create_model. One script per function here,
each the shared :data:`HEADER` and a block per prim.

A record (the dict ``set_metadata`` writes) is emitted as its ``repr``, so
whatever a URI or input holds comes out a valid literal.

Saved scenes keep the scripts they were written with and run them on every
cook, so the names a script calls are frozen: ``prim_metadata.set_metadata``,
``get_metadata``, ``add_metadata_input``, ``mark_inlined``,
``set_shot_metadata`` and ``prim_queries.apply_placement_op_order``,
``author_identity_placement_ops``.
"""

from __future__ import annotations

from tumblepipe import naming
from tumblepipe.config.channels import CHANNEL_KEY, read_published_channel
from tumblepipe.pipe.houdini.prim_metadata import ANIMATABLE_KEY
from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path
from tumblepipe.pipe.usd.instances import copies_are_animated
from tumblepipe.util.uri import Uri

#: Every script's first lines: the stage the node edits, and its root.
HEADER = (
    'import hou',
    '',
    'from tumblepipe.pipe.houdini import prim_metadata, prim_queries',
    '',
    'node = hou.pwd()',
    'stage = node.editableStage()',
    'root = stage.GetPseudoRoot()',
    '',
)


def _script(lines: list[str]) -> str:
    return '\n'.join([*HEADER, *lines, ''])


def record(
    uri, instance: str, *, channel: str | None = None,
    instances: int | None = None, animatable: bool | None = None,
    inputs: list | None = None,
) -> dict:
    """The metadata an asset prim carries; the keys a node does not stamp
    are left out."""
    out = {'uri': str(uri), 'instance': instance}
    if channel is not None:
        out[CHANNEL_KEY] = channel
    if instances is not None:
        out['instances'] = instances
    if animatable is not None:
        out[ANIMATABLE_KEY] = animatable
    out['inputs'] = list(inputs or [])
    return out


def shot_input(shot_uri, department: str) -> dict:
    """The input that records which shot department introduced an asset."""
    return {'uri': str(shot_uri), 'department': department, 'version': 'initial'}


def _set(path: str, metadata: dict, indent: str = '') -> list[str]:
    return [
        f'{indent}prim = root.GetPrimAtPath({path!r})',
        f'{indent}if prim.IsValid():',
        f'{indent}    prim_metadata.set_metadata(prim, {metadata!r})',
        '',
    ]


def _mark(path: str) -> list[str]:
    return [
        f'prim = root.GetPrimAtPath({path!r})',
        'if prim.IsValid():',
        '    prim_metadata.mark_inlined(prim)',
        '',
    ]


# ── import_asset ─────────────────────────────────────────────────

def _subasset_lines(
    subassets: list[dict], shot_uri=None, shot_department: str | None = None,
    inline: bool = False, animated=copies_are_animated,
) -> list[str]:
    """Lines that re-establish each tracked sub-asset of an imported asset.

    Exported layers carry no pipeline metadata (the layerbreak strips
    everything the set workfile authored, including the Duplicate LOP's
    defs and reference arcs), so the importing side must both tag every
    sub-asset root and re-define multi-instance duplicates: otherwise the
    duplicates compose as typeless overs (invisible in the session, while
    the render-stage flatten regenerates them) and the untagged roots trip
    the export drop-guard. Instance prims mirror the Duplicate LOP's output
    and the render stage's instance definitions: copies named
    {base}0..{base}N-1 referencing the base prim, base deactivated.
    Placement op VALUES compose from the set layer's overs (they survive
    the layerbreak in the localized sidecar) but their xformOpOrder does
    not (it lived in the stripped Duplicate defs), so each instance authors
    an order listing whichever placement ops actually composed (CommonAPI
    ordering, pivot inverted last), with an identity {base}_dup op as the
    no-placement fallback.

    The base prim gets the same treatment: it is the prototype the
    instances reference, but it never passes through the import node's
    Transform LOP (that only runs on a directly imported root), so without
    this it composes with no transform at all.

    Numbered duplicates AT or beyond the tracked count are deactivated:
    the import node's persistent layer (which holds these re-established
    defs) is localized into department exports as a sidecar, so a layer
    exported while an inflated count was live carries the phantom defs
    forever (the paleindia six-towers relapse). For a count of 1 the base
    prim is the instance, so every numbered sibling is stale and the base
    is re-activated.
    """
    lines = []
    if subassets:
        lines += ['from pxr import UsdGeom, Gf', '']
    for asset_info in subassets:
        asset_uri_raw = asset_info.get('asset')
        if not asset_uri_raw:
            continue
        try:
            sub_uri = Uri.parse(asset_uri_raw)
            base_path = uri_to_prim_path(sub_uri)
        except ValueError:
            continue
        base_name = sub_uri.segments[-1]
        instances = asset_info.get('instances', 1)
        channel = read_published_channel(asset_info, where=f"asset row {asset_uri_raw}")
        inputs = list(asset_info.get('inputs', []))
        if shot_uri is not None and shot_department is not None:
            shot_entry = {'uri': str(shot_uri), 'department': shot_department, 'version': 'initial'}
            if shot_entry not in inputs:
                inputs.append(shot_entry)
        animatable = False if inline else bool(animated(sub_uri))

        def stamp(var: str, instance: str) -> list[str]:
            if inline:
                return [f'    prim_metadata.mark_inlined({var})']
            metadata = record(
                asset_uri_raw, instance, channel=channel,
                animatable=animatable, inputs=inputs,
            )
            return [f'    prim_metadata.set_metadata({var}, {metadata!r})']

        # Deactivate stale numbered duplicates at/beyond the tracked count
        # (see above), before the wanted instances are (re)defined so a
        # later legitimate def downstream still wins.
        stale_threshold = instances if instances > 1 else 0
        lines += [
            f'# Sub-asset: {asset_uri_raw}',
            f'base = root.GetPrimAtPath({base_path!r})',
            'if base.IsValid():',
            '    for sibling in list(base.GetParent().GetChildren()):',
            '        sibling_name = sibling.GetName()',
            f'        if not sibling_name.startswith({base_name!r}):',
            '            continue',
            f'        suffix = sibling_name[{len(base_name)}:]',
            '        if not suffix.isdigit():',
            '            continue',
            f'        if int(suffix) >= {stale_threshold}:',
            '            sibling.SetActive(False)',
            # The transform a direct import would have given the base (see
            # prim_queries.author_identity_placement_ops), before the
            # instances below so the op set composes onto them through the
            # internal reference; their own placement values still win.
            '    if not prim_queries.apply_placement_op_order(base):',
            '        prim_queries.author_identity_placement_ops(base)',
        ]
        if instances <= 1:
            lines.append('    base.SetActive(True)')
            lines += stamp('base', base_name)
        else:
            parent_path = base_path.rsplit('/', 1)[0]
            lines.append('    base.SetActive(False)')
            for index in range(instances):
                instance_prim_path = f'{parent_path}/{base_name}{index}'
                dup_op = f'{base_name}_dup'
                # The set layer's overs carry the placement op VALUES but
                # not the xformOpOrder that applies them (see
                # prim_queries.apply_placement_op_order). With no placement
                # composed, an identity {base}_dup op keeps a transform
                # surface for layout tooling.
                lines += [
                    f'    inst = stage.DefinePrim({instance_prim_path!r})',
                    '    inst.SetActive(True)',
                    f'    inst.GetReferences().AddInternalReference({base_path!r})',
                    '    if not prim_queries.apply_placement_op_order(inst):',
                    f'        if not inst.GetAttribute({"xformOp:transform:" + dup_op!r}):',
                    '            dup_op = UsdGeom.Xformable(inst).AddTransformOp(',
                    f'                UsdGeom.XformOp.PrecisionDouble, {dup_op!r}',
                    '            )',
                    '            dup_op.Set(Gf.Matrix4d(1))',
                ]
                lines += stamp('inst', naming.get_instance_name(base_name, index))
        lines.append('')
    return lines


def asset_script(
    asset_uri: Uri, channel_name: str = 'default', shot_uri=None,
    shot_department: str | None = None, subassets: list[dict] | None = None,
    animated=copies_are_animated,
) -> str:
    """import_asset: the asset's metadata (its channel, so the export
    scrape carries it into the layer's context.json, and
    ``animated(asset_uri)``), with the shot department that introduced it
    as its input in a shot workfile; then its tracked sub-assets."""
    inputs = (
        [shot_input(shot_uri, shot_department)]
        if shot_uri is not None and shot_department is not None else []
    )
    metadata = record(
        asset_uri, asset_uri.segments[-1], channel=channel_name,
        animatable=bool(animated(asset_uri)), inputs=inputs,
    )
    return _script([
        *_set(uri_to_prim_path(asset_uri), metadata),
        *_subasset_lines(subassets or [], shot_uri, shot_department, animated=animated),
    ])


def asset_inline_script(asset_uri: Uri, subassets: list[dict] | None = None) -> str:
    """import_asset in 'inline' mode: the inline marker in place of the
    pipeline metadata (the asset is baked into the export, so it must not
    be scraped and re-referenced, and the publish guards must not read the
    missing metadata as a drop), on the asset and its sub-assets alike."""
    return _script([
        *_mark(uri_to_prim_path(asset_uri)),
        *_subasset_lines(subassets or [], inline=True),
    ])


# ── import_assets ────────────────────────────────────────────────

def assets_script(
    asset_imports: list[tuple], shot_uri=None, shot_department: str | None = None,
    animated=copies_are_animated,
) -> str:
    """import_assets: each row's ``(asset_uri, channel, version, instances)``
    metadata on its asset prim (copies get it through the Duplicate LOP),
    rows with no instances skipped."""
    inputs = (
        [shot_input(shot_uri, shot_department)]
        if shot_uri is not None and shot_department is not None else []
    )
    lines = []
    for asset_uri, channel, _version, instances in asset_imports:
        if instances == 0:
            continue
        lines.append(f'# Asset: {asset_uri}')
        lines += _set(uri_to_prim_path(asset_uri), record(
            asset_uri, asset_uri.segments[-1], channel=channel,
            animatable=bool(animated(asset_uri)), inputs=inputs,
        ))
    return _script(lines)


def copies_script(copies: list[tuple[str, str, str]]) -> str:
    """import_assets' dive: each Duplicate copy ``(prim path, asset uri,
    instance name)`` gets its own instance name, its metadata created when
    absent. customData composes through the Duplicate LOP's reference arcs,
    but re-authoring it per copy keeps each 'instance' unique and covers
    duplicate modes that do not reference the source. A copy that composed
    empty (an unresolved payload) is skipped."""
    lines = []
    for prim_path, asset_uri, instance_name in copies:
        fallback = record(asset_uri, instance_name)
        lines += [
            f'prim = root.GetPrimAtPath({prim_path!r})',
            'if prim.IsValid():',
            '    metadata = prim_metadata.get_metadata(prim)',
            '    if metadata is None:',
            f'        metadata = {fallback!r}',
            f'    metadata["instance"] = {instance_name!r}',
            '    prim_metadata.set_metadata(prim, metadata)',
            '',
        ]
    return _script(lines)


def inline_script(prim_paths: list[str]) -> str:
    """Any import node in 'inline' mode: the inline marker on each of its
    prims, and only those (assets from upstream nodes are untouched)."""
    return _script([line for path in prim_paths for line in _mark(path)])


# ── import_layer ─────────────────────────────────────────────────

def layer_inputs_script(
    entity_uri, department_name: str, version_name: str, assets: list[dict],
    animated=copies_are_animated,
) -> str:
    """import_layer: each of the layer's assets gets this department and
    version as an input (once), and ``animated(asset_uri)`` over whatever
    stamp the layer's export carried; metadata is created when absent."""
    layer_input = {'uri': str(entity_uri), 'department': department_name, 'version': version_name}
    lines = []
    for asset_info in assets:
        asset_uri = Uri.parse(asset_info['asset'])
        fallback = record(asset_info['asset'], asset_uri.segments[-1] if asset_uri.segments else '')
        lines += [
            f'# Asset: {asset_info["asset"]}',
            f'prim = root.GetPrimAtPath({uri_to_prim_path(asset_uri)!r})',
            'if prim.IsValid():',
            '    metadata = prim_metadata.get_metadata(prim)',
            '    if metadata is None:',
            f'        metadata = {fallback!r}',
            f'    prim_metadata.add_metadata_input(metadata, {layer_input!r})',
            f'    metadata[{ANIMATABLE_KEY!r}] = {bool(animated(asset_uri))}',
            '    prim_metadata.set_metadata(prim, metadata)',
            '',
        ]
    return _script(lines)


# ── import_shot ──────────────────────────────────────────────────

def shot_script(
    shot_uri, frame_range, fps, version_name: str, assets: list[dict],
    animated=copies_are_animated,
) -> str:
    """import_shot: the shot's metadata in the stage's customLayerData, and
    each asset record's metadata on its prim. Every record carries its
    channel, inputs and instances (the staged build's context.json writes
    them all)."""
    shot = {
        'uri': str(shot_uri),
        'start_frame': frame_range.start_frame,
        'end_frame': frame_range.end_frame,
        'start_roll': frame_range.start_roll,
        'end_roll': frame_range.end_roll,
        'fps': fps,
        'version': version_name,
        'inputs': [],
    }
    lines = [f'prim_metadata.set_shot_metadata(stage, {shot!r})', '']
    for asset_info in assets:
        asset_uri = Uri.parse(asset_info['asset'])
        channel = read_published_channel(
            asset_info, where=f'asset record {asset_info["asset"]}', required=True
        )
        lines.append(f'# Asset: {asset_info["asset"]}')
        lines += _set(uri_to_prim_path(asset_uri), record(
            asset_info['asset'], asset_uri.segments[-1], channel=channel,
            instances=asset_info['instances'], animatable=bool(animated(asset_uri)),
            inputs=asset_info['inputs'],
        ))
    return _script(lines)


def placement_op_order_script(prototype_paths: list[str], instance_paths: list[str]) -> str:
    """import_shot's duplicates: the xformOpOrder that applies the placement
    op values composed onto each Duplicate copy from the authoring
    department's exported sidecar (the order was stripped with the original
    Duplicate defs, so without it every copy stacks at the prototype).
    Prototypes first: the Duplicate LOP leaves its deactivated source with
    whatever transform composed, none for a prim that never passed an
    import Transform LOP, and authoring it before the copies lets the op
    set reach them through the reference arc."""
    lines = []
    for path in prototype_paths:
        lines += [
            f'prim = root.GetPrimAtPath({path!r})',
            'if prim.IsValid():',
            '    if not prim_queries.apply_placement_op_order(prim):',
            '        prim_queries.author_identity_placement_ops(prim)',
            '',
        ]
    for path in instance_paths:
        lines += [
            f'prim = root.GetPrimAtPath({path!r})',
            'if prim.IsValid():',
            '    prim_queries.apply_placement_op_order(prim)',
            '',
        ]
    return _script(lines)


# ── create_model ─────────────────────────────────────────────────

def model_script(asset_uri: Uri) -> str:
    """create_model: the asset's bare metadata on its prim."""
    return _script(_set(uri_to_prim_path(asset_uri), record(asset_uri, asset_uri.segments[-1])))
