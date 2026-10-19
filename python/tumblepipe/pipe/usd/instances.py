"""Asset instance copies for the static flatten.

A staged build records how many copies of an asset a department placed
(``context.json``), not prims for them: the GUI import makes the copies, and
the collapsed farm stage has to make the same ones as static USDA defs. The
placement op order each copy applies is read off the composed stage.
"""

from collections import defaultdict
from pathlib import Path

from tumblepipe.util.io import load_json


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

# The identity XformCommonAPI op set as USDA text, for the static flatten's
# prototype prims. Mirrors what pipe.houdini.prim_queries.author_identity_placement_ops
# authors in the GUI import, so a prototype composes the same whether the
# session built it or the flatten did — keep the two in step. The types are
# the ones UsdGeomXformCommonAPI itself writes.
IDENTITY_PLACEMENT_OPS_USDA = (
    'double3 xformOp:translate = (0, 0, 0)',
    'float3 xformOp:translate:pivot = (0, 0, 0)',
    'float3 xformOp:rotateXYZ = (0, 0, 0)',
    'float3 xformOp:scale = (1, 1, 1)',
    'uniform token[] xformOpOrder = ['
    '"xformOp:translate", "xformOp:translate:pivot", "xformOp:rotateXYZ", '
    '"xformOp:scale", "!invert!xformOp:translate:pivot"]',
)


def composed_placement_op_order(prim) -> list[str]:
    """xformOpOrder applying the placement op values composed on ``prim``.

    An order that composed is returned verbatim and never second-guessed —
    whatever authored it is the authority on how its own values apply.

    The derivation below covers only the case it was written for: values
    with no order at all. Exported set layers carry a duplicate's placement
    op VALUES (they survive the layerbreak in the localized import-node
    sidecar) but sometimes not the xformOpOrder that applies them — that
    order lived in the Duplicate LOP defs the export stripped. Then, and
    only then, derive one from what actually composed: ops with a value,
    XformCommonAPI ordering, pivot inverted last. Returns [] when no
    placement composed either: the caller decides the fallback (the
    flatten's copies then compose the prototype's placement).

    Deriving unconditionally *replaced* a real order: the static flatten
    authors this result into the collapsed ROOT layer, stronger than every
    department sublayer. A set whose duplicates kept a baked
    `xformOp:transform` had it swapped for a CommonAPI set applying the
    stale translate/rotate/scale values sitting beside it, and every copy
    moved on the farm while the GUI stayed put. xformOp:transform is not in
    PLACEMENT_OPS, so the derivation cannot see it.

    ``prim`` is a Usd.Prim; this module stays pxr-free at import time, so
    the caller owns the stage.
    """
    order_attr = prim.GetAttribute('xformOpOrder')
    if order_attr and order_attr.HasValue():
        composed = [str(op_name) for op_name in (order_attr.Get() or [])]
        if composed:
            return composed
    order = [
        op_name for op_name in PLACEMENT_OPS
        if prim.GetAttribute(op_name) and prim.GetAttribute(op_name).HasValue()
    ]
    if order and 'xformOp:translate:pivot' in order:
        order.append('!invert!xformOp:translate:pivot')
    return order


def asset_uri_to_prim_path(asset_uri: str) -> tuple[str, str, str]:
    """``(category, asset_name, prim_path)`` of an ``entity:/assets/`` URI.

    ``entity:/assets/CHAR/cupAndBall`` gives
    ``('CHAR', 'cupAndBall', '/CHAR/cupAndBall')``. Raises ValueError for
    anything that is not an asset URI: an asset entry naming something else
    is a corrupt context, and its copies would land at an invented prim.
    """
    prefix = 'entity:/assets/'
    parts = asset_uri[len(prefix):].split('/')
    if not asset_uri.startswith(prefix) or len(parts) < 2 or not all(parts[:2]):
        raise ValueError(f"not an asset entity URI: {asset_uri!r}")
    category, asset_name = parts[0], parts[1]
    return category, asset_name, f'/{category}/{asset_name}'


