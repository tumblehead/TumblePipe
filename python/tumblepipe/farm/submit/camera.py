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
(:data:`SCENE_SETTINGS`: samples, motion blur, DOF), each on its own: the
strongest layer that authors that attribute on the RenderSettings prim wins,
then the project default; one nothing authors renders at Karma's own default
(``None``). The dialog shows them as what ``⟨from scene⟩`` means (#69).

It is a preview of the answer, not the answer: a camera relationship or
setting authored somewhere this skips (a sublayer of an export, an asset)
would compose the same way but not show here. The farm still asks the
composed stage.

Split like ``submit_status``: :func:`plan_probe` does the config reads on the
main thread, :func:`read_camera` only opens files, on a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# RenderSettings prims sit near the root (/Render/rendersettings,
# /scene/Render/rendersettings); not descending further keeps an animation
# layer's thousands of prims out of the walk.
_MAX_DEPTH = 3

LAYER_SUFFIXES = ('.usd', '.usda', '.usdc')

#: The dialog's scene overrides and the RenderSettings attribute each one
#: overrides on the farm. Must match ``batch_submit.RENDER_OVERRIDE_MAP``
#: (``tests/test_farm_submit_scene_values.py`` pins it); not imported, so
#: this module stays light on the dialog's worker thread.
SCENE_SETTINGS = {
    'samples': 'karma:global:pathtracedsamples',
    'mblur': 'karma:object:mblur',
    'dof': 'karma:global:enable_dof',
}


@dataclass(frozen=True)
class CameraProbe:
    uri: str
    # (department, export:/<entity>/<channel>/<department>), strongest first.
    export_dirs: tuple[tuple[str, Path], ...]
    root_defaults: Path | None
    is_version: Callable[[str], bool] = field(compare=False, repr=False)
    version_code: Callable[[str], int] = field(compare=False, repr=False)


@dataclass(frozen=True)
class RenderCamera:
    uri: str
    camera: str | None
    source: str = ''       # the department whose layer authored it, or 'project default'
    error: str | None = None
    # Settings key (SCENE_SETTINGS) -> (value, source). A key nothing
    # authors maps to (None, ''): the renderer's own default.
    settings: dict = field(default_factory=dict, compare=False)


def plan_probe(entity_uri, channel: str, department: str) -> CameraProbe:
    """Resolve the folders to read for ``entity_uri`` rendered up to ``department``.

    Main thread only (config reads).
    """
    from tumblepipe.api import api
    from tumblepipe import naming
    from tumblepipe.util.uri import Uri
    from tumblepipe.pipe.paths import get_export_path
    from tumblepipe.farm.pipelines.preview import department_cut, entity_context

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
    for key, attribute in SCENE_SETTINGS.items():
        result[key] = (None, '')
        for source, authored in layers:
            if attribute in authored:
                result[key] = (authored[attribute], source)
                break
    return result


def _open_layer(path: Path):
    from pxr import Sdf
    return Sdf.Layer.FindOrOpen(str(path))


def _layer_camera(path: Path) -> str | None:
    layer = _open_layer(path)
    if layer is None:
        return None
    return authored_camera(layer)


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
        yield 'project default', probe.root_defaults


def read_camera(probe: CameraProbe) -> RenderCamera:
    """Worker thread: the camera the strongest published layer names, and
    the scene's value of each :data:`SCENE_SETTINGS` attribute.

    Stops opening layers once the camera and every setting are found; a
    setting nothing authors costs the same walk the camera already made
    when only the project default names it.
    """
    try:
        camera, camera_source = None, ''
        attributes = set(SCENE_SETTINGS.values())
        authored: list = []
        seen: set = set()
        for source, path in _probe_layers(probe):
            layer = _open_layer(path)
            if layer is None:
                continue
            if camera is None:
                camera = authored_camera(layer)
                camera_source = source if camera else ''
            values = authored_settings(layer, attributes - seen)
            seen |= values.keys()
            authored.append((source, values))
            if camera is not None and seen >= attributes:
                break
        return RenderCamera(
            probe.uri, camera, camera_source,
            settings=strongest_settings(authored),
        )
    except Exception as error:  # noqa: BLE001 — shown in the dialog, not raised
        return RenderCamera(probe.uri, None, error=str(error))
