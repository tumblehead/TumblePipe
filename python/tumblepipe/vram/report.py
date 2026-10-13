"""Join measured GPU clients and scanned scene data into one tree.

The scanners (:mod:`.usd_scan`, :mod:`.cop_scan`) produce flat records —
:class:`GeoPrim`, :class:`Texture`, :class:`CopLayer` — that know their own
bytes and nothing about each other. This module groups them the way an
artist looks for the culprit: under the memory client that holds them,
by the node that made them, down to a single primvar.

Every row carries three numbers:

``data``
    What the scene holds. Exact.
``upload``
    What the viewport model in :mod:`.sizes` says it puts on the GPU.
``share``
    The row's slice of the client's *measured* total, split in proportion
    to ``upload``. Shares of a client's rows sum to exactly its measured
    bytes, so the column adds up to the measurement; it is the estimate,
    and the window labels it so. When the viewport hook
    (:mod:`.hook`) measured a viewer, its prims' shares are their measured
    bytes instead, and those rows are marked ``exact``.

Pure Python: the property suite imports this without ``hou``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .clients import Client
from .sizes import ImageInfo

# ── Scan records ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Primvar:
    """One primvar (or ``points``/``normals``) on one prim."""

    name: str
    type_name: str
    interpolation: str
    values: int
    indices: int
    data_bytes: int
    upload_bytes: int


@dataclass(frozen=True)
class GeoPrim:
    """A geometry prim as the viewport would draw it.

    ``owner`` is the node blamed for it (the LOP that created it); ``instances``
    is how many times it is drawn — a prototype is uploaded once and each
    extra instance costs only ``instance_bytes``.
    """

    path: str
    type_name: str
    owner: str
    points: int = 0
    faces: int = 0
    face_vertices: int = 0
    topology_data: int = 0
    topology_upload: int = 0
    primvars: tuple[Primvar, ...] = ()
    instances: int = 1
    material: str = ""
    overhead_upload: int = 0

    #: Per-instance transform the viewport keeps on the GPU (a float4x4).
    INSTANCE_BYTES = 64

    @property
    def data_bytes(self) -> int:
        return self.topology_data + sum(p.data_bytes for p in self.primvars)

    @property
    def upload_bytes(self) -> int:
        once = (self.topology_upload + self.overhead_upload
                + sum(p.upload_bytes for p in self.primvars))
        return once + max(0, self.instances - 1) * self.INSTANCE_BYTES


@dataclass(frozen=True)
class Texture:
    """One texture file (all its UDIM tiles), however many materials use it."""

    path: str
    info: ImageInfo | None
    data_bytes: int
    upload_bytes: int
    materials: tuple[str, ...] = ()


@dataclass(frozen=True)
class CopLayer:
    """One cooked Copernicus layer an output of a COP node holds."""

    node: str
    output: str
    width: int
    height: int
    channels: int
    storage: str
    on_gpu: bool
    data_bytes: int
    gpu_bytes: int = 0

    @property
    def upload_bytes(self) -> int:
        """What the layer occupies on the GPU (its data size if not given)."""
        return self.gpu_bytes or self.data_bytes


# ── Report tree ──────────────────────────────────────────────────────────────


@dataclass
class Row:
    """One line of the report tree.

    ``ref`` is what selecting the row should select in Houdini: a node path,
    a prim path or a file path, per ``kind``.
    """

    label: str
    kind: str
    data: int = 0
    upload: int = 0
    share: int = 0
    measured: bool = False
    exact: bool = False
    ref: str = ""
    detail: str = ""
    children: list[Row] = field(default_factory=list)

    def walk(self):
        """This row and every row below it, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()


def apportion(total: int, weights: list[int]) -> list[int]:
    """Split ``total`` into integer parts proportional to ``weights``.

    Largest-remainder, so the parts sum to exactly ``total`` whenever any
    weight is positive; ties go to the earlier item. All-zero weights get
    nothing (there is nothing to blame), and negative weights count as zero.
    """
    w = [max(0, x) for x in weights]
    whole = sum(w)
    if total <= 0 or whole == 0:
        return [0] * len(w)
    exact = [total * x / whole for x in w]
    parts = [int(e) for e in exact]
    short = total - sum(parts)
    order = sorted(range(len(w)), key=lambda i: (-(exact[i] - parts[i]), i))
    for i in order[:short]:
        parts[i] += 1
    return parts


def _sum_up(row: Row) -> Row:
    """Make every parent's data/upload the sum of its children's."""
    if row.children:
        for child in row.children:
            _sum_up(child)
        row.data = sum(c.data for c in row.children)
        row.upload = sum(c.upload for c in row.children)
    return row


