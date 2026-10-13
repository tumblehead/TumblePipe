"""Itemise what a USD stage asks the viewport to draw.

Walks a composed stage the way Houdini's viewport would see it — visible,
non-guide prims; native instances counted per prototype; point-instanced
prototypes counted once with their instance count — and records, per
geometry prim, its points, topology and every primvar, sized by
:mod:`.sizes`. Materials bound to those prims are followed to the texture
files their shaders read.

Needs ``pxr`` but not ``hou``: who *owns* a prim, how an image's header is
read and which viewport rules apply are passed in, so the scan runs the
same in a GUI session, in hython, and against a stage built in a test.

The walk is incremental — :meth:`StageScan.step` does a bounded amount of
work and returns — because a lighting stage can hold tens of thousands of
prims, and the window must stay responsive while it reads them on the main
thread (a composed LOP stage is not safe to read from a worker while the
network may recook).
"""

from __future__ import annotations

import glob
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from pxr import Sdf, Usd, UsdGeom, UsdShade

from . import sizes
from .report import GeoPrim, Primvar, Texture
from .sizes import ImageInfo, TextureRules

#: Primvars the viewport always draws with, whatever the material reads.
ALWAYS_UPLOADED = frozenset({
    "points", "normals", "primvars:normals", "primvars:displayColor",
    "primvars:displayOpacity", "primvars:st", "primvars:uv",
})

#: Shader inputs that name the primvar a reader node fetches.
_READER_INPUTS = ("varname", "geomprop", "primvarName", "primvar_name")

_UDIM_RE = re.compile(r"<UDIM>|%\(UDIM\)d|\$F4|<udim>", re.IGNORECASE)


@dataclass
class ScanOptions:
    """What to include, and the viewport rules sizes are judged by."""

    time: Usd.TimeCode = field(default_factory=Usd.TimeCode.Default)
    purposes: frozenset[str] = frozenset({"default", "proxy"})
    rules: TextureRules = field(default_factory=TextureRules)


def default_owner(prim: Usd.Prim) -> str:
    """No node lookup available: blame the top-level prim it lives under."""
    parts = prim.GetPath().pathString.split("/")
    return "/" + parts[1] if len(parts) > 1 and parts[1] else "/"


def editor_node_ids(prim: Usd.Prim) -> list[int]:
    """The session ids of the LOP nodes Houdini records as editing ``prim``
    (``customData["HoudiniPrimEditorNodes"]``), oldest first."""
    ids = prim.GetCustomDataByKey("HoudiniPrimEditorNodes")
    if not ids:
        return []
    try:
        return [int(i) for i in ids]
    except (TypeError, ValueError):
        return []


def expand_udims(path: str) -> list[str]:
    """The tile files a UDIM path names, or ``[path]`` if it names none."""
    if not _UDIM_RE.search(path):
        return [path]
    # Escape the literal parts only, so a "[" in a folder name stays literal.
    pattern = "[1-9][0-9][0-9][0-9]".join(
        glob.escape(part) for part in _UDIM_RE.split(path))
    return sorted(glob.glob(pattern))


