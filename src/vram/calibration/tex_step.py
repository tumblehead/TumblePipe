import builtins
import os
import tempfile

import hou
import numpy as np
import OpenImageIO as oiio
from tumblepipe.vram import gpumem

d = os.path.join(tempfile.gettempdir(), "vram_inspector_test")


def make(name, w, h, ch, fmt):
    path = os.path.join(d, name).replace("\\", "/")
    if not os.path.isfile(path):
        out = oiio.ImageOutput.create(path)
        out.open(path, oiio.ImageSpec(w, h, ch, fmt))
        dtype = {"half": np.float16, "float": np.float32, "uint8": np.uint8}[fmt]
        data = np.random.rand(h, w, ch)
        if fmt == "uint8":
            data = data * 255
        out.write_image(data.astype(dtype))
        out.close()
    return path


if not hasattr(builtins, "_tex_queue"):
    builtins._tex_queue = [
        ("4k_half_rgb", make("albedo_4k.exr", 4096, 4096, 3, "half")),
        ("4k_u8_rgb_png", make("rgb_4k.png", 4096, 4096, 3, "uint8")),
        ("4k_f32_rgba", make("rgba_4k_f32.exr", 4096, 4096, 4, "float")),
        ("2k_half_rgb", make("albedo_2k.exr", 2048, 2048, 3, "half")),
        ("4k_u8_mono_png", make("mono_4k.png", 4096, 4096, 1, "uint8")),
        ("8k_u8_rgb_png", make("rgb_8k.png", 8192, 8192, 3, "uint8")),
    ]
    builtins._tex_results = {}
    builtins._tex_current = None

reading = {c.name: round(c.used_bytes / 2**20, 3)
           for c in gpumem.parse_usage(hou.hscript("gpumem -U")[0])
           if c.kind == "textures"}
if builtins._tex_current:
    builtins._tex_results[builtins._tex_current] = reading
img = hou.node("/stage/vramtest_mtl/albedo")
if builtins._tex_queue:
    name, path = builtins._tex_queue.pop(0)
    img.parm("file").set(path)
    hou.hscript("glcache -c")
    builtins._tex_current = name
    hou.ui.triggerUpdate()
else:
    builtins._tex_current = None
print(builtins._tex_results, "next:", builtins._tex_current)
