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
