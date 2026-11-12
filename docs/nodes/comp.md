# Compositing nodes

The Copernicus (COP) toolkit: the shot comp nodes and its camera, the
comp helpers under `_TumblePipe/comp`, the texture and material COPs under
`_TumblePipe/lookdev`, and the COP network context menu. Where renders land,
what colour space they are in, how the composite template wires a comp and
how it reaches the farm is in [Compositing](../compositing.md); this page is
about the nodes.

None of these HDAs carries built-in help text, so this page is the reference.

## `th::import_render` (COP)

A shot's finished render into a comp. Tab menu `_TumblePipe/pipeline`;
the composite template makes one per shot channel. Outputs, top to bottom
(issue #67, after Remy's mockup of 2026-11-12):

| Output | Type | What it is |
|---|---|---|
| **rgba**, **rgba 2** .. **rgba 8** | RGBA | Each channel's beauty, one output per channel read (its A is the channel's alpha). A one-channel import's beauty is **rgba**; an all-channels import puts each channel on an output of its own, in channel order, and **Reads** names which (`outputs: rgba default, rgba 2 chars`). An output no channel holds is empty |
| **lpe** | Cable | The light groups (`beauty_<tag>`, the LPE tags' AOVs): a light mix's input |
| **aov** | Cable | Every other AOV the comp reads: `albedo`, `normal`, `depth`, `uv`, `position` |
| **matte** | Cable | The `objid_*` mattes and `ramp_*` distance ramps (`th::mattes`), `holdout_*` holdouts, and any cryptomatte other than the two below |
| **cryptomaterials** | Cable | Every rank plane of the `cryptomaterials` cryptomatte (`cryptomaterials00`, `01`, `02`), whole: wire it straight into a Cryptomatte COP's **crypto1** |
| **cryptoprimitives** | Cable | The same for `cryptoprimitives` |
| **geometry** | Geometry | The shot's staged geometry, for a Rasterize Geometry's `geometry` (the Scene folder, below) |
| **camera** | Metadata | The shot's render camera, for a Rasterize Geometry's `camera_ref` |

Each cable field is named after its AOV; no cable carries a beauty.
Which cable an AOV rides is `pipe.comp.aov_cable`. What it resolves and
refuses is
in [Compositing → `th::import_render`](../compositing.md#thimport_render).
Source: [`otls/cop_th.import_render.1.0`](../../otls/cop_th.import_render.1.0/th_8_8Cop_1import__render_8_81.0/DialogScript),
[`cops/import_render.py`](../../python/tumblepipe/pipe/houdini/cops/import_render.py),
[`pipe/comp.py`](../../python/tumblepipe/pipe/comp.py).

Why eight fixed **rgba** outputs and not one per channel, named after it:
a digital asset's outputs are fixed by its type. In Houdini 22.0.429 an
instance can neither add outputs nor relabel them (tried through the
subnet's own output parms), so the node has eight, and the channel on each
is on **Reads**. A channel keeps its output: one added to the shot later
takes a free output, one not rendered yet keeps its output (empty) until it
is, so a wire stays on its channel through every Update. A ninth channel
has no output (**Reads** names it): read it with an import of its own.

| Label | Default | What it does |
|---|---|---|
| Shot | `from_context` | Shots only (*Select Shot*); the label under it shows what resolved. In a Multi's workfile the template pins it to the member |
| Channel | `default` | A menu of the shot's channels, then **All channels** (`*`): every channel in this one node (below) |
| Department | `best` | `best` = the highest render department below `composite` with a complete version (denoise, else render), unless the denoise was made from an older render than the newest one (a render with Denoise off); or one department |
| Version | `latest` | `latest` = the department's newest complete version, or a `v####` (needs a concrete Department) |
| Reads | | What resolved (`denoise v0004, 10 AOVs`), from what when it moved, or why nothing did; for All channels, which channel is on which output |
| Time Offset | From Multi | Moves the shot's renders to where the shot sits in this scene: frame F reads the render's frame F minus the offset. **From Multi** is the shot's place on a Multi's [shared timeline](../asset-browser/multis-and-roots.md#shared-timeline), and no offset in any other workfile; **None** keeps the render on its own frames; **Manual** uses **Offset Frames**. The farm always comps each shot on its own frames, whatever this says |
| Offset Frames | `0` | Manual only |
| Scene › Department, Version | `from_context`, `latest` | The staged shot **geometry** and **camera** read, through a [`th::import_scene`](#thimport_scene-cop) inside the node; its shot and channel are this node's (`default` for All channels) |
| Scene › Reads | | What the scene imported, or why **geometry** / **camera** are still empty |
| Scene › Camera, Primitives, Purpose, Time Sample, Static Frame | `/cameras/render_camera`, `/*`, Render, Animated | As on `th::import_scene` |
| Cache › Cache | on | Keeps each frame a beauty read cooks in memory, so the playbar plays a frame it has read once without reading the network again (the **rgba** outputs); see [the frame cache](#the-frame-cache) |
| Cache › Cache AOVs Too | off | Caches the AOV reads as well (the cables): far more memory, every AOV of every frame played |
| Cache › Max Frames | `250` | How many frames each read keeps |
| Cache › Clear Cache | | Empties every cache of the node: the next play reads from disk again |
| Outputs › AOV Outputs | off | Keeps a Cable Unpack beside the node for each cable that has fields (`<node>_lpe`, `<node>_aov`, `<node>_matte`, `<node>_cryptomaterials`, `<node>_cryptoprimitives`), with one typed output per field, and keeps its fields in step when the version changes. Turning it off removes the unpacks unless something reads them |

Buttons: **Update** re-resolves (opening the workfile does it too, for an
import on `latest`) and imports the scene; **Open Location** opens the resolved version's folder;
**Render** opens [Farm Submit](../asset-browser/submit-jobs.md) on the
Shots tab with this shot's Render cell ticked and this import's Channel
checked in the Render settings (every channel for **All channels**). A
concrete Department becomes the render's **Up to**, the department the
render is written under, so the import reads the new render once it is
done; `best` leaves each shot's own. It is the same window as the toolbar's
Render quick action, so an open one gives way to it.
Changing Channel, Department or Version resolves at once; a render that
cannot be comped shows its reason on **Reads** instead of a dialog. The File
COPs inside are rebuilt whenever the shot, channel, department or version
read changes (a copied import switched to another channel reads the new
channel even when both resolve to the same version).

Inside (dive into the node, then its `dive` subnet) the network reads left
to right: a row per File COP, the beauties first, then the AOVs by cable in
output order, each with its Cache COP and switch beside it; then the cable
packs, each beside its first read; then the outputs. Rows leave room for a
File COP's file name above its tile, so nothing overlaps.

**All channels** (`*`): each channel's beauty is on an output of its own
(lay them over each other with Blend COPs in Over mode, the last channel on
top, as the template stacks them), and the cables carry only the AOVs, as
`<channel>_<aov>`; turn on AOV Outputs for one output per AOV. An import
made with TumblePipe 1.99 carried the beauties in **aov**: its first Update
moves a wire on an old AOV Outputs beauty to the channel's output. A channel
not rendered yet is left out and named on **Reads** (and `export_comp` warns
it is unread). Version stays `latest`: to pin a version, read that channel
with an import of its own. A light mix grades one channel's light groups, so
it refuses an all-channels cable. Submit freezes each channel it read.
An all-channels **cryptomaterials** cable carries every channel's planes
(`default_cryptomaterials00`, `chars_cryptomaterials00`, ...), which is
several cryptomattes on one cable, untested in a Cryptomatte COP: for picking
mattes, read the channel with an import of its own.

**geometry** and **camera** stay empty until the scene has imported:
**Update** imports it, and opening the workfile does too while either output
is wired (a staged shot composed with payloads is a big read most comps never
need). **camera** opens only once its camera cooks, so a scene without the
camera prim never stops the renders cooking; **Reads** says why. Changing a
Scene Department, Version or Camera imports it again.

An import made before #67 (TumblePipe 1.103 and older) had three outputs:
**rgba** (for All channels, the channels laid over each other), **aovs**
(every AOV) and **beauties** (a cable of each channel's beauty). Opening such
a workfile moves its wires and rebuilds the import: a wire on **aovs** to
**aov** (a light mix's cable on to **lpe**); a wire on **rgba** stays on
**rgba**, or, for an import of several channels, moves to an over chain of
its channel outputs kept beside the node (`<node>_over1`, ...), so the
picture is the same; a wire taken from **beauties** through a Cable Unpack
(the old Channel Outputs, or one of your own) moves to its channel's output,
and the emptied unpack goes. A wire on an old AOV Outputs field moves to
the unpack of the cable its AOV rides now. A hand-made Cable Unpack on
**aov** keeps only the fields still there: rewire it to the other cables
for the rest. The over chain is not kept in step if channels are added
later.

### The frame cache

A File COP reads at the speed of the network (about half a second for a
1080p frame from the server), so a comp played only in real time behind a
Cache COP added by hand (#73). Each read inside the node now passes through
a Cache COP of its own, picked by a switch while its toggles are on: a beauty
read needs **Cache**, an AOV read **Cache** and **Cache AOVs Too**. The cache
sits on each read rather than on the outputs because Copernicus cooks every
output of a node together: an uncached **aov** would read every file again
anyway. A cache empties itself when its read changes (a new version, another
channel). Memory: a float 1080p RGBA frame is about 33 MB, so 250 frames of
one beauty can take 8 GB; lower **Max Frames** or turn **Cache** off for long
shots or many channels. The farm composite worker turns **Cache** off on every
import before it cooks (each frame is cooked once there; the cache never
changes a pixel). A local copy of a version's frames (to beat the network on
the first play too) is not available yet.

Why the per-AOV outputs are on companion nodes: a digital asset's output
count is fixed by its type, so only a built-in node (Cable Unpack, Rasterize
Geometry) can grow outputs with its fields. A companion's wires follow their
field when its fields change.

## `th::light_mix` (COP)

A channel's light groups mixed back into its beauty. Tab menu
`_TumblePipe/pipeline`; the template puts one under each import. Inputs
**rgba** (an import's beauty, wired from that import for you when it is
empty) and **lpe** (its light groups cable), in the order of the import's
outputs so the wires do not cross; output **rgba**. A mix saved cable first (in an
older workfile) is rewired in this order when the workfile opens, and a
cable taken from the import's **aov** (**aovs** before #67; the stock
composite template still wires **aov**) moves to **lpe** on the first sync or
open. A cable that still carries a
`beauty` field (built before 1.99) is read when **rgba** is empty. The mix is the graded groups
plus **Rest** (the beauty minus the groups), so at defaults it is the beauty
exactly; see [Compositing → `th::light_mix`](../compositing.md#thlight_mix).
Source: [`otls/cop_th.light_mix.1.0`](../../otls/cop_th.light_mix.1.0/th_8_8Cop_1light__mix_8_81.0/DialogScript),
[`cops/light_mix.py`](../../python/tumblepipe/pipe/houdini/cops/light_mix.py).

| Label | Default | What it does |
|---|---|---|
| Mix | | Which groups the mix grades on its own |
| Sync Rows | | Wires the import's **rgba** into an empty beauty input, then adds a row for each light group the cable has and no row does; never removes or changes a row. Pressed for you when the upstream import rebuilds |
| Rest › Mute, Exposure, Tint | off, `0`, white | The lights in no group |
| Light Groups › Group, Mute, Exposure, Tint | | One row per `beauty_<tag>`: Exposure in stops |

A light group without a row rides in Rest at unity, so it looks right before
anyone syncs.

## `th::export_comp` (COP)

The end of a comp graph, and its farm submit. Tab menu
`_TumblePipe/pipeline`; the template ends in one, display flag on. One RGBA
input, passed through unchanged to its **comp** output, so the node shows
what it exports and can be wired on. What a submit writes and chains is in
[Compositing → Farm submission and MP4s](../compositing.md#farm-submission-and-mp4s).
Source: [`otls/cop_th.export_comp.1.0`](../../otls/cop_th.export_comp.1.0/th_8_8Cop_1export__comp_8_81.0/DialogScript),
[`cops/export_comp.py`](../../python/tumblepipe/pipe/houdini/cops/export_comp.py).

| Label | Default | What it does |
|---|---|---|
| Shot | `from_context` | Shots only |
| Layer | `comp` | `comp` = the merged comp, written as the version's `slapcomp` layer; or a channel, for a per-channel output |
| Warning | | Shot channels no import reads (Layer `comp` only), and frames the Preview Frames List drops |
| Jobs › Preview Frames | off | Also render first, middle and last frame as a quick task, posted before the full range |
| Jobs › Preview Frames › List | empty | Beside the toggle (enabled while it is on). Empty: first, middle and last. Otherwise the frames to preview, space separated, in that order (key poses, say), any number; the panorama posted to Discord shows the first 8. A frame outside the comp's frames or off **Step**, or a word that is not a whole number, is dropped and named in **Warning** (checked against the node's Frame Range) and again by Submit (against the frames it comps); with none left the preview is first, middle and last |
| Jobs › Frame Range | From Config | The menu shows the frames From Config gives, e.g. `From Config (1001-1100)`. The frames every import's render covers (each frozen read's `context.json` range, intersected), else the shot's range plus roll; or From Settings (**Start/End-Frame**, **Pre/Post-Roll**). Submit warns when a range reaches past the renders: those frames comp over empty input. The farm log also has a `WARNING:` line per channel for any frame its render does not cover |
| Jobs › Step, Batch Size | `1`, `5` | |
| Jobs › Copy to Edit | off | Sync the comp into the edit when it is done |
| Farm › Pool, Priority | the project's default pool, `50` | |

Buttons:

- **Submit** — saves the workfile, asks where to run (Render farm / This
  machine) and submits *every* `export_comp` of the shot in the workfile
  as one composite version, with every import frozen at what it resolved.
  Unread channels are listed and asked about first. In a Multi's workfile,
  one version per member.
- **Open Location** — the folder the Layer writes into.

Gotchas:

- A Layer can be written by one `export_comp` per shot; Submit refuses two.
- A comp rendered by hand (a ROP Image) carries no `chromaticities`, so RV
  and Nuke read it as Rec.709; the farm stamps every frame. See
  [Colour space](../compositing.md#colour-space).

## `th::import_scene` (COP)

The shot's staged scene as **geometry** and its render camera as **camera**,
typed so both wire straight into a Rasterize Geometry (`camera_ref` and
`geometry`): Copernicus can rasterize anything in the shot. Tab menu
`_TumblePipe/pipeline`. Inside, an embedded
[`th::import_shot`](import-and-export.md#thimport_shot-lop) composes the
staged stage **with payloads**; a SOP Import lifts the prims out (LOP Import,
Unpack USD to polygons) and a Camera Import reads the camera through a
`lopimportcam`. Opening a workfile refreshes it through the inner import.
Source: [`otls/cop_th.import_scene.1.0`](../../otls/cop_th.import_scene.1.0/th_8_8Cop_1import__scene_8_81.0/DialogScript),
[`cops/import_scene.py`](../../python/tumblepipe/pipe/houdini/cops/import_scene.py).

| Label | Default | What it does |
|---|---|---|
| Shot | `from_context` | Shots only |
| Channel, Department, Version | `default`, `from_context`, `latest` | As on `import_shot` |
| Reads | | The staged version the inner import loaded |
| Geometry › Primitives | `/*` | The prims imported; cameras and lights add nothing |
| Geometry › Purpose | Render | Render / Proxy / Guide |
| Geometry › Time Sample | Animated | Animated follows the frame; Static imports **Static Frame** once |
| Camera › Camera | `/cameras/render_camera` | The camera prim |
| Import | | Runs the embedded `import_shot` |

The geometry is the whole staged shot, payloads and all: narrow
**Primitives** to what the comp needs on a heavy shot.

## `th::import_lop_camera` (COP)

Bring the shot's render camera into COPs, for the nodes that need real
camera data (depth, projections). Tab menu `_TumblePipe/comp`. It embeds a
[`th::import_shot`](import-and-export.md#thimport_shot-lop) that composes
the shot's staged stage **without payloads**, lifts `/cameras/render_camera`
out through a `lopimportcam` and feeds a `cameraimport` COP; see
[The shot camera in comp](../compositing.md#the-shot-camera-in-comp).
Its output is typed RGBA, so it cannot feed a Rasterize Geometry's camera
input: for that use [`th::import_scene`](#thimport_scene-cop), whose
**camera** output is Metadata.
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

| Label | Default | What it does |
|---|---|---|
| Time Sample | Static | **Static** imports one frame, so the COP network is not time dependent; **Animated** imports the current frame and recooks the network every frame |
| Static Frame | `$FSTART` | The frame Static imports (hidden when Animated) |
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
