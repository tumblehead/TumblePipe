"""Which camera a farm render of an entity will look through, for the Farm Submit dialog.

The farm renders through the camera the composed stage's RenderSettings prim
names (``usd.find_render_camera_prim_path``). Composing a shot's whole staged
build to show one path in a dialog is far too slow, so this reads the same
answer off the published layers directly: the latest export of each
department in the render cut, strongest (last in pipeline order) first, and
the first one that authors a RenderSettings ``camera`` relationship wins —
which is what composition would pick. With none, the project's
``root_default_prims.usda`` speaks.

It is a preview of the answer, not the answer: a camera relationship authored
somewhere this skips (a sublayer of an export, an asset) would compose the
same way but not show here. The farm still asks the composed stage.

Split like ``farm_status``: :func:`plan_probe` does the config reads on the
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


def plan_probe(entity_uri, channel: str, department: str) -> CameraProbe:
    """Resolve the folders to read for ``entity_uri`` rendered up to ``department``.

    Main thread only (config reads).
    """
    from tumblepipe.api import api
    from tumblepipe.util.uri import Uri
    from tumblepipe.pipe.paths import get_export_path
    from tumblepipe.farm.jobs.houdini._preview import department_cut, entity_context

    cut, _pool = department_cut(entity_context(entity_uri), department)
    export_dirs = tuple(
        (name, Path(get_export_path(entity_uri, channel, name, 'v0001')).parent)
        for name in reversed(cut)
    )
    root_defaults = api.storage.resolve(
        Uri.parse_unsafe('config:/usd/root_default_prims.usda')
    )
    return CameraProbe(
        uri=str(entity_uri),
        export_dirs=export_dirs,
        root_defaults=Path(root_defaults) if root_defaults else None,
        is_version=api.naming.is_valid_version_name,
        version_code=api.naming.get_version_code,
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


def _layer_camera(path: Path) -> str | None:
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(str(path))
    if layer is None:
        return None
    return authored_camera(layer)


def _latest_version_dir(probe: CameraProbe, directory: Path) -> Path | None:
    try:
        names = [
            entry.name for entry in directory.iterdir()
            if entry.is_dir() and probe.is_version(entry.name)
        ]
    except OSError:
        return None
    if not names:
        return None
    return directory / max(names, key=probe.version_code)


def read_camera(probe: CameraProbe) -> RenderCamera:
    """Worker thread: the camera the strongest published layer names."""
    try:
        for department, directory in probe.export_dirs:
            version_dir = _latest_version_dir(probe, directory)
            if version_dir is None:
                continue
            for path in sorted(version_dir.iterdir()):
                if path.suffix not in LAYER_SUFFIXES:
                    continue
                camera = _layer_camera(path)
                if camera:
                    return RenderCamera(probe.uri, camera, department)
        if probe.root_defaults is not None and probe.root_defaults.exists():
            camera = _layer_camera(probe.root_defaults)
            if camera:
                return RenderCamera(probe.uri, camera, 'project default')
        return RenderCamera(probe.uri, None)
    except Exception as error:  # noqa: BLE001 — shown in the dialog, not raised
        return RenderCamera(probe.uri, None, error=str(error))