def _spread(row: Row, total: int) -> None:
    """Set ``row.share`` to ``total`` and split it down by ``upload``."""
    row.share = total
    if row.children:
        parts = apportion(total, [c.upload for c in row.children])
        for child, part in zip(row.children, parts):
            _spread(child, part)


def _exact(row: Row, measured: dict[str, int]) -> int:
    """Give prim rows their measured bytes; parents sum them. Returns the share.

    A prim's own children (topology, primvars) split its measured bytes by
    ``upload`` — the measurement is per prim, not per primvar.
    """
    if row.kind == "prim":
        row.exact = True
        _spread(row, measured.get(row.ref, 0))
        return row.share
    if row.children:
        row.share = sum(_exact(c, measured) for c in row.children)
        row.exact = True
    return row.share


def _unscanned_row(scanned: set[str], measured: dict[str, int]) -> Row | None:
    """Prims the viewport drew that the stage scan did not itemise."""
    extra = [Row(path.rsplit("/", 1)[-1] or path, "prim", ref=path,
                 detail="drawn by the viewport; not itemised by the scan")
             for path, size in measured.items() if path not in scanned and size > 0]
    if not extra:
        return None
    return Row("(drawn, not itemised)", "node", children=extra,
               detail=f"{len(extra):,} prims")


def _sort(row: Row) -> Row:
    row.children.sort(key=lambda c: (-c.upload, -c.data, c.label))
    for child in row.children:
        _sort(child)
    return row


def prim_row(prim: GeoPrim) -> Row:
    """A prim, with one child for its topology and one per primvar."""
    children = []
    if prim.topology_data or prim.topology_upload:
        children.append(Row("topology", "topology", prim.topology_data,
                            prim.topology_upload, ref=prim.path,
                            detail=f"{prim.faces:,} faces, "
                                   f"{prim.face_vertices:,} vertices"))
    for pv in prim.primvars:
        children.append(Row(pv.name, "primvar", pv.data_bytes, pv.upload_bytes,
                            ref=prim.path,
                            detail=f"{pv.type_name} {pv.interpolation}, "
                                   f"{pv.values:,} values"
                                   + (f", {pv.indices:,} indices" if pv.indices else "")))
    if prim.overhead_upload:
        children.append(Row("viewport buffers", "overhead", 0, prim.overhead_upload,
                            ref=prim.path,
                            detail="ids and selection the viewport keeps per "
                                   "point and triangle (calibrated)"))
    extra = max(0, prim.instances - 1) * GeoPrim.INSTANCE_BYTES
    if extra:
        children.append(Row("instance transforms", "instances", 0, extra,
                            ref=prim.path, detail=f"{prim.instances:,} instances"))
    if prim.points or prim.primvars:
        detail = f"{prim.type_name}, {prim.points:,} points"
    else:
        # Sphere, Cube, Cylinder…: no points in USD; the viewport tessellates
        # them itself, and that small mesh is not itemised.
        detail = f"{prim.type_name} (implicit — tessellated by the viewport, not sized)"
    if prim.instances > 1:
        detail += f", ×{prim.instances:,}"
    return Row(prim.path.rsplit("/", 1)[-1] or prim.path, "prim",
               ref=prim.path, detail=detail, children=children)


def geometry_rows(prims: list[GeoPrim]) -> list[Row]:
    """Prims grouped under the node that owns them, one row per node."""
    by_owner: dict[str, list[GeoPrim]] = {}
    for prim in prims:
        by_owner.setdefault(prim.owner, []).append(prim)
    rows = []
    for owner, owned in by_owner.items():
        children = [prim_row(p) for p in owned]
        rows.append(Row(owner or "(unknown node)", "node", ref=owner,
                        detail=f"{len(owned):,} prims", children=children))
    return rows


def texture_rows(textures: list[Texture]) -> list[Row]:
    """One row per texture file; shared files appear once, listing users."""
    seen: dict[str, Texture] = {}
    users: dict[str, list[str]] = {}
    for tex in textures:
        seen.setdefault(tex.path, tex)
        bucket = users.setdefault(tex.path, [])
        for m in tex.materials:
            if m not in bucket:
                bucket.append(m)
    rows = []
    for path, tex in seen.items():
        info = tex.info
        detail = (f"{info.width}×{info.height}, {info.channels} ch, "
                  f"{info.channel_bytes * 8}-bit" if info else "unreadable")
        if info and info.tiles > 1:
            detail += f", {info.tiles} tiles"
        mats = users[path]
        if mats:
            detail += f" — {len(mats)} material{'s' if len(mats) != 1 else ''}"
        rows.append(Row(path.replace("\\", "/").rsplit("/", 1)[-1], "texture",
                        tex.data_bytes, tex.upload_bytes, ref=path, detail=detail))
    return rows


