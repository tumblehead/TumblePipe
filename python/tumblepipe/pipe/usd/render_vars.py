"""Refuse a render whose planes the farm cannot read.

The farm splits a rendered EXR by plane name and keeps the planes named in
the export's ``aov_names``: each render var's prim name, lowercased
(``pipe.aovs.pipeline_aov_name``). Karma names a plane after the var's
``driver:parameters:aov:name``, so a var whose plane is named anything else
renders a plane the farm never finds (Martin, 2026-11-05: Karma's own Beauty
toggle writes ``Beauty`` as plane ``C``, and the render failed with beauty
missing). Two vars of one AOV name render two planes of that name (Karma's
Albedo toggle beside the project's ``albedo``), and husk keeps one of them.

A cryptomatte var renders more than one plane (a preview ``X``, ranks
``X00``, ``X01``, ... and its manifest in the header), which the farm keeps
together only for a var named ``crypto*`` (``pipe.aovs.is_cryptomatte_aov``),
so a cryptomatte var must be named so and nothing else may be. Karma's own
Cryptomatte LOP names its plane in ``driver:parameters:aov:husk:name``
rather than ``driver:parameters:aov:name``: either names the plane.

The farm never repairs either: what renders is what the render department
published. ``render_var_problems`` lists what is wrong, and
``check_render_vars`` refuses with the fix spelled out, at submit (collapse)
and in the farm's stage task.

``pxr`` is imported inside the functions that read a stage, never at module
scope: ``farm.pipelines.preview`` imports :class:`RenderVarsError` from here,
and the plain-Python half of the collapse worker imports ``preview`` in the
farm's package env, which has no USD (v1.94.0 failed every collapse job with
``No module named 'pxr'``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tumblepipe.pipe.aovs import (
    CRYPTOMATTE_AOV_PREFIX,
    is_cryptomatte_aov,
    pipeline_aov_name,
)

if TYPE_CHECKING:
    from pxr import Sdf, Usd

AOV_NAME_ATTR = 'driver:parameters:aov:name'
HUSK_NAME_ATTR = 'driver:parameters:aov:husk:name'
PLANE_NAME_ATTRS = (AOV_NAME_ATTR, HUSK_NAME_ATTR)
CRYPTOMATTE_ATTR = 'driver:parameters:aov:karma:cryptomatte'

FIX = (
    "Karma's own AOV toggles (on Karma Render Properties or Karma Render "
    "Settings) author vars like these: switch them off and tick the AOV on "
    "th::render_vars, which names the var and its plane alike, then publish "
    "the render department again. A cryptomatte (th::render_vars' "
    "cryptoprimitives / cryptomaterials, or Karma's Cryptomatte LOP) must be named "
    f"{CRYPTOMATTE_AOV_PREFIX}*, and only a cryptomatte may be."
)


class RenderVarsError(ValueError):
    """The render vars would render planes the farm cannot read."""


def _products(stage: Usd.Stage, settings_prim_path: str) -> list[Sdf.Path]:
    from pxr import UsdRender
    settings = UsdRender.Settings(stage.GetPrimAtPath(settings_prim_path))
    if settings:
        targets = settings.GetProductsRel().GetForwardedTargets()
        if targets:
            return list(targets)
    # No products named: husk renders every product on the stage.
    return [prim.GetPath() for prim in stage.Traverse()
            if prim.IsA(UsdRender.Product)]


def plane_problem(var_path: str, plane, cryptomatte: bool = False) -> str | None:
    """What is wrong with a var at ``var_path`` writing plane ``plane``
    (None: no plane name), a cryptomatte or not, or None. The farm reads
    planes case-blind."""
    name = var_path.rsplit('/', 1)[-1]
    want = pipeline_aov_name(name)
    if cryptomatte and not is_cryptomatte_aov(name):
        return (f'{var_path} is a cryptomatte, which the farm keeps whole only '
                f'when its name starts with {CRYPTOMATTE_AOV_PREFIX!r}')
    if not cryptomatte and is_cryptomatte_aov(name):
        return (f'{var_path} is named like a cryptomatte but is not one '
                f'({CRYPTOMATTE_ATTR} is off)')
    if not plane:
        return f'{var_path} names no plane ({AOV_NAME_ATTR}); the farm reads {want!r}'
    if str(plane).lower() != want:
        return f'{var_path} writes plane {plane!r}; the farm reads {want!r}'
    return None


def _value(prim, name):
    attr = prim.GetAttribute(name)
    return attr.Get() if attr and attr.HasValue() else None


def var_planes(prim) -> list:
    """The plane names a RenderVar prim gives (``aov:name``, then
    ``aov:husk:name``), those it sets. Only an authored ``aov:husk:name``
    counts: HuskRenderVarAPI's schema falls back to ``$OS`` on every Karma
    var, which names nothing (hython 22.0.429)."""
    planes = [_value(prim, AOV_NAME_ATTR)]
    husk = prim.GetAttribute(HUSK_NAME_ATTR)
    if husk and husk.HasAuthoredValue():
        planes.append(husk.Get())
    return [plane for plane in planes if plane]


def is_cryptomatte_var(prim) -> bool:
    """Whether Karma renders the RenderVar prim as a cryptomatte."""
    return bool(_value(prim, CRYPTOMATTE_ATTR))


def var_problems(prim) -> list[str]:
    """What is wrong with a RenderVar prim's planes (each it names must be
    its own), one line each."""
    path = str(prim.GetPath())
    cryptomatte = is_cryptomatte_var(prim)
    planes = var_planes(prim) or [None]
    problems = []
    for plane in planes:
        problem = plane_problem(path, plane, cryptomatte)
        if problem is not None and problem not in problems:
            problems.append(problem)
    return problems


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
    from pxr import UsdRender
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
            problems.extend(
                f'{product_path}: {problem}' for problem in var_problems(prim)
            )
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
    from pxr import Sdf, Usd
    layer = Sdf.Layer.CreateAnonymous('.usda')
    if not layer.ImportFromString(usda_content):
        raise ValueError('the collapsed stage does not parse as USDA')
    # Render vars are never under a payload: no need to load the shot.
    check_render_vars(Usd.Stage.Open(layer, Usd.Stage.LoadNone), settings_prim_path)
