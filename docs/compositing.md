# Compositing

How rendered AOVs become a comp, and how a comp becomes a reviewable MP4.

## Where renders land

Farm renders write versioned frame stacks under

```
render:/render/<shot>/<render department>/<channel>/v####/<aov>/
```

(`<channel>` is the **channel** segment; see `docs/composition.md` for what a
channel is and why it is not a USD variant.)

with a `context.json` sidecar recording the frame range. A version is
*complete* when every frame in that range exists on disk; comp tooling
only ever selects complete versions, so a comp never picks up a
half-finished render. The `denoise` department sits above `render`, so
denoised output wins over raw when both exist.

## Colour space

Renders are ACEScg throughout — but two different attributes advertise that,
and different tools trust different ones, so every EXR the pipeline publishes
stamps **both** (`exr.ACESCG_ATTRIB_ARGS`):

- `oiio:ColorSpace` — what OIIO-based tools read. Karma writes it (`lin_ap1`),
  but oiiotool channel ops (`--ch`, `--chappend`) build a fresh image and
  reset it to `Raw`, so the denoise/slapcomp chains would otherwise lose it.
- `chromaticities` — the EXR-spec primaries, and the *only* thing RV and Nuke
  read. Karma never writes it, and **its absence means Rec.709 by spec** — so
  an unstamped ACEScg frame is displayed as Rec709 (correct pixels, wrong
  look). We stamp the AP1 primaries + D60 white point.

