"""Audit: build_comp's alpha follows the beauty, and Update follows the planes.

    hython scripts/verify_build_comp_alpha.py

The beauty is rendered RGBA and its A is the comp's alpha (see
docs/compositing.md, "Alpha lives in the beauty"). build_comp builds one
`alpha` subnet per channel and Update re-points it as a channel's newest
beauty changes shape. This drives the real node's Build and Update over EXR
fixtures and checks the wiring *and* the cooked pixels:

1. build from an RGB beauty (no A) is refused and builds nothing;
2. build from a raw render's RGBA beauty (`beauty.R/G/B/A`): the alpha
   subnet reads the beauty RGBA and splits output 3 (A) out, the channel's
   A is the beauty's;
3. Update to an RGBA beauty published by the denoise (bare `R,G,B,A`): every
   File COP asks for plane `C` -- a File COP asked for `beauty` on bare
   channels loads nothing ("rgba is missing");
4. Update to an RGB beauty is refused and leaves the comp as it was.

Needs Houdini (hython, with the th::build_comp HDA installed) and hoiiotool
from the same install for the fixtures. It puts this checkout's python/ first
on sys.path, so it tests the tree, not an installed package. Fixtures go to a
temp dir; no project data is read or written.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))
for _name in list(sys.modules):
    if _name == "tumblepipe" or _name.startswith("tumblepipe."):
        del sys.modules[_name]

import hou  # noqa: E402

from tumblepipe.config.timeline import BlockRange, FrameRange  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.pipe.houdini.cops import build_comp as bc  # noqa: E402

_failures = []

FRAME = 1001
CHANNEL = "chars"


def check(label, got, want):
    ok = got == want
    print(f"{'ok   ' if ok else 'FAIL '} {label}")
    if not ok:
        print(f"        got:  {got!r}")
        print(f"        want: {want!r}")
        _failures.append(label)


def close(label, got, want, tolerance=1e-3):
    ok = got is not None and abs(got - want) < tolerance
    print(f"{'ok   ' if ok else 'FAIL '} {label}")
    if not ok:
        print(f"        got:  {got!r}")
        print(f"        want: {want!r}")
        _failures.append(label)


def oiiotool(*args):
    exe = Path(os.environ["HFS"]) / "bin" / ("hoiiotool.exe" if os.name == "nt" else "hoiiotool")
    subprocess.run([str(exe), *map(str, args)], check=True, capture_output=True)


class FakeAov:
    """The two things build_comp asks an AOV record: where its frames are
    and what range they cover."""

    def __init__(self, label, root):
        self.label = label
        self.root = root

    def get_frame_range(self):
        return BlockRange(FRAME, FRAME)

    def get_aov_frame_path(self, frame):
        return self.root / self.label / f"{self.label}.{frame}.exr"


def write_render(root, beauty_fill, beauty_names):
    """One frame of a render: a beauty with ``beauty_names`` channels filled
    with ``beauty_fill``."""
    (root / "beauty").mkdir(parents=True)
    oiiotool("--create", "8x8", len(beauty_names),
             "--fill:color=" + ",".join(map(str, beauty_fill)), "8x8",
             "--chnames", ",".join(beauty_names),
             "-o", root / "beauty" / f"beauty.{FRAME:04d}.exr")
    return {"beauty": FakeAov("beauty", root)}


def refused(call):
    """True when ``call`` raises build_comp's "Cannot build the comp"."""
    try:
        call()
    except RuntimeError as error:
        return "Cannot build the comp" in str(error)
    return False


class Harness(bc.BuildComp):
    """build_comp with its project lookups answered by the test."""

    aovs = {}
    resolution = bc.Resolution.Full

    def require_entity_uri(self):
        return Uri.parse("entity:/shots/seq/shot")

    def list_render_department_names(self):
        return ["render"]

    def get_render_department_name(self):
        return "render"

    def get_source_name(self):
        return bc.Source.Render

    def get_proxy_resolution(self):
        return self.resolution

    def _resolve_aovs(self, source_name, shot_uri, resolution_name, render_department_names):
        types = {
            label: bc.AOVType.LPE if label == "beauty" else bc.AOVType.Mono
            for label in self.aovs
        }
        return {CHANNEL: dict(self.aovs)}, {CHANNEL: types}