class StageScan:
    """An incremental scan of one stage. Drive it with :meth:`step`."""

    def __init__(self, stage: Usd.Stage, options: ScanOptions | None = None, *,
                 owner_of: Callable[[Usd.Prim], str] = default_owner,
                 image_info: Callable[[str], ImageInfo | None] | None = None):
        self.stage = stage
        self.options = options or ScanOptions()
        self.owner_of = owner_of
        self.image_info = image_info or (lambda _path: None)
        self.prims: list[GeoPrim] = []
        self.visited = 0
        self.done = False
        self._materials: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {}
        self._textures: dict[str, Texture] = {}
        self._tex_users: dict[str, list[str]] = {}
        self._instancer_counts: dict[str, int] = {}
        self._work = self._walk()

    @property
    def textures(self) -> list[Texture]:
        """Every texture file found so far, each listing the materials using it."""
        return [Texture(p, t.info, t.data_bytes, t.upload_bytes,
                        tuple(self._tex_users.get(p, ())))
                for p, t in self._textures.items()]

    def step(self, max_prims: int = 200) -> bool:
        """Visit up to ``max_prims`` more prims. Returns True once finished."""
        if self.done:
            return True
        for _ in range(max_prims):
            if next(self._work, None) is None:
                self.done = True
                break
        return self.done

    def run(self) -> StageScan:
        """Scan to the end in one go (hython, tests)."""
        while not self.step(10_000):
            pass
        return self

    # ── walk ────────────────────────────────────────────────────────────

    def _walk(self) -> Iterator[bool]:
        prototypes: dict[str, int] = {}
        it = iter(Usd.PrimRange.Stage(self.stage, Usd.PrimDefaultPredicate))
        for prim in it:
            self.visited += 1
            if not self._shown(prim):
                it.PruneChildren()
                yield True
                continue
            if prim.IsInstance():
                proto = prim.GetPrototype()
                if proto:
                    key = proto.GetPath().pathString
                    prototypes[key] = prototypes.get(key, 0) + 1
            if prim.IsA(UsdGeom.PointInstancer):
                self._note_instancer(prim)
            if prim.IsA(UsdGeom.Gprim):
                self._record(prim, self._instances_for(prim))
            yield True

        for proto in self.stage.GetPrototypes():
            count = prototypes.get(proto.GetPath().pathString, 0)
            if not count:
                continue
            for prim in Usd.PrimRange(proto):
                self.visited += 1
                if prim.IsA(UsdGeom.Gprim) and self._shown(prim):
                    self._record(prim, count)
                yield True

    def _shown(self, prim: Usd.Prim) -> bool:
        """Visible and of a drawn purpose, judged on the prim's own opinion
        (an invisible or guide ancestor has already pruned it)."""
        if not prim.IsA(UsdGeom.Imageable):
            return True
        img = UsdGeom.Imageable(prim)
        vis = img.GetVisibilityAttr().Get(self.options.time)
        if vis == UsdGeom.Tokens.invisible:
            return False
        purpose = img.GetPurposeAttr().Get()
        return not purpose or purpose == "default" or purpose in self.options.purposes

    def _note_instancer(self, prim: Usd.Prim) -> None:
        inst = UsdGeom.PointInstancer(prim)
        targets = inst.GetPrototypesRel().GetTargets()
        indices = inst.GetProtoIndicesAttr().Get(self.options.time) or []
        counts = [0] * len(targets)
        for i in indices:
            if 0 <= i < len(counts):
                counts[i] += 1
        for target, n in zip(targets, counts):
            self._instancer_counts[target.pathString] = n

    def _instances_for(self, prim: Usd.Prim) -> int:
        """How many times a point instancer draws ``prim`` (1 if none does)."""
        path = prim.GetPath()
        total = 1
        for root, n in self._instancer_counts.items():
            if path.HasPrefix(Sdf.Path(root)):
                total *= max(n, 0)
        return total

    # ── per prim ────────────────────────────────────────────────────────

    def _record(self, prim: Usd.Prim, instances: int) -> None:
        if instances == 0:
            return  # a prototype no instance points at is never drawn
        t = self.options.time
        material, read_vars = self._material(prim)
        primvars: list[Primvar] = []

        points = 0
        pts_attr = prim.GetAttribute("points")
        if pts_attr and pts_attr.HasValue():
            pts = pts_attr.Get(t)
            points = len(pts) if pts is not None else 0
            primvars.append(self._plain("points", pts_attr, points, "vertex"))
        nrm_attr = prim.GetAttribute("normals")
        if nrm_attr and nrm_attr.HasValue():
            nrm = nrm_attr.Get(t)
            interp = UsdGeom.PointBased(prim).GetNormalsInterpolation() \
                if prim.IsA(UsdGeom.PointBased) else "vertex"
            primvars.append(self._plain("normals", nrm_attr,
                                        len(nrm) if nrm is not None else 0, interp))

        for pv in UsdGeom.PrimvarsAPI(prim).GetPrimvarsWithValues():
            primvars.append(self._primvar(pv, read_vars))

        if prim.IsA(UsdGeom.Mesh) and points and not any(
                p.name in ("normals", "N") for p in primvars):
            # Nothing authored: the viewport computes per-point normals and
            # uploads them like authored ones (measured: same bytes either way).
            primvars.append(Primvar("normals (generated)", "normal3f[]", "vertex",
                                    points, 0, 0, points * 12))

        faces = face_vertices = 0
        topo_data = topo_up = overhead = 0
        if prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            fvc = mesh.GetFaceVertexCountsAttr().Get(t)
            faces = len(fvc) if fvc is not None else 0
            fvi = mesh.GetFaceVertexIndicesAttr().Get(t)
            face_vertices = len(fvi) if fvi is not None else 0
            topo_data, topo_up = sizes.topology_bytes(faces, face_vertices)
            overhead = sizes.mesh_overhead_bytes(
                points, sizes.triangle_count(faces, face_vertices))
        elif prim.IsA(UsdGeom.Curves):
            cvc = UsdGeom.Curves(prim).GetCurveVertexCountsAttr().Get(t)
            faces = len(cvc) if cvc is not None else 0
            face_vertices = points
            topo_data = faces * 4
            topo_up = points * 4

        self.prims.append(GeoPrim(
            path=prim.GetPath().pathString,
            type_name=str(prim.GetTypeName()),
            owner=self.owner_of(prim),
            points=points,
            faces=faces,
            face_vertices=face_vertices,
            topology_data=topo_data,
            topology_upload=topo_up,
            primvars=tuple(primvars),
            instances=instances,
            material=material,
            overhead_upload=overhead,
        ))

    def _plain(self, name: str, attr: Usd.Attribute, count: int, interp: str) -> Primvar:
        type_name = str(attr.GetTypeName())
        data, up = sizes.primvar_bytes(type_name, count)
        return Primvar(name, type_name, interp, count, 0, data, up)

    def _primvar(self, pv: UsdGeom.Primvar, read_vars: frozenset[str]) -> Primvar:
        t = self.options.time
        attr_name = pv.GetAttr().GetName()
        type_name = str(pv.GetTypeName())
        value = pv.Get(t)
        try:
            values = len(value) if pv.GetTypeName().isArray and value is not None else 1
        except TypeError:
            values = 1
        indices = 0
        if pv.IsIndexed():
            idx = pv.GetIndices(t)
            indices = len(idx) if idx is not None else 0
        data, up = sizes.primvar_bytes(type_name, values, indices)
        base = pv.GetPrimvarName()
        if attr_name not in ALWAYS_UPLOADED and base not in read_vars:
            up = 0  # authored, but nothing the viewport draws reads it
        elif str(pv.GetInterpolation()) == UsdGeom.Tokens.faceVarying:
            up += (indices or values) * sizes.FACEVARYING_EXTRA_BYTES
        return Primvar(attr_name.removeprefix("primvars:"), type_name,
                       str(pv.GetInterpolation()), values, indices, data, up)

    # ── materials and textures ──────────────────────────────────────────

    def _material(self, prim: Usd.Prim) -> tuple[str, frozenset[str]]:
        """The bound material's path and the primvars its shaders read;
        records its textures on first sight."""
        try:
            mat, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        except Exception:
            return "", frozenset()
        if not mat:
            return "", frozenset()
        key = mat.GetPath().pathString
        if key not in self._materials:
            self._materials[key] = self._read_material(mat)
            for tex_path in self._materials[key][0]:
                users = self._tex_users.setdefault(tex_path, [])
                if key not in users:
                    users.append(key)
        return key, self._materials[key][1]

    def _read_material(self, mat: UsdShade.Material) -> tuple[tuple[str, ...], frozenset[str]]:
        """Texture files and primvar names the *drawn* shaders of ``mat`` read.

        Only shaders upstream of the surface (and displacement) output the
        viewport renders count. Houdini writes a UsdPreviewSurface fallback
        beside every MaterialX network, and it can point at a file the
        MaterialX side no longer uses — measured: such a file is never
        loaded — so walking every shader under the material over-counts.
        """
        files: list[str] = []
        read_vars: set[str] = set()
        for shader in self._drawn_shaders(mat):
            for inp in shader.GetInputs():
                value = inp.Get(self.options.time)
                if isinstance(value, Sdf.AssetPath):
                    path = value.resolvedPath or value.path
                    if path and path not in files:
                        files.append(path)
                elif inp.GetBaseName() in _READER_INPUTS and isinstance(value, str) and value:
                    read_vars.add(value)
        for path in files:
            if path not in self._textures:
                self._textures[path] = self._texture(path)
        return tuple(files), frozenset(read_vars)

    def _drawn_shaders(self, mat: UsdShade.Material) -> list[UsdShade.Shader]:
        """Shaders reachable upstream from the outputs the viewport uses:
        the ``mtlx`` or ``kma`` (Karma) render context when the material has
        one, else the universal one. Node graphs are seen through to the shaders inside."""
        roots: list[UsdShade.Shader] = []
        for compute in (mat.ComputeSurfaceSource, mat.ComputeDisplacementSource):
            # One render-context token per call; each falls back to the
            # universal output when the material has none of its own, so a
            # context-specific output is taken only if it differs from that.
            # Karma's material builder writes "kma:surface" (45 of the 49
            # materials in a HideAndReek lighting shot) and the viewport
            # draws it, so it outranks the universal preview fallback.
            universal, _n, _t = compute("")
            chosen = universal
            for context in ("mtlx", "kma"):
                shader, _n, _t = compute(context)
                if shader and (not universal or shader.GetPath() != universal.GetPath()):
                    chosen = shader
                    break
            if chosen:
                roots.append(chosen)
        if not roots:
            # No output connected at all: fall back to every shader in it.
            return [UsdShade.Shader(p) for p in Usd.PrimRange(mat.GetPrim())
                    if UsdShade.Shader(p)]
        seen: set[str] = set()
        out: list[UsdShade.Shader] = []
        todo = list(roots)
        while todo:
            shader = todo.pop()
            key = shader.GetPath().pathString
            if key in seen:
                continue
            seen.add(key)
            out.append(shader)
            for inp in shader.GetInputs():
                for attr in UsdShade.Utils.GetValueProducingAttributes(inp):
                    upstream = UsdShade.Shader(attr.GetPrim())
                    if upstream and upstream.GetPath().pathString not in seen:
                        todo.append(upstream)
        return out

    def _texture(self, path: str) -> Texture:
        tiles = expand_udims(path)
        first = tiles[0] if tiles else path
        info = self.image_info(first) if os.path.isfile(first) else None
        if info is not None and len(tiles) > 1:
            info = ImageInfo(info.width, info.height, info.channels,
                             info.channel_bytes, len(tiles))
        if info is None:
            return Texture(path, None, 0, 0)
        return Texture(path, info, sizes.image_bytes(info),
                       sizes.texture_upload_bytes(info, self.options.rules))
