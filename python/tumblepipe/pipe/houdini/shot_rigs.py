"""Camera rigs other packages give a shot's layout and animation workfiles.

With the project setting *Layout in Animate* on (``config.layout_animate``)
layout animates the shot on the USD stage in an APEX Animate LOP, and the
shot camera is a rig there, not a Camera LOP. Animation copies layout's keys
and rebuilds the same rig in its dive. Which rig is a package's choice: a
package registers one with :func:`register_camera_rig` (TumbleRig its
``th::apex_camera``), and TumblePipe calls it. Without one, the templates use
SideFX's own camera rig (Configure APEX Rig, Camera; APEX Scene Add Camera).

A registration is three functions, any of them None:

- ``layout(lop_node, shot_uri, rig_path)``: put the camera rig on the
  stage after ``lop_node``, its character at ``rig_path``
  (``th::layout_rigs`` passes ``/layout_rigs/camera``); returns the last
  LOP of what it added. Layout's publish bakes the scene and grafts the
  rig's camera (the first Camera prim under ``rig_path``) to
  ``CAMERA_PATH``.
- ``animation(sop_node, shot_uri, settings)``: add the rig to the APEX scene
  after ``sop_node`` in th::animate's dive, built with ``settings`` (what
  ``settings`` returned in layout, from the layout publish), its character
  named ``camera`` as layout's is (``/layout_rigs/camera``) so layout's
  keys find it; returns the last SOP of what it added.
- ``settings(lop_node)``: the rig's settings in the layout workfile, as a
  JSON-able dict, published with the layout keys so animation rebuilds the
  rig the way layout had it.

Like ``ui.shot_slate.register_camera_source``, nothing here imports the
package that registers: it calls in, TumblePipe only calls back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

#: Where every department finds the shot camera (the RenderSettings camera,
#: the playblast, the shot slate).
CAMERA_PATH = '/cameras/render_camera'


@dataclass(frozen=True)
class CameraRig:
    """One package's camera rig for shot workfiles."""
    name: str
    layout: Callable | None = None
    animation: Callable | None = None
    settings: Callable | None = None


_RIGS: list[CameraRig] = []


def register_camera_rig(name: str, *, layout=None, animation=None, settings=None) -> CameraRig:
    """Register ``name``'s camera rig (see the module docstring for the three
    functions). Registering a name again replaces it; the newest registration
    is the one :func:`camera_rig` returns."""
    rig = CameraRig(name, layout, animation, settings)
    unregister_camera_rig(name)
    _RIGS.append(rig)
    return rig


def unregister_camera_rig(name: str) -> None:
    _RIGS[:] = [rig for rig in _RIGS if rig.name != name]


def camera_rig(name: str | None = None) -> CameraRig | None:
    """The registered camera rig called ``name``, or the newest one; None
    when no package registered one (the templates then use SideFX's)."""
    if name is None:
        return _RIGS[-1] if _RIGS else None
    return next((rig for rig in _RIGS if rig.name == name), None)


def camera_rigs() -> list[CameraRig]:
    """Every registered camera rig, oldest first."""
    return list(_RIGS)