def collect_instance_info_from_context(layer_path: str) -> dict[str, list[str]]:
    """Instance names per asset prim path, from the context.json beside a layer.

    Maps an asset prim path (``/CHAR/cupAndBall``) to its copies' names.
    A layer with no context.json beside it (a project config template such
    as ``root_default_prims.usda``) records no instances, so that is {} —
    every pipeline export and staged build writes one.
    """
    instances_by_asset = defaultdict(list)

    context_path = Path(layer_path).parent / 'context.json'
    if not context_path.exists():
        return instances_by_asset
    context_data = load_json(context_path)

    for output in context_data.get('outputs', []):
        for asset_entry in output.get('parameters', {}).get('assets', []):
            asset_uri = asset_entry['asset']
            instance_count = asset_entry.get('instances', 1)

            # The base name comes from the asset URI, never from the entry's
            # 'instance' field. That field records whichever instance prim
            # the export scrape happened to walk first, and the prototype is
            # deactivated so the walk skips it — a set whose first live copy
            # was Haybale9 recorded 'Haybale9' as the base and regenerated
            # Haybale90..Haybale929: 30 phantoms referencing the prototype
            # with no placement, stacked at the origin. Every other point
            # that re-establishes instances already derives the base from
            # the URI (import_asset, import_shot, import_layer); agreeing
            # with them also heals the context.json files already written
            # with the wrong name, without a re-export.
            _, asset_name, prim_path = asset_uri_to_prim_path(asset_uri)
            # All instances get a numeric suffix: MiniFig0, MiniFig1,
            # MiniFig2 — matching the prim paths import_asset defines.
            # The prototype (MiniFig) is the original asset prim.
            for i in range(instance_count):
                instances_by_asset[prim_path].append(f"{asset_name}{i}")

    return instances_by_asset


def generate_instance_prim_definitions(
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
        placement_orders: Optional {prim_path: [op names]} from composing
                          the staged stage — instances present here author
                          that xformOpOrder (applying the placement values
                          composed from the authoring department's sidecar);
                          absent ones author no transform of their own and
                          compose the prototype's through the reference. A
                          prototype's own prim path may also appear: it then
                          applies that order instead of the identity
                          CommonAPI op set.

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
            # the composed placement values when known. Otherwise the
            # instance authors no transform at all and composes the
            # prototype's order and values through the reference, as the
            # GUI import's copies do (util.apply_placement_op_order reads
            # the order through that same arc). An identity dup op here
            # used to override the referenced order: HideAndReek 010's two
            # Kangaroo copies, placed on the prototype, rendered at the
            # origin on the farm while the session kept them in place.
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
                lines.append('    }')
                lines.append('')

            # Deactivate the original prototype so it doesn't render, but
            # give it the transform the instances reference: the prototype
            # is what they compose through, and the GUI import authors it
            # (util.author_identity_placement_ops), so leaving it bare here
            # would diverge the farm from the session. Placement composed on
            # the prototype itself only needs its order applied.
            base_order = placement_orders.get(base_prim_path)
            lines.append(f'    over "{asset_name}" (')
            lines.append('        active = false')
            lines.append('    )')
            lines.append('    {')
            if base_order:
                order_str = ', '.join(f'"{op_name}"' for op_name in base_order)
                lines.append(f'        uniform token[] xformOpOrder = [{order_str}]')
            else:
                for op_line in IDENTITY_PLACEMENT_OPS_USDA:
                    lines.append(f'        {op_line}')
            lines.append('    }')
            lines.append('')

        lines.append('}')

    return '\n'.join(lines)


def composed_placement_orders(
    stage,
    instances_by_asset: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Placement op order per instance and prototype, from the composed stage.

    Derives each instance prim's xformOpOrder — and its prototype's, keyed
    by the prototype's own prim path — from the placement op values that
    actually composed on ``stage`` (composed_placement_op_order). The
    instance prims exist on the staged stage only as typeless overs, which
    is enough — their attributes compose and are queryable via
    GetPrimAtPath.
    """
    orders = {}
    for prim_path, instance_names in instances_by_asset.items():
        parent_path = prim_path.rsplit('/', 1)[0]
        # The prototype the instances reference, keyed by its own path:
        # when placement composed on it the static defs apply that order,
        # otherwise they author the identity op set.
        base_prim = stage.GetPrimAtPath(prim_path)
        if base_prim:
            base_order = composed_placement_op_order(base_prim)
            if base_order:
                orders[prim_path] = base_order
        for instance_name in instance_names:
            instance_path = f'{parent_path}/{instance_name}'
            prim = stage.GetPrimAtPath(instance_path)
            # No department authored placement on this copy, so nothing
            # composed there: the static def composes the prototype's.
            if not prim:
                continue
            order = composed_placement_op_order(prim)
            if order:
                orders[instance_path] = order
    return orders
