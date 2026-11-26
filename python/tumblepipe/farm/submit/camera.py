"""Which camera a farm render of an entity will look through, for the Farm Submit dialog.

The farm renders through the camera the composed stage's RenderSettings prim
names (``usd.find_render_camera_prim_path``). Composing a shot's whole staged
build to show one path in a dialog is far too slow, so this reads the same
answer off the published layers directly: the latest export of each
department in the render cut, strongest (last in pipeline order) first, and
the first one that authors a RenderSettings ``camera`` relationship wins —
which is what composition would pick. With none, the project's
``root_default_prims.usda`` speaks.

The same pass reads the render settings the dialog can override
(``settings.RENDER_OVERRIDES``: samples, motion blur, DOF), each on its own: the
strongest layer that authors that attribute on the RenderSettings prim wins,
then the project default; one nothing authors renders at Karma's own default
(``None``). The dialog shows them as what ``⟨from scene⟩`` means (#69).

It is a preview of the answer, not the answer: a camera relationship or
setting authored somewhere this skips (a sublayer of an export, an asset)
would compose the same way but not show here. The farm still asks the
composed stage.

Split like ``submit_status``: :func:`plan_probe` does the config reads on the
main thread, :func:`read_camera` only opens files, on a worker thread.

The Camera field is a farm override (Remy, 2026-11-20): unset, the scene's
camera renders; set, the farm renders through it. :func:`effective_camera`
is the order a render's camera is decided in, :func:`camera_note` what is
worth a second look (a department left on Houdini's ``/cameras/camera1``,
the default of every stock Karma / Render Settings LOP, which is how
Remy's shots came to name a camera nothing defined), :func:`camera_options`
the field's menu. Pure; ``tests/test_farm_submit_camera.py`` pins them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tumblepipe.farm.submit import settings as resolve
from tumblepipe.farm.submit.settings import RENDER_OVERRIDES

# RenderSettings prims sit near the root (/Render/rendersettings,
# /scene/Render/rendersettings); not descending further keeps an animation
# layer's thousands of prims out of the walk.
_MAX_DEPTH = 3

LAYER_SUFFIXES = ('.usd', '.usda', '.usdc')

@dataclass(frozen=True)
class CameraProbe:
    uri: str
    # (department, export:/<entity>/<channel>/<department>), strongest first.
    export_dirs: tuple[tuple[str, Path], ...]
    root_defaults: Path | None
    is_version: Callable[[str], bool] = field(compare=False, repr=False)
    version_code: Callable[[str], int] = field(compare=False, repr=False)


#: The source of a camera the project's ``root_default_prims.usda`` names.
PROJECT_DEFAULT = 'project default'
#: The source of the camera the shot's render settings in config name
#: (``render.camera``, on the shot's root layer when ``apply_config`` is on).
SHOT_SETTING = 'shot settings'
#: The source of Farm Submit's own Camera.
FARM_SUBMIT = 'Farm Submit'


@dataclass(frozen=True)
class RenderCamera:
    uri: str
    camera: str | None
    source: str = ''       # the department whose layer authored it, or PROJECT_DEFAULT
    error: str | None = None
    # Settings key (settings.RENDER_OVERRIDES) -> (value, source). A key nothing
    # authors maps to (None, ''): the renderer's own default.
    settings: dict = field(default_factory=dict, compare=False)
    # Camera prims the layers read define, in reading order (the Camera
    # field's menu), and those of the project default alone (None when it
    # was not read): config's camera only reaches a render when it is one.
    cameras: tuple = field(default=(), compare=False)
    root_cameras: tuple | None = field(default=None, compare=False)


def plan_probe(entity_uri, channel: str, department: str) -> CameraProbe:
    """Resolve the folders to read for ``entity_uri`` rendered up to ``department``.

    Main thread only (config reads).
    """
    from tumblepipe.api import api
    from tumblepipe import naming
    from tumblepipe.util.uri import Uri
    from tumblepipe.pipe.paths import get_export_path
    from tumblepipe.config.department import entity_context
    from tumblepipe.farm.pipelines.preview import department_cut

    cut, _pool = department_cut(entity_context(entity_uri), department)
    export_dirs = tuple(
        (name, Path(get_export_path(entity_uri, channel, name, 'v0001')).parent)
        for name in reversed(cut)
    )
    root_defaults = api.storage.resolve(
        Uri.parse('config:/usd/root_default_prims.usda')
    )
    return CameraProbe(
        uri=str(entity_uri),
        export_dirs=export_dirs,
        root_defaults=Path(root_defaults) if root_defaults else None,
        is_version=naming.is_valid_version_name,
        version_code=naming.get_version_code,
    )


def authored_camera(layer) -> str | None:
    """The ``camera`` target a RenderSettings prim spec in ``layer`` authors.

    An untyped spec (an ``over`` on the settings prim) counts too, as long as
    it is not a RenderProduct — products carry a ``productName``.
    """
    def visit(spec, depth: int) -> str | None:
        rel = spec.relationships.get('camera') if spec.relationships else None
        typed = spec.typeName == 'RenderSettings'
        over = not spec.typeName and 'productName' not in spec.attributes
        if rel is not None and (typed or over):
            targets = rel.targetPathList.GetAddedOrExplicitItems()
            if targets:
                return str(targets[0])
        if depth >= _MAX_DEPTH:
            return None
        for child in spec.nameChildren:
            found = visit(child, depth + 1)
            if found:
                return found
        return None

    for root in layer.rootPrims:
        found = visit(root, 1)
        if found:
            return found
    return None


def _attribute_value(layer, attr):
    """An attribute spec's default, else its first time sample, else None."""
    if attr.HasDefaultValue():
        return attr.default
    times = layer.ListTimeSamplesForPath(attr.path)
    if times:
        return layer.QueryTimeSample(attr.path, min(times))
    return None


