"""The pipeline's per-prim customData: which asset a prim is, what
imported it, and the stage-wide shot metadata. Generated node scripts
import this module, so it keeps no name a script would shadow."""

import json
import logging
import os
from pathlib import Path

from tumblepipe.config import channels as _channels
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini.prim_queries import iter_prim_children, iter_scene

logger = logging.getLogger(__name__)

# Whether the asset is animated in a shot: its Animatable box, or a rig
# published in any channel (pipe.usd.instances.copies_are_animated). The
# import nodes stamp it when they author the rest of the metadata, and
# Import Rigs reads it to find the rigs a shot needs.
ANIMATABLE_KEY = 'animatable'


def get_metadata(prim):
    """The prim's pipeline customData, or None when it carries none.

    The channel is always returned under ``channel``. Exported layers, and
    the metadata scripts saved scenes carry, from before the variant→channel
    rename write it as ``variant``; both are read through the published-data
    reader (:func:`tumblepipe.config.channels.read_published_channel`).
    """
    if prim is None or not prim.IsValid(): return None
    if not prim.HasMetadata('customData'): return None
    _metadata = prim.GetMetadata('customData')
    if _metadata is None: return None
    if 'uri' not in _metadata: return None
    metadata = _metadata.copy()
    metadata['inputs'] = json.loads(metadata['inputs'])
    metadata[_channels.CHANNEL_KEY] = _channels.read_published_channel(
        metadata, where=f'customData of {prim.GetPath()}'
    )
    metadata.pop(_channels.PUBLISHED_LEGACY_CHANNEL_KEY, None)
    return metadata


def set_metadata(prim, metadata):
    _metadata = metadata.copy()
    _metadata['inputs'] = json.dumps(_metadata['inputs'])
    prim.SetMetadata('customData', _metadata)


def add_metadata_input(metadata, input_datum):
    input_data = set(map(json.dumps, metadata['inputs']))
    input_data.add(json.dumps(input_datum))
    metadata['inputs'] = list(map(json.loads, input_data))


def mark_inlined(prim):
    """Replace pipeline metadata with an 'inlined' marker.

    An inlined asset (import node in 'inline' import mode) is deliberately
    baked into the export instead of being re-referenced, so its customData
    must carry no 'uri' (or the export scrape would re-add it as a sublayer)
    while still telling list_dropped_asset_prims the missing metadata is
    intentional, not a drop.
    """
    prim.SetMetadata('customData', {'inlined': True})


def is_inlined(prim):
    if prim is None or not prim.IsValid(): return False
    if not prim.HasMetadata('customData'): return False
    _metadata = prim.GetMetadata('customData')
    if _metadata is None: return False
    return bool(_metadata.get('inlined', False))


def is_asset(prim):
    metadata = get_metadata(prim)
    if metadata is None: return False
    uri = Uri.parse(metadata['uri'])
    return uri.segments[0] == 'assets' if len(uri.segments) > 0 else False


def list_assets(root):
    """
    List all asset metadata dicts in the stage, including prim paths.

    Walks the scene tree looking for prims with asset metadata in customData.

    Returns list of dicts with:
    - 'prim_path': The scene prim path (e.g., /CHAR/mom)
    - 'metadata': The asset metadata dict (uri, instance, inputs, etc.)
    """
    def get_asset_info(prim):
        metadata = get_metadata(prim)
        if metadata is None:
            return None
        return {
            'prim_path': str(prim.GetPath()),
            'metadata': metadata
        }

    results = []
    for prim in iter_scene(root, is_asset):
        info = get_asset_info(prim)
        if info is not None:
            results.append(info)
    return results


def list_animatable_assets(root, untagged=None):
    """The :func:`list_assets` entries whose prim is tagged animatable.

    A prim imported before the tag existed carries none until the node
    that brought it in imports again. ``untagged(uri)`` answers for those
    (the tag's own rule, read from the project now); without it they are
    left out.
    """
    def animatable(metadata):
        if ANIMATABLE_KEY in metadata:
            return bool(metadata[ANIMATABLE_KEY])
        return untagged is not None and untagged(Uri.parse(metadata['uri']))

    return [
        asset_info for asset_info in list_assets(root)
        if animatable(asset_info['metadata'])
    ]


def _normcased_roots(roots) -> list[str]:
    """Resolve + normcase the pipeline roots once for containment tests."""
    return [os.path.normcase(str(Path(root).resolve())) for root in roots]


def _path_under_roots(candidate: str, normed_roots) -> bool:
    """True if ``candidate`` resolves inside one of ``normed_roots``.

    Case-insensitive and separator-aware so a ``P:\\proj\\export`` root
    matches a sublayer resolved to ``P:/proj/export/...`` (Windows paths
    differ freely in case and slash direction; ``is_relative_to`` is
    purely lexical and case-sensitive, so we normcase both sides).
    """
    if not candidate:
        return False
    target = os.path.normcase(str(Path(candidate).resolve()))
    for root in normed_roots:
        if target == root or target.startswith(root + os.sep):
            return True
    return False


