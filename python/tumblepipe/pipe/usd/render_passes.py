"""Render layers as USD render passes (Houdini's Render Pass LOP, husk --pass).

A shot's render layers are its channels with prims named in ``channel_prims``
(:func:`tumblepipe.config.channels.channel_layers`): bottom to top, each a set
of prims, ``default`` without a pattern taking every prim no other layer
claims. Each layer becomes a ``RenderPass`` prim at ``/Render/Passes/<channel>``
that husk renders with ``--pass``, all from one stage:

- the layers **above** go in the pass's ``matte`` collection: they cut a hole
  (alpha 0) in this layer while still casting their shadows onto it, so a
  background layer comes out with the characters' shadows and holes for
  them, ready to sit under the characters' layer;
- the layers **below** are taken out of the ``cameraVisibility`` collection:
  unseen, but still shadowing and reflecting into this layer.

This layer's own prims are excluded from ``matte`` and included in
``cameraVisibility``, and nothing inside them is held out or hidden, so
overlapping patterns still leave a layer its own prims. When ``default``
has a pattern, prims no layer names are hidden from camera in every pass:
in no layer, as in a layered playblast, but still shadowing. Verified with Karma CPU and XPU
(22.0.368): a matte prim keeps its shadow on the prims behind it, a prim out
of ``cameraVisibility`` keeps its shadow, and the slapcomp's "over" of the
layers rebuilds the frame.

Which prims a pattern names comes from :func:`expand_pattern`: Houdini's own
``hou.LopSelectionRule`` where ``hou`` is loaded (the same match the
layered playblast makes), else a glob subset good for plain paths, ``*``,
``?``, ``**`` and ``^`` exclusions.
"""

from dataclasses import dataclass
import re
import sys

from pxr import Sdf, Usd, UsdGeom, UsdRender

from tumblepipe.pipe.render_layers import RenderPassError, render_pass_path


# --- pattern expansion -------------------------------------------------------

_HOUDINI_ONLY = re.compile(r'[%{}@<>]')


def _component_regex(component: str) -> str:
    out = []
    i = 0
    while i < len(component):
        char = component[i]
        if char == '*':
            out.append('[^/]*')
        elif char == '?':
            out.append('[^/]')
        elif char == '[':
            end = component.find(']', i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                out.append('[' + component[i + 1:end].replace('\\', '\\\\') + ']')
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    return ''.join(out)


def _glob_regex(token: str) -> re.Pattern:
    """``token`` (an absolute path glob) as a regex over prim paths. ``**``
    is one or more levels, wherever it stands, as in Houdini:
    ``/SET/**`` is what is under ``/SET``, and ``/*/**/*`` does not match
    ``/SET/tree`` (checked against ``hou.LopSelectionRule``, 22.0.368)."""
    if not token.startswith('/'):
        raise RenderPassError(
            f'prim pattern {token!r} is not an absolute path; outside Houdini '
            f'only absolute paths and globs (/CHAR/*, /SET/**) are understood'
        )
    components = [c for c in token.split('/') if c]
    parts = []
    for component in components:
        if component == '**':
            parts.append('(?:/[^/]+)+')
        else:
            parts.append('/' + _component_regex(component))
    return re.compile('^' + ''.join(parts) + '$')


def glob_expand(stage: Usd.Stage, pattern: str) -> set[Sdf.Path]:
    """The prims ``pattern`` matches, without Houdini: whitespace
    separated absolute-path globs, a ``^`` (or ``-``) token removing its
    matches. Houdini-only syntax (``%type:Mesh``, ``{...}``) is refused."""
    if _HOUDINI_ONLY.search(pattern):
        raise RenderPassError(
            f'prim pattern {pattern!r} uses Houdini-only syntax; it can only be '
            f'expanded where Houdini runs. Use plain paths and globs '
            f'(/CHAR/*, /SET/**) in channel_prims.'
        )
    include, exclude = [], []
    for token in pattern.split():
        if token[0] in '^-':
            exclude.append(_glob_regex(token[1:]))
        else:
            include.append(_glob_regex(token))
    if not include:
        return set()
    matched = set()
    for prim in stage.Traverse():
        path = str(prim.GetPath())
        if any(r.match(path) for r in include) and not any(r.match(path) for r in exclude):
            matched.add(prim.GetPath())
    return matched


def expand_pattern(stage: Usd.Stage, pattern: str) -> set[Sdf.Path]:
    """The prims ``pattern`` names on ``stage``: Houdini's match where
    ``hou`` is already loaded (hython, a Houdini session), else
    :func:`glob_expand`. Never imported here: the submit runner is Houdini's
    bundled Python, which must take no licence."""
    hou = sys.modules.get('hou')
    rule = getattr(hou, 'LopSelectionRule', None)
    if rule is None:  # no Houdini, or a headless stand-in for hou
        return glob_expand(stage, pattern)
    return {Sdf.Path(path) for path in rule(pattern).expandedPaths(stage=stage)}


# --- layers to passes --------------------------------------------------------

def top_most(paths) -> set[Sdf.Path]:
    """``paths`` less any path under another one of them."""
    ordered = sorted({Sdf.Path(str(p)) for p in paths}, key=lambda p: p.pathElementCount)
    kept: list[Sdf.Path] = []
    for path in ordered:
        if not any(path.HasPrefix(root) for root in kept):
            kept.append(path)
    return set(kept)


def geometry_roots(stage: Usd.Stage, roots) -> set[Sdf.Path]:
    """The ``roots`` that hold something drawn (a gprim or point instancer,
    instance proxies included): a remainder layer's cameras, lights and
    render prims have no business in a holdout collection."""
    roots = {Sdf.Path(str(r)) for r in roots}
    found = set()
    for prim in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies()):
        if not (prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.PointInstancer)):
            continue
        for prefix in prim.GetPath().GetPrefixes():
            if prefix in roots:
                found.add(prefix)
                break
    return found