def authored_settings(layer, names) -> dict:
    """The values ``layer`` authors for the attributes ``names`` on a
    RenderSettings prim spec (typed, or an ``over`` that is not a
    RenderProduct, as :func:`authored_camera` reads). Only the authored ones
    are keys; the first spec found that authors one wins."""
    wanted = set(names)
    found: dict = {}

    def visit(spec, depth: int) -> None:
        typed = spec.typeName == 'RenderSettings'
        over = not spec.typeName and 'productName' not in spec.attributes
        if typed or over:
            for name in wanted - found.keys():
                attr = spec.attributes.get(name) if spec.attributes else None
                if attr is None:
                    continue
                value = _attribute_value(layer, attr)
                if value is not None:
                    found[name] = value
        if depth >= _MAX_DEPTH or not wanted - found.keys():
            return
        for child in spec.nameChildren:
            visit(child, depth + 1)

    for root in layer.rootPrims:
        visit(root, 1)
        if not wanted - found.keys():
            break
    return found


def strongest_settings(layers) -> dict:
    """Settings key -> (value, source), from ``layers`` strongest first.

    ``layers`` is ``(source, {attribute: value})`` per layer, strongest
    first, the project default last. Each setting is decided on its own,
    as composition decides each attribute: the first layer authoring it
    wins. One no layer authors is ``(None, '')``: the renderer's default.
    """
    result = {}
    for key, attribute in RENDER_OVERRIDES.items():
        result[key] = (None, '')
        for source, authored in layers:
            if attribute in authored:
                result[key] = (authored[attribute], source)
                break
    return result


def _open_layer(path: Path):
    from pxr import Sdf
    return Sdf.Layer.FindOrOpen(str(path))


def _latest_version_dir(probe: CameraProbe, directory: Path) -> Path | None:
    try:
        names = [
            entry.name for entry in directory.iterdir()
            if entry.is_dir() and probe.is_version(entry.name)
        ]
    except FileNotFoundError:
        # Never rendered / exported: the folder is made on first write.
        return None
    if not names:
        return None
    return directory / max(names, key=probe.version_code)


def _probe_layers(probe: CameraProbe):
    """``(source, path)`` for every layer the probe reads, strongest first:
    each cut department's latest export, then the project default."""
    for department, directory in probe.export_dirs:
        version_dir = _latest_version_dir(probe, directory)
        if version_dir is None:
            continue
        for path in sorted(version_dir.iterdir()):
            if path.suffix in LAYER_SUFFIXES:
                yield department, path
    if probe.root_defaults is not None and probe.root_defaults.exists():
        yield PROJECT_DEFAULT, probe.root_defaults


def authored_cameras(layer) -> list[str]:
    """The Camera prims ``layer`` defines near its root (the depth the
    settings walk reads: ``/cameras/x``, ``/scene/cameras/x``)."""
    found: list[str] = []

    def visit(spec, depth: int) -> None:
        if spec.typeName == 'Camera':
            found.append(str(spec.path))
        if depth >= _MAX_DEPTH:
            return
        for child in spec.nameChildren:
            visit(child, depth + 1)

    for root in layer.rootPrims:
        visit(root, 1)
    return found


