# Modelling and utility nodes

The stroke-driven modelling SOPs under `_TumblePipe/model` and
`_TumblePipe/lookdev`, the MaterialX triplanar VOP, and the VOP network
context menu. Every operator declared in [`hpm.toml`](../../hpm.toml) is
covered by one of the node pages — the table in
[Pipeline nodes](index.md#where-the-nodes-are) says which — so there are no
leftovers to document here; the recipes (`th::th_configure_*`) are on
[Lighting and rendering → Recipes](lighting-and-rendering.md#recipes-thth_configure_).

None of these HDAs carries built-in help text, so this page is the reference.

## `th::mesh_blender` (SOP)

Paint a `blend` attribute on a mesh and blend the painted region into a
remeshed volume of itself — a brush for melting detail together. Tab menu
`_TumblePipe/model`. The node wraps Houdini's attribute-paint stroke tool:
enter its viewer state and paint; the **Brush** and **Stroke** tabs are the
standard paint controls.
Source: [`otls/sop_th.mesh_blender.1.0`](../../otls/sop_th.mesh_blender.1.0/th_8_8Sop_1mesh__blender_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Reset All Changes | | Clear the paint |
| Brush › LMB / Shift / Ctrl, MMB / Shift / Ctrl | Paint FG / Smooth / Paint BG, Sample FG / Sample BG / Erase | Which operation each mouse button and modifier performs |
| Brush › Paint Mode, Shape | Over, Volume | Over / Add / Maximum / Minimum / Multiply; Volume / Surface / Screen / Fill / Nearest Point |
| Brush › FG Float, BG Float | `1`, `0` | The values painted (the attribute is a float) |
| Brush › Radius, Opacity, Soft Edge | `0.06`, `1`, `0.5` | Plus **Spray Size** for the Screen shape, pressure scales, Connected / Front Face / Visible Only |
| Attributes › Attribute Name, Attribute Type | `blend`, Float | One row per painted attribute |
| Symmetry › Enable Mirroring, Origin, Direction | off, `0 0 0`, `1 0 0` | |
| Mesh Explude Group | *(empty)* | Geometry kept out of the blend |
| Blurring Iterations | `3` | Smoothing of the blended region |
| Voxel Size, Isovalue, Target Size | `0.021`, `0.01`, `0.1` | The VDB-from-polygons and remesh that produce the blended surface |
| Visualize Blended Mesh | off | Show the result as a guide |
| Attributes › Primitives / Points / Vertices | *(none)* / `Cd` / `N` | Attributes carried across the remesh |

The **Rename** and **Reset** buttons on each attribute row have **never
worked**: they call `hou.phm().renameattrib()` / `resetattrib()`, and the HDA
shipped without a `PythonModule` for them to reach, so every click raises
`AttributeError`. Rename by retyping **Attribute Name**; reset with
**Reset All Changes**. The gap is recorded in
[HDA callback audit](../development.md#hda-callback-audit).

## `th::mesh_carver` (SOP)

Carve grooves into a mesh along drawn strokes. Tab menu
`_TumblePipe/lookdev`. Draw with the embedded curve tool, and a swept
profile is subtracted from the mesh along each stroke. The first tab is the
stock Curve SOP interface (draw and edit modes, topology and tangent
operations); the parms that are the carver's own:
Source: [`otls/sop_th.mesh_carver.1.0`](../../otls/sop_th.mesh_carver.1.0/th_8_8Sop_1mesh__carver_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Curve › Radius, Color, Opacity, Live Reprojection | `0.045`, dark green, `1`, Send Rays | The stroke, projected onto the geometry |
| Mesh › Carve Group, Invert Selection | *(empty)*, off | Prims that may be carved |
| Show Guide Mesh | off | |
| Subdivide Carved Mesh, Mesh Subdivisions | off, `1` | Subdivide before carving |
| Carve › Clear Stroke History | | Deletes every drawn stroke |
| Carve › Carve Shape | Round Tube | Second Input Cross Sections / Round Tube / Square Tube / Ribbon |
| Carve Resolution, Carve Radius, Carve Column | `0.05`, `1`, `5` | The sweep along the stroke (**Carve Column** hidden for a custom cross section) |
| Carve Depth | `0.5` | How deep the profile sinks |
| Triangulate Carved Faces | on | |
| Visualize Strokes | off | |
| Edit Curve | | Puts the scene viewer into the curve state to edit the strokes |
| Carve Shadow, Blurring Iterations | `0.5`, `2` | Darken the carved faces (colour) and blur that darkening |

## `th::mesh_lasso` (SOP)

Lasso a region on screen and turn it into a mesh — a flat plane, an
extruded slab or a smoothed blob. Tab menu `_TumblePipe/model`. Draw with
the stroke tool (**Projection** defaults to Screen Plane, so you lasso in
the viewport); each closed stroke becomes an island.
Source: [`otls/sop_th.mesh_lasso.1.0`](../../otls/sop_th.mesh_lasso.1.0/th_8_8Sop_1mesh__lasso_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Lasso › Projection | Screen Plane | XY / YZ / ZX / Screen Plane / Geometry |
| Clear Strokes | | Delete every stroke |
| Color | white | Stroke colour attribute |
| Geometry Options › Merge With Inputmesh | off | Output the input mesh too |
| Geometry Options › Surface Distance | `0` | Offset the lasso mesh from the surface |
| Geometry Options › Subdivide Input Mesh, Subdivisions | off, `1` | |
| Mesh › Mesh Type | Plane | Plane / Extruded / Blob |
| Mesh Resolution | `0.1` | |
| Add UVS to lasso Islands | on | |
| Blob Mesh Options › Distance Scale, Radius, Edge bevel, Smooth Mesh Strenght, Input/Output Min/Max, Remap | `1`, `4.055`, `0.019`, `2`, `0 1 0 1`, ramp | Shape of the blob |
| Extrude Mesh Options › Extrude Distance Scale, Bevel Distance, Bevel Divisions | `1`, `0`, `1` | Shape of the extrusion |

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

In a VOP network the radial's context menu (see
[Radial menus → COP and VOP network context menus](../radial-menus.md#cop-and-vop-network-context-menus))
shows a **VOP** menu registered from
[`startup.py`](../../python/tumblepipe/startup.py) (`_register_vop_menu`).
It is a port of Houdini's own `vop.json` radial with its broken entries
fixed, and every item simply places a MaterialX or Karma VOP. It only
exists while the `tumbleradial` package is installed.

| Submenu | Items (node placed) |
|---|---|
| Image | Image (`mtlximage`), Tiled Image (`mtlxtiledimage`), Image Sequence (`mtlximagesequence`), Triplanar (`mtlxtriplanarprojection`) |
| Adjust | Remap (`mtlxremap`), Color Correct (`mtlxcolorcorrect`) |
| Generators | Constant (`mtlxconstant`), Noise (`mtlxfractal3d`), Occlusion (`mtlxambientocclusion`), Voronoi (`kma_voronoinoise3d`), Geometry Color (`mtlxgeomcolor`), Geometry Property (`mtlxgeompropvalue`), Curvature (`kma_curvature`) |
| Utility | Normal Map (`mtlxnormalmap`), Bump (`mtlxbump`), Multiply (`mtlxmultiply`), Add (`mtlxadd`), Subtract (`mtlxsubtract`), Divide (`mtlxdivide`) |

The Triplanar entry places Houdini's plain `mtlxtriplanarprojection`, not
`th::triplanar_projection` above.
