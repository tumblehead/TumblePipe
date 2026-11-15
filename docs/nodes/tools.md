# Modelling and utility nodes

The MaterialX triplanar VOP and the VOP network context menu. The
stroke-driven modelling SOPs that were here (Mesh Lasso, Mesh Carver, Mesh
Blender) moved to hModel (`tumblehead/hmodel`) under the same type names.
TumblePipe depends on hModel, so it installs with TumblePipe and scenes that
hold those nodes keep loading. Every operator declared in [`hpm.toml`](../../hpm.toml) is
covered by one of the node pages — the table in
[Pipeline nodes](index.md#where-the-nodes-are) says which — so there are no
leftovers to document here; the recipes (`th::th_configure_*`) are on
[Lighting and rendering → Recipes](lighting-and-rendering.md#recipes-thth_configure_).

None of these HDAs carries built-in help text, so this page is the reference.

## `th::triplanar_projection` (VOP)

A MaterialX image lookup with a choice of projection. Tab menu
`_TumblePipe/lookdev`, inside a MaterialX material. One output, **output**
(vector). Inside it switches between a tiled image lookup on the geometry's
UVs, a MaterialX triplanar projection and Karma's hex-tiled triplanar.
Source: [`otls/vop_th.triplanar_projection.1.0`](../../otls/vop_th.triplanar_projection.1.0/th_8_8Vop_1triplanar__projection_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Projection | UV | UV / Tri-Planar / Hex-Planar |
| Axis | all three | Which axes the Tri-Planar projection uses (only shown for Tri-Planar) |
| Image | *(empty)* | The texture |

## The VOP network context menu

In a VOP network **Alt+E** (see
[Radial menus → COP and VOP network context menus](../radial-menus.md#cop-and-vop-network-context-menus))
opens a **VOP** menu registered from
[`startup.py`](../../python/tumblepipe/startup.py) (`_build_vop_menu`). It
is not on Radial's X key: over a VOP network, holding X and clicking a node
is Houdini's Visualize Output.
It began as a port of Houdini's own `vop.json` radial and now works like
Radial's own network menus: four submenus (Image, Adjust, Generators,
Utility) and a **Surface** centre (release: Standard Surface; open it for
OpenPBR, Surface Material, Displacement). Every item simply places a
MaterialX or Karma VOP, and `tests/test_radial_startup.py` checks each node
type and icon against the installed Houdini. It only exists while the
`tumbleradial` package is installed.

| Submenu | Items (node placed) |
|---|---|
| Image | Image (`mtlximage`), Tiled Image (`mtlxtiledimage`), Image Sequence (`mtlximagesequence`), Triplanar (`mtlxtriplanarprojection`) |
| Adjust | Remap (`mtlxremap`), Color Correct (`mtlxcolorcorrect`) |
| Generators | Constant (`mtlxconstant`), Noise (`mtlxfractal3d`), Occlusion (`mtlxambientocclusion`), Voronoi (`kma_voronoinoise3d`), Geometry Color (`mtlxgeomcolor`), Geometry Property (`mtlxgeompropvalue`), Curvature (`kma_curvature`) |
| Utility | Normal Map (`mtlxnormalmap`), Bump (`mtlxbump`), Multiply (`mtlxmultiply`), Add (`mtlxadd`), Subtract (`mtlxsubtract`), Divide (`mtlxdivide`) |

The Triplanar entry places Houdini's plain `mtlxtriplanarprojection`, not
`th::triplanar_projection` above.