def read_camera(probe: CameraProbe) -> RenderCamera:
    """Worker thread: the camera the strongest published layer names, and
    the scene's value of each ``settings.RENDER_OVERRIDES`` attribute.

    Stops opening layers once the camera and every setting are found; a
    setting nothing authors costs the same walk the camera already made
    when only the project default names it. The project default (one small
    file) is read either way, for the cameras it defines.
    """
    try:
        camera, camera_source = None, ''
        attributes = set(RENDER_OVERRIDES.values())
        authored: list = []
        seen: set = set()
        cameras: list[str] = []
        root_cameras = None
        for source, path in _probe_layers(probe):
            layer = _open_layer(path)
            if layer is None:
                continue
            defined = authored_cameras(layer)
            cameras.extend(c for c in defined if c not in cameras)
            if source == PROJECT_DEFAULT:
                root_cameras = tuple(defined)
            if camera is None:
                camera = authored_camera(layer)
                camera_source = source if camera else ''
            values = authored_settings(layer, attributes - seen)
            seen |= values.keys()
            authored.append((source, values))
            if camera is not None and seen >= attributes:
                break
        if root_cameras is None and probe.root_defaults is not None and probe.root_defaults.exists():
            layer = _open_layer(probe.root_defaults)
            if layer is not None:
                root_cameras = tuple(authored_cameras(layer))
                cameras.extend(c for c in root_cameras if c not in cameras)
        return RenderCamera(
            probe.uri, camera, camera_source,
            settings=strongest_settings(authored),
            cameras=tuple(cameras), root_cameras=root_cameras,
        )
    except Exception as error:  # noqa: BLE001 — shown in the dialog, not raised
        return RenderCamera(probe.uri, None, error=str(error))


# ── the Camera field (Remy, 2026-11-20) ───────────────────
#
# Farm Submit's Camera is a farm override like Samples: left unset nothing
# is sent and the scene's own camera renders (node-authored settings come
# from the node); set, the farm renders through it (the collapse authors it
# on the settings prim and refuses one that is not on the stage). The
# functions below decide what the field shows and offers; pure, no files.


def configured_camera(properties) -> str | None:
    """The camera the shot's render settings in config name, when they reach
    its renders: ``render.camera`` with ``render.apply_config`` on
    (``pipe.usd.render_overrides``), as a prim path. None otherwise."""
    from tumblepipe.config.render_config import valid_camera
    from tumblepipe.pipe.usd.render_overrides import applies

    render = properties.get('render') if isinstance(properties, dict) else None
    if not isinstance(render, dict) or not applies(render):
        return None
    return valid_camera(render.get('camera'))


def effective_camera(override, found: RenderCamera | None, configured: str | None) -> tuple[str | None, str]:
    """``(camera, source)`` a farm render of one shot goes through, as far as
    the dialog can tell, strongest first:

    1. Farm Submit's Camera (``override``), when it is a prim path;
    2. the camera a department's published render settings name
       (``found.source`` is that department);
    3. the shot's configured camera (:func:`configured_camera`), which the
       shot's root layer authors only when the project default defines it;
    4. the camera the project default names;
    5. ``(None, '')``: nothing names one.

    The order composition gives the layers (department exports over the
    root layer over the project default), with the farm's opinion on top.
    """
    from tumblepipe.pipe.usd.render_settings import valid_camera_override

    picked = valid_camera_override(override)
    if picked is not None:
        return picked, FARM_SUBMIT
    if found is not None and found.camera and found.source != PROJECT_DEFAULT:
        return found.camera, found.source
    defined = found.root_cameras if found is not None else None
    if configured and defined is not None and configured in defined:
        return configured, SHOT_SETTING
    if found is not None and found.camera:
        return found.camera, PROJECT_DEFAULT
    return None, ''


