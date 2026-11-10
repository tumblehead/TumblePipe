"""The render camera's frame, following the entity's configured resolution.

A shot's resolution lives in the database (``render.resolution``), but the
Camera LOP a layout scene builds its render camera with defaults to a 16:9
aspect ratio, and nothing tied the two together: a project set to 1920x1920
still framed its layout at 16:9 until someone added a Render Settings LOP
by hand. The layout template binds the camera's aspect ratio parms to
:func:`aspect` with a Python expression, so the frame follows the database,
including edits made after the workfile was created.
"""

import hou

from tumblepipe.config.renderer import get_entity_render_settings
from tumblepipe.util.uri import Uri


def resolution(entity: str) -> tuple[int, int] | None:
    """The configured (width, height) for ``entity``, a URI string; None
    when the entity has no usable resolution."""
    try:
        settings = get_entity_render_settings(Uri.parse(entity))
    except Exception:
        return None
    value = settings.get('resolution')
    if not value or len(value) != 2:
        return None
    width, height = int(value[0]), int(value[1])
    if width <= 0 or height <= 0:
        return None
    return width, height


def aspect(entity: str, axis: int) -> int:
    """One component of the camera's aspect ratio for ``entity``: the
    resolution's width (axis 0) or height (axis 1). Falls back to 16:9,
    the Camera LOP's own default."""
    res = resolution(entity) or (16, 9)
    return res[axis]


def bind_aspect(camera_node, entity_uri: Uri) -> None:
    """Drive a Camera LOP's aspect ratio from ``entity_uri``'s resolution."""
    camera_node.parm('aperture').set('setratio')
    for axis, parm_name in enumerate(('aspectratiox', 'aspectratioy')):
        camera_node.parm(parm_name).setExpression(
            "__import__('tumblepipe.pipe.houdini.render_camera', "
            f"fromlist=['aspect']).aspect({str(entity_uri)!r}, {axis})",
            hou.exprLanguage.Python,
        )


# ── A LOP Import Camera's frame ─────────────────────────────────────────
#
# A ``lopimportcam`` takes its focal length, aperture and aspect ratio from
# the USD camera, but its resolution is a plain parm left at Houdini's
# 1280 x 720 default: USD keeps the resolution on the RenderSettings prim,
# not on the camera. A COP Camera Import read through one (th::import_scene,
# th::import_lop_camera) then framed a 1920 x 1080 shot at 1280 x 720, so a
# Rasterize Geometry pass came out at two thirds of the render's size. The
# embedded camera's ``resx`` is bound to :func:`stage_render_width`; its
# ``resy`` keeps Houdini's own expression, the width times the camera's
# aspect ratio.

LOP_IMPORT_CAMERA_WIDTH = 1280
"""The ``lopimportcam`` default, kept when the stage names no resolution."""

RENDER_SETTINGS_ROOT = '/Render'
"""Where the pipeline's stages keep their RenderSettings prims."""


def pick_render_resolution(
    settings_path: str,
    resolutions: dict[str, tuple[int, int]],
) -> tuple[int, int] | None:
    """The resolution the stage renders at.

    ``settings_path`` is the stage's ``renderSettingsPrimPath`` metadata
    ('' when unauthored) and ``resolutions`` every RenderSettings prim under
    :data:`RENDER_SETTINGS_ROOT` with its resolution, in stage order. The
    named prim wins; without one (or naming none of them), the first. A
    non-positive resolution is no resolution.
    """
    def usable(value):
        return (value is not None and len(value) == 2
                and int(value[0]) > 0 and int(value[1]) > 0)

    if settings_path in resolutions and usable(resolutions[settings_path]):
        value = resolutions[settings_path]
        return int(value[0]), int(value[1])
    for value in resolutions.values():
        if usable(value):
            return int(value[0]), int(value[1])
    return None


def stage_render_width(camera_node, lop_parm: str = 'loppath') -> int:
    """The render width of the stage a ``lopimportcam`` reads its camera from
    (its ``loppath`` LOP), for the node's ``resx`` expression. Falls back to
    :data:`LOP_IMPORT_CAMERA_WIDTH`."""
    try:
        lop = camera_node.node(camera_node.parm(lop_parm).eval())
        stage = lop.stage() if lop is not None else None
    except hou.Error:
        stage = None
    if stage is None:
        return LOP_IMPORT_CAMERA_WIDTH
    from pxr import Usd, UsdRender

    root = stage.GetPrimAtPath(RENDER_SETTINGS_ROOT)
    resolutions = {}
    if root:
        for prim in Usd.PrimRange(root):
            if prim.IsA(UsdRender.Settings):
                value = UsdRender.Settings(prim).GetResolutionAttr().Get()
                resolutions[str(prim.GetPath())] = (
                    tuple(value) if value is not None else None
                )
    settings_path = str(stage.GetMetadata('renderSettingsPrimPath') or '')
    resolution = pick_render_resolution(settings_path, resolutions)
    return resolution[0] if resolution else LOP_IMPORT_CAMERA_WIDTH


LOP_IMPORT_CAMERA_WIDTH_EXPR = (
    "__import__('tumblepipe.pipe.houdini.render_camera',"
    "fromlist=['stage_render_width']).stage_render_width(pwd())"
)
"""The ``resx`` expression (Python) on an embedded ``lopimportcam``. No
whitespace: it is written into the HDAs' ``.chn`` sections (otls/README.md)."""
