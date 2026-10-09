# Compositing

How rendered AOVs become a comp, and how a comp becomes a reviewable MP4.

## Where renders land

Farm renders write versioned frame stacks under

```
render:/render/<shot>/<render department>/<variant>/v####/<aov>/
```

(`<variant>` is the **channel** segment. That slot holds the channel
*value*, not the literal word, so there is nothing in the path to rename; see
`docs/composition.md` for the spellings that are moving and how.)

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
and every [`th::mattes`](nodes/lighting-and-rendering.md#thmattes-lop) row and
[`th::puzzlemattes`](nodes/lighting-and-rendering.md#thpuzzlemattes-lop)
matte has a **Compression** menu. They default to lossless **ZIP** for data
and lossy **DWAB** for colour. Colour is `beauty`, its `beauty_<tag>` light
groups and their variance, `albedo` and the LPE splits (`diffuse`, `specular`,
`volume`, `emission`); everything else is data — the `objid_*` / `holdout_*`
mattes, `depth`, `normal`, `alpha`, `position`, `uv`, `samples`, the other
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

An AOV no node sets falls back to the project's RenderProduct, which asks for
DWAB. Up to 1.64.0 `th::render_vars` set every AOV to DWAB and
`th::puzzlemattes` set none, so mattes and depth were lossy; 1.64.0 overrode
data passes to ZIP on the farm instead, which the next release replaced with
the menus.

## The build_comp node

`th::Cop/build_comp` is the COP (Copernicus) node that assembles a shot
comp. Drop it in a `copnet` inside a **composite** department workfile —
it resolves the shot from the workfile's `context.json` sidecar.

**Update** builds (or refreshes) the network:

- one subnet per shot channel, containing a typed `file` COP per AOV —
  LPE passes (`beauty`, `beauty_*`), masks (`objid_*`), mono passes
  (`alpha`, `holdout_*`, the `ramp_*` distance ramps), and utility passes
  (`depth`, `normal`, `albedo`, …),
- a mask's outputs follow its channel count, read from the header of the
  AOV's first rendered frame: a 3-channel `th::puzzlemattes` matte is split
  into R, G and B outputs, a 1-channel `th::mattes` matte is one output named
  after the AOV. A header that can't be read is treated as 3-channel,
- each import pinned to the **latest complete version** of that channel's
  AOV, searching render departments up to the node's selected department,
- a grade subnet per channel with the LPE passes re-summed to a graded
  beauty,
- channels over-merged back-to-front in shot channel order.

The imports resolve against the shot's `variants` property — the frozen
storage key for its channels (every shot has at least `default`). Re-pressing **Update** re-resolves to newer
versions; `build_comp` is deliberately excluded from the Asset Browser's
import refresh on workfile open, so a comp never silently retargets —
the artist decides when to take new renders.

The **source** switch flips the whole network between farm renders and
locally generated proxy frames; **Preview** renders the current frame in
place.

The first **Update** refuses to build when the renders cannot make a comp:
no channel has a complete render, or a rendered channel is missing its
`beauty` or `alpha` AOV. It says which, and names the folder it searched.
Nothing is built and the node is not marked built, so the next **Update**
after the renders land builds normally.

## Rendering without a farm

Submit Render Jobs, the composite chain, farm playblasts and farm publishes
all submit to a Thinkbox **Deadline** farm. A machine without the Deadline
Client (whose installer sets `DEADLINE_PATH`) is told so when it presses
Submit; see `docs/deadline.md` for setting one up. Without a farm:

- **Publish** — choose *Local* in the publish process dialog.
- **Playblast** — the `th::playblast` node renders and publishes locally.
- **Renders for comp** — render with Karma yourself and write the frames
  where `build_comp` looks:

  ```
  render:/render/shots/<seq>/<shot>/render/<channel>/v0001/
      context.json
      beauty/<seq>_<shot>_<channel>_beauty_v0001.1001.exr
      alpha/<seq>_<shot>_<channel>_alpha_v0001.1001.exr
      <aov>/<seq>_<shot>_<channel>_<aov>_v0001.1001.exr
  ```

  `<channel>` is a shot channel (`default` unless the shot has more).
  `context.json` holds the rendered range, e.g.
  `{"first_frame": 1001, "last_frame": 1100, "step_size": 1}`. Every frame
  in that range must exist for every AOV folder or the version is ignored.
  Write one EXR per AOV per frame, with its layer named after the AOV (the
  name Karma gives the AOV's subimage). `beauty` and `alpha` are required;
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

**Submit** on the node hands the saved workfile to the composite job
family, which chains on Deadline:

1. *stage* — package the workfile,
2. *partial/full composite* — render the node's COP graph per channel on
   the farm, writing versioned frames under
   `render:/render/<shot>/composite/<variant>/v####/`,
3. per-channel *MP4* conversion, plus *edit* and *slapcomp* aggregation,
4. *slapcomp MP4* and a Discord *notify* with the result.

MP4s are written both as a versioned playblast and as the shot's rolling
*daily*. Frame range, step and batch size come from the shot config by
default and can be overridden on the node.

Independent of comp, every full **render** job also auto-chains a
*slapcomp* — a headless oiiotool over-composite of the latest complete
beauty/alpha across departments — followed by its own MP4 and Discord
notify. That quick-comp is what makes fresh renders reviewable before
any composite workfile exists.

## Playblast

A **playblast** is a fast GL preview of a shot. There are two ways to make
one, and they write to the same place — the versioned
`render:/playblast/<shot>/<dept>/v####.mp4` and the shot's rolling daily —
so their versions interleave:

- **In-session** — the `th::playblast` LOP/SOP node renders through
  Houdini's own GL (the viewport flipbook / OpenGL ROP) right in the
  artist's session. It is *viewport-accurate*: what you see is what you
  get. Use it when the look has to match the viewport.
- **On the farm** — tick a shot's **Playblast** cell in the
  [Farm Submit dialog](asset-browser/submit-jobs.md) (shots only; publish cells on
  the same row run first, and the playblast then shows what they publish)
  and each ticked shot gets one job:
  a single task renders the shot's staged `default` stage with husk and
  **Karma XPU** in a preview mode — through a `RenderSettings` prim the
  submitter authors on the collapsed stage at
  `/Render/tumblepipe_playblast`, aimed at the same render camera the
  project's own settings name (one raw colour buffer instead of the Karma
  LPE AOVs those settings order, and husk finds a settings prim outside
  `/Render` only when the root layer names it) — then encodes an MP4 and
  writes the
  versioned playblast
  **and** the daily, exactly like the render/composite MP4s above. The
  frame range (rolls included) and fps come from the shot config per
  shot. So do resolution (720p default) and pool/priority, unless you
  change them in the dialog: those fields are tri-state, and an untouched
  one lets every checked shot use its own configured value rather than
  imposing one shot's on the batch. A field you do set renders upright
  instead of italic and applies to everything checked; `↺` hands it back.
  The **department** comes from the dialog too, and cuts the composed
  stack the same way a render's does — up to and including the one picked,
  see [Composition → The department cut](composition.md#the-department-cut).

The two do not look the same. The farm frames are a clay preview, not a
viewport capture and not a final render: scene materials and lights are
off, a headlight on the camera shades the stage with ambient occlusion,
and the stage's display colours still show. That difference is exactly
why the in-session node stays — playblast locally when the look must
match the viewport, submit to the farm to offload a batch. The farm job's
Deadline group is `karma`, the same GPU workers final renders use, see
[Farm worker prerequisites](deadline.md#farm-worker-prerequisites).

Farm playblasts used to render with husk's Hydra **Storm** (GL) delegate,
which is closer to the viewport. It never survived a real shot on the farm:
HideAndReek/010 on maria-2060 and 030 on judas both sat at 0% on the first
frame for over ten minutes, then husk crashed (`0xE06D7363`). XPU rendered
030's first frame in 72 s, most of it loading the stage and building the
scene on the GPU. Storm was removed rather than kept as an option.
