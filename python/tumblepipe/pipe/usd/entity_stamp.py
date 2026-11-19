"""Which entity owns a prim: the stamp exports write, and how to read it.

An asset's department export stamps the asset's root prim, in the published
layer, with the asset's entity URI and the department and version that
authored the layer::

    customData = {
        dictionary tumblepipe = {
            string entity = "entity:/assets/CHAR/hero"
            dictionary departments = {
                string model = "v0003"
            }
        }
    }

``customData`` is a dictionary, so USD composes it key by key. An asset's
department layers each add their own department under ``departments``, and
the composed prim lists every department that contributed. The stamp sits
on the asset's own root prim in the asset's own layers, so a shot that
brings in three assets answers the right asset for each prim: no shot layer
writes the stamp.

The import nodes have long written the asset's URI as ``customData["uri"]``
on the same prim, in the importing workfile's layer
(``pipe.houdini.prim_metadata``). The readers here fall back on it, so an
asset exported before the stamp existed still answers wherever a TumblePipe
import node brought it in. A prim with neither has no known owner, and the
readers return ``None`` for it.

No ``hou`` here: the readers take a ``Usd.Stage`` and prim paths, the
writers an ``Sdf.Layer``. ``pipe.houdini.selection_entities`` is the
Houdini side (the entities of the current LOP selection).
"""

from dataclasses import dataclass, field
from pathlib import Path

from tumblepipe.util.uri import Uri

#: The key the stamp lives under in a prim's ``customData``.
STAMP_KEY = 'tumblepipe'

#: Inside the stamp: the owning entity's URI, as a string.
ENTITY_KEY = 'entity'

#: Inside the stamp: department name -> the version that authored the layer.
DEPARTMENTS_KEY = 'departments'

#: The import nodes' own key for the asset URI (``prim_metadata``).
IMPORT_URI_KEY = 'uri'

#: Where an answer came from: the asset's own exported layers ...
SOURCE_EXPORT = 'export'

#: ... or the import node that brought the asset into this stage.
SOURCE_IMPORT = 'import'


@dataclass(frozen=True)
class PrimEntity:
    """The entity that owns a prim.

    ``prim_path`` is the stamped prim that answered: the prim asked about,
    or its nearest stamped ancestor. ``departments`` maps each department
    that stamped the prim to the version it exported; it is empty when the
    answer came from an import node (``source`` is :data:`SOURCE_IMPORT`).
    """
    uri: Uri
    prim_path: str
    departments: dict = field(default_factory=dict, compare=False)
    source: str = SOURCE_EXPORT


# ── Writing ──────────────────────────────────────────────────────


def stamp_value(entity_uri: Uri, department_name: str, version_name: str) -> dict:
    """The stamp one department's export of ``entity_uri`` writes."""
    return {
        ENTITY_KEY: str(entity_uri),
        DEPARTMENTS_KEY: {department_name: version_name},
    }


def stamp_layer(
    layer,
    prim_path: str,
    entity_uri: Uri,
    department_name: str,
    version_name: str,
) -> None:
    """Stamp ``prim_path`` in ``layer`` (an ``Sdf.Layer``) as ``entity_uri``'s,
    authored by ``department_name`` at ``version_name``.

    A layer that authors nothing at ``prim_path`` gets an ``over`` there: it
    defines nothing, and the stamp composes onto the prim the asset's other
    layers define. Whatever else the prim's ``customData`` holds in this
    layer is kept; a stamp already there (an export of a stage that baked
    one in) is replaced, since this layer is this department's.
    """
    from pxr import Sdf

    spec = layer.GetPrimAtPath(prim_path)
    if spec is None:
        spec = Sdf.CreatePrimInLayer(layer, prim_path)
    custom_data = dict(spec.customData)
    custom_data[STAMP_KEY] = stamp_value(entity_uri, department_name, version_name)
    spec.customData = custom_data


def stamp_exported_layer(
    layer_path: Path,
    entity_uri: Uri,
    department_name: str,
    version_name: str,
) -> bool:
    """Stamp the asset root prim of the exported layer file at ``layer_path``.

    Only an asset owns a prim: its root, ``/<category>/<asset>``. A shot's
    layers hold other entities' prims (the assets it imports, which carry
    their own stamps) and none of its own, so a shot export is left as it
    is and this returns False. Raises when the layer cannot be opened or
    saved.
    """
    from pxr import Sdf

    prim_path = owned_prim_path(entity_uri)
    if prim_path is None:
        return False
    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        raise RuntimeError(f'Could not open the exported layer {layer_path}')
    stamp_layer(layer, prim_path, entity_uri, department_name, version_name)
    if not layer.Save():
        raise RuntimeError(f'Could not save the exported layer {layer_path}')
    return True


