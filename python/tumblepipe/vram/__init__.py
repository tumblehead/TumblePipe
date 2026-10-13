"""VRAM Inspector — what in the scene is using the GPU's memory.

Houdini measures its GPU memory per *client* (``gpumem -U``: the Vulkan
texture cache, each viewer pane's geometry, the OpenCL pool, Karma XPU) but
never per node. This package puts the two halves side by side:

* **measured** — the client totals ``gpumem`` reports, which are exact
  (:mod:`.gpumem`);
* **itemised** — what the scene *holds*: every USD prim's points, topology
  and primvars, every texture its materials bind, every Copernicus layer on
  the GPU, read from the stage and the image headers (:mod:`.usd_scan`,
  :mod:`.cop_scan`) and sized the way the viewport would upload them
  (:mod:`.sizes`).

:mod:`.report` joins them: the itemised rows are exact facts about the scene,
and a client's measured total is split across them in proportion — the split
is the estimate, and the window says so.

Everything except :mod:`.usd_scan`, :mod:`.cop_scan`, :mod:`.collect` and
:mod:`.ui` is pure Python, importable by the property suite.
"""