def _prim_pulls_from_roots(prim, normed_roots) -> bool:
    """True if any layer composing into ``prim`` resolves under an asset root.

    ``GetPrimStack`` returns every PrimSpec contributing to the prim across
    sublayers, references and payloads, so this recognises an imported
    asset however its layer arrived — TumblePipe's import nodes sublayer
    the staged file, but a raw Reference/Payload LOP is covered too. On a
    live stage each contributing layer carries a resolved ``realPath``; an
    ``entity:/assets/`` ``identifier`` is matched as a fallback for a layer
    not yet resolved to a real path.

    Only the asset export tree counts (the caller passes ``export:/assets``):
    department-authored shot geometry composes from a shot-department export
    (``export/shots/.../<dept>/...`` or an ``entity:/shots/`` arc), which is
    deliberately NOT matched — such geometry never carried per-asset metadata
    and is not a dropped asset. A catalogued asset that lost its customData
    still pulls its geometry through the asset export tree (assets are
    referenced, not baked, into shot-department layers), so it is still
    recognised here.
    """
    for spec in prim.GetPrimStack():
        layer = getattr(spec, 'layer', None)
        if layer is None:
            continue
        if _path_under_roots(getattr(layer, 'realPath', '') or '', normed_roots):
            return True
        identifier = getattr(layer, 'identifier', '') or ''
        if 'entity:/assets/' in identifier:
            return True
    return False


def list_dropped_asset_prims(root, asset_export_roots, ignore_prim_paths=()):
    """Return prim paths that sit beside a real asset but carry no metadata.

    An asset's parent (its category prim) is proven to be a category by the
    presence of at least one metadata-carrying child. Any *other* Scope/Xform
    child of that same parent that lacks metadata is an asset whose customData was
    dropped somewhere between import and export: because list_assets() only
    sees prims with metadata, such a prim silently vanishes from the export
    and from every downstream import. Triggers include a layerbreak
    stripping the authored customData, a stale import node whose metadata
    script never cooked against the current entity, or (historically) the
    scrape itself skipping instance proxies — see iter_prim_children.

    Mirrors iter_scene's traversal: we stop at the first metadata boundary, so
    an asset's own internal sub-Scopes (e.g. the 'mtl' material scope) are
    never mistaken for dropped assets.

    Three kinds of metadata-less sibling are NOT drops:
    - a grouping prim whose subtree holds metadata-carrying assets (artist
      set-dressing Xforms, e.g. Duplicate LOP destination prims) — we
      descend into it and keep checking instead;
    - a prim listed in ignore_prim_paths (the exporting entity's own root,
      which only gets tagged when some *other* workfile imports it) — also
      descended into, not flagged;
    - a prim that composes from NO asset export layer (see
      asset_export_roots) — geometry the artist authored directly, or
      department-authored shot content (an FX sim, a set-dress cache)
      pulled in from another department's shot export. A real dropped
      asset still pulls its geometry from the *asset* export tree
      (``export/assets/...``) via the import node's sublayer — assets are
      referenced, not baked, into shot-department layers — and only its
      customData tag went missing. Geometry that composes solely from a
      shot-department export (``export/shots/.../<dept>/...``) or from no
      pipeline layer at all never carried per-asset metadata, so it is a
      supported addition to the hierarchy, not a drop, and must never
      block the export. This is the check that distinguishes "an asset
      that lost its metadata" (block) from "shot/department geometry that
      never had any" (allow).

    Prims carrying the 'inlined' marker (see mark_inlined) are deliberately
    baked into the export by an import node in 'inline' mode — they are
    neither drops nor descended into.

    asset_export_roots: filesystem roots (the resolved ``export:/assets``
    tree) against which a metadata-less prim's composed layers are tested.
    """
    ignored = set(ignore_prim_paths)
    normed_roots = _normcased_roots(asset_export_roots)
    dropped = []
    allowed = []

    def subtree_has_asset(prim):
        def has_metadata(p): return get_metadata(p) is not None
        return next(iter_scene(prim, has_metadata), None) is not None

    def walk(prim):
        # Categories and asset roots compose as Scope OR Xform depending on
        # which layer wins (import nodes author Scopes; asset exports type
        # them Xform via set_kinds) — accept both or Xform-typed assets
        # become invisible to the guard.
        scope_children = [
            child for child in iter_prim_children(prim)
            if child.GetTypeName() in ('Scope', 'Xform')
        ]
        has_asset = any(get_metadata(child) is not None for child in scope_children)
        if not has_asset:
            # No asset here yet — keep looking deeper for category scopes.
            for child in scope_children:
                if is_inlined(child): continue
                walk(child)
            return
        # This prim is a category: a metadata-less Scope/Xform sibling of a
        # real asset is a drop, unless it is ignored or merely groups
        # tracked assets deeper down. Don't descend past asset boundaries.
        for child in scope_children:
            if get_metadata(child) is not None: continue
            if is_inlined(child): continue
            if str(child.GetPath()) in ignored or subtree_has_asset(child):
                walk(child)
                continue
            if not _prim_pulls_from_roots(child, normed_roots):
                # Composes from no asset export layer -> artist-authored or
                # department-authored shot geometry, a supported addition,
                # not a dropped asset.
                allowed.append(str(child.GetPath()))
                continue
            dropped.append(str(child.GetPath()))

    walk(root)
    if allowed:
        logger.info(
            "export drop-guard: allowed %d metadata-less prim(s) that "
            "compose from no asset export layer (artist-added or "
            "department shot geometry, not dropped assets): %s",
            len(allowed), ", ".join(allowed)
        )
    return dropped


def set_shot_metadata(stage, metadata):
    """Set shot metadata on stage-level customLayerData."""
    _metadata = metadata.copy()
    _metadata['inputs'] = json.dumps(_metadata['inputs'])
    layer_data = dict(stage.GetRootLayer().customLayerData)
    layer_data['shot_metadata'] = _metadata
    stage.GetRootLayer().customLayerData = layer_data
