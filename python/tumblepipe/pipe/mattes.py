"""Author mattes and distance ramps onto a USD stage (the body of ``th::mattes``).

Two kinds of single-channel AOV, both for grading:

- **Matte** ``objid_<name>``: 1 on the matte's prims, 0 elsewhere. A constant
  float primvar ``objid_<name>`` is set on each matched prim (primvars
  inherit, so a prim's descendants carry it too) and a primvar RenderVar
  reads it -- the mechanism ``th::puzzlemattes`` already uses, one channel
  instead of three. A matched GeomSubset (one material's faces, e.g. a
  bench's planks) gets a per-face ``uniform`` primvar on its mesh instead.
- **Distance ramp** ``ramp_<name>``: 0 at ``near``, 1 at ``far``, clamped;
  the sky reads 1. Karma evaluates it through a *global AOV material*
  (``karma:global:globalaovmaterial``): a Karma Ray Import of
  ``ray:hitdist`` (distance from the camera) or ``ray:hitPz`` (depth along
  the view axis) through a MaterialX range node into an ``aov:`` input of an
  unlit surface. Verified on Karma XPU, GPU and CPU (2026-10-08).

A render has ONE global AOV material, so this module owns the one at
``MATERIAL_PATH`` and refuses to replace a different one already set.

Pure ``pxr``: the Houdini side (``pipe/houdini/lops/mattes.py``) resolves the
prim patterns and hands the paths in.
"""

from dataclasses import dataclass, field
import json
import re

from pxr import Sdf, Usd, UsdGeom, UsdShade

from tumblepipe.pipe.aovs import RAMP_AOV_PREFIX

MATERIAL_PATH = '/Render/tumblepipe/mattes_aov_material'
VARS_SCOPE = '/Render/Products/Vars'
MATTE_PREFIX = 'objid_'  # one of pipe.aovs.MATTE_AOV_PREFIXES
RAMP_PREFIX = RAMP_AOV_PREFIX
MEASURES = {
    'distance': 'ray:hitdist',  # Euclidean distance from the camera
    'depth': 'ray:hitPz',       # depth along the camera's view axis
}
COMPRESSION_ATTR = 'driver:parameters:aov:husk:OpenEXR:compression'
GLOBAL_AOV_ATTR = 'karma:global:globalaovmaterial'
_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class MattesError(ValueError):
    """The entries can't be authored as given; the message says why."""


@dataclass
class Matte:
    name: str
    prim_paths: list[str] = field(default_factory=list)
    compression: str = 'zip'

    @property
    def aov(self) -> str:
        return MATTE_PREFIX + self.name


@dataclass
class Ramp:
    name: str
    near: float = 0.0
    far: float = 10.0
    measure: str = 'distance'
    compression: str = 'zip'

    @property
    def aov(self) -> str:
        return RAMP_PREFIX + self.name


def validate(mattes: list[Matte], ramps: list[Ramp]) -> None:
    """Raise MattesError for a name, range or measure that can't be authored."""
    seen = set()
    for entry in [*mattes, *ramps]:
        if not _NAME.match(entry.name or ''):
            raise MattesError(
                f'"{entry.name}" is not a usable name: use letters, digits '
                'and _, not starting with a digit'
            )
        if entry.aov in seen:
            raise MattesError(f'{entry.aov} is defined twice')
        seen.add(entry.aov)
    for ramp in ramps:
        if ramp.measure not in MEASURES:
            raise MattesError(
                f'ramp "{ramp.name}": unknown measure "{ramp.measure}" '
                f'(use {", ".join(MEASURES)})'
            )
        if not ramp.far > ramp.near:
            raise MattesError(
                f'ramp "{ramp.name}": Far ({ramp.far}) must be greater than '
                f'Near ({ramp.near})'
            )


