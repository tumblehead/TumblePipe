"""Audit: the shot slate frames a Solaris camera as the old /obj camera did.

    hython scripts/verify_slate_lop_camera.py

Magnus/Soren 2026-11-20: shot workfiles are Solaris only, so the animation
template no longer makes ``/obj/anim_camera``, a LOP Import Camera lifting
``/cameras/render_camera`` out of ``camera_edit``. The shot slate was its
only reader in TumblePipe: it placed the chip under the camera's frame from
the /obj camera's world transform, focal length, aperture and resolution.
For a viewer looking through the USD camera prim itself it now reads those
from the prim (``shot_slate.usd_camera``).

In memory this keys a camera prim over three frames (transform under a
transformed parent, focal length, apertures), lifts it into an /obj LOP
Import Camera as the old template did, and checks at each frame that the
prim read gives the /obj camera's frame: world matrix, aperture over focal
length (the /obj camera holds both in mm, USD in tenths of a scene unit;
the frame's width is their ratio) and frame aspect. Then that a prim which is no camera, or no prim,
reads as none. Needs Houdini (python/ on PYTHONPATH); touches no project
data.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

import hou  # noqa: E402

from tumblepipe.pipe.houdini.ui import shot_slate  # noqa: E402

_failures = []

CAMERA = '/cameras/render_camera'
FRAMES = (1001, 1002, 1003)

# Keyed per frame: (translate, rotate xyz, focal, h aperture, v aperture)
KEYS = {
    1001: ((1.0, 2.0, 30.0), (0.0, 10.0, 0.0), 35.0, 36.0, 24.0),
    1002: ((-4.5, 3.25, 22.0), (-12.0, 35.0, 3.0), 50.0, 36.0, 20.25),
    1003: ((8.0, -1.5, 12.0), (20.0, -80.0, -7.5), 85.0, 24.89, 18.66),
}

AUTHOR = '''
from pxr import Gf, Sdf, Usd, UsdGeom
stage = hou.pwd().editableStage()
# a transformed parent: the world matrix is the camera's under it
parent = UsdGeom.Xform.Define(stage, '/cameras')
parent.AddTranslateOp().Set(Gf.Vec3d(5.0, -2.0, 1.5))
parent.AddRotateYOp().Set(15.0)
cam = UsdGeom.Camera.Define(stage, '/cameras/render_camera')
xf = UsdGeom.Xformable(cam)
t_op, r_op = xf.AddTranslateOp(), xf.AddRotateXYZOp()
for frame, (t, r, focal, h, v) in KEYS.items():
    tc = Usd.TimeCode(frame)
    t_op.Set(Gf.Vec3d(*t), tc)
    r_op.Set(Gf.Vec3f(*r), tc)
    cam.GetFocalLengthAttr().Set(focal, tc)
    cam.GetHorizontalApertureAttr().Set(h, tc)
    cam.GetVerticalApertureAttr().Set(v, tc)
UsdGeom.Xform.Define(stage, '/cameras/not_a_camera')
'''


def check(label, ok, detail=''):
    print(f"{'ok   ' if ok else 'FAIL '} {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        _failures.append(label)


def close(a, b, tol=1e-4):
    return abs(a - b) <= tol * max(1.0, abs(b))


def main():
    stage_net = hou.node('/stage')
    author = stage_net.createNode('pythonscript', 'author_camera')
    author.parm('python').set(f'KEYS = {KEYS!r}\n' + AUTHOR)
    # the old template's camera: a LOP Import Camera of the LOP
    obj_cam = hou.node('/obj').createNode('lopimportcam', 'anim_camera')
    obj_cam.parm('loppath').set(author.path())
    obj_cam.parm('primpath').set(CAMERA)

    for frame in FRAMES:
        hou.setFrame(frame)
        got = shot_slate.usd_camera(author.stage(), CAMERA, frame)
        check(f"frame {frame}: the prim reads as a camera", got is not None)
        if got is None:
            continue
        want_rows = obj_cam.worldTransform().asTupleOfTuples()
        matrix_ok = all(close(a, b) for ra, rb in zip(got['xform'], want_rows)
                        for a, b in zip(ra, rb))
        check(f"frame {frame}: world matrix is the /obj camera's", matrix_ok,
              f"prim {got['xform']} obj {want_rows}")
        # The /obj camera holds them in mm, USD in tenths of a scene unit
        # (x100 here): the frame's width is their ratio, which must match.
        prim_fov = got['aperture'] / got['focal']
        obj_fov = obj_cam.evalParm('aperture') / obj_cam.evalParm('focal')
        check(f"frame {frame}: aperture over focal is the /obj camera's",
              close(prim_fov, obj_fov), f"{prim_fov} vs {obj_fov}")
        obj_aspect = (obj_cam.evalParm('resx') * obj_cam.evalParm('aspect')
                      / obj_cam.evalParm('resy'))
        # resy is an integer: the /obj camera's aspect is rounded to a pixel
        check(f"frame {frame}: frame aspect is the /obj camera's",
              close(got['aspect'], obj_aspect, tol=2.0 / obj_cam.evalParm('resy')),
              f"{got['aspect']} vs {obj_aspect}")
        _t, _r, focal, h, v = KEYS[frame]
        check(f"frame {frame}: the playhead's keys, not another frame's",
              close(got['focal'], focal) and close(got['aspect'], h / v))

    stage = author.stage()
    check("a prim that is no camera reads as none",
          shot_slate.usd_camera(stage, '/cameras/not_a_camera', 1001) is None)
    check("no prim reads as none",
          shot_slate.usd_camera(stage, '/cameras/missing', 1001) is None)

    if _failures:
        print("\n%d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
