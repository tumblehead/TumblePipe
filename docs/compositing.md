# Compositing

How rendered AOVs become a comp, and how a comp becomes a reviewable MP4.

## Where renders land

Farm renders write versioned frame stacks under

```
render:/render/<shot>/<render department>/<variant>/v####/<aov>/
```

with a `context.json` sidecar recording the frame range. A version is
*complete* when every frame in that range exists on disk; comp tooling
only ever selects complete versions, so a comp never picks up a
half-finished render. The `denoise` department sits above `render`, so
denoised output wins over raw when both exist.

## The build_comp node

`th::Cop/build_comp` is the COP (Copernicus) node that assembles a shot
comp. Drop it in a `copnet` inside a **composite** department workfile —
it resolves the shot from the workfile's `context.json` sidecar.

**Update** builds (or refreshes) the network:

- one subnet per shot variant, containing a typed `file` COP per AOV —
  LPE passes (`beauty`, `beauty_*`), masks (`objid_*`), mono passes
  (`alpha`, `holdout_*`), and utility passes (`depth`, `normal`,
  `albedo`, …),
- each import pinned to the **latest complete version** of that variant's
  AOV, searching render departments up to the node's selected department,
- a grade subnet per variant with the LPE passes re-summed to a graded
  beauty,
- variants over-merged back-to-front in shot variant order.

The imports resolve against the shot's `variants` property (every shot
has at least `default`). Re-pressing **Update** re-resolves to newer
versions; `build_comp` is deliberately excluded from the Asset Browser's
import refresh on workfile open, so a comp never silently retargets —
the artist decides when to take new renders.

The **source** switch flips the whole network between farm renders and
locally generated proxy frames; **Preview** renders the current frame in
place.

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
2. *partial/full composite* — render the node's COP graph per variant on
   the farm, writing versioned frames under
   `render:/render/<shot>/composite/<variant>/v####/`,
3. per-variant *MP4* conversion, plus *edit* and *slapcomp* aggregation,
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
- **On the farm** — tick **Playblast** in the Submit Jobs dialog (shots
  only, alongside Publish and Render) and each checked shot gets one job:
  a single task renders the shot's staged `default` stage with husk's
  Hydra **Storm** (GL) delegate — no `--camera`, so husk uses the render
  camera baked into the stage's `RenderSettings`, the same one the Karma
  render reads — then encodes an MP4 and writes the versioned playblast
  **and** the daily, exactly like the render/composite MP4s above. The
  frame range (rolls included) and fps come from the shot config per
  shot; department, resolution (720p default) and pool/priority come from
  the dialog.

The two are not interchangeable look-wise: `husk` cannot load Houdini's
own GL delegate, so a farm playblast is *Storm-shaded*, not a
pixel-identical copy of the interactive viewport. That difference is
exactly why the in-session node stays — playblast locally when the look
must match the viewport, submit to the farm to offload a batch. The
farm job's Deadline group is `playblast`, kept separate from `karma` so
previews never contend with final-frame render slots; those workers must
have a GL-capable GPU context.
