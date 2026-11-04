"""Carry an APEX character's scene files into the exported layer's folder.

A character on a USD stage (SOP Import APEX Scene, Configure APEX Rig, APEX
SOP Rig Builder, APEX Animate) keeps its rig, skeleton and animation in
Houdini geometry, not in USD: the prims only point at it, through
``houdini:apex:character:files`` (HoudiniApexCharacterAPI) and
``sceneFiles`` (a HoudiniApexScene prim). In the session those are ``op:``
paths to locked geometry. When the USD ROP writes the layer it saves each
one to its node's save path, ``$HIP/usd/$OS.bgeo.sc`` by default, and points
the layer at that file: the workfile's folder, overwritten by the next
export and by anyone working in the file. (With the save path turned off it
writes the file into a ``<layer>.geometry/`` folder beside the layer
instead.)

A published version must not depend on the work area, so the export copies
every such file that lies outside the layer's folder into ``APEX_DIR``
beside the layer and points the layer at the copy. A path still on ``op:``
(geometry the ROP did not write) or a file that does not exist cannot be
published and is reported instead.

``classify`` and ``unique_name`` are pure; ``embed_apex_files`` needs USD
(``pxr``) but not Houdini.
"""

from __future__ import annotations

import logging
import posixpath
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Below the layer's folder, where the copied scene files land.
APEX_DIR = 'apex'

#: The asset attributes that point at APEX scene geometry.
FILE_ATTRIBUTES = ('houdini:apex:character:files', 'sceneFiles')

#: The schema an APEX character prim carries.
CHARACTER_API = 'HoudiniApexCharacterAPI'

INSIDE = 'inside'
EXTERNAL = 'external'
UNSAVED = 'unsaved'


@dataclass
class EmbedResult:
    """What one pass did: raw path -> new path, and what it could not."""
    rewritten: dict = field(default_factory=dict)
    unsaved: list = field(default_factory=list)
    missing: list = field(default_factory=list)


def is_file_attribute(name: str) -> bool:
    """Whether an attribute named ``name`` points at APEX scene files."""
    return name in FILE_ATTRIBUTES


def classify(raw: str, layer_dir: str) -> str:
    """Where the authored path ``raw`` of a layer in ``layer_dir`` points:
    UNSAVED (an ``op:`` path: session geometry), INSIDE the layer's folder
    (it travels with the layer), or EXTERNAL. Paths compare as written,
    case-insensitively, with either slash; a relative path is relative to
    ``layer_dir``."""
    if raw.startswith('op:'):
        return UNSAVED
    path = raw.replace('\\', '/')
    root = layer_dir.replace('\\', '/').rstrip('/')
    is_absolute = path.startswith('/') or (len(path) > 1 and path[1] == ':')
    if not is_absolute:
        path = f'{root}/{path}'
    path = posixpath.normpath(path).lower()
    root = posixpath.normpath(root).lower()
    return INSIDE if path.startswith(root + '/') else EXTERNAL


def unique_name(name: str, used) -> str:
    """``name``, or ``<stem>_<n><extensions>`` for the first ``n`` not in
    ``used``. The number goes before the first dot: Houdini reads the
    format from the whole compound extension, so ``rig.bgeo.sc`` becomes
    ``rig_1.bgeo.sc``, never ``rig.bgeo_1.sc``."""
    if name not in used:
        return name
    stem, dot, extensions = name.partition('.')
    index = 1
    while True:
        candidate = f'{stem}_{index}{dot}{extensions}'
        if candidate not in used:
            return candidate
        index += 1


def _values(attr_spec):
    """Every authored value of ``attr_spec``: (time or None, value)."""
    from pxr import Sdf

    values = []
    if attr_spec.HasDefaultValue():
        values.append((None, attr_spec.default))
    layer = attr_spec.layer
    for time in layer.ListTimeSamplesForPath(attr_spec.path):
        values.append((time, layer.QueryTimeSample(attr_spec.path, time)))
    return [
        (time, value) for time, value in values
        if isinstance(value, (Sdf.AssetPath, Sdf.AssetPathArray, list, tuple))
    ]