def _render_var(stage: Usd.Stage, aov: str, source_name: str, source_type: str,
                data_format: str, compression: str, background: float | None):
    var = stage.DefinePrim(Sdf.Path(f'{VARS_SCOPE}/{aov}'), 'RenderVar')
    var.CreateAttribute('dataType', Sdf.ValueTypeNames.Token, variability=Sdf.VariabilityUniform).Set('float')
    var.CreateAttribute('sourceName', Sdf.ValueTypeNames.String, variability=Sdf.VariabilityUniform).Set(source_name)
    var.CreateAttribute('sourceType', Sdf.ValueTypeNames.Token, variability=Sdf.VariabilityUniform).Set(source_type)
    var.CreateAttribute('driver:parameters:aov:name', Sdf.ValueTypeNames.String).Set(aov)
    var.CreateAttribute('driver:parameters:aov:husk:format', Sdf.ValueTypeNames.Token).Set(data_format)
    var.CreateAttribute('driver:parameters:aov:karma:filter', Sdf.ValueTypeNames.String).Set(
        json.dumps(['ubox', {}]))
    var.CreateAttribute(COMPRESSION_ATTR, Sdf.ValueTypeNames.String).Set(compression)
    if background is not None:
        # Misses never run a shader: the sky keeps the buffer's background.
        # Karma reads defaultvalue; husk's clearValue alone is not enough.
        var.CreateAttribute('driver:parameters:aov:karma:defaultvalue', Sdf.ValueTypeNames.Float).Set(background)
        var.CreateAttribute('driver:parameters:aov:husk:clearValue', Sdf.ValueTypeNames.Float).Set(background)
    return var


def _add_to_products(stage: Usd.Stage, settings_path: str, var_paths: list[str]) -> None:
    # Upstream of th::render_settings there is no product yet; it collects
    # every RenderVar under the Vars scope into the product it makes.
    settings = stage.GetPrimAtPath(settings_path)
    products = settings.GetRelationship('products').GetTargets() if settings else []
    for product_path in products:
        product = stage.GetPrimAtPath(product_path)
        if not product:
            continue
        ordered = product.GetRelationship('orderedVars') or product.CreateRelationship('orderedVars')
        targets = ordered.GetTargets()
        for path in var_paths:
            if Sdf.Path(path) not in targets:
                ordered.AddTarget(Sdf.Path(path))


def _author_ramp_material(stage: Usd.Stage, ramps: list[Ramp]) -> None:
    material = UsdShade.Material.Define(stage, Sdf.Path(MATERIAL_PATH))
    material.GetPrim().CreateAttribute('config:mtlx:version', Sdf.ValueTypeNames.String).Set('1.39')
    surface = UsdShade.Shader.Define(stage, Sdf.Path(f'{MATERIAL_PATH}/surface'))
    surface.CreateIdAttr('ND_surface_unlit')
    surface_out = surface.CreateOutput('out', Sdf.ValueTypeNames.Token)
    material.CreateOutput('kma:surface', Sdf.ValueTypeNames.Token).ConnectToSource(surface_out)
    for ramp in ramps:
        hit = UsdShade.Shader.Define(stage, Sdf.Path(f'{MATERIAL_PATH}/hit_{ramp.name}'))
        hit.CreateIdAttr('kma_rayimport_float')
        hit.CreateInput('name', Sdf.ValueTypeNames.String).Set(MEASURES[ramp.measure])
        hit_out = hit.CreateOutput('out', Sdf.ValueTypeNames.Float)

        rng = UsdShade.Shader.Define(stage, Sdf.Path(f'{MATERIAL_PATH}/range_{ramp.name}'))
        rng.CreateIdAttr('ND_range_float')
        rng.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(hit_out)
        rng.CreateInput('inlow', Sdf.ValueTypeNames.Float).Set(float(ramp.near))
        rng.CreateInput('inhigh', Sdf.ValueTypeNames.Float).Set(float(ramp.far))
        rng.CreateInput('outlow', Sdf.ValueTypeNames.Float).Set(0.0)
        rng.CreateInput('outhigh', Sdf.ValueTypeNames.Float).Set(1.0)
        rng.CreateInput('doclamp', Sdf.ValueTypeNames.Int).Set(1)
        rng_out = rng.CreateOutput('out', Sdf.ValueTypeNames.Float)

        surface.CreateInput(f'aov:{ramp.aov}', Sdf.ValueTypeNames.Float).ConnectToSource(rng_out)
        material.CreateOutput(ramp.aov, Sdf.ValueTypeNames.Float).ConnectToSource(rng_out)


