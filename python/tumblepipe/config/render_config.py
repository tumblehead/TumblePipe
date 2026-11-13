"""The render settings a shot's root layer applies, as config holds them (#70).

Pure: no config store, no ``hou``, no Qt. ``render`` is a nested object on
any entity node (the project ``entity:/``, ``entity:/shots``, a sequence, a
shot); ``JsonConfigStore.get_properties`` merges it key by key, root first,
so each key below is inherited on its own: a sequence may pin only the
resolution and keep following the project's camera.

The levels, strongest first, as the Entity Editor names them
(:data:`LEVELS`): the shot, its sequence, **all shots** (``entity:/shots``),
**the project** (``entity:/``), then **the pipeline defaults** (the
project's ``schemas.json``). All shots is the weakest level the editor
edits, and it still has two levels below it to inherit from.

These keys of ``render`` reach a render, through the shot's root layer
(``pipe.usd.render_overrides``):

- ``apply_config``: whether the root layer authors anything below at all
  (off, or unset, renders use the RenderSettings the scene carries);
- ``resolution``: two positive integers;
- ``camera``: a prim path, used only when that camera exists in the
  project's ``root_default_prims.usda``;
- the Karma settings (:data:`KARMA`, the keys of
  ``render_overrides.KARMA_SETTINGS``: ``pathtracedsamples``, the five ray
  limits, ``dicingqualityscale``, ``enabledof``, ``enablemblur``), each only
  when an entity level (the project, all shots, a sequence, the shot) pins
  it: a key only the pipeline defaults hold keeps the value of the
  project's render defaults layer (:func:`unset_default`).

The Entity Editor offers all of them (:data:`FIELDS`, grouped as
:data:`SECTIONS`). The rest of ``render`` (``overscan``, the dicing camera
keys, ``enabledenoising``) is not authored on the stage. A department's own render settings node and
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
IMAGE_FIELDS = (APPLY, RESOLUTION, CAMERA)

#: The Karma keys of ``render`` the root layer authors when a level pins
#: them, and the value each takes: ``int`` a whole number above 0,
#: ``float`` a number not below 0, ``bool`` on or off. The keys and rules
#: of ``pipe.usd.render_overrides.KARMA_SETTINGS`` (config may not import
#: pipe; ``tests/test_entity_editor_render.py`` holds the two equal).
KARMA = {
    'pathtracedsamples': 'int',
    'dicingqualityscale': 'float',
    'diffuselimit': 'float',
    'reflectionlimit': 'float',
    'refractionlimit': 'float',
    'volumelimit': 'float',
    'ssslimit': 'float',
    'enabledof': 'bool',
    'enablemblur': 'bool',
}
SAMPLES = f'{RENDER}.pathtracedsamples'
DICING = f'{RENDER}.dicingqualityscale'
DIFFUSE_LIMIT = f'{RENDER}.diffuselimit'
REFLECTION_LIMIT = f'{RENDER}.reflectionlimit'
REFRACTION_LIMIT = f'{RENDER}.refractionlimit'
VOLUME_LIMIT = f'{RENDER}.volumelimit'
SSS_LIMIT = f'{RENDER}.ssslimit'
DOF = f'{RENDER}.enabledof'
MBLUR = f'{RENDER}.enablemblur'
KARMA_FIELDS = tuple(f'{RENDER}.{key}' for key in KARMA)
FIELDS = IMAGE_FIELDS + KARMA_FIELDS

#: How the Entity Editor's Render settings panel groups them.
SECTIONS = (
    ('Image', IMAGE_FIELDS),
    ('Quality', (SAMPLES, DICING)),
    ('Ray limits', (DIFFUSE_LIMIT, REFLECTION_LIMIT, REFRACTION_LIMIT, VOLUME_LIMIT, SSS_LIMIT)),
    ('Effects', (DOF, MBLUR)),
)

LABELS = {
    APPLY: 'Apply to render',
    RESOLUTION: 'Resolution',
    CAMERA: 'Camera',
    SAMPLES: 'Samples',
    DICING: 'Dicing quality',
    DIFFUSE_LIMIT: 'Diffuse limit',
    REFLECTION_LIMIT: 'Reflection limit',
    REFRACTION_LIMIT: 'Refraction limit',
    VOLUME_LIMIT: 'Volume limit',
    SSS_LIMIT: 'SSS limit',
    DOF: 'Depth of field',
    MBLUR: 'Motion blur',
}

#: Short names for the Shots table's Render column, which lists what a row
#: pins in a word each.
SHORT = {
    APPLY: 'apply', RESOLUTION: 'res', CAMERA: 'cam', SAMPLES: 'samples',
    DICING: 'dicing', DIFFUSE_LIMIT: 'diffuse', REFLECTION_LIMIT: 'reflect',
    REFRACTION_LIMIT: 'refract', VOLUME_LIMIT: 'volume', SSS_LIMIT: 'sss',
    DOF: 'dof', MBLUR: 'mblur',
}

# ── the levels ───────────────────────────────────────────

#: The levels a shot's setting can come from, weakest first, as the editor
#: names them; ``entity_sheet`` labels each value's source with these (a
#: sequence as ``sequence <name>``).
DEFAULTS_LEVEL = 'the pipeline defaults'
PROJECT_LEVEL = 'the project'
ALL_SHOTS_LEVEL = 'all shots'
LEVELS = (DEFAULTS_LEVEL, PROJECT_LEVEL, ALL_SHOTS_LEVEL, 'sequence', 'shot')


def is_karma(name: str) -> bool:
    return name in KARMA_FIELDS


def unset_default(name: str, source: str) -> bool:
    """Whether a value of ``name`` that comes from ``source`` reaches no
    render: a Karma setting only the pipeline defaults hold. The root
    layer leaves it to the project's render defaults layer, so the editor
    shows it as not set, and typing that same value pins it."""
    return is_karma(name) and source == DEFAULTS_LEVEL


def weaker_levels(level: str) -> str:
    """What a value set on ``level`` (``all shots``, ``sequence``, or a
    shot) falls back to when Inherit takes it away, in words."""
    if level == ALL_SHOTS_LEVEL:
        return f'{PROJECT_LEVEL}, then {DEFAULTS_LEVEL}'
    if level == 'sequence':
        return f'{ALL_SHOTS_LEVEL}, {PROJECT_LEVEL}, then {DEFAULTS_LEVEL}'
    return f'its sequence, {ALL_SHOTS_LEVEL}, {PROJECT_LEVEL}, then {DEFAULTS_LEVEL}'


def pins_text(names: Iterable[str]) -> str:
    """The Render column's text for the fields a row pins: their short
    names in panel order ('' for none)."""
    chosen = set(names)
    return ', '.join(SHORT[name] for name in FIELDS if name in chosen)

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

    ``levels`` run weakest first (the pipeline defaults, the project, all
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
    if is_karma(name):
        return _parse_karma(name, value, label)
    raise ValueError(f'No render setting named {name}')


def valid_karma(name: str, value):
    """``value`` as Karma setting ``name`` holds it, or None when it is not
    of that setting's type: ``render_overrides.karma_value``'s rule (a whole
    number above 0, a finite number not below 0, a bool; a bool is not a
    number)."""
    kind = KARMA.get(split(name)[1]) if is_karma(name) else None
    if kind is None:
        return None
    if kind == 'bool':
        return value if isinstance(value, bool) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float('inf'), float('-inf')):
        return None
    if kind == 'int':
        return int(value) if isinstance(value, int) and value > 0 else None
    return float(value) if value >= 0 else None


def _parse_karma(name: str, value, label: str):
    kind = KARMA[split(name)[1]]
    if kind == 'bool':
        if isinstance(value, bool):
            return value
        text = str(value).strip().casefold()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f'{label} takes on or off, not "{value}"')
    if isinstance(value, str):
        text = value.strip()
        try:
            value = int(text) if kind == 'int' else float(text)
        except ValueError:
            value = None
    elif kind == 'float' and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    found = valid_karma(name, value)
    if found is None:
        if kind == 'int':
            raise ValueError(f'{label} takes a whole number above 0, not "{value}"')
        raise ValueError(f'{label} takes a number of 0 or more, not "{value}"')
    return found


def format_number(value: float) -> str:
    """A float in full and short: ``2`` for 2.0, ``0.25`` for 0.25; it
    reads back as the same float."""
    return str(int(value)) if float(value).is_integer() and abs(value) < 1e15 else repr(float(value))


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
    if is_karma(name):
        found = valid_karma(name, value)
        if found is None:
            return str(value)
        if isinstance(found, bool):
            return 'on' if found else 'off'
        return str(found) if isinstance(found, int) else format_number(found)
    return str(value)


# ── resolution presets ───────────────────────────────────

#: The resolutions the Render settings panel's preset menu offers, as the
#: usdrender ROP's resolution menu does; the project's own comes first.
RESOLUTION_PRESETS = (
    ('HD 1080', (1920, 1080)),
    ('HD 720', (1280, 720)),
    ('UHD 4K', (3840, 2160)),
    ('2K DCI', (2048, 1080)),
    ('2K Scope', (2048, 858)),
    ('Square 1080', (1080, 1080)),
)


def resolution_presets(project=None) -> list[tuple[str, tuple[int, int]]]:
    """The preset menu's entries, ``(label, (w, h))``: the project's
    resolution first (when it is one), then :data:`RESOLUTION_PRESETS`,
    each size once."""
    entries = []
    found = valid_resolution(project)
    if found:
        size = (found[0], found[1])
        named = next((label for label, s in RESOLUTION_PRESETS if s == size), '')
        entries.append((f'Project ({named})' if named else 'Project', size))
    seen = {size for _label, size in entries}
    for label, size in RESOLUTION_PRESETS:
        if size not in seen:
            entries.append((label, size))
            seen.add(size)
    return entries


def preset_text(label: str, size: tuple[int, int]) -> str:
    """A preset as its menu entry reads: ``HD 1080  1920 x 1080``."""
    return f'{label}  {size[0]} x {size[1]}'


def effect(values: Mapping[str, object], karma: Mapping[str, object] | None = None) -> str:
    """What a shot whose settings resolve to ``values`` renders with, in
    a sentence or two for the editor. ``karma``: the Karma settings a level
    pins (none from the pipeline defaults alone), which the root layer
    authors too when applied."""
    pinned = [
        f'{LABELS[name].lower()} {format_value(name, value)}'
        for name in KARMA_FIELDS
        if name in (karma or {}) and valid_karma(name, (karma or {})[name]) is not None
        for value in ((karma or {})[name],)
    ]
    if values.get(APPLY) is not True:
        text = (
            'Not applied: renders use the RenderSettings in the scene. The '
            'resolution still sizes the layout camera and playblasts.'
        )
        if pinned:
            text += ' The Karma settings set here wait for Apply to render.'
        return text
    resolution = valid_resolution(values.get(RESOLUTION))
    camera = valid_camera(values.get(CAMERA))
    parts = []
    if resolution:
        parts.append(f'renders at {resolution[0]} x {resolution[1]}')
    if camera:
        parts.append(
            f'through {camera} when root_default_prims.usda has that camera'
        )
    also = f' It also sets {", ".join(pinned)}.' if pinned else ''
    if not parts:
        if pinned:
            return f'The root layer sets {", ".join(pinned)}.'
        return 'Applied, but there is no valid resolution or camera to author.'
    return 'The root layer ' + ' and '.join(parts) + '.' + also
