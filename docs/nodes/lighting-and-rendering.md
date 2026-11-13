# Lighting and rendering

The nodes a lighter and a render TD touch: Karma settings and presets, the
AOV set, light groups, mattes and distance ramps, per-layer overrides, the turntable
studio, a handful of light and card helpers, playblasts, and the recipes.
Entity, Department and Channel behave as described in
[Pipeline nodes → Shared concepts](index.md#shared-concepts). How a
submission composes the stage, where the render settings prim lives and what
the farm actually renders is in
[Composition → Render staging](../composition.md#render-staging); where the
frames land and how they become a comp is in [Compositing](../compositing.md).

None of these HDAs carries built-in help text, so this page is the reference.

## `th::render_settings` (LOP)

Karma render settings for the shot, with named presets: render quality and
camera. Tab menu `_TumblePipe/pipeline`; drop it in the shot's `/stage`
after the lights. AOVs are not chosen here: tick them on
[`th::render_vars`](#thrender_vars-lop), the one place a shot's AOVs are
listed (the render template puts one after this node). Inside is a Karma
Render Properties node driven by the parms below, and an embedded
`th::render_vars` whose beauty renders when nothing upstream has authored a
`beauty` RenderVar, so a shot always renders at least the RGBA beauty. Light
groups and mattes come from [`th::lpe_tags`](#thlpe_tags-lop) and
[`th::mattes`](#thmattes-lop) downstream.

**Old AOVs section.** Up to 1.103 this node had an **AOVs** section with
the same toggles and Compression menus as `th::render_vars`. Its parms are
still on the node, hidden, and still drive the embedded `th::render_vars`,
so a scene that ticked AOVs there renders exactly what it did (Houdini drops
the saved values of parms an HDA no longer has, so removing them would have
silently dropped those AOVs). When such a scene loads, the node's comment
says which old AOVs still render, and a **Drop Old AOVs** button appears
below Settings: tick those AOVs on `th::render_vars`, then press it to stop
rendering them from here. The same note follows loading a preset saved with
old AOVs ticked. A new node has none ticked and shows no button.

The Karma Render Properties node inside keeps **its own AOV toggles off**.
They author Karma's names (its **Beauty** writes a var `Beauty` as a plane
named `C`, its **Albedo** an `Albedo` beside the project's `albedo`), which the
farm cannot read. While one is on the node errors, naming the toggle and the
`th::render_vars` toggle to tick instead. A scene saved with one on has it
switched off when it loads, the matching old AOV ticked so the render keeps
it, and a comment on the node saying which, to move to `th::render_vars`
(`render_settings.lock_karma_aovs`). The check also errors
on any render var on the stage whose plane name is not its prim name, and on
two vars of one AOV name (`check_render_vars` inside the node).
Source: [`otls/lop_th.render_settings.1.0`](../../otls/lop_th.render_settings.1.0/th_8_8Lop_1render__settings_8_81.0/DialogScript),
[`lops/render_settings.py`](../../python/tumblepipe/pipe/houdini/lops/render_settings.py).

| Label | Default | What it does |
|---|---|---|
| Preset Name | `default` | Menu of `default` plus every `*.json` in the project's `_config/presets/houdini/lops/render_settings/` |
| Load Preset | | Sets every parm on the node from the preset file; does nothing if the file does not exist (`default` has no file until you save one) |
| Save Preset | | Writes **all** the node's parms (camera and resolution included) to `<Preset Name>.json`; disabled until the unlabelled toggle beside it is ticked, and re-locks after a load or save |
| Camera | `/cameras/render_camera` | The render camera — the path the shot templates author and playblast and render read. Projects made from the template before 2026-10-07 keep it at `/scene/cameras/render_camera`; set this to match |
| Width/Height-Pixels | `1920 1080` | Resolution |
| Path Traced Samples | `64` | |
| Light Sampling Quality | `1` | Scales every light's sampling quality. Below 1 sends fewer shadow rays; 0.5 measured ~15% faster with no extra noise on HideAndReek, time better spent on Path Traced Samples |
| Diffuse / Reflection / Refraction / Volume / SSS Limit | `2 / 4 / 4 / 0 / 1` | Karma ray limits |
| Enable Depth of Field, Enable Motion Blur | on, on | |
| Dicing Camera | off | Dice displacement from a copy of the camera at **Source Frame** (`1001`), optionally with **Add Focal Length**; **Quality Scale** `0.5` |
| Drop Old AOVs | | Shown only while the hidden old AOVs section (up to 1.103) has an AOV ticked or its beauty menu changed; sets them all back to off, leaving the fallback beauty |

Gotchas:

- These values are what the farm renders. The Farm Submit dialog's Samples,
  Motion blur and DOF send nothing unless you set them there, and its
  **Camera** row shows the camera this node's published layer names — see
  [Submit Jobs → Render](../asset-browser/submit-jobs.md#render).
- The prim these settings are written to is project-owned (`/Render/…` or
  `/scene/Render/…`); the submit dialog's overrides find it by asking the
  stage — see
  [Where the render settings live](../composition.md#where-the-render-settings-live).
- A preset is a snapshot of the whole node, the hidden old AOVs included.
  Loading one overwrites the camera path too. A preset saved from 1.94 to
  1.103 can tick old AOVs: they render, and the node says so as above.

## `th::render_vars` (LOP)

Tick the AOVs a render should write. Tab menu `_TumblePipe/rendering`; put
it after `render_settings`. Every toggle is a RenderVar prim under
`/Render/Products/Vars`, and **every toggle is off by default** — a fresh
node adds nothing until you tick something. This is the one place a shot's
AOVs are chosen: the render template builds one after `th::render_settings`.
(`th::render_settings` had the same toggles in an **AOVs** section up to
1.103; see [Old AOVs section](#thrender_settings-lop).)
Source: [`otls/lop_th.render_vars.1.1`](../../otls/lop_th.render_vars.1.1/th_8_8Lop_1render__vars_8_81.1/DialogScript),
[`pipe/houdini/aov.py`](../../python/tumblepipe/pipe/houdini/aov.py).

| Toggle | AOV | What it contains |
|---|---|---|
| beauty | `beauty` | Full beauty (`C.*[LO]`), **RGBA** (`color4f`): its A is the alpha every comp uses |
| beauty variance | `beauty_mse` | Beauty variance, for the denoiser |
| samples | `samples` | Pixel sample count |
| albedo, albedo variance | `albedo`, `albedo_mse` | Base colour and its variance |
| normal, normal variance | `normal`, `normal_mse` | Hit normal and its variance |
| uv | `uv` | `st` |
| position | `position` | Hit position |
| diffuse / specular / volume / emission | `diffuse`, `specular`, `volume`, `emission` | LPE splits (`C<RD>.*L`, `C<RG>.*L`, `CV.*L`, `C.*O`) |
| depth | `depth` | Hit depth (`ray:hitPz`) |
| diffuse direct, diffuse indirect | `diffuse_direct`, `diffuse_indirect` | `C<RD>L`, `C<RD>.+L` |
| specular direct, specular indirect | `specular_direct`, `specular_indirect` | `C<RG>L`, `C<RG>.+L` |
| transmission / sss / coat | `transmission`, `sss`, `coat` | `C<TG>.*L`, `C<TD>.*L`, the BSDF labelled `coat` |
| unshadowed, shadow | `unshadowed`, `shadow` | The beauty with nothing casting shadows, and the light shadows take away (`unshadowed − shadow = beauty`). Not `beauty_*`: slapcomp sums those as light groups |
| ao | `ao` | Ambient occlusion |
| motion vectors / velocity | `motionvectors`, `velocity` | Screen-space motion (`ray:motionvector`) and world-space velocity (`ray:velocity`) |
| cryptoprimitives, cryptomaterials | `cryptoprimitives`, `cryptomaterials` | Cryptomattes of the prims (`__name`) and the materials (`__materialname`), overlap 6; see [Cryptomattes](#cryptomattes) |

These are Karma's own AOVs (the sources its Render Properties toggles use)
under TumblePipe names: lowercase, the var and its plane named alike.

There is no alpha toggle: the alpha is the beauty's A. The separate `alpha`
AOV this node used to write came from `ray:hit`, which Karma's holdouts do not
affect, so it counted held-out objects as solid; see
[Compositing → Alpha lives in the beauty](../compositing.md#alpha-lives-in-the-beauty).
Its `alpha` and `alpha_compression` parms are still on the node, hidden and
inert, so render templates and saved scenes that set them load cleanly.
Placed nodes picked up the change in place (same 1.1 version) — re-publish
the render department to render with it. A project's own
`root_default_prims.usda` also authors the default render vars; migration
v10 drops its `alpha` there too (see
[Configuration](../configuration.md)).

Every consumer sorts AOVs the same way (`aov.py`): `beauty` first, then the
`beauty_<tag>` light groups, then the `objid_*` / `holdout_*` mattes, then
everything else — that is the order `th::import_render`'s cable and the turntable read
them in.

An AOV's name is its RenderVar's prim name, lowercased: that is what the
publish lists and what the farm splits the EXR by, so the plane Karma writes
(`driver:parameters:aov:name`) must be that name too. The farm never renames
or drops a var. A render whose product lists a var with any other plane name
(Karma's own **Beauty** toggle writes `Beauty` as plane `C`), or two vars of
one AOV name (Karma's **Albedo** beside the project's `albedo`), is refused
at submit, or by the farm's stage task, with every such var named and the fix
(`pipe/usd/render_vars.py`). `th::render_settings` errors on the same thing
in the scene, and the publish's `render_var_names` and `render_var_twins`
checks report it. Up to 1.93 such a render failed on the farm with
`Missing required AOVs in rendered output: ['beauty']`.

### Cryptomattes

Tick **cryptoprimitives** or **cryptomaterials** to render a cryptomatte:
pick objects or materials by name in comp instead of rendering an `objid_*`
matte per selection. Karma renders a cryptomatte `X` as a preview plane `X`,
rank planes `X00`, `X01`, `X02` (two id/coverage pairs each, overlap 6) and
its manifest in the EXR header. The farm keeps that together: each
cryptomatte is published as **one** EXR in its AOV folder holding the
preview, the ranks (float) and its own manifest, written lossless whatever
the beauty uses, and the denoise passes it through untouched
(`apps/exr.py` `cryptomattes`). Its Compression menu offers lossless
choices only.

Karma's own **Karma Cryptomatte** LOP works too: the farm reads the plane
it names in `driver:parameters:aov:husk:name` (1.101 and older refused every such
render as naming no plane). Whatever authors it, a cryptomatte
var must be named `crypto*` (Karma's defaults `CryptoPrimitives` and
`CryptoMaterials` are), and a var named `crypto*` must be a cryptomatte:
that name is how the farm, the denoise and the comp know one
(`pipe/aovs.py` `is_cryptomatte_aov`), and a render breaking either rule is
refused at submit like any other var naming problem.

A cryptomatte works from a **rendered** EXR only, not in the Solaris
viewport. Karma's interactive render (the viewport, a Live Render, and so a
Copernicus slap comp block) delivers just the 3-channel preview plane `X`:
no `X00`... rank planes and no manifest, which only husk writes into the
EXR. A slap comp block asking for `cryptomaterials00` therefore fails with
"Input layer cryptomaterials00 not produced by the renderer", and a
Cryptomatte COP fed from it warns "No manifest found" (Houdini 22.0.429,
checked 2026-11-12; SideFX's Cryptomatte COP help says the same of slap
comp). Neither is a render settings problem: pull the matte in a comp from
[`th::import_render`](../compositing.md#thimport_render) on a farm or disk
render instead, or [publish a slap comp for the farm](#slap-comps), which
husk runs on the rendered frame with every plane in it.

Each AOV is one row: its toggle, its name, and its **Compression** menu, the
EXR compression that AOV is written with, greyed out while the toggle is off
(on `th::render_settings` the beauty's menu stays live, since the beauty
renders unticked too).
Data AOVs (`normal`, `depth`, `position`, `uv`, `samples`, `ao`,
`motionvectors`, `velocity`, the `*_mse` variances other than `beauty_mse`)
default to lossless **ZIP**, colour
AOVs to **DWAB**. The farm writes exactly what is set here; see
[Compositing → Compression](../compositing.md#compression).

## Slap comps

A Copernicus slap comp is a COP block (Block Begin to Block End) run on
every rendered image: in the Solaris viewport while you light, and, once
published, by husk on the farm. It is not the render chain's *slapcomp* job,
the oiiotool stack of a shot's channels
([Compositing → Farm submission](../compositing.md#farm-submission-and-mp4s)).

**Put one down with the Tab menu tool *TumblePipe Slap Comp*** (in any COP
network, under `_TumblePipe/pipeline`). It is Houdini's own Slap Comp Block
(a Slap Comp Import, a Block Begin and a Block End, wired, the Block End
registered as a slap comp block) with every slap comp AOV named `beauty`.
Houdini's stock block names its layer `C`, Karma's colour AOV, and
TumblePipe's render settings name that AOV `beauty`, so the stock block finds
nothing until the AOV is renamed on all three nodes. The preset does that;
the render settings keep `beauty`. Put your COPs between Block Begin and
Block End, then pick the block from the viewport's slap comp button.
To pull another AOV in, add a row with the same name (the AOV's name in the
render settings) on all three nodes. The viewport's interactive render
delivers no cryptomatte ranks (see [Cryptomattes](#cryptomattes)); husk's
farm render does.
Source: [`pipe/slap_comp.py`](../../python/tumblepipe/pipe/slap_comp.py),
[`cops/slap_comp_preset.py`](../../python/tumblepipe/pipe/houdini/cops/slap_comp_preset.py),
the tool in [`toolbar/tumblepipe.shelf`](../../toolbar/tumblepipe.shelf).

**Farm renders apply it once it is published.** A farm render does not open
your workfile: it renders the shot's published stage, so the block has to be
published too. The preset's Block End carries a **TumblePipe** folder:

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The shot or asset whose farm renders apply the slap comp: an entity URI, or `from_context` for the workfile's own |
| Use on Farm Renders | on | Off: Publish to Farm writes a version without a program, which stops farm renders applying one |
| Publish to Farm | | Compiles the block into the entity's next version of `export:/<entity>/_slapcomp/v####/` (`<entity>_slapcomp_v####.bgeo.sc`, plus `context.json` naming the block, the hip and the AOVs it reads and writes), and warns when the block names an AOV the entity's exports do not render |
| Published | | The version this node last published |

The compiled program is what husk's `--slap-comp` reads: the Block to
Geometry COP's output, a geometry file holding the block as an APEX graph.
Houdini's USD Render ROP writes the same file for a Slap Comp whose Source is
COP Network (`<usd>_slapcomp_<n>.bgeo.sc` beside the USD it renders), and
that is how this was found (Houdini 22.0.429: `husk --help`, the ROP's
**Husk › Slap Comp** tab, help `/copernicus/slap_comp#husk`).

Every farm render of the entity submitted afterwards (Farm Submit's Render,
preview and full, every channel, a direct or Standalone render, a turntable)
bundles the newest published program into its render jobs and runs husk
with `--slap-comp <program>`; the job title says `+ slap comp v####`. The
program is copied into a render job when the job is made (at Submit, or on
the farm by the collapse or stage job that makes it), so a later publish
changes only renders made after it. What that means:

- **The beauty on disk is the slap comped one.** husk replaces each AOV a
  slap comp outputs, so the published `beauty` (and everything downstream:
  denoise, the slapcomp stack, MP4s, comps) carries the slap comp. Keep the
  untouched beauty by writing to another AOV, which must already be one the
  render writes.
- **Each channel's render runs it.** With several channels the slap comp runs
  on each layer before the slapcomp stacks them. An effect that scales colour
  (a vignette, a grade) stacks to the same result; one that adds light (a
  glow) comes out once per layer.
- **Tiles must be 1.** husk runs a slap comp on whatever it renders, so in a
  tiled render on each tile: Farm Submit refuses a render of an entity with a
  published slap comp unless **Tiles** is 1 (its default is 4).
- **A wrong AOV name fails the frame.** husk exits with "Error running
  slapcomp/image filters" and writes nothing when the block reads or writes
  an AOV the render does not have, and the render task fails. No SOPs inside the block (SideFX: husk cannot run them).

## `th::lpe_tags` (LOP)

Light groups: tag lights with an LPE tag and get one `beauty_<tag>` AOV per
tag. Tab menu `_TumblePipe/pipeline`; put it after the lights and before the
render settings. Internally an LPE Tag LOP tags the lights row by row, then a
loop reads the tags back **off the cooked stage** and builds a RenderVar for
each one it finds.
Source: [`otls/lop_th.lpe_tags.1.0`](../../otls/lop_th.lpe_tags.1.0/th_8_8Lop_1lpe__tags_8_81.0/DialogScript),
[`lops/lpe_tags.py`](../../python/tumblepipe/pipe/houdini/lops/lpe_tags.py),
[`pipe/houdini/prim_queries.py`](../../python/tumblepipe/pipe/houdini/prim_queries.py) (`propose_lpe_tags`, `unmatched_lpe_tags`).

| Label | Default | What it does |
|---|---|---|
| Status | *(hidden when empty)* | *No light carries: a, b …* — tags whose Lights pattern selects nothing. Each one silently costs its `beauty_<tag>` AOV |
| Generate LPEs from scene | | Fills the rows from the lights in the node's input: a light's own LPE tag if it has one, else its name before the first `_` (so `side_key` and `side_rim` both propose `side`), else its whole name (`key`, `rim`, `dome`). Every light gets a row. With no lights it leaves the rows as they are and says so. A starting point only — edit the rows |
| Tags › LPE Tag | `light_group#` | The tag; the AOV is `beauty_<tag>` |
| Tags › Lights | all input prims | Prim pattern of the lights that get the tag |

The node authors no Karma image filter: a render comes out un-denoised and
Farm Submit's **Denoise** is the one denoise (OIDN with the albedo and normal
AOVs, on the farm). Before 2026-11-10 it switched on Karma's own OIDN filter,
so husk denoised every render and Denoise ran OIDN a second time.

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

## `th::mattes` (LOP)

Single-channel grading passes, as many as you need from one node: object
**mattes** and camera **distance ramps**, one row each (click **+** on the
multiparm to add one). Tab menu `_TumblePipe/rendering`; place it in the
render workfile **before** `th::render_settings`, which collects the AOVs
into its product. The shot render template places one, empty, after
`th::lpe_tags`. It replaced `th::puzzlemattes`, which packed three mattes
into the R, G and B of one AOV; that node is no longer shipped.
Source: [`otls/lop_th.mattes.1.0`](../../otls/lop_th.mattes.1.0/th_8_8Lop_1mattes_8_81.0/DialogScript),
[`lops/mattes.py`](../../python/tumblepipe/pipe/houdini/lops/mattes.py),
[`pipe/mattes.py`](../../python/tumblepipe/pipe/mattes.py).

- **Matte** → AOV `objid_<Name>`: 1 on the matched prims and everything
  under them, 0 elsewhere. A constant float primvar `objid_<Name>` on the
  prims, read by a primvar RenderVar. To matte part of a mesh, give the path of a **GeomSubset**, for
  example `/SET/Park/geo/bench/planks` for the wood of a bench whose legs and
  nails are other subsets of the same mesh: the mesh gets a per-face primvar,
  1 on the subset's faces and 0 on the rest.
- **Distance ramp** → AOV `ramp_<Name>`: 0 at **Near**, rising linearly to 1
  at **Far**, clamped; the sky reads 1. For grading (a depth-cue or
  atmosphere key in Resolve), not for a compositing depth pass — `depth`
  from `th::render_vars` holds true distances.

| Label | Default | What it does |
|---|---|---|
| Render Settings | `/Render/rendersettings` | Settings prim the ramps' global AOV material is set on; change it only on a project whose settings live elsewhere |
| Mattes › Name | *(empty)* | AOV is `objid_<Name>`. A row without a name is skipped |
| Mattes › Primitives | *(empty)* | Prim pattern (the action button opens the prim picker) |
| Mattes › Compression | ZIP | Keep it lossless: DWA softens the matte's edges |
| Distance Ramps › Name | *(empty)* | AOV is `ramp_<Name>`. A row without a name is skipped |
| Distance Ramps › Near / Far | 0 / 10 | Scene units from the camera where the ramp reads 0 and 1 |
| Distance Ramps › Measure | Distance from camera | *Distance from camera* is the ray length (round falloff); *Depth along view axis* is planar (`ray:hitPz`) |
| Distance Ramps › Compression | ZIP | Lossless keeps the ramp smooth under a strong grade |

Names use letters, digits and `_`, not starting with a digit. A bad name,
the same AOV twice, or Far not above Near is a node error.

How the ramp works: Karma evaluates it through a *global AOV material*
(`karma:global:globalaovmaterial` on the render settings), which the node
authors at `/Render/tumblepipe/mattes_aov_material` — a Karma Ray Import of
`ray:hitdist` or `ray:hitPz` through a MaterialX range node into an `aov:`
input of an unlit surface. It works on Karma XPU (GPU and CPU) and CPU.
Rays that miss run no shader, so the sky gets the RenderVar's default value,
set to 1. A render has **one** global AOV material: if something upstream
already set a different one, the node errors rather than replace it.

The farm's denoise passes mattes, ramps and cryptomattes through untouched, and
`th::import_render` reads each `objid_*` (one channel) as a single mask and
`ramp_*` as a mono pass.

The denoise job leaves mattes alone: it runs OIDN over every AOV except the
`*_mse` variances, the `objid_*` / `holdout_*` mattes, the `ramp_*`
distance ramps and the `crypto*` cryptomattes, which it publishes as byte
copies of the render — denoising would soften their edges (and scramble a
cryptomatte's ids).

## Render layers (channels)

A render layer is a **channel** of the shot, not a node: add the channel to
the shot (the entity's `channels` property: the Entity Editor's
[Layers column](../asset-browser/entity-editor.md#render-layers), or
`config/channels.py`). Then either name its prims in `channel_prims` and let the
farm render it as a render pass (below), or in the render workfile branch
the shot's chain once per channel and end each branch in its own
[`th::export_layer`](import-and-export.md#thexport_layer-lop) with **Channel**
set to that channel. Downstream, `th::import_layer` / `th::import_shot` with the
same Channel load it, and the farm renders one job per checked channel. What a
channel is, and why it is not a USD variant, is in
[Channels](../composition.md#channels-and-why-they-are-not-usd-variants).

**Add channels bottom to top.** The render's slapcomp daily stacks the
channels in the shot's channel order, the first (`default`) at the bottom
and the last on top. Add `background` before `characters`
before an overlay such as `feathers` (see
[the slapcomp](../compositing.md#farm-submission-and-mp4s)).

### Render layers from `channel_prims` (render passes)

The quickest way to split a shot needs no branch and no extra exports:
in the [Entity Editor](../asset-browser/entity-editor.md#render-layers)'s
Shots tab, double-click the shot's (or its sequence's) **Layers** cell, add
a layer and give it its prims, or type `characters: /CHAR/*` into the cell,
then Apply. That writes the shot's channels and its `channel_prims`
property (see [Configuration](../configuration.md), the same map the
[layered playblasts](#layered-playblasts) read):

```json
"channels": ["default", "characters"],
"channel_prims": {"characters": "/CHAR/*"}
```

Every farm render of the shot then carries one USD **render pass** per layer
(`/Render/Passes/<channel>`, what Houdini's Render Pass LOP authors), and each
channel's render job renders its own pass with `husk --pass`. Each pass:

- **holds out the layers above it** (the pass's `matte` collection): they cut
  a transparent hole in the layer and still cast their shadows onto it, so
  the background comes out with the characters' shadows and holes for them;
- **hides the layers below it from camera** (`cameraVisibility`): unseen, but
  still shadowing and reflecting into the layer;
- keeps its own prims visible and never held out, even where patterns
  overlap.

`default` without a pattern takes every prim no other layer names. If
`default` has a pattern too, prims no layer names are hidden from camera
in every layer (as in a layered playblast), still casting shadows. The
slapcomp stacks the layers "over" in channel order and gives back the full
frame. Comp with the beauty's alpha: Karma's holdouts leave utility AOVs
(`normal`, depth, positions) untouched, so those still show a held-out
prim (see [Alpha lives in the beauty](../compositing.md#alpha-lives-in-the-beauty)).

A channel no department ever exported is a **pass-only** layer: it renders
the `default` channel's stage and its pass picks the layer out. A channel
that *was* exported (a branch, as below) keeps rendering its own stage, with
its pass on top. Outside Houdini (the submit runner, plain-Python farm
tasks) a pattern is matched as a path glob (`/CHAR/*`, `/SET/**`,
`/CHAR/hero[12]`, `^` to exclude); Houdini-only syntax such as
`%type:Mesh`, and a `[...]` set that is not one (`[]`, `[z-a]`), is refused
there, so keep `channel_prims` to paths and globs. The Entity Editor's
Layers column runs the same check, so it never takes a pattern the farm
would refuse.

Farm Submit's **Channels** menu opens with every layer of such a shot
checked, `default` (the remainder) included, so the slapcomp is whole
(see [Farm Submit: Render](../asset-browser/submit-jobs.md#render)).

### Render layers from branches

When a layer needs different *content*, not just different visibility (its
own lights, a different look), branch the render workfile per channel as
above. What makes the layers differ is ordinary Solaris, placed in the
branch before its export. Holdouts can come from `channel_prims` (above), or
by hand with a **Render Geometry Settings** LOP per layer:

- **background**: the characters (`/CHAR/*`) *Invisible to primary rays*, so
  they still shadow and reflect into the set;
- **characters**: the set and props (`/SET/* /PROP/*`) as **Holdout: Matte**,
  so the set cuts the characters out;
- any further layer (e.g. **feathers**) holds out everything else the same way.

Put `th::mattes` upstream of the branch so every layer carries the same matte
and ramp AOVs. Farm Submit's **Channels** menu opens with the branch
channels checked but not `default`, which would render the whole unsplit
shot; check it when you want that too. `th::render_layer_setup` and the *th configure render layer
matte* recipe were removed in 1.67.0: neither applied its overrides (the HDA's
internal references pointed at parameters that did not exist).

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
job's `collapsed_stage_<channel>.usda` or `stage_<channel>.usd`.

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
- The studio's HDRI and backdrop fabric are files embedded in the HDA
  (`opdef:/th::Lop/lookdev_studio::2.0?environment_texture.exr`,
  `?drape_fabric.jpg`). husk cannot read `opdef:` paths, so the farm export
  copies every `opdef:` / `oplib:` file the stage uses into `embedded/` beside
  the exported stage and points the attributes there
  (`pipe/usd/embedded_assets.py`). Up to 1.101.0 farm turntables rendered
  without them.
- **Step size** is honoured on the farm: a task renders only its stepped
  frames (step 10, batch 5: 1001, 1011 … 1041). Up to 1.101.0 the render
  worker dropped the step and rendered every frame of the range.
- Farm Submit can render an asset's turntable without this workfile: the
  Assets tab's **Render** column builds a studio around the asset's staged
  build on the farm, with these defaults (see
  [An asset's turntable](../asset-browser/submit-jobs.md#an-assets-turntable)).
  It writes to the same `turntable:/render/<asset>/render/main` as this
  node's **Submit Render To Farm** for the `default` channel.

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

- A **new** PSD is copied from the package's `resources/krita/blank_template.psd`
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

Playblast the stage through the pipeline's playblast folders. Tab menu
`_TumblePipe/pipeline`; feed it the stage to preview. **Playblast** caches
the stage to a temporary USD, renders one JPG per frame with the chosen
camera (through TumbleView, the farm's viewport renderer, with husk, or Houdini's
flipbook ROP: **Renderer**), encodes an MP4 and copies it to the
next `render:/playblast/<shot>/<department>/<sequence>_<shot>_<department>_v####.mp4` **and** the shot's
rolling daily. How this compares with a farm playblast is in
[Compositing → Playblast](../compositing.md#playblast).
Source: [`otls/lop_th.playblast.1.0`](../../otls/lop_th.playblast.1.0/th_8_8Lop_1playblast_8_81.0/DialogScript),
[`lops/playblast.py`](../../python/tumblepipe/pipe/houdini/lops/playblast.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The shot — the shared Browse... button (shots only, *Select Shot*), as on `th::import_shot`; the label beside it shows what `from_context` resolved to |
| Department | `from_context` | `from_context` (the workfile's department) or one of the shot's **renderable** departments |
| Camera | first camera | The cameras in the input stage: under `/cameras` or `/scene/cameras`, else anywhere on the stage. Left empty it is the first one, the camera the menu shows |
| Renderer | TumbleView | **TumbleView**: the farm playblast's renderer (hdThPlayblast) run with husk, the same look as a farm playblast, with a progress bar that stops husk when cancelled. **Houdini GL**: Houdini's flipbook, exactly as the viewport draws (hair, curves and volumes too). Existing nodes take the default, TumbleView |
| Shading | Lit | TumbleView only: Lit, Clay, Albedo or Occlusion, as in the farm dialog |
| Lighting (TumbleView) | Scene Lights | TumbleView only: what lights Lit and Clay. **Scene Lights**: the scene's own lights, or the camera lights when it has none. **Headlight**: key, fill and rim lights that follow the camera, always. Parm `viewport_lighting`, the render setting `th:lighting` |
| Backface Culling | Off | TumbleView only. **Off**: both sides of every face are drawn. **Back Faces**: every mesh's back faces are left out, so a single-sided wall seen from behind is see-through. Parm `cull`, the render setting `th:cull` |
| Transparency | On | TumbleView only. **On**: see-through surfaces (opacity, transmission or `displayOpacity` under 1) show what is behind them. **Off**: every surface is drawn solid, e.g. eyes whose corneas you do not need. Parm `transparency`, the render setting `th:transparency` |
| Shading Mode, Lighting | Smooth Shaded, Headlight Only | Houdini GL only: viewport look of the flipbook |
| Frame Range | From Config | The shot's configured range plus roll, or From Settings (**First/Last-Frame**, **Pre/Post-Roll**). The menu names the frames From Config gives, e.g. `From Config (1001-1100)` |
| Resolution | the shot's render resolution | An expression: the shot's `render.resolution`, so the playblast has the render's frame; `1280 720` while no shot resolves. Type over it for your own size; Revert to Defaults brings the shot's back. A node made before 1.85 keeps the size it had |
| Motion Blur, Depth of Field, Blur Samples | off, on, 16 | Blurs from the camera's Shutter Open/Close and its F-Stop and Focus Distance: TumbleView's, or the flipbook's own under Houdini GL; Blur Samples is how many images each frame averages |
| Render Layers | off | TumbleView only: also write one movie per render layer, see [Layered playblasts](#layered-playblasts) |
| Playblast | | Render, encode and publish |
| View latest | | Open the newest MP4 for this shot and department (a message if there is none) |
| Browse All | | Open the playblast folder |

Gotchas:

- TumbleView draws meshes, USD shapes, basis curves and points: curves as
  ribbons of their width (at least a pixel across, so a curve with no width
  is a line, as in the viewport) and points as dots (at least two pixels).
  Volumes, and hair that a Houdini procedural (a groom) would make, are
  left out, as on the farm. Switch **Renderer** to Houdini GL when the
  preview needs them.
- TumbleView draws a see-through material (opacity, transmission or an
  opacity cut-out under 1) or a `displayOpacity` under 1 blended over what
  is behind it, one opacity per mesh; an opacity map (a texture) is not
  read and leaves the surface opaque. **Transparency** = Off (the render
  setting `th:transparency` = `off`, or `TH_PLAYBLAST_TRANSPARENCY`) draws
  every surface solid instead, whatever its opacity: corneas, glasses and
  windows hide what is behind them, as an opaque surface does. The SOP
  `th::playblast` (and its Live playblast) has the same **Transparency**
  parm, and the farm dialog's playblast has **Transparency**. See
  [Compositing → Playblast](../compositing.md#playblast).
- TumbleView draws materials bound to some of a mesh's faces (GeomSubsets)
  on those faces, the rest in the mesh's own material, as the viewport does.
- TumbleView's Lit and Clay shading use the scene's own lights (distant,
  sphere, rect, disk, cylinder and dome, as bright as Karma renders them, no
  shadows); a scene without lights gets fixed lights that follow the camera.
  **Lighting** = Headlight (the render setting `th:lighting` = `headlight`,
  or `TH_PLAYBLAST_LIGHTING`) keeps the camera lights even in a lit scene.
  See [Compositing → Playblast](../compositing.md#playblast).
- TumbleView draws both sides of every face, as husk asks for no culling.
  **Backface Culling** = Back Faces (the render setting `th:cull` = `back`,
  or `TH_PLAYBLAST_CULL`) leaves out every mesh's back faces, whatever the
  mesh's `doubleSided`: a room's wall between the camera and the action
  stops hiding it when its faces point inward. The SOP `th::playblast` has
  the same **Lighting** and **Backface Culling** parms (its Live playblast
  uses them too), and the farm dialog's playblast has **Lighting** and
  **Culling**.
- TumbleView always draws over its grey backdrop unless
  `TH_PLAYBLAST_ALPHA=1` (or the render setting `th:alpha`) asks for a
  transparent background: alpha is then each pixel's coverage. What the
  frames hold then (premultiplied in EXR, straight in display space in PNG)
  is in [Development → hdThPlayblast: transparent
  background](../development.md#hdthplayblast-transparent-background).
- The node only ever playblasts a **shot**: From context inside an asset
  workfile resolves nothing. The department must be renderable — a
  non-renderable workfile department resolves nothing too.
- In a **Multi**'s workfile Entity `from_context` resolves nothing either:
  a Multi holds several shots, and a playblast writes one shot's MP4 and
  daily. Pick the member with the **Entity** button; Department can stay on
  `from_context`. The Department menu there lists the departments the Multi
  covers. Pressing a button with nothing resolved says which Multi the
  workfile belongs to. `th::lookdev_studio`, which has no entity parm at
  all, refuses the same way: its turntable renders one asset. From 1.83 a
  new Multi animation workfile has this done already: each member's
  `animate_shot_<member>` dive holds a SOP `th::playblast` set to that shot
  and `render_camera`.
- In a Multi with a **shared timeline** a member sits later on the
  session's timeline than on its own frames (its time offset). The
  playblast caches the frames the session shows it on, and the movie is
  the shot on its own frames, with either renderer. Before 2026-11-11 it
  cached the shot's own frames there, which for a member with an offset
  were another stretch of the session.
- A parm that does not resolve (Entity, Department, Camera, no frame range
  or fps) shows as a warning dialog titled *Playblast* that names the parm to
  fix. Before 1.83 it showed as a Python traceback, which read as a crash.
  Headless (hython, the farm) it still raises.
- Nodes saved before 1.52.3 carried an *Entity* menu (From context / From
  settings) and a *Shot* menu. Those parms are gone: an old node opens on
  `from_context`, so one that had a shot picked under From settings needs it
  picked again with the Browse... button.
- A stage with no camera at all fails the button with *there is no camera on
  the stage: the shot has no camera published*.
- A new node's Camera is empty, which Houdini shows as the menu's first
  camera. Before 1.83 pressing Playblast then failed with *camera '' is not a
  camera on the stage* although the menu showed a camera picked; empty now
  means that first camera.

### Layered playblasts

With **Render Layers** on, a playblast also renders the shot once per render
layer (the shot's [channels](#render-layers-channels)) and writes each as a
movie of that layer's prims alone on a transparent background:
`render:/playblast/<shot>/<department>/<layer>/<sequence>_<shot>_<department>_<layer>_v####.mov`,
the same version number as the whole picture's MP4. They are QuickTime
Animation movies, lossless with alpha (H.264 has no alpha). The
[sequence player](../asset-browser/play-shots.md) shows such a version as a
*layers* take: the layers stacked in channel order, each one a row on the
timeline that can be hidden.

Which prims a layer holds comes from the shot's `channel_prims` config
property, a channel → prim pattern map set in the Entity Editor's
[Layers column](../asset-browser/entity-editor.md#render-layers) on the
shot or, for every shot under it, on its sequence (or on the project in the
Config Editor). It merges channel by channel down the tree: a shot can replace one
channel's pattern, or drop it with `""`, and keep the rest:

```json
"channel_prims": {"characters": "/CHAR/*", "fx": "/FX/*"}
```

- A channel with a pattern is a layer of the prims it matches (on the cached
  stage, at the first frame).
- `default` without a pattern is everything the other layers leave, so
  naming only `characters` splits the shot into background and characters.
- Any other channel without a pattern makes no layer. A layer with nothing
  to draw makes no movie: a pattern matching nothing (or only a camera or
  empty groups), or a `default` whose every prim the other layers claim.
- Render Layers on with no patterns at all refuses, naming the property.

The layers are not held out of one another: the set's layer is the whole set,
also behind the characters, and the player stacks the layers "over" one
another. The camera always stays visible. Hiding works by making the other
prims invisible, so an instance is kept or hidden whole. Houdini GL cannot
render a transparent background, so Render Layers needs the Viewport
renderer. The farm playblast does not make layers.

## `th::playblast` (SOP)

The same publish path from a SOP context, rendered with TumbleView (our
viewport renderer, hdThPlayblast, through husk) like the LOP's default.
Tab menu `_TumblePipe/pipeline`. It carries the shared Browse... button (shots
only, *Select Shot*) and writes the same versioned MP4 and daily as the LOP.
Source: [`otls/sop_th.playblast.2.0`](../../otls/sop_th.playblast.2.0/th_8_8Sop_1playblast_8_82.0/DialogScript),
[`sops/playblast.py`](../../python/tumblepipe/pipe/houdini/sops/playblast.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The shot |
| Department | `from_context` | Menu of the shot's renderable departments — **pick one**: the node does not resolve `from_context` here, and an unlisted value makes the buttons fail |
| Camera | *(empty)* | Cameras in the stage the node reads, found as on the LOP. Empty is the first one, as on the LOP |
| Playblast, View Latest, Browse All | | As the LOP |
| Live, Stop Live | | Render in the background to this machine's live folder for the player, no version: see [`th::animate` → Live Playblast](import-and-export.md#live-playblast) |

The frame range is always the shot's configured range plus roll; there is no
override. The camera and the set are read from a LOP node named `IN_stage`
that the HDA addresses relative to itself, so the node depends on where it is
placed. In a Multi with a shared timeline, the shot sits later on the
session's timeline than on its own frames (its time offset): the node
caches the frames the session shows it on and renders, and names, the
shot's own.

What it renders: its input, written to USD at the prims its `path` attribute
names (a character's `/<category>/<character>/...`, where the animation
publishes; geometry with no `path` goes under `/live`), over the shot stage
with everything but `/SET`, the camera and `/Render` deactivated
(`stage/keep`). So a character's last published animation cannot show
through the one being playblasted. The stage is pruned rather than grafted
from: graftbranches flattens its source, which strips everything above the
shot import's Layer Break, the set and the camera included. The node writes that to a `cache.usd` over the shot's range
(`stage/cache`) and renders it with husk, as the LOP does: lit shading, the
shot's render resolution (1280x720 when it has none), a progress bar that
stops husk when cancelled. It used to render through an OpenGL ROP, which
drew unlit frames on every run after the first in a session (2026-11-11).

Its input can be geometry or an APEX scene. A scene (a Scene Animate's
output, as in a `th::animate` dive, which has no Scene Invoke since 1.92) is
invoked inside the node first, outputting each character's `Base` shapes, so
the playblast shows the posed characters and not the rigs.

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

The recipes were saved in Houdini 20.5; re-save any that misbehave on a
newer major. They are distinct from the asset browser's project recipes
(saved node clusters under `<project>/recipes/`, see
[Recipes](../asset-browser/recipes.md)).
