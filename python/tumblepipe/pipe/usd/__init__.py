"""USD layer work that does not need a Houdini session.

Each submodule owns one job; import from the submodule, not from here.

* ``usda`` — writing and reading back the text of sublayer-only .usda files.
* ``sublayer_refs`` — the ``entity:/`` sublayer URIs a staged build records:
  generating them, parsing them back, and reading them out of staged files.
* ``render_settings`` — finding a stage's RenderSettings prim and camera, and
  the USDA sections the farm collapse authors against them.
* ``instances`` — asset instance copies and the placement op orders that
  position them, for the static flatten.
* ``collapse`` — flattening a staged build into one resolver-free .usda for
  the farm.
* ``arcs`` — the composition arcs a layer declares and whether they survive
  publish.
* ``stitch`` — exporting a frame range in chunks and stitching them back.
"""
