# Compositing nodes

The Copernicus (COP) toolkit: the shot comp builder and its camera, the
comp helpers under `_TumblePipe/comp`, the texture and material COPs under
`_TumblePipe/lookdev`, and the COP network context menu. Where renders land,
what colour space they are in and how a comp reaches the farm is in
[Compositing](../compositing.md); this page is about the nodes.

None of these HDAs carries built-in help text, so this page is the reference.

## `th::build_comp` (COP)

Build and submit the shot comp. Tab menu `_TumblePipe/pipeline`; drop it in
a `copnet` inside a **composite** shot workfile. What **Update** builds —
one subnet per shot channel with a typed `file` COP per AOV pinned to the
latest complete render version, a grade subnet per channel, channels
over-merged in shot order — is described in
[Compositing → The build_comp node](../compositing.md#the-build_comp-node)
and not repeated here.
Source: [`otls/cop_th.build_comp.1.0`](../../otls/cop_th.build_comp.1.0/th_8_8Cop_1build__comp_8_81.0/DialogScript),
[`cops/build_comp.py`](../../python/tumblepipe/pipe/houdini/cops/build_comp.py).

| Label | Default | What it does |
|---|---|---|
| Shot | `from_context` | The shot; the label under it shows what resolved |
| Input | `denoise` | The highest render department to read from — the imports search departments up to this one |
| Quality | Render | **Render** reads farm frames; **Proxy** reads locally generated proxy frames |
| Proxy Resolution | Full Size | Full / Half / Quarter — scales the proxy imports by 1, 2 or 4 |
| Farm › Pool, Priority | first pool, `50` | |
| Farm › Render Layer | `all` | Listed, but nothing reads it |
| Jobs › Submit Partial | on | Also render **Start/Middle/End-Frame** as a quick preview task |
| Jobs › Submit Full | on | Render the full range |
| Jobs › Frame Range | From Config | The shot's range plus roll, or From Settings (**Start/End-Frame**, **Pre/Post-Roll**) |
| Jobs › Step size, Batch size | `0`, `5` | |

Buttons:

- **Update COP network** — builds the network on first press, then
  re-resolves every import to the newest complete version. With Quality on
  Proxy it instead cooks the node's TOP network to generate proxy frames;
  **Stop** cancels that cook.
- **Render to mplay** — renders the current frame in place.
- **Submit to farm** — saves the hip, flips Quality to Render for the
  save, and submits a composite job: a *stage* task, a *partial composite*
  (three frames, when Submit Partial is on) and a *full composite* (when
  Submit Full is on), one layer per shot channel. What the job writes and
  chains (MP4s, slapcomp, Discord) is in
  [Farm submission and MP4s](../compositing.md#farm-submission-and-mp4s).

Gotchas:

- The node needs a saved **shot** workfile; Submit fails with *Invalid
  workfile path* otherwise, and `from_context` in an asset workfile resolves
  nothing.
- Update is the only thing that retargets versions. The Asset Browser's
  refresh-on-open deliberately skips this node, so a comp never silently
  picks up new renders — press Update when you want them.
- Frames written by **Render to mplay** carry no `chromaticities`, so RV and
  Nuke read them as Rec.709; see
  [Colour space](../compositing.md#colour-space).

## `th::import_lop_camera` (COP)

Bring the shot's render camera into COPs, for the nodes that need real
camera data (depth, projections). Tab menu `_TumblePipe/comp`. It embeds a
[`th::import_shot`](import-and-export.md#thimport_shot-lop) that composes
the shot's staged stage **without payloads**, lifts `/cameras/render_camera`
out through a `lopimportcam` and feeds a `cameraimport` COP; see
[The shot camera in comp](../compositing.md#the-shot-camera-in-comp).
Source: [`otls/cop_th.import_lop_camera.1.0`](../../otls/cop_th.import_lop_camera.1.0/th_8_8Cop_1import__lop__camera_8_81.0/DialogScript),
[`cops/import_lop_camera.py`](../../python/tumblepipe/pipe/houdini/cops/import_lop_camera.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | Shots only (*Select Shot*); `from_context` in an asset workfile resolves nothing |
| Channel | `default` | |
| Department | `from_context` | Every shot department, not just the publishable ones — the camera is read from whichever department authored it |
| Version | `latest` | As on `import_shot` |
| Transform Type | Local to World | Local to World / Local / Parent to World |
| Import | | Runs the embedded `import_shot` |

## `th::a_b_slider` (COP)

Wipe between two images. Tab menu `_TumblePipe/comp`. Inputs **A** and
**B** (RGBA), output **out**: a ramp mask drives a blend.
Source: [`otls/cop_th.a_b_slider.1.0`](../../otls/cop_th.a_b_slider.1.0/th_8_8Cop_1a__b__slider_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Direction | Horizontal | Horizontal / Vertical / Radial / Concentric wipe |
| Position | `0.5` | Where the wipe sits |

## `th::cop_paint` (COP)

Paint strokes over an image. Tab menu `_TumblePipe/comp`. Input and output
**source** (RGBA). **Paint** opens a floating scene viewer (front view)
on the internal draw SOP with the source image shown as reference and
enters its stroke state; **Clear** wipes the strokes. **Radius**, **Opacity**
and **Color** are the brush.
Source: [`otls/cop_th.cop_paint.1.0`](../../otls/cop_th.cop_paint.1.0/th_8_8Cop_1cop__paint_8_81.0/DialogScript).

## `th::depth_cull` (COP)

Cull an object by depth against the scene. Tab menu `_TumblePipe/comp`. Three
inputs — **scene_depth** (Mono), **object_depth** (Mono), **object_color**
(RGBA) — and one **result** (RGBA): the object's colour where its depth is
in front of the scene's. It is a single OpenCL kernel with no parms.
Source: [`otls/cop_th.depth_cull.1.0`](../../otls/cop_th.depth_cull.1.0/th_8_8Cop_1depth__cull_8_81.0/DialogScript).

## `th::roto_mask` (COP)

Draw a roto shape over an image. Tab menu `_TumblePipe/comp`. Input
**source** (RGBA); outputs **source** (passed through) and **mask** (Mono).
**Roto Mask** opens a floating front-view scene viewer on the internal curve
SOP with the source image as reference and enters the curve state; **Feather
Mask** (`0`) softens the edge.
Source: [`otls/cop_th.roto_mask.1.0`](../../otls/cop_th.roto_mask.1.0/th_8_8Cop_1roto__mask_8_81.0/DialogScript).

## `th::cop_material` (COP)

A MaterialX standard-surface material fed from COP images. Tab menu
`_TumblePipe/lookdev`. Inputs **basecolor** (RGBA), **roughness** (Mono),
**height** (Mono); output **mat**. Inside, a LOP material library holds a
standard surface whose maps are triplanar projections of the inputs, with
bump and displacement from the height. There are no parms.
Source: [`otls/cop_th.cop_material.1.0`](../../otls/cop_th.cop_material.1.0/th_8_8Cop_1cop__material_8_81.0/DialogScript).

## `th::cop_material_library` (LOP)

Collect `cop_material` outputs into a material library on the stage. Tab
menu `_TumblePipe/lookdev`. Its one parm, **Material Container** (default
`/materials/`), is the prefix the materials are written under; the internal
COP network holds the `cop_material` nodes and a foreach fetches each one's
material layer and merges them. Dive in to add materials.
Source: [`otls/lop_th.cop_material_library.1.0`](../../otls/lop_th.cop_material_library.1.0/th_8_8Lop_1cop__material__library_8_81.0/DialogScript).

## `th::gradient_map` (COP)

Map an image's luminance through a colour ramp. Tab menu
`_TumblePipe/lookdev`. Input and output **source** (RGBA).
Source: [`otls/cop_th.gradient_map.1.0`](../../otls/cop_th.gradient_map.1.0/th_8_8Cop_1gradient__map_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Equalize Input | off | Equalize the input before mapping |
| Gradient Map | 3-point ramp | The colours to map to |

## `th::mask_painter` (COP)

Paint a mask onto geometry in the viewport and get it back as a texture.
Tab menu `_TumblePipe/lookdev`. Input **geometry** (Geometry), output
**source** (Mono). The node has no parms of its own: every brush setting is
driven through its **paint viewer state** — enter the node's state in the
scene viewer and it hands you over to the internal Texture Mask Paint SOP's
state, whose brush options appear in the viewer toolbar. The stroke data is
stashed in hidden parms on the node.
Source: [`otls/cop_th.mask_painter.1.0`](../../otls/cop_th.mask_painter.1.0/th_8_8Cop_1mask__painter_8_81.0/DialogScript).

## `th::paint_scatter` (COP)

Procedurally generate a painted-stroke texture set over a LOP's geometry.
Tab menu `_TumblePipe/lookdev`. No inputs; outputs **geo** (Geometry) and
the maps **basecolor**, **rough**, **normal**, and two Mono outputs
labelled **ao** and **height**.
Source: [`otls/cop_th.paint_scatter.1.0`](../../otls/cop_th.paint_scatter.1.0/th_8_8Cop_1paint__scatter_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| LOP Path, Primitives | *(empty)* | The geometry to paint over |
| Auto UVs | off | Generate UVs before rasterizing |
| Resolution | `1024` | 512 – 8192 |
| Brush Generation › Seed, Stroke Density, Normal Angle Scale, Random Scale | `0`, `1000`, `0.05`, `1` | How many strokes and how they follow the surface |
| Stamp Settings › Stamp Scale, Stroke Angle, Stroke Falloff | `21`, `0`, `0.291` | |
| Bristle Settings › Bristle Size, Bristle Height | `0.1`, `0.01` | |
| Rough Max / Min, Stroke Color Multiplier | `1` / `0`, `0.223` | |

The two Mono outputs are labelled the wrong way round relative to their
internal names (the output named `height` is labelled *ao* and vice versa).

## `th::tileable_texture` (COP)

Make an image tile by blending offset copies through edge ramps. Tab menu
`_TumblePipe/lookdev`. Input and output **source** (RGBA).
Source: [`otls/cop_th.tileable_texture.1.0`](../../otls/cop_th.tileable_texture.1.0/th_8_8Cop_1tileable__texture_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Horizontal Ramp, Vertical Ramp | 4-point ramps | Blend weight across each seam |
| Texture Scale | `1 1` | |

## `th::lop_import` (COP)

Bring LOP geometry into a COP network. Tab menu `_TumblePipe/lookdev`. No
inputs; output **geometry** (Geometry). **LOP Path** and **Primitives** pick
the prims, which are imported, unpacked to polygons and handed to COPs — the
usual first node before a rasterize.
Source: [`otls/cop_th.lop_import.1.0`](../../otls/cop_th.lop_import.1.0/th_8_8Cop_1lop__import_8_81.0/DialogScript).

## The COP network context menu

In a Copernicus network the radial's context menu (see
[Radial menus → COP and VOP network context menus](../radial-menus.md#cop-and-vop-network-context-menus))
shows a **COP** menu registered from
[`startup.py`](../../python/tumblepipe/startup.py) (`_register_cop_menu`).
It only exists while the `tumbleradial` package is installed.

| Item | What it does |
|---|---|
| Convert › Mono / UV / RGB / RGBA | For each selected node, appends one convert node per output whose type differs from the target (`mono`, `<type>touv`, `<type>torgb`, `<type>torgba`) and lays them out |
| Pattern › Fractal Noise, Worley Noise, Tile, Rasterize Geo, SDF Shape, Stamp, Ramp | Places that COP |
| Filter › Blur, Dilate/Erode, Distort, Remap, Feather, HSV Adjust, Invert | Places that COP |
| Composite › Over, Blend, Multiply, Add, Under, Subtract, Divide | Creates a `blend` COP in that mode with the first two selected nodes as its inputs, named `<a>_<mode>_<b>` |
| File | Places a `file` COP |
| Render | For each selected node, creates a `rop_image` next to it pointing at the node, writing `$HIP/render/<node>.$F4.exr` |

The Convert, Composite and Render entries run the helpers in
[`tools/coputils.py`](../../python/tumblepipe/tools/coputils.py)
(`type_convert`, `composite`, `render_cop`) on the current selection.
Composite needs **two** selected nodes — with one selected it errors rather
than doing nothing.