def alpha_at_centre(node):
    """The A of the channel subnet's RGBA output at the frame's centre."""
    hou.setFrame(FRAME)
    layer = node.layer(0)
    if layer is None:
        return None
    w, h = layer.bufferResolution()
    return layer.bufferIndex(w // 2, h // 2)[3]


def rgb_at_centre(node):
    hou.setFrame(FRAME)
    layer = node.layer(0)
    if layer is None:
        return None
    w, h = layer.bufferResolution()
    return layer.bufferIndex(w // 2, h // 2)[:3]


def main():
    root = Path(tempfile.mkdtemp(prefix="tp-build-comp-alpha-"))
    # Create the node first: the HDA's OnCreated reloads build_comp, which
    # would undo the stand-ins below
    copnet = hou.node("/obj").createNode("copnet", "comp")
    comp_native = copnet.createNode("th::build_comp::1.0", "build_comp")
    saved = (bc.list_channels, bc.get_frame_range, bc.get_fps, bc.playbar)
    bc.list_channels = lambda shot_uri: [CHANNEL]
    bc.get_frame_range = lambda shot_uri: FrameRange(FRAME, FRAME, 0, 0)
    bc.get_fps = lambda shot_uri: None
    bc.playbar = SimpleNamespace(set_fps=lambda fps: None, set_frame_range=lambda frame_range: None)
    try:
        raw = write_render(root / "v0001", (0.2, 0.3, 0.4, 0.25), ["beauty.R", "beauty.G", "beauty.B", "beauty.A"])
        rgba = write_render(root / "v0002", (0.5, 0.6, 0.7, 0.6), ["R", "G", "B", "A"])
        rgb_only = write_render(root / "v0003", (0.1, 0.1, 0.1), ["R", "G", "B"])

        comp = Harness(comp_native)
        dive = comp.native().node("dive")

        # 1. an RGB beauty cannot be comped
        Harness.aovs = rgb_only
        check("rgb only: Build is refused", refused(comp._build), True)
        check("rgb only: nothing is built", dive.node(CHANNEL), None)

        # 2. built from a raw render's RGBA beauty
        Harness.aovs = raw
        comp._build()
        channel = dive.node(CHANNEL)
        alpha = channel.node("alpha")
        check("raw: an alpha subnet is built", alpha is not None, True)
        file_node = alpha.node(f"{CHANNEL}_alpha")
        check("raw: it reads the beauty's plane, RGBA", (file_node.parm("aov1").eval(), file_node.parm("type1").eval()), ("beauty", 3))
        check("raw: A is split output 3", alpha.node("outputs").inputConnections()[0].outputIndex(), 3)
        grade = channel.node("grade")
        check("raw: alpha feeds the grade's last input", grade.inputs()[-1].path(), alpha.path())
        close("raw: the channel's A is the beauty's A", alpha_at_centre(channel), 0.25)

        # 3. Updated to the denoise's RGBA publish (bare R,G,B,A)
        Harness.aovs = rgba
        comp.update()
        alpha = channel.node("alpha")
        file_node = alpha.node(f"{CHANNEL}_alpha")
        check("rgba: the alpha reads the beauty file", Path(file_node.parm("filename").eval()).name, f"beauty.{FRAME:04d}.exr")
        check("rgba: as plane C, RGBA", (file_node.parm("aov1").eval(), file_node.parm("type1").eval()), ("C", 3))
        beauty_file = channel.node("beauty").node(f"{CHANNEL}_beauty")
        check("rgba: Update re-points the beauty's plane too", beauty_file.parm("aov1").eval(), "C")
        rgb = rgb_at_centre(channel.node("beauty"))
        check("rgba: the beauty loads (not 'rgba is missing')", rgb is not None and abs(rgb[0] - 0.5) < 1e-3, True)
        close("rgba: the channel's A is the beauty's A", alpha_at_centre(channel), 0.6)

        # 4. Update onto an RGB beauty is refused and changes nothing
        Harness.aovs = rgb_only
        check("rgb only: Update is refused", refused(comp.update), True)
        check("rgb only: the alpha still reads the RGBA beauty", Path(file_node.parm("filename").eval()).parent.parent.name, "v0002")
        close("rgb only: the channel's A is unchanged", alpha_at_centre(channel), 0.6)
    finally:
        bc.list_channels, bc.get_frame_range, bc.get_fps, bc.playbar = saved
        shutil.rmtree(root, ignore_errors=True)

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
