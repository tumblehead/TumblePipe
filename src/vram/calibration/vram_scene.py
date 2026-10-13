import os, tempfile, hou
import OpenImageIO as oiio
import numpy as np

tex_dir = os.path.join(tempfile.gettempdir(), "vram_inspector_test")
os.makedirs(tex_dir, exist_ok=True)
tex = os.path.join(tex_dir, "albedo_4k.exr").replace("\\", "/")
if not os.path.isfile(tex):
    spec = oiio.ImageSpec(4096, 4096, 3, "half")
    out = oiio.ImageOutput.create(tex)
    out.open(tex, spec)
    out.write_image(np.random.rand(4096, 4096, 3).astype(np.float16))
    out.close()

stage = hou.node("/stage")
for c in stage.children():
    if c.name().startswith("vramtest_"):
        c.destroy()
made = stage.createNode("sopcreate", "vramtest_grid")
grid = made.node("sopnet/create").createNode("grid")
grid.parm("rows").set(1000)
grid.parm("cols").set(1000)
grid.parm("sizex").set(10)
grid.parm("sizey").set(10)
uv = made.node("sopnet/create").createNode("uvproject")
uv.setInput(0, grid)
uv.setDisplayFlag(True)
uv.setRenderFlag(True)

lib = stage.createNode("materiallibrary", "vramtest_mtl")
lib.setInput(0, made)
mat = lib.createNode("mtlxstandard_surface", "albedo_mat")
img = lib.createNode("mtlximage", "albedo")
img.parm("file").set(tex)
img.parm("signature").set("color3")
mat.setInput(mat.inputIndex("base_color"), img)
lib.parm("matpathprefix").set("/materials/")
assign = stage.createNode("assignmaterial", "vramtest_assign")
assign.setInput(0, lib)
assign.parm("primpattern1").set("/vramtest_grid/**")
assign.parm("matspecpath1").set("/materials/albedo_mat")
sphere = stage.createNode("sphere", "vramtest_sphere")
merge = stage.createNode("merge", "vramtest_merge")
merge.setInput(0, assign)
merge.setInput(1, sphere)
merge.setDisplayFlag(True)
stage.layoutChildren()

cops = hou.node("/obj").node("vramtest_cops") or hou.node("/obj").createNode("copnet", "vramtest_cops")
for c in cops.children():
    c.destroy()
noise = cops.createNode("fractalnoise", "noise2k")
noise.cook(force=True)

viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
viewer.setPwd(stage)
print("scene built", tex)