The stamp is applied at every publish boundary — `split_subimages` (raw
render), `exr.encode` (denoise; the mattes and ramps it passes through are
byte copies that keep the split's stamp), and slapcomp's composite writes — never in
the render/COP nodes themselves, so frames Karma or COPs write directly (an
interactive ROP, a `build_comp` preview) still lack `chromaticities` and read
as Rec709 in RV. Set the input colour space manually there. When adding a new
oiiotool publish step, splice `ACESCG_ATTRIB_ARGS` into its final write.

## Compression

Each AOV's EXR compression is set **on the node that adds it**, in the render
department: every AOV of [`th::render_vars`](nodes/lighting-and-rendering.md#thrender_vars-lop)
(and of `th::render_settings`' **AOVs** section, the same menus) and every [`th::mattes`](nodes/lighting-and-rendering.md#thmattes-lop) row
has a **Compression** menu. They default to lossless **ZIP** for data
and lossy **DWAB** for colour. Colour is `beauty`, its `beauty_<tag>` light
groups and their variance, `albedo` and the LPE splits (`diffuse`, `specular`,
`volume`, `emission`, their direct/indirect splits, `transmission`, `sss`,
`coat`, `unshadowed`, `shadow`); everything else is data — the `objid_*` / `holdout_*`
mattes, `depth`, `normal`, `position`, `uv`, `samples`, the other
variances. DWA's quantisation is invisible in a picture but shows up in a
comp that reads the numbers: soft matte edges, banded depth. The rule the
defaults follow lives in [`pipe/aovs.py`](../python/tumblepipe/pipe/aovs.py).

The farm writes what the render department published and changes nothing on
the way: husk writes each AOV with its node's setting, the split into per-AOV
files keeps it, and the denoise publish re-encodes each denoised AOV with
the compression its render input had. Mattes (`objid_*`, `holdout_*`) and
distance ramps (`ramp_*`) are not denoised at all: OIDN would soften their
edges, so the denoise publishes them as byte copies of the render input —
same pixels, same compression. To change a pass's compression, change the
menu and re-publish the render department.

## Alpha lives in the beauty

The `beauty` is rendered **RGBA** (`color4f`), and its A is the alpha every
comp uses. There is no separate `alpha` AOV any more. The one there was came
from Karma's `ray:hit`, a *utility* AOV, and Karma's holdouts do not touch
utility AOVs (Render Geometry Settings › Holdout Mode: "Holdout Mode does not
affect the utility AOVs such as ray:hitP"). So in a render layer with the set
held out (Matte), that alpha still counted the set as solid — HideAndReek
070's `characters` channel showed the tree canopy through it. The beauty's
own alpha honours holdouts: 0 over a held-out object, 1 over what is seen.

- **Denoise** runs OIDN over the beauty's RGB only. Its alpha is not
  denoised: it goes around the denoise straight from the render input and
  is published back onto the denoised RGB, so the published beauty is RGBA
  with the render's own coverage, in the beauty's compression.
- **`build_comp`** takes each channel's alpha from the beauty, and only
  from the beauty: it refuses to build or update from a beauty with no `A`
  channel (an RGB beauty, rendered before the change — re-render it), and
  never imports an `alpha` AOV.
- **slapcomp** does the same: each channel's alpha is its beauty's `A`,
  and a beauty without one fails the slapcomp (re-render it). The composite
  job's slapcomp resolves only the beauty.
- The decision is made from the beauty file itself -- whether it has an
  `A` channel -- never from which AOV folders exist. slapcomp takes that
  channel by its index in oiiotool's order (`R,G,B,A`), not the EXR
  header's, which lists channels alphabetically (`A,B,G,R`).
- **The edit gets the alpha too.** *Copy to edit* copies the RGBA beauty,
  and DaVinci Resolve can honour its alpha: the sky (alpha 0) and
  motion-blur/DOF edges turn transparent and show whatever track is below.
  On a timeline where a render should read as a solid picture, set the
  clip's **Alpha Mode** to *None*; use *Premultiplied* only where layers are
  meant to stack (a characters channel over a background channel).

The light groups (`beauty_<tag>`, from `th::lpe_tags`) stay RGB: they are
summed back into a beauty, and an alpha in each would be added once per
group. A comp that re-sums them takes A from the beauty, as above.

An AOV no node sets falls back to the project's RenderProduct, which asks for
DWAB, so every node that adds a data AOV sets its compression.

## The build_comp node

`th::Cop/build_comp` is the COP (Copernicus) node that assembles a shot
comp. Drop it in a `copnet` inside a **composite** department workfile —
it resolves the shot from the workfile's `context.json` sidecar.

**Update** builds (or refreshes) the network:

- one subnet per shot channel, containing a typed `file` COP per AOV —
  LPE passes (`beauty`, `beauty_*`), masks (`objid_*`), mono passes
  (`holdout_*`, the `ramp_*` distance ramps), and utility passes
  (`depth`, `normal`, `albedo`, …),
- an `alpha` subnet per channel feeding the grade subnet's `alpha` input:
  the RGBA beauty's A (read RGBA and split). Update re-points it at the
  channel's newest beauty, and refuses a newest beauty without an `A`,
  leaving the comp as it was. A comp built before the alpha came from the
  beauty (no `alpha` subnet in a channel) is refused too; build it again,
- one mask output per `objid_*` matte, named after the AOV. A matte must be
  one channel (`th::mattes`), read from the header of the AOV's first
  rendered frame: a 3-channel puzzle matte (from the retired
  `th::puzzlemattes`) or an unreadable header refuses the build or update,
  naming the matte — re-render it with `th::mattes`,
- each import pinned to the **latest complete version** of that channel's
  AOV, searching render departments up to the node's selected department,
- each `file` COP asks for the plane its file actually has: a raw render
  split names its channels `<aov>.R…`, found by the AOV's name, while the
  denoise publishes bare `R,G,B(,A)`, which the File COP only finds as `C`
  (asked for `beauty`, it loads nothing). The header of the first frame
  decides, on Build and again on every Update, so a comp built on a raw
  render follows its channels to the denoised publish,
- a grade subnet per channel with the LPE passes re-summed to a graded
  beauty,
- channels over-merged back-to-front in shot channel order.

The imports resolve against the shot's `channels` property
(every shot has at least `default`). Re-pressing **Update** re-resolves to newer
versions; `build_comp` is deliberately excluded from the Asset Browser's
import refresh on workfile open, so a comp never silently retargets —
the artist decides when to take new renders.

The **source** switch flips the whole network between farm renders and
locally generated proxy frames; **Preview** renders the current frame in
place.

### Proxies

With the source on **Proxy**, **Update** first makes the proxies, then
points the comp at them. It resolves the renders exactly as the Render
source does (newest complete version per AOV, departments up to the
node's Input), then cooks the TOP network inside the node
(`proxy/tops`, driven by `cops/comp_proxy.py`): one local task per
missing frame of every AOV the comp reads, each a File COP into a ROP
Image at the **Proxy Resolution**'s pixel scale (Half writes half the
width and height, Quarter a quarter). The beauty is written RGBA (the
comp's alpha is its A), `objid_*` mattes, holdouts and ramps one channel,
everything else RGB. When the cook finishes, every import is pointed at
the proxies and its resample scales them back up, so the comp keeps its
full-size canvas.

A proxy mirrors its render on disk, under the `proxy` storage:

```
<proxy>/render/<resolution>/<shot>/<department>/<channel>/v####/<aov>/<same file names>
```

with a copy of the render's `context.json` in the `v####` folder. Frames
already there are not cooked again, so a second Update is quick and only
a new render version makes new proxies. Proxies are a cache: delete the
folder to free space or to force them to be remade. The tasks run
locally, a quarter of the CPU count at a time. Farm submission always
comps the full renders.

The first **Update** refuses to build when the renders cannot make a comp:
no channel has a complete render, or a rendered channel is missing its
`beauty` AOV. It says which, and names the folder it searched.
Nothing is built and the node is not marked built, so the next **Update**
after the renders land builds normally.

## Rendering without a farm

Submit Render Jobs, the composite chain, farm playblasts and farm publishes
all submit to a Thinkbox **Deadline** farm. A machine without the Deadline
Client (whose installer sets `DEADLINE_PATH`) is told so when it presses
Submit; see `docs/deadline.md` for setting one up. Choosing **This machine**
(Farm Submit's **Run on**, the publish dialog, or the choice `build_comp`'s
Submit asks for) runs the same jobs here instead, in the background (see
*Running farm jobs on this machine* in `docs/deadline.md`). Without either:

- **Publish** — choose *In this session* in the publish process dialog.
- **Playblast** — the `th::playblast` node renders and publishes locally.
- **Renders for comp** — render with Karma yourself and write the frames
  where `build_comp` looks:

  ```
  render:/render/shots/<seq>/<shot>/render/<channel>/v0001/
      context.json
      beauty/<seq>_<shot>_<channel>_beauty_v0001.1001.exr
      <aov>/<seq>_<shot>_<channel>_<aov>_v0001.1001.exr
  ```

  `<channel>` is a shot channel (`default` unless the shot has more).
  `context.json` holds the rendered range, e.g.
  `{"first_frame": 1001, "last_frame": 1100, "step_size": 1}`. Every frame
  in that range must exist for every AOV folder or the version is ignored.
  Write one EXR per AOV per frame, with its layer named after the AOV (the
  name Karma gives the AOV's subimage). `beauty` is required, and must
  be RGBA — its A is the comp's alpha (an RGB beauty is refused);
  `beauty_*` light groups, `objid_*`, `holdout_*`, `ramp_*`, `albedo`, `normal`,
  `depth`, `uv` and `position` are picked up when present. Use the next
  free `v####` for each new render.

## The shot camera in comp

`th::Cop/import_lop_camera` brings the shot's render camera into COPs, for
the comp nodes that need real camera data (depth, projections). Like every
entity-aware `th::` HDA its Entity defaults to `from_context`, so dropped in
a comp workfile it resolves that shot's camera with nothing to configure.

Internally it composes the shot's staged stage with an embedded
`th::import_shot`, lifts `/cameras/render_camera` out through a
`lopimportcam`, and feeds that to a `cameraimport` COP. It loads **no
payloads** — a camera is a light prim, and comp has no use for the shot's
geometry, so composing it would be a large bill for nothing.

## Farm submission and MP4s

**Submit** on the node asks where to run (Render farm or This machine) and
hands the saved workfile to the composite job family, which chains on
Deadline, or in the same order on this machine:

1. *stage* — package the workfile,
2. *partial/full composite* — render the node's COP graph per channel on
   the farm, writing versioned frames under
   `render:/render/<shot>/composite/<channel>/v####/`,
3. per-channel *MP4* conversion, plus *edit* and *slapcomp* aggregation,
4. *slapcomp MP4* and a Discord *notify* with the result.

MP4s are written both as a versioned playblast and as the shot's rolling
*daily*. Frame range, step and batch size come from the shot config by
default and can be overridden on the node.

Independent of comp, every full **render** job also auto-chains a
*slapcomp* — a headless oiiotool over-composite of the latest complete
RGBA beauty across departments — followed by its own MP4 and Discord
notify. That quick-comp is what makes fresh renders reviewable before
any composite workfile exists. When a render has several channels, the
slapcomp stacks them bottom-up in the shot's channel order: the first
(`default`, which every channel list starts with) at the bottom, the last on
top — so list `background` before `characters` before `feathers`.

## Playblast

A **playblast** is a fast viewport-style preview of a shot. There are two ways to make
one, and they write to the same place — the versioned
`render:/playblast/<shot>/<dept>/<sequence>_<shot>_<dept>_v####.mp4` and the
shot's rolling daily — so their versions interleave. The shot in the file
name lets an editor that labels clips by file name (Resolve) tell the shots
apart; playblasts made up to 1.87 are bare `v####.mp4` and are still read,
each keeping the name it was written with:

- **In-session** — the `th::playblast` node renders right in the artist's
  session. The LOP's **Renderer** picks how: **Viewport (hdThPlayblast)**,
  the default, is the farm playblast's own renderer (below), run with husk
  on the node's cache, so an in-session playblast looks like a farm one;
  its **Shading** is the farm dialog's (lit, clay, albedo, occlusion), and a
  progress bar shows husk's frames (cancel stops it). **Houdini GL** is
  Houdini's own flipbook (OpenGL ROP), *viewport-accurate*: what you see is
  what you get, hair, curves and volumes included, with its own Shading
  Mode and Lighting. **Motion Blur**, **Depth of Field** and **Blur
  Samples** (off, off, 16) work with both, from the camera's shutter and
  lens: the viewport renderer's blurs, or the flipbook's own. The SOP
  `th::playblast` always renders through Houdini GL. With the viewport
  renderer, **Render Layers** also writes a movie per render layer (the
  shot's channels, prims picked by its `channel_prims`), which the sequence
  player stacks: see
  [Layered playblasts](nodes/lighting-and-rendering.md#layered-playblasts).
  The farm playblast makes no layers.
- **On the farm** — tick a shot's **Playblast** cell in the
  [Farm Submit dialog](asset-browser/submit-jobs.md) (shots only; publish cells on
  the same row run first, and the playblast then shows what they publish)
  and each ticked shot gets one job:
  a single task renders the shot's staged `default` stage with husk and
  the dialog's **Renderer**: `viewport` (the default) or **Karma XPU** in
  a preview mode — through a `RenderSettings` prim the
  submitter authors on the collapsed stage at
  `/Render/tumblepipe_playblast`, aimed at the same render camera the
  project's own settings name (one raw colour buffer instead of the Karma
  LPE AOVs those settings order, and husk finds a settings prim outside
  `/Render` only when the root layer names it) — then encodes an MP4 and
  writes the
  versioned playblast
  **and** the daily, exactly like the render/composite MP4s above. The
  frame range (rolls included) and fps come from the shot config per
  shot. So do resolution (the shot's render resolution, 1280 × 720 when it
  has none), the look (Renderer, Light, Samples, Materials) and pool/priority, unless you
  change them in the dialog: those fields are tri-state, and an untouched
  one lets every checked shot use its own configured value rather than
  imposing one shot's on the batch. A field you do set renders upright
  instead of italic and applies to everything checked; `↺` hands it back.
  The **department** comes from the dialog too, and cuts the composed
  stack the same way a render's does — up to and including the one picked,
  see [Composition → The department cut](composition.md#the-department-cut).

The `viewport` renderer is TumblePipe's own Hydra render delegate,
`hdThPlayblast`, shipped with the package. It draws what the viewport draws
— proxy geometry where an asset has one, USD shapes (cubes, spheres,
cylinders, cones, capsules) as meshes, the stage's display colours and
textures (including the colours Karma materials take from them) — on the
worker's CPU. A mesh exported without normals gets them computed as Houdini
does: smooth, with hard edges kept only where faces meet at more than 60
degrees (a box stays a box). It needs no GPU and husk takes no licence for
it, and HideAndReek/010's 6.8 million triangles draw in about 0.34 s a frame
at 1280 × 720 with eight samples a pixel, the post pass below included. It
ignores Light, Samples and Materials.
It draws meshes only, so hair and other curves are left out, and husk does
not run the stage's Houdini procedurals (hair grooms) for it: they would
make nothing it draws, and running them before every frame cost
HideAndReek/010 about 3 s a frame.

It draws the way a game engine does: the geometry goes into a render
buffer (colour, normal and depth per sample), and a post pass makes the
picture from it, each pixel the average of its samples. A pixel has eight,
placed so that no two share a row or a column (the positions graphics
cards use for 8× multisampling): an edge close to upright or level, a shelf
leg or a door frame, passes each sample at a different place, so as it
crosses a pixel the pixel takes nine steps from background to edge where a
2 × 2 grid gives three, and the edge no longer stair-steps or crawls. That
pass adds screen-space ambient occlusion in three
tiers: small (creases and contacts, 4 cm), medium (folds and limbs, 25 cm)
and large (whole objects against walls and floors, 1.2 m), radii in scene
units, assuming metres. The occlusion is worked out for every sample, not
once per pixel, so it is anti-aliased with the rest of the picture: a chair
leg's contact shadow does not spill onto the wall behind it, and creases
and frame edges do not crawl as the camera moves. A tier fades in as it
grows past a pixel on screen. Then it shades as the dialog's **Shading** says:

| Shading | Look |
|---|---|
| `lit` (default) | the scene's colours under a key, fill and rim light and a sky/ground ambient, all fixed to the camera so no shot comes out dark |
| `clay` | the same lights on plain grey |
| `albedo` | the scene's colours, unlit |
| `occlusion` | white, darkened only by the ambient occlusion |

Every option has the occlusion. There are no shadows, and the scene's own
lights are not used: it is close to the in-session node's look, not
identical. Outside the farm (husk by hand), the stage's RenderSettings can
set `th:shading` (`lit`, `clay`, `albedo`, `occlusion`), `th:ao` (bool) and
`th:samples` (samples a pixel: 1, 2, 4, 8 or 16, 1 for no anti-aliasing);
unset, the delegate reads `TH_PLAYBLAST_SHADING`, `TH_PLAYBLAST_AO`
(`0`/`off` turns the occlusion off) and `TH_PLAYBLAST_SAMPLES`; the farm
worker passes the dialog's shading that way and leaves the samples at the
default, eight. On HideAndReek/010 one sample a pixel takes about 0.14 s a frame,
four 0.27 s, eight 0.34 s and sixteen 0.56 s.

A worker can make the same picture on its GPU instead (Vulkan or DX12, no
display needed): set `TH_PLAYBLAST_GPU=1` in that worker's environment.
Windows builds only for now. An Intel GPU draws through DX12: the Intel
Vulkan driver on the CI worker (UHD Graphics 630) left a trace of the
frame before in each motion-blurred frame of a sequence. Every other GPU
draws through Vulkan where it has it. The log's `hdThPlayblast: GPU` line
names the card and the backend it got. The drawing and the post pass (occlusion,
shading, the average of each pixel's samples) both run on the card, and
only the finished image and its depth come back. The geometry stays on the
card from frame to frame, so a frame sends only the meshes Hydra rebuilt:
on HideAndReek/010, its 40 animated meshes, 81 MB of 864 MB. Measured side
by side on an RTX 3070 (1280 × 720, eight samples), 010 renders in
0.08–0.12 s a frame on the GPU against 0.28 s on the CPU, and husk's whole
frame takes 0.15–0.7 s against 0.35–0.9 s. The first frame sends
everything, about a quarter of a second more. The card keeps at most 3 GB
of geometry; past that, the rest is sent every frame as before. The CPU
path stays the reference: from the same render buffer the GPU's post pass
makes the CPU's picture to within rounding, and the two rasterisers differ
only on a few edge pixels (0.04% of 010's). It stays opt-in until it has run
on the farm.
A worker whose GPU fails falls back to the CPU and says so in the task
log. `TH_PLAYBLAST_COMPARE=1` draws every mesh both ways and logs the ones
that differ, a check for a new GPU or driver. `TH_PLAYBLAST_TIMING=1` logs
where each frame's time goes: the drawing and post pass step by step (on
the GPU: the meshes and megabytes sent and kept on the card, the drawing,
the post pass and the read-back), then how long Hydra spent syncing meshes,
how many it rebuilt, how many it updated by their points alone (a mesh
whose points are all that changed keeps its triangles) and the time since
the last frame. On HideAndReek/010
that last figure is 0.4–0.9 s against the CPU render's 0.34 s; the rest is
husk updating the stage.

### Motion blur and depth of field

The viewport renderer can blur, both off by default; the dialog's **Blur**
row turns them on (Motion blur, Depth of field, Blur samples, 16 by
default). Off, the picture is the one above, to the byte. **Motion blur**
spreads what moves over the render camera's shutter (its Shutter Open and
Close, e.g. −0.25..0.25 frames): deforming meshes by their points (or their
velocities when they have no points over time), moving transforms and
instancers by their transforms, and the camera's own motion. **Depth of
field** defocuses by the camera's lens, its F-Stop and Focus Distance, as a
thin lens does: an F-Stop of 0 is a pinhole and stays sharp. Focusing at
infinity does *not* turn it off: everything nearer than infinity is then out
of focus, as through a real lens.

Both come from drawing the frame several times and averaging the pictures
(an accumulation buffer): each of the blur samples is drawn at its own
moment in the shutter and through its own point on the lens, in groups of as
many as a pixel has samples, and each group goes through the post pass
before the groups are averaged. The samples' moments and lens points are
fixed, not random, so a still frame does not flicker and the CPU and the GPU
draw the same picture. What does not move under the shutter is placed
once for all of a frame's blur samples rather than once per sample (and,
when the lens is a pinhole and the camera still, projected and sorted into
screen bands once too); on the GPU the groups are averaged on the card and
only the average comes back. The sample count is rounded up to a power of two, at
least the samples a pixel and at most 64. A frame where nothing moves under
the shutter and the lens is a pinhole is drawn once, as with the blurs off.

Outside the farm, the stage's RenderSettings can set `th:motionBlur`,
`th:depthOfField` (bools), `th:blurSamples` and `th:fps` (for velocities, in
frames a second, default 24); unset, the delegate reads
`TH_PLAYBLAST_MOTION_BLUR`, `TH_PLAYBLAST_DOF`, `TH_PLAYBLAST_BLUR_SAMPLES` and
`TH_PLAYBLAST_FPS`, which is how the farm worker passes the dialog's look and
the shot's fps. Points move in a straight line from shutter open to close,
and transforms through up to four keys; an instancer's per-instance
positions and the focal length are taken at the frame, and a moved surface
keeps the normals it has at shutter open.

Measured on HideAndReek/010 (1280 × 720, eight samples a pixel, 16 blur
samples, RTX 3070), on frames where something moves:

| | blurs off | motion blur | depth of field, or both |
|---|---|---|---|
| CPU render | 0.27–0.28 s: draw 0.13 s (transform 50 ms, bin 15 ms, fill 50 ms), post pass 0.12 s | 0.60 s: two groups, drawn in 0.29 s together (transform 76 ms, bin 71 ms, fill 0.14 s), each with its own 0.12 s post pass | 0.89–0.91 s: as motion blur, but every sample sees through its own point on the lens (transform 0.18 s, bin 0.23 s) |
| GPU render | 0.08 s: upload 20 ms, draw 33 ms, post pass 3 ms, read-back 6 ms | 0.14 s: upload 30–35 ms (102 MB with the closing points), draw 63 ms, post pass 7–8 ms, read-back 10 ms | the same |

husk's whole frame then takes 0.92 s on the CPU with motion blur (1.93 s
before the shared placing), 1.2 s with both blurs, and 0.46 s on the GPU.
At 64 blur samples the CPU takes 2.4 s a frame with motion blur (6.8 s
before) and 3.6 s with both, the GPU 0.69 s (0.75 s before, when every
group was read back). The occlusion in each group's post pass is now the
largest part of a blurred CPU frame (0.84 s of 2.4 s at 64 samples).

The `karma` renderer does not look like the viewport. Its frames are a clay
preview, not a viewport capture and not a final render: by default scene
materials are off and the scene's lights are replaced by one white dome
light, and the stage's display colours still show. The dialog's **Light** setting can
instead put a headlight with ambient occlusion on the camera (the look
before 1.85) or keep the scene's own lights, and **Materials** set to *Scene materials* turns
the materials back on. The dome is authored into the collapsed stage
(`/TumblePipePlayblastLights/dome`, every scene light deactivated beside
it); husk's own `--headlight dome` cannot do it, since Karma's headlight
ignores the mode. Lights inside an instance cannot be deactivated from
the collapse and stay lit beside the dome. A Karma playblast takes a Karma
licence. That difference is exactly why the in-session node stays — playblast locally when the look must
match the viewport, submit to the farm to offload a batch. The farm job's
Deadline group is `karma`, the same GPU workers final renders use, see
[Farm worker prerequisites](deadline.md#farm-worker-prerequisites).

Farm playblasts used to render with husk's Hydra **Storm** (GL) delegate,
which is closer to the viewport. It never survived a real shot on the farm:
HideAndReek/010 on maria-2060 and 030 on judas both sat at 0% on the first
frame for over ten minutes, then husk crashed (`0xE06D7363`). XPU rendered
030's first frame in 72 s, most of it loading the stage and building the
scene on the GPU. Storm was removed rather than kept as an option.