def _author_subset_mattes(aov: str, subsets: list) -> None:
    """Matte the faces of GeomSubsets: a per-face primvar on each parent mesh.

    A GeomSubset is not renderable, so a primvar on it reaches no AOV. Its
    mesh gets a uniform ``aov`` primvar instead: 1 on the faces the subsets
    list, 0 on the rest (the planks of a bench mesh whose legs and nails are
    other subsets). Several subsets of one mesh merge into one array. A mesh
    the matte already covers whole keeps its constant 1.
    """
    faces_by_mesh = {}
    for subset in subsets:
        mesh_prim = subset.GetPrim().GetParent()
        if not mesh_prim.IsA(UsdGeom.Mesh):
            continue
        if subset.GetElementTypeAttr().Get() != UsdGeom.Tokens.face:
            continue
        faces_by_mesh.setdefault(mesh_prim.GetPath(), (mesh_prim, set()))[1].update(
            int(index) for index in (subset.GetIndicesAttr().Get() or []))
    for mesh_prim, faces in faces_by_mesh.values():
        primvars = UsdGeom.PrimvarsAPI(mesh_prim)
        existing = primvars.GetPrimvar(aov)
        if existing and existing.GetInterpolation() == UsdGeom.Tokens.constant:
            continue
        face_count = len(UsdGeom.Mesh(mesh_prim).GetFaceVertexCountsAttr().Get() or [])
        values = [0.0] * face_count
        for index in faces:
            if 0 <= index < face_count:
                values[index] = 1.0
        primvar = primvars.CreatePrimvar(aov, Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.uniform)
        primvar.Set(values)


def author(stage: Usd.Stage, mattes: list[Matte], ramps: list[Ramp], settings_path: str) -> list[str]:
    """Author every matte and ramp; return the RenderVar paths added.

    Works before or after th::render_settings: with a product already on the
    settings the vars are added to its orderedVars, otherwise the settings
    node collects them. Raises MattesError for unusable entries or a
    different global AOV material already set on the settings.
    """
    validate(mattes, ramps)
    var_paths = []

    for matte in mattes:
        subsets = []
        for prim_path in matte.prim_paths:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim:
                continue
            if prim.IsA(UsdGeom.Subset):
                subsets.append(UsdGeom.Subset(prim))
                continue
            primvar = UsdGeom.PrimvarsAPI(prim).CreatePrimvar(
                matte.aov, Sdf.ValueTypeNames.Float, UsdGeom.Tokens.constant)
            primvar.Set(1.0)
        _author_subset_mattes(matte.aov, subsets)
        var = _render_var(stage, matte.aov, matte.aov, 'primvar', 'half',
                          matte.compression, background=None)
        var_paths.append(str(var.GetPath()))

    if ramps:
        # Placed before th::render_settings the settings prim doesn't exist
        # yet: an `over` holds the attribute and survives the settings node,
        # which never authors it (checked in H22.0.368, 2026-10-08).
        settings = stage.GetPrimAtPath(settings_path) or stage.OverridePrim(Sdf.Path(settings_path))
        current = settings.GetAttribute(GLOBAL_AOV_ATTR)
        value = current.Get() if current and current.HasAuthoredValue() else ''
        if value and value != MATERIAL_PATH:
            raise MattesError(
                f'the render settings already use {value} as their global AOV '
                'material; a render has only one, so distance ramps cannot be added'
            )
        _author_ramp_material(stage, ramps)
        settings.CreateAttribute(GLOBAL_AOV_ATTR, Sdf.ValueTypeNames.String).Set(MATERIAL_PATH)
        for ramp in ramps:
            var = _render_var(stage, ramp.aov, ramp.aov, 'raw', 'float',
                              ramp.compression, background=1.0)
            var_paths.append(str(var.GetPath()))

    if var_paths:
        _add_to_products(stage, settings_path, var_paths)
    return var_paths
