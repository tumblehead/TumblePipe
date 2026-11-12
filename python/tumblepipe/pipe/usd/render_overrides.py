"""The shot's configured render settings, written into its root layer when
the project opts in (issue #52; samples and ray limits since 2026-11-12).

A shot's RenderSettings come from the project's static
``_config/usd/root_default_prims.usda``. Config's ``render`` object
(project, all shots, sequence, shot; editable in the Entity Editor, the
Database Editor and by tumblepipe-mcp) used to reach no render at all. With
``render.apply_config`` on (inherited like the rest of ``render``; new
projects have it on, existing ones off until their owner opts in), the
shot's root layer carries an ``over`` of the settings prim with:

- ``resolution``: the shot's resolved resolution, when it is two positive
  integers;
- ``camera``: the shot's resolved camera, **only when that camera exists on
  the root defaults stage**. Eight live projects keep their template camera
  under ``/scene/cameras``, where the schema default ``/cameras/render_camera``
  names nothing; pointing their renders at it would render from no camera;
- the Karma settings of :data:`KARMA_SETTINGS` (samples, ray limits, dicing
  quality, depth of field, motion blur), each under the attribute name
  Houdini 22.0's Karma Render Settings LOP authors, **when an entity level
  pins it** (the project ``entity:/``, ``entity:/shots``, a sequence, the
  shot) and the value is of the attribute's type. A key only the schema
  holds is not authored: the project's own defaults layer is what its
  renders always used for it, and the defaults layer sits under the root
  layer, so a project that pins nothing composes exactly as before. (The
  schema's defaults for these keys predate this and do not match every
  project's defaults layer: the shipped schema said 64 samples and diffuse
  limit 2 where the template layer says 32 and 1. The scaffold now carries
  the template's values; existing projects still show the old schema
  values, greyed, in the Database Editor, though renders use their defaults
  layer's.)

Precedence, weakest first: the defaults layer < this root layer < the
shot's department layers (a Karma Render Settings or th::render_settings
node in a workfile authors on the same prim in its export, which sits above
the root layer in the staged build) < Farm Submit's per-job overrides
(``farm.pipelines.batch_submit.RENDER_OVERRIDE_MAP``, authored in the
collapsed render layer's own opinions, the strongest there are). The farm's
motion blur and depth of field overrides author the same attributes as
``enablemblur`` / ``enabledof`` here, so they win over them.

The settings prim is the one the defaults stage declares
(``find_render_settings_prim_path``), never an assumed ``/Render/...``.
"""

from dataclasses import dataclass, field

APPLY_KEY = 'apply_config'


@dataclass(frozen=True)
class KarmaSetting:
    """How one ``render`` key is authored: its attribute, its USDA
    declaration (as the template's defaults layer declares it) and its
    value type ('int', 'float' or 'bool')."""
    attribute: str
    declaration: str
    kind: str


#: ``render`` key -> Karma attribute. Names checked against Houdini
#: 22.0.429's Karma Render Settings LOP (``karmarenderproperties``): its
#: parameters carry these keys' names, and author these attributes.
#: ``enablemblur`` is ``karma:object:mblur`` (the LOP's "Motion Blur"
#: toggle, and what Farm Submit's override authors), not
#: ``disableMotionBlur``, so the farm's per-job override still wins.
#: Keys of ``render`` with no Karma attribute stay unapplied: ``overscan``
#: (no RenderSettings attribute), ``usedicingcamera`` / ``dicingframe`` /
#: ``dicingfocallength`` (no dicing camera path to author) and
#: ``enabledenoising`` (a farm post-process).
KARMA_SETTINGS: dict[str, KarmaSetting] = {
    'pathtracedsamples': KarmaSetting('karma:global:pathtracedsamples', 'int', 'int'),
    'diffuselimit': KarmaSetting('karma:object:diffuselimit', 'custom float', 'float'),
    'reflectionlimit': KarmaSetting('karma:object:reflectlimit', 'custom float', 'float'),
    'refractionlimit': KarmaSetting('karma:object:refractlimit', 'custom float', 'float'),
    'volumelimit': KarmaSetting('karma:object:volumelimit', 'custom float', 'float'),
    'ssslimit': KarmaSetting('karma:object:ssslimit', 'custom float', 'float'),
    'dicingqualityscale': KarmaSetting('karma:global:dicingqualityscale', 'float', 'float'),
    'enabledof': KarmaSetting('karma:global:enable_dof', 'custom bool', 'bool'),
    'enablemblur': KarmaSetting('karma:object:mblur', 'custom bool', 'bool'),
}


def karma_value(key: str, value):
    """``value`` as ``key``'s attribute holds it, or None when it is not of
    that type: samples a positive int, limits and quality a number not
    below 0, toggles a bool. A bool is not a number here."""
    setting = KARMA_SETTINGS.get(key)
    if setting is None:
        return None
    if setting.kind == 'bool':
        return value if isinstance(value, bool) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float('inf'), float('-inf')):
        return None
    if setting.kind == 'int':
        return int(value) if isinstance(value, int) and value > 0 else None
    return float(value) if value >= 0 else None


