"""Audit: build_comp's alpha follows the beauty, and Update follows the planes.

    hython scripts/verify_build_comp_alpha.py

The beauty is rendered RGBA and its A is the comp's alpha; renders from before
that carry an RGB beauty and a separate `alpha` AOV (see
docs/compositing.md, "Alpha lives in the beauty"). build_comp builds one
`alpha` subnet per channel and Update re-points it as a channel's newest
render changes shape. This drives the real node's Build and Update over EXR
fixtures and checks the wiring *and* the cooked pixels:

1. build from a legacy render (raw `beauty.R/G/B` + `alpha.Z`): the alpha
   subnet reads the alpha AOV, the channel's A is that AOV's value;
2. Update to an RGBA beauty published by the denoise (bare `R,G,B,A`, no
   alpha AOV): the alpha subnet reads the beauty RGBA, splits output 3 (A)
   out, and every File COP asks for plane `C` -- a File COP asked for
   `beauty` on bare channels loads nothing ("rgba is missing");
3. Update to an RGB beauty with no alpha: the alpha subnet goes and the
   channel comps opaque (A = 1);
4. a comp that never had an alpha subnet gets one on Update, resampled at
   the comp's proxy scale like the channel's other imports.

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


def write_render(root, beauty_fill, beauty_names, alpha_value=None):
    """One frame of a render: a beauty with ``beauty_names`` channels filled
    with ``beauty_fill``, and an `alpha.Z` AOV when ``alpha_value`` is set."""
    (root / "beauty").mkdir(parents=True)
    oiiotool("--create", "8x8", len(beauty_names),
             "--fill:color=" + ",".join(map(str, beauty_fill)), "8x8",
             "--chnames", ",".join(beauty_names),
             "-o", root / "beauty" / f"beauty.{FRAME:04d}.exr")
    aovs = {"beauty": FakeAov("beauty", root)}
    if alpha_value is not None:
        (root / "alpha").mkdir(parents=True)
        oiiotool("--create", "8x8", 1, f"--fill:color={alpha_value}", "8x8",
                 "--chnames", "alpha.Z", "-o", root / "alpha" / f"alpha.{FRAME:04d}.exr")
        aovs["alpha"] = FakeAov("alpha", root)
    return aovs


class Harness(bc.BuildComp):
    """build_comp with its project lookups answered by the test."""

    aovs = {}
    resolution = bc.Resolution.Full

    def get_shot_uri(self):
        return Uri.parse_unsafe("entity:/shots/seq/shot")

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
    # Create the nodes first: the HDA's OnCreated reloads build_comp, which
    # would undo the stand-ins below
    copnet = hou.node("/obj").createNode("copnet", "comp")
    comp_native = copnet.createNode("th::build_comp::1.0", "build_comp")
    fresh_native = copnet.createNode("th::build_comp::1.0", "fresh")
    saved = (bc.list_channels, bc.get_frame_range, bc.get_fps, bc.util)
    bc.list_channels = lambda shot_uri: [CHANNEL]
    bc.get_frame_range = lambda shot_uri: FrameRange(FRAME, FRAME, 0, 0)
    bc.get_fps = lambda shot_uri: None
    bc.util = SimpleNamespace(set_fps=lambda fps: None, set_frame_range=lambda frame_range: None)
    try:
        legacy = write_render(root / "v0001", (0.2, 0.3, 0.4), ["beauty.R", "beauty.G", "beauty.B"], 0.25)
        rgba = write_render(root / "v0002", (0.5, 0.6, 0.7, 0.6), ["R", "G", "B", "A"])
        rgb_only = write_render(root / "v0003", (0.1, 0.1, 0.1), ["R", "G", "B"])

        comp = Harness(comp_native)
        dive = comp.native().node("dive")

        # 1. built from a legacy render
        Harness.aovs = legacy
        comp._build()
        channel = dive.node(CHANNEL)
        alpha = channel.node("alpha")
        check("legacy: an alpha subnet is built", alpha is not None, True)
        check("legacy: it reads the alpha AOV's plane", alpha.node(f"{CHANNEL}_alpha").parm("aov1").eval(), "alpha")
        check("legacy: no split", alpha.node("split"), None)
        grade = channel.node("grade")
        check("legacy: alpha feeds the grade's last input", grade.inputs()[-1].path(), alpha.path())
        close("legacy: the channel's A is the alpha AOV", alpha_at_centre(channel), 0.25)

        # 2. Updated to the denoise's RGBA publish (bare R,G,B,A, no alpha AOV)
        Harness.aovs = rgba
        comp._update()
        alpha = channel.node("alpha")
        file_node = alpha.node(f"{CHANNEL}_alpha")
        split = alpha.node("split")
        check("rgba: the alpha reads the beauty file", Path(file_node.parm("filename").eval()).name, f"beauty.{FRAME:04d}.exr")
        check("rgba: as plane C, RGBA", (file_node.parm("aov1").eval(), file_node.parm("type1").eval()), ("C", 3))
        check("rgba: A is split output 3", split is not None and alpha.node("outputs").inputConnections()[0].outputIndex(), 3)
        beauty_file = channel.node("beauty").node(f"{CHANNEL}_beauty")
        check("rgba: Update re-points the beauty's plane too", beauty_file.parm("aov1").eval(), "C")
        rgb = rgb_at_centre(channel.node("beauty"))
        check("rgba: the beauty loads (not 'rgba is missing')", rgb is not None and abs(rgb[0] - 0.5) < 1e-3, True)
        close("rgba: the channel's A is the beauty's A", alpha_at_centre(channel), 0.6)

        # 3. Updated to an RGB beauty with no alpha anywhere
        Harness.aovs = rgb_only
        comp._update()
        check("rgb only: the alpha subnet is gone", channel.node("alpha"), None)
        close("rgb only: the channel comps opaque", alpha_at_centre(channel), 1.0)

        # 4. a comp that never had an alpha subnet gets one on Update, at the
        # comp's proxy scale
        Harness.aovs = rgb_only
        fresh = Harness(fresh_native)
        fresh._build()
        fresh_channel = fresh.native().node("dive").node(CHANNEL)
        check("fresh: built without an alpha subnet", fresh_channel.node("alpha"), None)
        Harness.aovs = rgba
        Harness.resolution = bc.Resolution.Half
        fresh._update()
        Harness.resolution = bc.Resolution.Full
        fresh_alpha = fresh_channel.node("alpha")
        check("fresh: Update builds the alpha subnet", fresh_alpha is not None, True)
        check(
            "fresh: resampled like the beauty",
            fresh_alpha is not None and fresh_alpha.node("resample").parm("scale").eval(),
            fresh_channel.node("beauty").node("resample").parm("scale").eval(),
        )
        check(
            "fresh: wired into the grade",
            fresh_channel.node("grade").inputs()[-1].path() if fresh_alpha else None,
            fresh_alpha.path() if fresh_alpha else "an alpha subnet",
        )
    finally:
        bc.list_channels, bc.get_frame_range, bc.get_fps, bc.util = saved
        shutil.rmtree(root, ignore_errors=True)

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
