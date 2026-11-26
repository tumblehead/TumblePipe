"""Which pipeline layers wrote a prim: its department history.

For a prim on a composed stage, every layer that holds a spec for it is
one row, strongest first: the entity, department and version that wrote
the layer, whether the layer *defined* the prim (the weakest ``def``, where
it first exists) or only *changed* it, and the properties it authored
there. The department jump's History panel (Radial, Alt+D) shows this for
the selected prims.

Who wrote a layer is read, in order, from:

1. the ``context.json`` every export writes beside its files
   (``pipe.context.save_export_context`` / ``save_layer_context``): the
   entity, department, version, user and time;
2. the export stamp (``pipe.usd.entity_stamp``) on the owning asset's root
   prim in that layer: entity, department and version;
3. nothing: an anonymous layer (the open workfile's own LOP layers) or a
   file outside the pipeline. The row still names the layer.

Nothing here imports ``hou``: the readers take a ``Usd.Stage`` and prim
paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tumblepipe.pipe.usd import entity_stamp as es
from tumblepipe.util.uri import Uri

#: A row's layer created the prim: the weakest ``def`` in its stack.
DEFINED = "defined"
#: A row's layer only changes a prim some weaker layer defines.
CHANGED = "changed"

#: How many folders up from a layer file a ``context.json`` is looked for.
_CONTEXT_DEPTH = 2


@dataclass(frozen=True)
class LayerOwner:
    """Who wrote a layer. Any field may be None when it is not known."""
    entity: Uri | None
    department: str | None
    version: str | None
    user: str | None = None
    timestamp: str | None = None
    #: Where the answer came from: ``"context"`` or ``"stamp"``.
    source: str = "context"


@dataclass(frozen=True)
class Opinion:
    """One layer's say on a prim."""
    #: The layer's identifier (its path, or an ``anon:`` name).
    layer: str
    #: Who wrote it, or None (an anonymous layer, a file outside the pipeline).
    owner: LayerOwner | None
    #: :data:`DEFINED` or :data:`CHANGED`.
    kind: str
    #: The properties this layer authors on the prim, sorted.
    properties: tuple[str, ...]
    #: The layer lives only in memory: the open workfile's own LOP layers.
    anonymous: bool = False

    @property
    def label(self) -> str:
        """What a row reads: ``MODEL v0012``, or the layer's file name."""
        if self.owner is not None and self.owner.department:
            version = f" {self.owner.version}" if self.owner.version else ""
            return f"{self.owner.department}{version}"
        if self.anonymous:
            return "this workfile"
        return Path(self.layer).name or self.layer


# ── Who wrote a layer ────────────────────────────────────────────


def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def _owner_from_context(data) -> LayerOwner | None:
    """The writer named in a ``context.json``: a layer export's
    ``outputs[0]``, or an export's flat record."""
    if not isinstance(data, dict):
        return None
    record = data
    outputs = data.get("outputs")
    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
        record = outputs[0]
    department = record.get("department")
    if not department:
        return None
    uri = None
    raw = record.get("uri")
    if raw:
        try:
            uri = Uri.parse(str(raw))
        except Exception:  # noqa: BLE001  an unreadable URI is no URI
            uri = None
    return LayerOwner(
        entity=uri,
        department=str(department),
        version=str(record["version"]) if record.get("version") else None,
        user=str(record["user"]) if record.get("user") else None,
        timestamp=str(record["timestamp"]) if record.get("timestamp") else None,
        source="context",
    )


def context_owner(layer_path) -> LayerOwner | None:
    """The writer of the layer file at *layer_path*, from the
    ``context.json`` in its folder or one of the :data:`_CONTEXT_DEPTH`
    folders above it; None when there is none."""
    if not layer_path:
        return None
    folder = Path(str(layer_path)).parent
    for _ in range(_CONTEXT_DEPTH + 1):
        found = _owner_from_context(_read_json(folder / "context.json"))
        if found is not None:
            return found
        if folder.parent == folder:
            break
        folder = folder.parent
    return None


def stamp_owner(layer, root_path: str | None) -> LayerOwner | None:
    """The writer of *layer* according to the export stamp on the asset
    root *root_path* in it; None without one."""
    if layer is None or not root_path:
        return None
    spec = layer.GetPrimAtPath(root_path)
    if spec is None:
        return None
    stamp = dict(spec.customData).get(es.STAMP_KEY)
    if not isinstance(stamp, dict):
        return None
    departments = stamp.get(es.DEPARTMENTS_KEY)
    if not isinstance(departments, dict) or len(departments) != 1:
        return None
    (department, version), = departments.items()
    uri = None
    try:
        uri = Uri.parse(str(stamp.get(es.ENTITY_KEY)))
    except Exception:  # noqa: BLE001
        uri = None
    return LayerOwner(entity=uri, department=str(department),
                      version=str(version), source="stamp")


def layer_owner(layer, root_path: str | None = None) -> LayerOwner | None:
    """Who wrote *layer* (an ``Sdf.Layer``): its ``context.json``, else the
    stamp on *root_path* in it, else None."""
    if layer is None or layer.anonymous:
        return None
    return context_owner(layer.realPath) or stamp_owner(layer, root_path)


# ── A prim's history ─────────────────────────────────────────────


def prim_history(stage, prim_path, _owners: dict | None = None) -> list[Opinion]:
    """The layers that hold a spec for the prim at *prim_path* on *stage*,
    strongest first, one row per layer. Empty when there is no such prim.

    The row of the weakest layer that ``def``-ines the prim is
    :data:`DEFINED`; every other row is :data:`CHANGED`. A layer that holds
    several specs for the prim (through a reference or a variant inside it)
    is one row with their properties together.
    """
    from pxr import Sdf

    if stage is None:
        return []
    try:
        path = Sdf.Path(str(prim_path)).GetPrimPath()
    except Exception:  # noqa: BLE001
        return []
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return []

    owner_entity = es.owning_entity(stage, path)
    root = owner_entity.prim_path if owner_entity is not None else None
    owners = {} if _owners is None else _owners

    order: list[str] = []
    by_layer: dict[str, dict] = {}
    weakest_def = None
    for spec in prim.GetPrimStack():
        layer = spec.layer
        key = layer.identifier
        if key not in by_layer:
            order.append(key)
            if key not in owners:
                owners[key] = layer_owner(layer, root)
            by_layer[key] = {"layer": layer, "props": set(), "def": False}
        row = by_layer[key]
        row["props"].update(p.name for p in spec.properties)
        if spec.specifier == Sdf.SpecifierDef:
            row["def"] = True
            weakest_def = key

    return [
        Opinion(
            layer=key,
            owner=owners[key],
            kind=DEFINED if key == weakest_def else CHANGED,
            properties=tuple(sorted(by_layer[key]["props"])),
            anonymous=bool(by_layer[key]["layer"].anonymous),
        )
        for key in order
    ]


def history(stage, prim_paths) -> dict[str, list[Opinion]]:
    """:func:`prim_history` for each of *prim_paths*, in the order given;
    each layer's writer is read once."""
    owners: dict = {}
    return {str(p): prim_history(stage, p, owners) for p in prim_paths}


def departments_of(rows) -> list[str]:
    """The departments in *rows*, weakest first (the order they built the
    prim up), each once."""
    seen: list[str] = []
    for row in reversed(list(rows)):
        dept = row.owner.department if row.owner is not None else None
        if dept and dept not in seen:
            seen.append(dept)
    return seen
