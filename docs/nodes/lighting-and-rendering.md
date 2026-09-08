# Lighting and rendering

The nodes a lighter and a render TD touch: Karma settings and presets, the
AOV set, light-group and puzzle mattes, per-layer overrides, the turntable
studio, a handful of light and card helpers, playblasts, and the recipes.
Entity, Department and Channel behave as described in
[Pipeline nodes → Shared concepts](index.md#shared-concepts). How a
submission composes the stage, where the render settings prim lives and what
the farm actually renders is in
[Composition → Render staging](../composition.md#render-staging); where the
frames land and how they become a comp is in [Compositing](../compositing.md).

None of these HDAs carries built-in help text, so this page is the reference.

## `th::render_settings` (LOP)

Karma render settings for the shot, with named presets. Tab menu
`_TumblePipe/pipeline`; drop it in the shot's `/stage` after the lights.
Inside it is a Karma Render Properties node driven by the parms below, plus
an embedded `th::render_vars` that has **only `beauty` ticked** and is not
on the interface — every other AOV comes from a separate
[`th::render_vars`](#thrender_vars-lop), [`th::lpe_tags`](#thlpe_tags-lop)
or [`th::puzzlemattes`](#thpuzzlemattes-lop) downstream.
Source: [`otls/lop_th.render_settings.1.0`](../../otls/lop_th.render_settings.1.0/th_8_8Lop_1render__settings_8_81.0/DialogScript),
[`lops/render_settings.py`](../../python/tumblepipe/pipe/houdini/lops/render_settings.py).

| Label | Default | What it does |
|---|---|---|
| Preset Name | `default` | Menu of `default` plus every `*.json` in the project's `_config/presets/houdini/lops/render_settings/` |
| Load Preset | | Sets every parm on the node from the preset file; does nothing if the file does not exist (`default` has no file until you save one) |
| Save Preset | | Writes **all** the node's parms (camera and resolution included) to `<Preset Name>.json`; disabled until the unlabelled toggle beside it is ticked, and re-locks after a load or save |
| Camera | `/cameras/render_camera` | The render camera — the path the shot templates author and playblast and render read |
| Width/Height-Pixels | `1920 1080` | Resolution |
| Path Traced Samples | `64` | |
| Diffuse / Reflection / Refraction / Volume / SSS Limit | `2 / 4 / 4 / 0 / 1` | Karma ray limits |
| Enable Depth of Field, Enable Motion Blur | on, on | |
| Dicing Camera | off | Dice displacement from a copy of the camera at **Source Frame** (`1001`), optionally with **Add Focal Length**; **Quality Scale** `0.5` |

Gotchas:

- The prim these settings are written to is project-owned (`/Render/…` or
  `/scene/Render/…`); the submit dialog's overrides find it by asking the
  stage — see
  [Where the render settings live](../composition.md#where-the-render-settings-live).
- A preset is a snapshot of the whole node. Loading one overwrites the
  camera path too.

## `th::render_vars` (LOP)

Tick the AOVs a render should write. Tab menu `_TumblePipe/rendering`; put
it after `render_settings`. Every toggle is a RenderVar prim under
`/Render/Products/Vars`, and **every toggle is off by default** — a fresh
node adds nothing until you tick something.
Source: [`otls/lop_th.render_vars.1.1`](../../otls/lop_th.render_vars.1.1/th_8_8Lop_1render__vars_8_81.1/DialogScript),
[`lops/render_vars.py`](../../python/tumblepipe/pipe/houdini/lops/render_vars.py),
[`pipe/houdini/aov.py`](../../python/tumblepipe/pipe/houdini/aov.py).

| Toggle | AOV | What it contains |
|---|---|---|
| beauty | `beauty` | Full beauty (`C.*[LO]`) |
| variance (after beauty) | `beauty_mse` | Beauty variance, for the denoiser |
| samples | `samples` | Pixel sample count |
| alpha | `alpha` | |
| albedo, variance | `albedo`, `albedo_mse` | Base colour and its variance |
| normal, variance | `normal`, `normal_mse` | Hit normal and its variance |
| uv | `uv` | `st` |
| position | `position` | Hit position |
| diffuse / specular / volume / emission | `diffuse`, `specular`, `volume`, `emission` | LPE splits (`C<RD>.*L`, `C<RG>.*L`, `CV.*L`, `C.*O`) |
| depth | `depth` | Hit depth (`ray:hitPz`) |

Every consumer sorts AOVs the same way (`aov.py`): `beauty` first, then the
`beauty_<tag>` light groups, then the `objid_*` / `holdout_*` mattes, then
everything else — that is the order `build_comp` and the turntable read them
in.

## `th::lpe_tags` (LOP)

Light groups: tag lights with an LPE tag and get one `beauty_<tag>` AOV per
tag. Tab menu `_TumblePipe/pipeline`; put it after the lights and before the
render settings. Internally an LPE Tag LOP tags the lights row by row, then a
loop reads the tags back **off the cooked stage** and builds a RenderVar for
each one it finds.
Source: [`otls/lop_th.lpe_tags.1.0`](../../otls/lop_th.lpe_tags.1.0/th_8_8Lop_1lpe__tags_8_81.0/DialogScript),
[`lops/lpe_tags.py`](../../python/tumblepipe/pipe/houdini/lops/lpe_tags.py),
[`pipe/houdini/util.py`](../../python/tumblepipe/pipe/houdini/util.py) (`propose_lpe_tags`, `unmatched_lpe_tags`).

| Label | Default | What it does |
|---|---|---|
| Status | *(hidden when empty)* | *No light carries: a, b …* — tags whose Lights pattern selects nothing. Each one silently costs its `beauty_<tag>` AOV |
| Generate LPEs from scene | | Fills the rows from the lights' names: everything before the first `_` becomes the tag, so `side_key` and `side_rim` both propose `side`; a light with no `_` proposes nothing. A starting point only — edit the rows |
| Tags › LPE Tag | `light_group#` | The tag; the AOV is `beauty_<tag>` |
| Tags › Lights | all input prims | Prim pattern of the lights that get the tag |

Gotchas:

- A tag nothing carries produces no AOV and no error — only the Status line
  says so. Read it before submitting.
- An **empty** Lights pattern makes the LPE Tag LOP tag every light
  `Untagged_Lights`, which the loop skips by design — so every tagged AOV
  disappears at once.
- Mesh lights count. They keep the type name `Mesh` (the light API is an
  applied schema), and an earlier version of the scrape dropped them; see
  [LPE tag harness](../development.md#lpe-tag-harness) for the contract.
- The node writes to a hardcoded `/Render/rendersettings`; on a project
  whose settings live under `/scene` see
  [Where the render settings live](../composition.md#where-the-render-settings-live).

## `th::puzzlemattes` (LOP)

One RGB matte AOV from three prim patterns. Tab menu
`_TumblePipe/rendering`. The node adds a `color3f` RenderVar named
`objid_<Name>` sourced from a primvar it sets on the matched prims, so the
red, green and blue channels of the AOV each mask one pattern. `build_comp`
picks every `objid_*` up as a mask and splits it into R, G and B outputs.
Source: [`otls/lop_th.puzzlemattes.4.0`](../../otls/lop_th.puzzlemattes.4.0/th_8_8Lop_1puzzlemattes_8_84.0/DialogScript),
[`lops/puzzle_mattes.py`](../../python/tumblepipe/pipe/houdini/lops/puzzle_mattes.py).

| Label | Default | What it does |
|---|---|---|
| Name | `$OS` | AOV name suffix: the AOV is `objid_<Name>` |
| Channels › Red / Green / Blue | *(empty)* | Prim pattern per channel (the action button opens the prim picker) |

The node has a viewer state, but it is a stub: selecting prims in it does
not fill the channels. Type the patterns.

## `th::render_layer_setup` (LOP)

Per-render-layer visibility, holdout and material overrides. Tab menu
`_TumblePipe/lighting`. Three toggles at the top — **Geometry Overwrite**,
**Material Overwrite**, **Overwrite settings** — enable the three kinds of
override; the last one also reveals its folder.
Source: [`otls/lop_th.render_layer_setup.1.0`](../../otls/lop_th.render_layer_setup.1.0/th_8_8Lop_1render__layer__setup_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Render Settings Overwrites › Deactive Materials | off | Deactivate the materials |
| Render Settings Overwrites › Disable Lighting | on | Karma's global *disable lighting* |
| Overwrites › Layer # › Geometry Overwrites › Include › Primitives | *(empty)* | Prims this row applies to |
| … › Include › Render Visibility | Invisible to primary rays (Phantom) | Menu: visible to all, primary only, primary and shadow, phantom, no diffuse, no secondary, no shadow, unrenderable |
| … › Include › Holdout Mode | None | None / Matte / Background |
| … › Exclude › Render Visibility | Invisible to secondary rays | Applied to everything the Include pattern does not match |
| … › Exclude › Holdout Mode | None | |
| Overwrites › Layer # › Material Overwrites › Material Path, Primitives | *(empty)* | Assign a stage material to a prim pattern for this layer |

The multiparm builds one **Layer #** per render layer, each with as many
geometry rows as you add. What a render layer is in this pipeline — a
channel, not a USD variant — is in
[Channels](../composition.md#channels-and-why-they-are-not-usd-variants).

## `th::render_debug` (LOP)

Preview what the farm's **stage task** renders. Tab menu
`_TumblePipe/debug`. **Build** clears the node's dive target and rebuilds
inside it the exact graph the stage task exports for the chosen shot and
channel (`render_stage.build_render_stage_graph`), so you can dive in and
inspect the composed stage.
Source: [`otls/lop_th.render_debug.1.0`](../../otls/lop_th.render_debug.1.0/th_8_8Lop_1render__debug_8_81.0/DialogScript),
[`lops/render_debug.py`](../../python/tumblepipe/pipe/houdini/lops/render_debug.py),
[`pipe/houdini/render_stage.py`](../../python/tumblepipe/pipe/houdini/render_stage.py).

| Label | Default | What it does |
|---|---|---|
| Shot | `from_context` | Follow the workfile, or pick a shot from the menu |
| Channel | *(first channel)* | Which channel's build to preview |
| Build | | Rebuild the graph |

Without a shot the node bypasses itself with the comment *Bypassed: No shot
selected*. Two caveats on "the same graph", spelled out in
[Stage task](../composition.md#stage-task-standalonetrue): it previews the
stage task only — a **default** submission renders the flattened stage
instead — and it has no department parm, so it always shows the **full**
department stack, never a cut one. To see what husk really got, open the
job's `collapsed_stage_<variant>.usda` or `stage_<variant>.usd`.

## `th::lookdev_studio` (LOP)

A turntable stage for look development: camera, light rigs, podium and
backdrop, slapcomp, local render and a farm submission. Tab menu
`_TumblePipe/lookdev`; feed it the asset stage. The interface is large; the
parms that matter:
Source: [`otls/lop_th.lookdev_studio.2.0`](../../otls/lop_th.lookdev_studio.2.0/th_8_8Lop_1lookdev__studio_8_82.0/DialogScript),
[`lops/lookdev_studio.py`](../../python/tumblepipe/pipe/houdini/lops/lookdev_studio.py).

| Label | Default | What it does |
|---|---|---|
| Selection › Primitives, Exclude Primitives | *(empty)* | What goes on the turntable |
| Framing Method | Input Prims | Frame the camera to the input prims' bounds, or to a **Box** of the given **Size** |
| Viewport › Render Pass | beauty | beauty / clay / wireframe / uv |
| Turntable Camera, Timeline, OCIO, Render view, Reset | | Look through `/cameras/turntable_camera`; set the playbar to the loop; set the viewer to *sRGB - Display* / *ACES 1.0 - SDR Video*; toggle the viewport between Houdini VK and Karma XPU; zero the camera and asset transforms |
| Camera › Focal Length, F-Stop, Look At Asset, Translate/Rotate Camera, Camera shake | `35`, `0`, on, … | |
| Asset › Animated Asset, Freeze Frame | off, `1001` | Hold an animated asset on one frame unless ticked |
| Turntable › Motion Type | rotate asset | rotate asset / swivel camera / circle |
| Turntable › Asset Turn Framerange | `1001 1101` | The asset turn; **Light Turn Framerange** follows it (one more turn of the same length) |
| Lighting › Lights | Neutral | None / Studio / Neutral / Sun / Morning / Spotlight; **Edit Lights** opens the rig in a floating network editor |
| Lighting › Environment | built-in HDRI | Tick **Use file path** to use your own; **Exposure**, **Lights Exposure**, **Light Rotation** |
| Staging › Podium, Background Color, Background Distance | None, `0.4` grey | Disk / Slab / Box / Cylinder |
| Utilities › Show spheres, Show Scale Ref | off, off (`1.8` m) | Chrome/grey balls and a scale figure |
| Slapcomp › Vignette, Chromatic Abberation, Lense Distortion, Apply Slapcomp | | Viewport slapcomp; **Render With Slapcomp** (on) applies it to renders |
| Render › Resolution, Path Traced Samples | `2048 2048`, `64` | **Choose Resolution** is Houdini's preset menu |
| Playblast To MPlay, Render to MPlay, Render to Disk in Background | | Flipbook or Karma-render the loop; **Output** `$HIP/turntable/$OS.$F4.exr` |
| Farm › Pool, Priority, Step size, Batch size | first pool, `50`, `10`, `5` | |
| Farm › Start/End-Frame | loop start, start + 5 × loop length | Check it — the default end is not the loop end |
| Submit Render To Farm | | Saves the hip and submits an export + render job (purpose `turntable`, channel `main`, render department `render`, no denoise) with the AOVs found on the node's stage |

Gotchas:

- Submitting needs a saved pipeline workfile — the entity comes from the
  `context.json` beside the hip, and the button fails with *Invalid workfile
  path* otherwise.
- The studio writes to a hardcoded `/Render/rendersettings`; see
  [Where the render settings live](../composition.md#where-the-render-settings-live).
- The Karma preview renders and flipbooks land in `$HIP/turntable/` and
  MPlay, not in the pipeline's render tree.

## `th::light_blocker` (LOP)

A card with a translucent material, for shaping light. Tab menu
`_TumblePipe/lighting`.
Source: [`otls/lop_th.light_blocker.1.0`](../../otls/lop_th.light_blocker.1.0/th_8_8Lop_1light__blocker_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Path | `/lights/lightblockers/$OS` | Where the card prim goes |
| Translucency | `1 1 1` | How much light passes; lower it to block |
| Color | `0.8` grey | Card colour |
| Transform | Rotate `0 0 90` | Standard LOP transform block |

## `th::karmafogbox` (LOP)

A Karma fog volume in a primitive shape. Tab menu `_TumblePipe/lighting`.
Source: [`otls/lop_th.karmafogbox.1.0`](../../otls/lop_th.karmafogbox.1.0/th_8_8Lop_1karmafogbox_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Primitive Path | `/volumes/$OS_VOL` | |
| Shape | Box | Box / Sphere / Tube / Cone / Capsule / Torus / Custom Shape |
| Custom Shape LOP Path, Custom Shape Primitives | `/stage/import_asset_layer1`, `/PROP/DracCar` | With Custom Shape: the LOP and prims to fill. The defaults are leftovers from another project — set both |
| Density, Shadow Density, Scattering Phase, Color | `0.1`, `1`, `0`, white | |
| Translate / Rotate / Scale / Uniform Scale | identity | |

The **Emission Color** and **Emission Strength** parms are hidden for every
shape (their hide rule never passes), so the fog cannot emit from the
interface.

## `th::light_streaks` (LOP)

Emissive streak geometry — god-rays and light shafts as renderable prims.
Tab menu `_TumblePipe/lighting`.
Source: [`otls/lop_th.light_streaks.1.4`](../../otls/lop_th.light_streaks.1.4/th_8_8Lop_1light__streaks_8_81.4/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Primitive Name | `$OS` | |
| Base, Emission, Color, Opacity, Opacity Map | `0.025`, `1`, warm orange, `0.4` grey, *(none)* | The streak material |
| Specular, Roughness, Bump Scale, Displacement Scale | `0`, `0.5`, `0.25`, `0` | |
| Mask by Edges, Enable Y Mask, Enable Y Colors | off | Fade by distance to edges (**Radius From**, **Radius**, **Remap**), by bounding-box height (**Y Minimum/Maximum**, **Ramp Mask**), or colour by height (**Color Ramp** plus noise) |
| Subdivide | `0` | |
| Advanced › Cast Shadows | off | |
| Advanced › Clip by Opacity Texture, Clip Below | off, `0.0001` | Cut away geometry where the opacity map is below the threshold |
| Advanced › Primitive Path | `/lights/lightStreaks` | |

## `th::image_card` (LOP)

A textured card. Tab menu `_TumblePipe/lighting`. The only parm is
**Color Map** (default `uvgrid_grey.pic`); its action button creates a COP
network for the map. Size and placement are not on the interface — dive in
to change them.
Source: [`otls/lop_th.image_card.1.0`](../../otls/lop_th.image_card.1.0/th_8_8Lop_1image__card_8_81.0/DialogScript).

## `th::image_plane_painter` (LOP)

Paint an image in an external editor and get it back as a plane, a convex
hull or a traced mesh. Tab menu `_TumblePipe/utils`.
Source: [`otls/lop_th.image_plane_painter.1.1`](../../otls/lop_th.image_plane_painter.1.1/th_8_8Lop_1image__plane__painter_8_81.1/DialogScript),
its [`PythonModule`](../../otls/lop_th.image_plane_painter.1.1/th_8_8Lop_1image__plane__painter_8_81.1/PythonModule).

| Label | Default | What it does |
|---|---|---|
| Trace Container | `$OS` | Prim name |
| Image Save Path, Image Name | `$HIP/images`, `$OS` | The PSD is `<path>/<name>.psd` |
| Image Resolution | `1000` × plane aspect | |
| Image Editor | Krita's default install path | The executable **Edit Image** launches |
| Edit Image | | Opens the PSD; if there is none yet, creates one from a template and opens that |
| Reload Image | | Reloads the image COP, recreates one null output per image layer and pins a thumbnail of the PSD in the network editor |
| Open Image Location | | |
| Mesh Method | Image Plane | Image Plane / Convex Image (**Dilude/Erode Amount**, **Mesh Resolution**) / Trace Image (**Expand Mesh**, **Edge Poly Lenght**, **Mesh Size**, **Threshold**) |
| Seperate Prim Per PSD Layer, Layer Offset | off, `0.001` | One prim per layer, stacked |
| Plane Size | `1 1` | |
| Material Properties › Emission, Roughness, Reverse Normals, Add Point Colors from image | `0`, `0.8`, off, on | |
| Transform | identity | |

Gotchas:

- A **new** PSD is copied from `W:\_pipeline\pipeline\krita\blank_template.psd`
  — a hardcoded legacy path. Without it Edit Image reports *Failed to create
  PSD file*. Image Resolution is read but not applied to the template.
- The node needs the GUI (it drives the network editor and the editor
  process); it does nothing useful headless.

## `th::projection_mapping` (LOP)

Project an image from a camera onto a prim as a material. Tab menu
`_TumblePipe/lookdev`. Inside it lifts the camera into an OBJ camera, renders
the projection through a COP network to a texture and assigns that texture
in a material at **Material Path**.
Source: [`otls/lop_th.projection_mapping.1.1`](../../otls/lop_th.projection_mapping.1.1/th_8_8Lop_1projection__mapping_8_81.1/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Camera LOP Path | first input prim | The LOP holding the camera |
| Camera Prim Path | *(empty)* | The camera prim |
| Projected Prim | first input prim | What receives the projection |
| Material Path | `/scene/mat/Projection_MAT` | Where the material is written |

The HDA's Python module carries two helpers (`init_projection_cam`,
`flipbook_camera`) that no parm calls, and the first reads a parm that does
not exist — they are not reachable from the interface.

## `th::mesh_outline` (LOP)

Draw an outline stroke around meshes, as geometry. Tab menu
`_TumblePipe/lookdev`.
Source: [`otls/lop_th.mesh_outline.1.0`](../../otls/lop_th.mesh_outline.1.0/th_8_8Lop_1mesh__outline_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Outlined Primitives | `%type:Mesh` | |
| Outline Prim Path | `/` | Where the outline geometry goes |
| Time Sample | Static | Static / Animated |
| Line Thickness | `0.007` | Plus **Shrink near Intersection / Thinness / Concavavity** |
| Paint Thickness | | Enters a paint state on the internal SOP to paint a `line_thickness` attribute |
| Line Color › Line Diffuse, Color Mode | `1`, Constant | Constant colour (**Constant Color**, black) or the geometry's colour with **Hue Shift / Saturation / Value / Gamma / Brightness** |
| Line Texture › Texture mode | None | None / Copnet (**Edit Copnet Texture** opens the internal COP net) / File (**Line Texture File**) |
| Texture Offset, Texture Scale | `0`, `1` | |

## `th::playblast` (LOP)

Flipbook the stage through the pipeline's playblast folders. Tab menu
`_TumblePipe/pipeline`; feed it the stage to preview. **Playblast** caches
the stage to a temporary USD, renders one JPG per frame through Houdini's
flipbook ROP with the chosen camera, encodes an MP4 and copies it to the
next `render:/playblast/<shot>/<department>/v####.mp4` **and** the shot's
rolling daily. How this compares with a farm playblast is in
[Compositing → Playblast](../compositing.md#playblast).
Source: [`otls/lop_th.playblast.1.0`](../../otls/lop_th.playblast.1.0/th_8_8Lop_1playblast_8_81.0/DialogScript),
[`lops/playblast.py`](../../python/tumblepipe/pipe/houdini/lops/playblast.py).

| Label | Default | What it does |
|---|---|---|
| Entity | From context | From context (the workfile's shot and department) or From settings |
| Shot, Department | | With From settings: any shot; its **renderable** departments |
| Camera | first camera | The cameras under `/cameras` in the input stage |
| Shading Mode, Lighting | Smooth Shaded, Headlight Only | Viewport look of the flipbook |
| Frame Range | From Config | The shot's configured range plus roll, or From Settings (**First/Last-Frame**, **Pre/Post-Roll**) |
| Resolution | `1280 720` | |
| Playblast | | Render, encode and publish |
| View latest | | Open the newest MP4 for this shot and department (a message if there is none) |
| Browse All | | Open the playblast folder |

Gotchas:

- The node only ever playblasts a **shot**: From context inside an asset
  workfile resolves nothing. The department must be renderable — a
  non-renderable workfile department resolves nothing too.
- No camera under `/cameras` fails the button with *No camera path found*.

## `th::playblast` (SOP)

The same publish path from a SOP context, rendered through an OpenGL ROP.
Tab menu `_TumblePipe/pipeline`. It carries the shared Entity button (shots
only, *Select Shot*) and writes the same versioned MP4 and daily as the LOP.
Source: [`otls/sop_th.playblast.2.0`](../../otls/sop_th.playblast.2.0/th_8_8Sop_1playblast_8_82.0/DialogScript),
[`sops/playblast.py`](../../python/tumblepipe/pipe/houdini/sops/playblast.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The shot |
| Department | `from_context` | Menu of the shot's renderable departments — **pick one**: the node does not resolve `from_context` here, and an unlisted value makes the buttons fail |
| Camera | *(empty)* | Cameras under `/cameras` of the stage the node reads |
| Playblast, View Latest, Browse All | | As the LOP |

The frame range is always the shot's configured range plus roll; there is no
override. The camera and geometry are read from a LOP node named `IN_stage`
that the HDA addresses relative to itself, so the node depends on where it is
placed.

## Recipes (`th::th_configure_*`)

Five node-cluster recipes ship as Data operators in
[`otls/Recipes.hda`](../../otls/Recipes.hda). They have no Tab submenu;
place them from the **Alt+R** recipes radial
([Radial menus → Alt+R](../radial-menus.md#altr-recipes)). Each drops a
small pre-wired network you then retarget:

- **th configure forest gobo** (LOP) — a disk light with a gobo light
  filter whose map is a COP network of scattered, noise-animated leaf and
  branch silhouettes, grafted onto the input stage. The filter map points at
  `op:/stage/copnet_gobo/OUT`, so it only resolves when placed in `/stage`.
- **th configure grass** (LOP) — a SOP Create that grows grass under
  `/SET/SnailForest/geo/grass` with a grass material assigned. The path is
  from the project it was saved in; change **Path Prefix** first.
- **th configure lop import** (SOP) — a LOP Import of `/CHAR` from the
  enclosing LOP input, unpacked to polygons, with a `rest` output (frame
  `1001`, rest swapped in) and an `anim` output.
- **th configure material override** (LOP) — unassign every material,
  assign one unlit override material (`/scene/mat/override_MAT`) and prune
  the lights: a flat-shaded pass.
- **th configure render layer matte** (LOP) — render geometry settings that
  matte everything `%type:Boundable`, un-matte `/CHAR/**`, edit
  `/Render/rendersettings`, and end in a `th::export_render_layer` node on
  department `light`. **That node type is not shipped** (it is not in
  [`hpm.toml`](../../hpm.toml)), so the last node of the recipe lands as a
  missing type; replace it with `th::export_layer`.

The recipes were saved in Houdini 20.5; re-save any that misbehave on a
newer major. They are distinct from the network-catalog recipes under the
package's `recipes/` directory, which is currently empty apart from its
[README](../../recipes/README.md).
