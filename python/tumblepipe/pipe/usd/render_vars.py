"""Refuse a render whose planes the farm cannot read.

The farm splits a rendered EXR by plane name and keeps the planes named in
the export's ``aov_names``: each render var's prim name, lowercased
(``pipe.aovs.pipeline_aov_name``). Karma names a plane after the var's
``driver:parameters:aov:name``, so a var whose plane is named anything else
renders a plane the farm never finds (Martin, 2026-11-05: Karma's own Beauty
toggle writes ``Beauty`` as plane ``C``, and the render failed with beauty
missing). Two vars of one AOV name render two planes of that name (Karma's
Albedo toggle beside the project's ``albedo``), and husk keeps one of them.

The farm never repairs either: what renders is what the render department
published. ``render_var_problems`` lists what is wrong, and
``check_render_vars`` refuses with the fix spelled out, at submit (collapse)
and in the farm's stage task.
"""

from pxr import Sdf, Usd, UsdRender

from tumblepipe.pipe.aovs import pipeline_aov_name

AOV_NAME_ATTR = 'driver:parameters:aov:name'

FIX = (
    "Karma's own AOV toggles (on Karma Render Properties or Karma Render "
    "Settings) author vars like these: switch them off and tick the AOV on "
    "th::render_vars, which names the var and its plane alike, then publish "
    "the render department again."
)


class RenderVarsError(ValueError):
    """The render vars would render planes the farm cannot read."""


def _products(stage: Usd.Stage, settings_prim_path: str) -> list[Sdf.Path]:
    settings = UsdRender.Settings(stage.GetPrimAtPath(settings_prim_path))
    if settings:
        targets = settings.GetProductsRel().GetForwardedTargets()
        if targets:
            return list(targets)
    # No products named: husk renders every product on the stage.
    return [prim.GetPath() for prim in stage.Traverse()
            if prim.IsA(UsdRender.Product)]


def plane_problem(var_path: str, plane) -> str | None:
    """What is wrong with a var at ``var_path`` writing plane ``plane``
    (None: no plane name), or None. The farm reads planes case-blind."""
    want = pipeline_aov_name(var_path.rsplit('/', 1)[-1])
    if not plane:
        return f'{var_path} names no plane ({AOV_NAME_ATTR}); the farm reads {want!r}'
    if str(plane).lower() != want:
        return f'{var_path} writes plane {plane!r}; the farm reads {want!r}'
    return None


def twin_problems(var_paths) -> list[str]:
    """One line per pair of vars in ``var_paths`` that make the same AOV."""
    seen: dict[str, str] = {}
    problems = []
    for path in var_paths:
        aov = pipeline_aov_name(str(path).rsplit('/', 1)[-1])
        if aov in seen:
            problems.append(f'{seen[aov]} and {path} are both the AOV {aov!r}')
        else:
            seen[aov] = str(path)
    return problems


def render_var_problems(stage: Usd.Stage, settings_prim_path: str) -> list[str]:
    """What the products the settings render would get wrong, one line each."""
    problems = []
    for product_path in _products(stage, settings_prim_path):
        product = UsdRender.Product(stage.GetPrimAtPath(product_path))
        if not product:
            continue
        targets = list(product.GetOrderedVarsRel().GetForwardedTargets())
        for target in targets:
            prim = stage.GetPrimAtPath(target)
            if not prim:
                continue
            attr = prim.GetAttribute(AOV_NAME_ATTR)
            plane = attr.Get() if attr and attr.HasValue() else None
            problem = plane_problem(str(target), plane)
            if problem is not None:
                problems.append(f'{product_path}: {problem}')
        problems.extend(f'{product_path}: {twin}' for twin in twin_problems(targets))
    return problems


def check_render_vars(stage: Usd.Stage, settings_prim_path: str) -> None:
    """Raise ``RenderVarsError`` naming every problem and the fix."""
    problems = render_var_problems(stage, settings_prim_path)
    if problems:
        raise RenderVarsError(
            'the render vars would render planes the farm cannot read:\n  '
            + '\n  '.join(problems) + '\n' + FIX
        )


def check_render_vars_in_usda(usda_content: str, settings_prim_path: str) -> None:
    """``check_render_vars`` on a root layer's text."""
    layer = Sdf.Layer.CreateAnonymous('.usda')
    if not layer.ImportFromString(usda_content):
        raise ValueError('the collapsed stage does not parse as USDA')
    # Render vars are never under a payload: no need to load the shot.
    check_render_vars(Usd.Stage.Open(layer, Usd.Stage.LoadNone), settings_prim_path)