def camera_note(override, found: RenderCamera | None, configured: str | None) -> str | None:
    """A line for the artist when the camera the shot renders through is
    worth a second look, else None. Never when Farm Submit's Camera is set.

    - Houdini's default ``/cameras/camera1`` named by a department: the
      Camera of a Karma or Render Settings LOP left as created.
    - A department's camera that is not the shot's configured one.
    """
    from tumblepipe.pipe.usd.render_settings import (
        HOUDINI_DEFAULT_CAMERA, valid_camera_override,
    )

    if valid_camera_override(override) is not None:
        return None
    camera, source = effective_camera(None, found, configured)
    if camera is None or source in (PROJECT_DEFAULT, SHOT_SETTING):
        return None
    if camera == HOUDINI_DEFAULT_CAMERA:
        return (
            f"{source}'s export names {camera}, the default of Houdini's "
            f"Karma LOPs: check the Camera of {source}'s render settings node, "
            "or pick the camera here"
        )
    if configured and camera != configured:
        return f"{source}'s export names {camera}; the shot's camera setting is {configured}"
    return None


@dataclass(frozen=True)
class Shot:
    """One rendering shot as the Camera field reads it: its ``name``, the
    ``channel`` its camera is read from, its ``configured`` camera
    (:func:`configured_camera`) and its lookup (``found``, None while it
    is being read)."""
    name: str
    channel: str
    configured: str | None
    found: RenderCamera | None


@dataclass(frozen=True)
class CameraSummary:
    """What the Camera field shows for the rendering shots.

    ``options`` its menu; ``placeholder`` what the empty field says;
    ``read`` whether every shot's lookup is in (until then ``lines`` and
    ``notes`` are empty); ``lines`` one per shot for the tooltip; ``notes``
    the lines worth a second look (:func:`camera_note`), named by shot when
    there are several.
    """
    options: list[str]
    placeholder: str
    read: bool
    lines: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def summary(shots, override) -> CameraSummary:
    """The Camera field over ``shots`` (:class:`Shot`, in row order) with
    the form's ``override`` (None when unpinned): the scene's camera when
    they agree, ``varies`` when not, ``Reading…`` while a lookup is out."""
    read = bool(shots) and all(s.found is not None for s in shots)
    options = camera_options(
        [s.configured for s in shots if s.configured],
        [s.found for s in shots] if read else [],
    )
    if not read:
        return CameraSummary(options, "Reading…" if shots else resolve.FROM_SCENE_TEXT, False)
    lines, notes, scene = [], [], set()
    for shot in shots:
        found = shot.found
        if found.error:
            lines.append(f"{shot.name}: not read  {found.error}")
            continue
        camera, source = effective_camera(None, found, shot.configured)
        scene.add(camera)
        lines.append(
            f"{shot.name}: {camera}  ({source}, {shot.channel})" if camera
            else f"{shot.name}: none named"
        )
        note = camera_note(override, found, shot.configured)
        if note:
            notes.append(note if len(shots) == 1 else f"{shot.name}: {note}")
    if len(scene) == 1 and None not in scene:
        placeholder = f"from scene: {next(iter(scene))}"
    elif scene and scene != {None}:
        placeholder = f"varies: {resolve.PER_ENTITY_TEXT}"
    else:
        placeholder = "from scene: none named"
    return CameraSummary(options, placeholder, True, tuple(lines), tuple(notes))


def scene_values(key: str, shots) -> tuple[str | None, tuple[str, ...]]:
    """``(placeholder, lines)`` of override field ``key`` (Samples, Motion
    blur, DOF) over the shots' lookups, once every one is read
    (:attr:`CameraSummary.read`; pass none before): the scene's values
    (``settings.scene_placeholder``, None with none read) and a line per
    shot naming its value and where it is authored. A shot whose lookup
    failed is named, and left out of the placeholder."""
    kind = resolve.FIELDS_BY_KEY[key].kind
    values, lines = [], []
    for shot in shots:
        found = shot.found
        if found.error:
            lines.append(f"{shot.name}: not read  {found.error}")
            continue
        value, source = found.settings.get(key, (None, ''))
        values.append(value)
        shown = resolve.scene_value_text(value, kind)
        lines.append(f"{shot.name}: {shown}  ({source})" if source else f"{shot.name}: {shown}")
    return resolve.scene_placeholder(values, kind), tuple(lines)


def camera_options(configured, found_list) -> list[str]:
    """The Camera field's menu: the configured cameras of the shots
    (``configured``, in order), the cameras each render goes through, then
    every camera the layers read define, each once. ``found_list`` holds
    each shot's :class:`RenderCamera` (or None, not read yet)."""
    options: list[str] = []

    def add(path) -> None:
        if path and path not in options:
            options.append(path)

    for path in configured:
        add(path)
    for found in found_list:
        if found is not None:
            add(found.camera)
    for found in found_list:
        if found is not None:
            for path in found.cameras:
                add(path)
    return options