def owned_prim_path(entity_uri: Uri) -> str | None:
    """The prim an entity owns in its exports, or None when it owns none.

    An asset ``entity:/assets/<category>/<asset>`` owns
    ``/<category>/<asset>`` (``prim_queries.uri_to_prim_path``).
    """
    segments = entity_uri.segments
    if entity_uri.purpose != 'entity' or len(segments) != 3:
        return None
    if segments[0] != 'assets':
        return None
    return '/' + '/'.join(segments[1:])


# ── Reading ──────────────────────────────────────────────────────


def _entity_uri(raw) -> Uri | None:
    """``raw`` as an entity URI, or None for anything else."""
    if not isinstance(raw, str):
        return None
    try:
        uri = Uri.parse(raw)
    except ValueError:
        return None
    if uri.purpose != 'entity' or not uri.segments:
        return None
    return uri


def strip_stamp(custom_data: dict) -> dict:
    """``custom_data`` without the export stamp.

    For code that reads a prim's composed ``customData`` and writes it back
    into its own layer: the stamp belongs to the asset's layers, and a copy
    in a stronger layer would pin the departments and versions of the day
    it was copied.
    """
    return {key: value for key, value in custom_data.items() if key != STAMP_KEY}


def read_stamp(prim) -> PrimEntity | None:
    """The entity ``prim`` itself is stamped with, or None.

    The export stamp answers first: it is the asset's own word. Without
    one, the URI an import node wrote on the prim does. A malformed stamp
    (not a dictionary, no entity URI in it) counts as no stamp.
    """
    if prim is None or not prim.IsValid():
        return None
    custom_data = prim.GetMetadata('customData')
    if not custom_data:
        return None
    prim_path = str(prim.GetPath())

    stamp = custom_data.get(STAMP_KEY)
    if isinstance(stamp, dict):
        uri = _entity_uri(stamp.get(ENTITY_KEY))
        if uri is not None:
            departments = stamp.get(DEPARTMENTS_KEY)
            return PrimEntity(
                uri=uri,
                prim_path=prim_path,
                departments={
                    str(name): str(version)
                    for name, version in departments.items()
                } if isinstance(departments, dict) else {},
                source=SOURCE_EXPORT,
            )

    uri = _entity_uri(custom_data.get(IMPORT_URI_KEY))
    if uri is not None:
        return PrimEntity(uri=uri, prim_path=prim_path, source=SOURCE_IMPORT)
    return None


def owning_entity(stage, prim_path) -> PrimEntity | None:
    """The entity that owns the prim at ``prim_path``: its own stamp, or
    its nearest stamped ancestor's.

    None when the path names no prim on ``stage``, or no prim from it up to
    the root is stamped: geometry authored in the workfile, or an asset
    exported before the stamp existed that no import node tagged. Nothing
    is read from prim names or paths.
    """
    from pxr import Sdf

    if stage is None:
        return None
    try:
        path = Sdf.Path(str(prim_path))
    except Exception:
        return None
    if not path.IsAbsolutePath():
        return None
    prim = stage.GetPrimAtPath(path.GetPrimPath())
    while prim is not None and prim.IsValid() and not prim.IsPseudoRoot():
        found = read_stamp(prim)
        if found is not None:
            return found
        prim = prim.GetParent()
    return None


def owning_entities(stage, prim_paths) -> dict:
    """``{prim path: PrimEntity | None}`` for each of ``prim_paths``, in the
    order given. A stage that mixes stamped and unstamped assets answers
    each prim on its own."""
    return {
        str(prim_path): owning_entity(stage, prim_path)
        for prim_path in prim_paths
    }


def distinct_entities(stage, prim_paths) -> list[PrimEntity]:
    """The entities that own ``prim_paths``, one entry per entity, in the
    order first met. Prims with no known owner are left out, so the result
    is empty when nothing selected is a pipeline entity's."""
    seen = {}
    for found in owning_entities(stage, prim_paths).values():
        if found is not None and found.uri not in seen:
            seen[found.uri] = found
    return list(seen.values())
