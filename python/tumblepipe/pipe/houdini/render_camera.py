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