def cop_rows(layers: list[CopLayer]) -> list[Row]:
    """Copernicus layers on the GPU, grouped by node."""
    by_node: dict[str, list[CopLayer]] = {}
    for layer in layers:
        if layer.on_gpu:
            by_node.setdefault(layer.node, []).append(layer)
    rows = []
    for node, held in by_node.items():
        children = [Row(layer.output, "layer", layer.data_bytes, layer.upload_bytes,
                        ref=node,
                        detail=f"{layer.width}×{layer.height}, {layer.channels} ch, "
                               f"{layer.storage}")
                    for layer in held]
        rows.append(Row(node, "node", ref=node, children=children))
    return rows


def build(clients: list[Client],
          geometry: dict[str, list[GeoPrim]],
          textures: list[Texture],
          cop_layers: list[CopLayer],
          exact: dict[str, dict[str, int]] | None = None) -> list[Row]:
    """The report: one top-level row per measured client.

    ``geometry`` maps a client's pane (``Client.pane``) to the prims scanned
    from that viewer's stage. Textures hang under the texture-cache client,
    Copernicus layers under Copernicus's buffer cache (or the OpenCL pool
    when there is none). ``exact`` maps a pane to the viewport hook's
    measured ``prim path → bytes``; that client's prims show their measured
    bytes rather than a share. Scanned data with no client to
    hang under (no viewer, no cache yet) gets an unmeasured row of its own, so
    nothing found is ever dropped.
    """
    rows: list[Row] = []
    placed_tex = placed_cop = False
    placed_panes: set[str] = set()
    # Copernicus layers belong to Copernicus's own cache when the registry
    # lists one (H22: "COP OpenCL Buffer Cache"), else to the generic OpenCL pool.
    cop_home = "cop" if any(c.kind == "cop" for c in clients) else "opencl"
    for client in clients:
        row = Row(client.name, client.kind, measured=True, ref=client.pane,
                  detail=client.device)
        measured = (exact or {}).get(client.pane) if client.kind == "geometry" else None
        if client.kind == "geometry" and client.pane in geometry:
            row.children = geometry_rows(geometry[client.pane])
            placed_panes.add(client.pane)
        if measured is not None:
            scanned = {p.path for p in geometry.get(client.pane, ())}
            extra = _unscanned_row(scanned, measured)
            if extra is not None:
                row.children.append(extra)
            _sum_up(row)
            _exact(row, measured)
            row.share = client.used_bytes
            rows.append(row)
            continue
        elif client.kind == "textures" and not placed_tex:
            row.children = texture_rows(textures)
            placed_tex = True
        elif client.kind == cop_home and not placed_cop:
            row.children = cop_rows(cop_layers)
            placed_cop = True
        _sum_up(row)
        _spread(row, client.used_bytes)
        rows.append(row)

    unmeasured: list[Row] = []
    for pane, prims in geometry.items():
        if pane not in placed_panes and prims:
            unmeasured.append(Row(f"Geometry ({pane})", "geometry", ref=pane,
                                  children=geometry_rows(prims)))
    if not placed_tex and textures:
        unmeasured.append(Row("Textures", "textures", children=texture_rows(textures)))
    if not placed_cop and any(layer.on_gpu for layer in cop_layers):
        unmeasured.append(Row("Copernicus", "cop", children=cop_rows(cop_layers)))
    for row in unmeasured:
        _sum_up(row)
        # Not measured: the model's own figure is the best there is.
        for r in row.walk():
            r.share = r.upload
        rows.append(row)

    for row in rows:
        _sort(row)
    rows.sort(key=lambda r: (not r.measured, -r.share, r.label))
    return rows


def coverage(row: Row) -> float | None:
    """How much of a measured client the model accounts for: Σupload ÷ share.

    About 1 means the scan explains the measurement; well under 1, the
    client holds things the scan cannot see; over 1, the viewport holds less
    than the scene asks for (evicted or reduced textures). ``None`` when
    there is nothing to compare.
    """
    if not row.measured or row.share <= 0 or not row.children:
        return None
    return row.upload / row.share


def human_bytes(n: int) -> str:
    """``1536`` → ``"1.5 KB"``; 1024-based like Houdini's memory figures; never negative.

    Three significant figures from KB up, so a column of them lines up by
    magnitude: ``"2.36 GB"``, ``"407 MB"``, ``"12.5 KB"``, ``"0 B"``.
    """
    n = max(0, int(n))
    if n < 1000:
        return f"{n} B"
    value = float(n)
    # Step up a unit at 999.5, not 1024: anything from there would round to
    # four digits ("1000 GB"), so it reads as "0.98 TB" instead.
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 999.5 or unit == "TB":
            break
    if value >= 99.95:
        text = f"{value:.0f}"
    elif value >= 9.995:
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return f"{text} {unit}"