def resolve_layers(
    stage: Usd.Stage, layers: list[tuple[str, str | None]],
) -> tuple[list[tuple[str, set[Sdf.Path]]], set[Sdf.Path]]:
    """``([(channel, its prims)], unclaimed)`` for ``channel_layers``
    output, layers bottom to top. The remainder layer (pattern ``None``)
    gets every drawn prim no pattern claims; without one, those prims are
    ``unclaimed``: in no layer, as in a layered playblast."""
    from tumblepipe.pipe.usd import layer_split

    matched = {
        name: top_most(expand_pattern(stage, pattern))
        for name, pattern in layers if pattern is not None
    }
    claimed = set().union(*matched.values()) if matched else set()
    rest = geometry_roots(stage, layer_split.remainder_keep(stage, claimed))
    resolved = []
    has_remainder = False
    for name, pattern in layers:
        if pattern is None:
            prims, has_remainder = rest, True
        else:
            prims = matched[name]
        resolved.append((name, prims))
    return resolved, (set() if has_remainder else rest)


@dataclass(frozen=True)
class LayerPass:
    """One layer's pass: what it holds out and hides, and what is its own."""
    name: str
    own: tuple[Sdf.Path, ...]
    matte: tuple[Sdf.Path, ...]
    hidden: tuple[Sdf.Path, ...]


def layer_passes(
    resolved: list[tuple[str, set[Sdf.Path]]], unclaimed=(),
) -> list[LayerPass]:
    """Each layer's pass: the layers above held out, the layers below and
    the ``unclaimed`` prims hidden from camera (still shadowing)."""
    passes = []
    for index, (name, own) in enumerate(resolved):
        above = set().union(*(prims for _, prims in resolved[index + 1:]))
        below = set().union(*(prims for _, prims in resolved[:index]), unclaimed)
        # Overlapping patterns: what lies inside this layer's own prims is
        # its own. (A path above one of them is handled by the collections'
        # own-prims exclude/include, the more specific rule.)
        above = {p for p in above if not any(p.HasPrefix(o) for o in own)}
        below = {p for p in below if not any(p.HasPrefix(o) for o in own)}
        passes.append(LayerPass(
            name=name,
            own=tuple(sorted(top_most(own))),
            matte=tuple(sorted(top_most(above))),
            hidden=tuple(sorted(top_most(below))),
        ))
    return passes


def author_passes(stage: Usd.Stage, passes: list[LayerPass], settings_prim_path: str) -> None:
    """Author ``passes`` as RenderPass prims at the stage's edit target,
    each rendering through ``settings_prim_path``."""
    for layer_pass in passes:
        render_pass = UsdRender.Pass.Define(stage, render_pass_path(layer_pass.name))
        render_pass.CreateRenderSourceRel().SetTargets([Sdf.Path(settings_prim_path)])
        prim = render_pass.GetPrim()

        matte = Usd.CollectionAPI.Apply(prim, 'matte')
        matte.CreateIncludeRootAttr(False)
        if layer_pass.matte:
            matte.CreateIncludesRel().SetTargets(list(layer_pass.matte))
            if layer_pass.own:
                matte.CreateExcludesRel().SetTargets(list(layer_pass.own))

        camera = Usd.CollectionAPI.Apply(prim, 'cameraVisibility')
        camera.CreateIncludeRootAttr(True)
        if layer_pass.hidden:
            camera.CreateExcludesRel().SetTargets(list(layer_pass.hidden))
            if layer_pass.own:
                camera.CreateIncludesRel().SetTargets(list(layer_pass.own))


def apply_render_layers(
    stage: Usd.Stage, layers: list[tuple[str, str | None]], settings_prim_path: str,
) -> list[LayerPass]:
    """Expand ``layers`` on ``stage`` and author their passes at its edit
    target. Returns the passes authored (none for no layers)."""
    if not layers:
        return []
    passes = layer_passes(*resolve_layers(stage, layers))
    author_passes(stage, passes, settings_prim_path)
    return passes


def apply_render_layers_to_usda(
    usda_content: str, layers: list[tuple[str, str | None]], settings_prim_path: str,
) -> str:
    """``usda_content`` (a root layer) with ``layers``' passes authored in
    it. Composed, not appended as text: the content may already hold an
    ``over`` of the settings prim's parent."""
    if not layers:
        return usda_content
    layer = Sdf.Layer.CreateAnonymous('.usda')
    if not layer.ImportFromString(usda_content):
        raise RenderPassError('the collapsed stage does not parse as USDA')
    stage = Usd.Stage.Open(layer, Usd.Stage.LoadAll)
    apply_render_layers(stage, layers, settings_prim_path)
    return layer.ExportToString()
