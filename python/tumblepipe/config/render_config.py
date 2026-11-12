"""The render settings a shot's root layer applies, as config holds them (#70).

Pure: no config store, no ``hou``, no Qt. ``render`` is a nested object on
any entity node (the project ``entity:/``, ``entity:/shots``, a sequence, a
shot); ``JsonConfigStore.get_properties`` merges it key by key, root first,
so each key below is inherited on its own: a sequence may pin only the
resolution and keep following the project's camera.

These keys of ``render`` reach a render, through the shot's root layer
(``pipe.usd.render_overrides``):

- ``apply_config``: whether the root layer authors anything below at all
  (off, or unset, renders use the RenderSettings the scene carries);
- ``resolution``: two positive integers;
- ``camera``: a prim path, used only when that camera exists in the
  project's ``root_default_prims.usda``;
- the Karma settings of ``render_overrides.KARMA_SETTINGS``
  (``pathtracedsamples``, the five ray limits, ``dicingqualityscale``,
  ``enabledof``, ``enablemblur``), each only when an entity level (the
  project, all shots, a sequence, the shot) pins it: a key only the schema
  holds keeps the value of the project's defaults layer.

The Entity Editor offers the first three (this module's :data:`FIELDS`);
the Karma settings are edited in the Database Editor. The rest of
``render`` (``overscan``, the dicing camera keys, ``enabledenoising``) is
not authored on the stage. A department's own render settings node and
Farm Submit's per-job overrides (samples, motion blur, depth of field, ray
limits) are stronger layers: they win over the root layer.

The Entity Editor's sheet (``config.entity_sheet``) addresses each key as a
dotted field name (``render.camera``); :func:`read_field`,
:func:`write_field` and :func:`clear_field` map one onto an entity's
properties, and :func:`resolve` is the cascade with where each value comes
from. Nothing here needs a layout migration: a missing key is inherited,
and a missing ``apply_config`` everywhere is off.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

RENDER = 'render'
APPLY_KEY = 'apply_config'
CAMERA_KEY = 'camera'
RESOLUTION_KEY = 'resolution'

#: The sheet's field names: ``render.<key>``.
APPLY = f'{RENDER}.{APPLY_KEY}'
CAMERA = f'{RENDER}.{CAMERA_KEY}'
RESOLUTION = f'{RENDER}.{RESOLUTION_KEY}'
FIELDS = (APPLY, RESOLUTION, CAMERA)

LABELS = {
    APPLY: 'Apply to render',
    RESOLUTION: 'Resolution',
    CAMERA: 'Camera',
}

_TRUE = frozenset({'1', 'true', 'yes', 'y', 'on', 'x'})
_FALSE = frozenset({'0', 'false', 'no', 'n', 'off'})
_RESOLUTION_TEXT = re.compile(r'^\s*([0-9]+)\s*(?:[xX×*,]|\s)\s*([0-9]+)\s*$')

_MISSING = object()


def is_nested(name: str) -> bool:
    """Whether ``name`` is a dotted field (``render.camera``), not a
    top-level property."""
    return '.' in name


def split(name: str) -> tuple[str, str]:
    """``render.camera`` -> ``('render', 'camera')``."""
    parent, _, key = name.partition('.')
    return parent, key


def read_field(properties, name: str, default=_MISSING):
    """``properties[name]``, a dotted name reaching into its object.

    ``default`` (or a private sentinel, see :func:`has_field`) when it is
    not there, including when the parent is not an object."""
    if not isinstance(properties, Mapping):
        return default
    if not is_nested(name):
        return properties.get(name, default)
    parent, key = split(name)
    inner = properties.get(parent)
    if not isinstance(inner, Mapping):
        return default
    return inner.get(key, default)


def has_field(properties, name: str) -> bool:
    return read_field(properties, name) is not _MISSING


def write_field(properties: dict, name: str, value) -> dict:
    """Pin ``value`` as ``name`` in ``properties`` (an entity's OWN
    properties), in place; the rest of the object is kept as stored. A
    parent that is not an object (a broken hand edit) is replaced."""
    if not is_nested(name):
        properties[name] = value
        return properties
    parent, key = split(name)
    inner = properties.get(parent)
    inner = dict(inner) if isinstance(inner, Mapping) else {}
    inner[key] = copy.deepcopy(value)
    properties[parent] = inner
    return properties


def clear_field(properties: dict, name: str) -> dict:
    """Remove ``name`` from ``properties`` in place, so it inherits again.
    An object left empty goes too: an empty ``render`` pins nothing, but
    it is noise in the database."""
    if not is_nested(name):
        properties.pop(name, None)
        return properties
    parent, key = split(name)
    inner = properties.get(parent)
    if not isinstance(inner, Mapping) or key not in inner:
        return properties
    inner = dict(inner)
    del inner[key]
    if inner:
        properties[parent] = inner
    else:
        del properties[parent]
    return properties


def nest(values: Mapping[str, object]) -> dict:
    """Flat sheet values (some dotted) as properties to store."""
    out: dict = {}
    for name, value in values.items():
        write_field(out, name, value)
    return out


@dataclass(frozen=True)
class Resolved:
    """A field's value once inherited, and the level it comes from ('' with
    no level holding it)."""
    value: object
    source: str


def resolve(levels: Iterable[tuple[str, Mapping | None]], names: Iterable[str] = FIELDS) -> dict:
    """The cascade: each of ``names`` as the strongest of ``levels`` that
    holds it, with that level's label.

    ``levels`` run weakest first (the project's defaults, the project, all
    shots, the sequence, the shot), each ``(label, own properties)``. A
    level holding the key, even with ``None``, wins over the levels before
    it, as ``JsonConfigStore.get_properties`` merges; so does a level whose
    ``render`` is not an object (a broken hand edit), which the merge lets
    replace everything before it."""
    found = {name: Resolved(None, '') for name in names}
    for label, properties in levels:
        for name in found:
            value = read_field(properties, name)
            if value is not _MISSING:
                found[name] = Resolved(copy.deepcopy(value), label)
            elif wipes(properties, name):
                found[name] = Resolved(None, '')
    return found


def wipes(properties, name: str) -> bool:
    """Whether ``properties`` holds ``name``'s parent as something that is
    not an object, which replaces what the levels before it held."""
    if not is_nested(name) or not isinstance(properties, Mapping):
        return False
    parent, _key = split(name)
    return parent in properties and not isinstance(properties[parent], Mapping)


# ── values ───────────────────────────────────────────────

def valid_resolution(value) -> list[int] | None:
    """``[w, h]`` of two positive integers (bools are not), else None; the
    rule the root layer applies (``pipe.usd.render_overrides``)."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in value):
        return None
    return [int(value[0]), int(value[1])]