@dataclass(frozen=True)
class RenderOverride:
    """What a shot's root layer authors on its RenderSettings prim.

    ``settings`` are ``(attribute, value)`` pairs, sorted by attribute."""
    settings_path: str
    camera: str | None = None
    resolution: tuple[int, int] | None = None
    settings: tuple[tuple[str, object], ...] = field(default=())

    def to_json(self) -> dict:
        data = {
            'settings_path': self.settings_path,
            'camera': self.camera,
            'resolution': list(self.resolution) if self.resolution else None,
        }
        # Absent when empty, so an override with no Karma settings records
        # exactly what it did before they existed.
        if self.settings:
            data['settings'] = {name: value for name, value in self.settings}
        return data

    @staticmethod
    def from_json(data) -> 'RenderOverride | None':
        if not isinstance(data, dict) or not data.get('settings_path'):
            return None
        resolution = data.get('resolution')
        settings = data.get('settings')
        settings = tuple(sorted(settings.items())) if isinstance(settings, dict) else ()
        return RenderOverride(
            str(data['settings_path']),
            data.get('camera'),
            tuple(resolution) if resolution else None,
            settings,
        )


def applies(render: dict | None) -> bool:
    """Whether the project (or sequence, or shot) opted in."""
    return bool(render) and render.get(APPLY_KEY) is True


def valid_resolution(value) -> tuple[int, int] | None:
    """``[w, h]`` of two positive integers (bools are not), else None."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in value):
        return None
    return int(value[0]), int(value[1])


def plan_settings(pinned: dict | None) -> tuple[tuple[str, object], ...]:
    """The Karma attributes ``pinned`` (the ``render`` keys an entity level
    pins, merged) asks for: each valid key of :data:`KARMA_SETTINGS`."""
    if not isinstance(pinned, dict):
        return ()
    found = []
    for key, setting in KARMA_SETTINGS.items():
        if key not in pinned:
            continue
        value = karma_value(key, pinned[key])
        if value is not None:
            found.append((setting.attribute, value))
    return tuple(sorted(found))


def plan_override(render: dict | None, settings_path: str | None,
                  camera_exists, pinned: dict | None = None) -> RenderOverride | None:
    """The override for a shot whose resolved ``render`` settings are
    ``render``, on a defaults stage whose settings prim is ``settings_path``.
    ``camera_exists(path)`` says whether a camera prim is at ``path``;
    ``pinned`` is the ``render`` the entity levels alone hold (no schema
    defaults), which the Karma settings come from.
    None when the project did not opt in, the stage has no settings prim,
    or there is nothing to author."""
    if not applies(render) or not settings_path:
        return None
    camera = render.get('camera')
    camera = camera if isinstance(camera, str) and camera.startswith('/') and camera_exists(camera) else None
    resolution = valid_resolution(render.get('resolution'))
    settings = plan_settings(pinned)
    if camera is None and resolution is None and not settings:
        return None
    return RenderOverride(settings_path, camera, resolution, settings)


def _usda_value(value) -> str:
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, float):
        return repr(value)
    return str(value)


def override_usda(override: RenderOverride) -> str:
    """The ``over`` prims that author ``override``, to append to a layer."""
    declarations = {s.attribute: s.declaration for s in KARMA_SETTINGS.values()}
    names = [name for name in override.settings_path.split('/') if name]
    lines = []
    for depth, name in enumerate(names):
        pad = '    ' * depth
        lines.append(f'{pad}over "{name}"')
        lines.append(f'{pad}{{')
    pad = '    ' * len(names)
    if override.camera is not None:
        lines.append(f'{pad}rel camera = <{override.camera}>')
    for attribute, value in override.settings:
        declaration = declarations.get(attribute, 'custom float')
        lines.append(f'{pad}{declaration} {attribute} = {_usda_value(value)}')
    if override.resolution is not None:
        w, h = override.resolution
        lines.append(f'{pad}uniform int2 resolution = ({w}, {h})')
    for depth in reversed(range(len(names))):
        lines.append(f'{"    " * depth}}}')
    return '\n'.join(lines)


def plan_for_defaults(render: dict | None, defaults_path,
                      pinned: dict | None = None) -> RenderOverride | None:
    """:func:`plan_override` against the root defaults layer on disk. Opens
    it with pxr only when the project opted in."""
    if not applies(render):
        return None
    from pxr import Usd, UsdGeom
    from tumblepipe.pipe.usd.render_settings import find_render_settings_prim_path
    stage = Usd.Stage.Open(str(defaults_path))
    # Opted in, a defaults layer with no settings prim (or several) is an
    # error the build reports, not a reason to skip the override quietly.
    settings_path = find_render_settings_prim_path(stage)

    def camera_exists(path: str) -> bool:
        prim = stage.GetPrimAtPath(path)
        return bool(prim) and prim.IsValid() and prim.IsA(UsdGeom.Camera)

    return plan_override(render, settings_path, camera_exists, pinned)
