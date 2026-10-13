"""VRAM Inspector — what in the scene is using the GPU's memory.

Houdini keeps its GPU memory per *client* (the Vulkan texture cache, each
viewer pane's geometry, the OpenCL pool, Karma XPU) but never per node. This
package puts the two halves side by side:

* **measured** — by the viewport hook (``src/vram/hook``, :mod:`.hook`), a
  DSO that reads Houdini's GPU memory registry and, during each viewport's
  redraw, measures every drawn prim's geometry. Houdini's ``gpumem`` command
  is never used: its geometry total is inflated and it can crash Houdini;
* **itemised** — what the scene *holds*: every USD prim's points, topology
  and primvars, every texture its materials bind, every Copernicus layer on
  the GPU, read from the stage and the image headers (:mod:`.usd_scan`,
  :mod:`.cop_scan`) and sized the way the viewport would upload them
  (:mod:`.sizes`).

:mod:`.report` joins them: the itemised rows are exact facts about the scene;
a viewer prim shows its own measured bytes, and the other clients' totals are
split across their rows in proportion — the split is the estimate, and the
window says so.

Everything except :mod:`.usd_scan`, :mod:`.cop_scan`, :mod:`.collect` and
:mod:`.ui` is pure Python, importable by the property suite.
"""