def valid_camera(value) -> str | None:
    """A prim path: starts with ``/``, more than ``/``, no spaces."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) < 2 or not text.startswith('/') or any(c.isspace() for c in text):
        return None
    return text.rstrip('/') or None


def parse(name: str, value) -> object:
    """``value`` (typed text, or a value as stored) as ``name`` stores it.
    Raises ValueError for one the root layer could not use."""
    label = LABELS.get(name, name)
    if name == APPLY:
        if isinstance(value, bool):
            return value
        text = str(value).strip().casefold()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f'{label} takes on or off, not "{value}"')
    if name == RESOLUTION:
        if isinstance(value, str):
            match = _RESOLUTION_TEXT.match(value)
            if match is None:
                raise ValueError(f'{label} takes width x height, like 1920x1080, not "{value}"')
            value = [int(match.group(1)), int(match.group(2))]
        found = valid_resolution(value)
        if found is None:
            raise ValueError(f'{label} takes two whole numbers above 0, not "{value}"')
        return found
    if name == CAMERA:
        found = valid_camera(value)
        if found is None:
            raise ValueError(f'{label} takes a prim path, like /cameras/render_camera, not "{value}"')
        return found
    raise ValueError(f'No render setting named {name}')


def format_value(name: str, value) -> str:
    """How a cell or field shows ``value`` (None: nothing anywhere)."""
    if name == APPLY:
        if value is None:
            return 'off (not set)'
        return 'on' if value is True else 'off' if value is False else str(value)
    if value is None:
        return ''
    if name == RESOLUTION:
        found = valid_resolution(value)
        return f'{found[0]} x {found[1]}' if found else str(value)
    return str(value)


def effect(values: Mapping[str, object]) -> str:
    """What a shot whose settings resolve to ``values`` renders with, in
    one sentence for the editor."""
    if values.get(APPLY) is not True:
        return (
            'Not applied: renders use the RenderSettings in the scene. The '
            'resolution still sizes the layout camera and playblasts.'
        )
    resolution = valid_resolution(values.get(RESOLUTION))
    camera = valid_camera(values.get(CAMERA))
    parts = []
    if resolution:
        parts.append(f'renders at {resolution[0]} x {resolution[1]}')
    if camera:
        parts.append(
            f'through {camera} when root_default_prims.usda has that camera'
        )
    if not parts:
        return 'Applied, but there is no valid resolution or camera to author.'
    return 'The root layer ' + ' and '.join(parts) + '.'