def _file_attribute_specs(layer) -> list:
    specs = []

    def visit(path):
        if not path.IsPropertyPath():
            return
        if not is_file_attribute(path.name):
            return
        spec = layer.GetAttributeAtPath(path)
        if spec is not None:
            specs.append(spec)

    layer.Traverse(layer.pseudoRoot.path, visit)
    return specs


def embed_apex_files(layer_path) -> EmbedResult:
    """Copy the APEX scene files ``layer_path`` points at from outside its
    folder into ``APEX_DIR`` beside it, and point the layer at the copies.
    Saves the layer when anything changed."""
    from pxr import Sdf

    result = EmbedResult()
    layer_path = Path(layer_path)
    layer_dir = layer_path.parent
    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        raise RuntimeError(f'Could not open {layer_path}')
    target = layer_dir / APEX_DIR
    used = {p.name for p in target.iterdir()} if target.is_dir() else set()
    copied: dict = {}  # resolved source -> new authored path

    def embed(raw: str) -> str:
        if not raw:
            return raw
        kind = classify(raw, str(layer_dir))
        if kind == INSIDE:
            return raw
        if kind == UNSAVED:
            if raw not in result.unsaved:
                result.unsaved.append(raw)
            return raw
        source = Path(raw)
        if not source.is_absolute():
            source = layer_dir / source
        if not source.is_file():
            if raw not in result.missing:
                result.missing.append(raw)
            return raw
        key = source.resolve().as_posix().lower()
        if key not in copied:
            name = unique_name(source.name, used)
            target.mkdir(exist_ok=True)
            shutil.copy2(source, target / name)
            used.add(name)
            copied[key] = f'./{APEX_DIR}/{name}'
        result.rewritten[raw] = copied[key]
        return copied[key]

    def rewrite(value):
        if isinstance(value, Sdf.AssetPath):
            new = embed(value.path)
            return value if new == value.path else Sdf.AssetPath(new)
        paths = [
            path.path if isinstance(path, Sdf.AssetPath) else str(path)
            for path in value
        ]
        new_paths = [embed(path) for path in paths]
        if new_paths == paths:
            return value
        return Sdf.AssetPathArray(new_paths)

    changed = False
    for spec in _file_attribute_specs(layer):
        for time, value in _values(spec):
            new_value = rewrite(value)
            if new_value is value:
                continue
            changed = True
            if time is None:
                spec.default = new_value
            else:
                layer.SetTimeSample(spec.path, time, new_value)
    if changed:
        layer.Save()
        logger.info(
            'Copied %d APEX scene file(s) into %s', len(copied), target,
        )
    return result


def character_prim_paths(stage, root_path: str = '/') -> list[str]:
    """The APEX character prims at or below ``root_path`` on ``stage``: a
    prim with the HoudiniApexCharacterAPI schema, read from its
    ``apiSchemas`` metadata so it is found without Houdini's schema
    plugin (standalone USD does not know the schema)."""
    from pxr import Usd

    root = stage.GetPrimAtPath(root_path)
    if root is None or not root.IsValid():
        return []
    paths = []
    for prim in Usd.PrimRange(root):
        if CHARACTER_API in _api_schema_names(prim):
            paths.append(str(prim.GetPath()))
    return paths


def _api_schema_names(prim) -> list[str]:
    """The composed applied schemas, plus the authored ``apiSchemas``
    items: standalone USD leaves a schema it does not know out of the
    former."""
    names = list(prim.GetAppliedSchemas())
    list_op = prim.GetMetadata('apiSchemas')
    if list_op is not None:
        names += [
            name for name in list_op.GetAddedOrExplicitItems()
            if name not in names
        ]
    return names
