"""The shot's configured render camera and resolution, written into its root
layer when the project opts in (issue #52).

A shot's RenderSettings come from the project's static
``_config/usd/root_default_prims.usda``. Config's ``render.camera`` and
``render.resolution`` (project, sequence, shot; editable in the Database
Editor and by tumblepipe-mcp) used to reach no render at all. With
``render.apply_config`` on (inherited like the rest of ``render``; new
projects have it on, existing ones off until their owner opts in), the
shot's root layer carries an ``over`` of the settings prim with:

- ``resolution``: the shot's resolved resolution, when it is two positive
  integers;
- ``camera``: the shot's resolved camera, **only when that camera exists on
  the root defaults stage**. Eight live projects keep their template camera
  under ``/scene/cameras``, where the schema default ``/cameras/render_camera``
  names nothing; pointing their renders at it would render from no camera.

The settings prim is the one the defaults stage declares
(``find_render_settings_prim_path``), never an assumed ``/Render/...``.
"""

from dataclasses import dataclass

APPLY_KEY = 'apply_config'


@dataclass(frozen=True)
class RenderOverride:
    """What a shot's root layer authors on its RenderSettings prim."""
    settings_path: str
    camera: str | None = None
    resolution: tuple[int, int] | None = None

    def to_json(self) -> dict:
        return {
            'settings_path': self.settings_path,
            'camera': self.camera,
            'resolution': list(self.resolution) if self.resolution else None,
        }

    @staticmethod
    def from_json(data) -> 'RenderOverride | None':
        if not isinstance(data, dict) or not data.get('settings_path'):
            return None
        resolution = data.get('resolution')
        return RenderOverride(
            str(data['settings_path']),
            data.get('camera'),
            tuple(resolution) if resolution else None,
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


def plan_override(render: dict | None, settings_path: str | None,
                  camera_exists) -> RenderOverride | None:
    """The override for a shot whose resolved ``render`` settings are
    ``render``, on a defaults stage whose settings prim is ``settings_path``.
    ``camera_exists(path)`` says whether a camera prim is at ``path``.
    None when the project did not opt in, the stage has no settings prim,
    or there is nothing to author."""
    if not applies(render) or not settings_path:
        return None
    camera = render.get('camera')
    camera = camera if isinstance(camera, str) and camera.startswith('/') and camera_exists(camera) else None
    resolution = valid_resolution(render.get('resolution'))
    if camera is None and resolution is None:
        return None
    return RenderOverride(settings_path, camera, resolution)


def override_usda(override: RenderOverride) -> str:
    """The ``over`` prims that author ``override``, to append to a layer."""
    names = [name for name in override.settings_path.split('/') if name]
    lines = []
    for depth, name in enumerate(names):
        pad = '    ' * depth
        lines.append(f'{pad}over "{name}"')
        lines.append(f'{pad}{{')
    pad = '    ' * len(names)
    if override.camera is not None:
        lines.append(f'{pad}rel camera = <{override.camera}>')
    if override.resolution is not None:
        w, h = override.resolution
        lines.append(f'{pad}uniform int2 resolution = ({w}, {h})')
    for depth in reversed(range(len(names))):
        lines.append(f'{"    " * depth}}}')
    return '\n'.join(lines)


def plan_for_defaults(render: dict | None, defaults_path) -> RenderOverride | None:
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

    return plan_override(render, settings_path, camera_exists)
