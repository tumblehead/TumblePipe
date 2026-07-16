# Asset composition and staging

How exported layers become the files that downstream workfiles import.

## Department exports and staged files

Every publish writes a versioned layer under
`export/<entity>/<variant>/<department>/v####/` together with a
`context.json` sidecar. The sidecar's `parameters.assets` records every
pipeline asset present on the exported stage (scraped from the
`customData` metadata that import nodes author on asset root prims).

The *staged* file (`_staged/<variant>/v####/<Entity>_v####.usda`) is what
imports actually load. For an asset it sublayers, strongest first:

1. the asset's own department layers in pipeline order
   (e.g. lookdev over model),
2. the staged file of every asset tracked in those department layers —
   the *nested assets* of a set-style asset — weaker than the asset's
   own layers, so its placement overrides win. Tracked refs carry the
   sub-asset's variant and are pinned to its staged version at build
   time, so a pinned build stays frozen; `latest`-mode imports strip
   or ignore the pin, so floating still cascades. A tracked asset
   already reachable through another tracked asset's staged file gets
   no direct ref of its own — a second, independently pinned ref could
   pin a different version of the same prims.

"Pipeline order" is literally the department's position in the project's
department pool (see *Departments* in {doc}`configuration`): the build
sublayers the departments in *reversed* pool order, so a department further
down the pool composes **stronger**. Reordering the pool therefore restages
every entity in the project, which is why the pool editor warns before it
does.

The build composes whatever a department **exported**, not what its entity is
scoped to. An entity's department assignment governs menus, decks and task
graphs; a department with an export still composes even if the entity is no
longer scoped to it, and a department with no export is skipped whether it is
assigned or not. So scoping an entity in the browser can never change what
renders.

When department layers disagree about a tracked asset (instance count,
variant, inputs), the **most recently exported** layer that records it
wins, as one consistent snapshot. It is never a `max()` across layers:
a stale department — lookdev exported before a model rework halved the
copies — would pin the old count forever, because any workfile that
imports the staged asset re-composes the inflated count onto its stage
and scrapes it back into its own next export.

Shot staged files work the same way: shot department layers, then
shot-flow assets, then the root department (which carries the scene
reference). The newest-export-wins merge applies to shot-flow asset
counts and inputs too; scene assets carry the instance counts from
their scene context, with shot-flow entries taking precedence when an
asset appears in both.

A variant staged build only *overrides* the departments that actually
exported under that variant: a department with no export under the
build's variant falls back to its default-variant export, and the
staged file's sublayer URI names the variant the layer really resolved
from. A render variant that prunes a shot down to its characters
therefore still composes the default animation, lights, and camera —
without the fallback the variant's staged stage held only the
variant-exporting department plus root, and the farm rendered an empty
(black) scene that the live session, which composes the full node
graph rather than the staged file, never showed.

## Layer save paths and export portability

A published layer travels as a folder — from the export temp directory
into the version folder, then across machines and the farm — so every
filesystem path it composes from must stay **inside its own folder**.
Cross-entity composition goes through `entity:/` URIs, which the
resolver handles everywhere. The export refuses to publish a layer
whose sublayer/reference/payload arcs *escape* the folder (absolute
paths, or relative `../` climbs), even when the target exists at export
time — such an arc dangles, or silently reads another machine's state,
after publish.

The common way to author an escaping arc without noticing is the LOP
**Layer Save Path**: Houdini 22 creates `sopcreate` and `sopimport`
nodes with it *enabled* and pointing at `$HIP/usd/$OS.usd` (Houdini 21
shipped it off). On export, a save-path'ed layer is written next to the
workfile instead of flattening into the published layer, which then
composes empty everywhere else. The pipeline neutralizes the default at
node-creation time (`scripts/lop/*_OnCreated.py`) and the scene
templates pin it off explicitly; if an export still aborts with an
"outside the export folder" error, disable *Enable Layer Save Path* on
the named node and re-export.

Deliberate *relative sibling* save paths are fine and used by the asset
HDAs themselves (`payload.usd`, `geo.usdc`, `lookdev.usdc`) — they stay
inside the version folder and travel with it.

One deliberate exemption: **versioned caches** written by `th::cache`
publish *by reference* instead of being copied into the version folder —
caches can be far too large to duplicate per publish, live on shared
storage (the `project:`/`proxy:` `lops_cache` locations), and are
immutable per cache version. The export pins such arcs to absolute
paths (so they survive the temp→version move) and the escaping-path
guard lets them through; a cache version that has since been deleted
still aborts the export as a dangling arc. Off-site consumers (e.g. a
cloud render submit) must gather/inline these references, as they
already must for textures.

`th::cache` carries an **Entity** parm (defaulting, like every
entity-aware `th::` HDA, to `from_context`). It drives the **From Entity
(Database)** frame-range source, which caches over the entity's authored
range — start/end *plus* pre/post roll — instead of a hand-typed one, so a
shot whose range is retimed in the database does not silently keep caching
the old span. The entity deliberately does **not** decide *where* the cache
is written: the cache directory is derived from the workfile's own location,
because the export guard above resolves the allowed `lops_cache` roots from
the workfile (`lops.cache.list_cache_locations()`) and cannot see a per-node
parm. Pointing a cache node's Entity elsewhere therefore changes what it
caches, not where it lands.

The export also refuses arcs whose target does not exist at all
(*dangling* paths, e.g. a payload anchored to a machine-local scratch
file) — every consumer would import the asset empty. One exception:
bare relative arcs (no `./` or `../` prefix) are USD *search paths*,
which the resolver looks up in its search roots rather than next to the
layer. An arc the resolver locates — such as Quick Surface Material's
`houdini/usd/materials/...` library, resolved against `$HFS` — resolves
on every machine with the same Houdini install and is allowed through.

`scripts/fix_enabled_savepaths.py` (hython) sweeps a project's
workfiles for nodes that already saved the enabled state — dry-run by
default, `--apply` disables and resaves with a backup copy.

## Two ways to build multi-asset environments

- **Scenes and groups** (config-driven): a scene lists member assets in
  the project configuration; the scene's staged file sublayers each
  member's staged file, and shots pick the scene up through their root
  department. Placement lives in the scene.
- **Nested assets** (workfile-driven): import assets directly into
  another asset's workfile (e.g. dressing a `SET` with towers and
  props). The export tracks the imported assets through their prim
  metadata — including copies made with Duplicate LOPs — and the staged
  build re-references each one, so the set composes complete geometry
  downstream. Placement lives in the set's workfile.

Both compose transparently: a shot that imports the set (or a scene
containing it) resolves the nested assets through the set's staged file.

The layerbreak strips the *imported* composition from an export, but
the import node's own persistent layer — tracked-asset metadata,
artist transforms, and the re-established instance defs — is
localized into the export as a `stage/<node>/transform.usd` sidecar.
The staged `context.json` is therefore the single authority for
instance counts, and every consumption point re-establishes from it
rather than trusting composed defs: the import nodes re-tag each
tracked asset root, re-define duplicates (`{name}0..{name}N-1`
referencing the base prim, base deactivated), author the
`xformOpOrder` that applies the composed placement ops (their values
survive in the sidecar, but the order lived in the stripped Duplicate
defs), and deactivate any numbered duplicate at or beyond the tracked
count — a layer exported while an inflated count was live carries the
phantom defs in its sidecar and would otherwise resurrect them on
every import.

The context is the authority for the *count* only — `{name}` always
comes from the asset's URI, never from the entry's `instance` field.
That field is written from whichever tracked prim the export scrape
walked first, and since the prototype is deactivated the scrape only
sees live copies, so it records a copy's own name: paleindia's
30-haybale set recorded `instance: "Haybale9"`. Reading a base name
back out of it regenerated `Haybale90..Haybale929` — a second set of
30, referencing the real prototype and so carrying its geometry, with
no placement to compose, stacked at the origin.

The prototype those duplicates reference is re-established too. Only a
*directly* imported root passes through the import node's Transform LOP
(`xformdescription 'import'`), so a sub-asset root named by a staged
`context.json` composes with no transform at all unless the importing
side authors one — importing a set left a bare prototype where importing
the same asset by hand gave the full XformCommonAPI op set. Each point
authors the identity set on it
(`pipe.houdini.util.author_identity_placement_ops`, mirrored as USDA text
in `pipe.usd.IDENTITY_PLACEMENT_OPS_USDA` for the flatten), before the
duplicates, so the op set reaches them through the reference arc while
their own locally-authored placement values still win.

Authoring the prototype is guarded by the same order derivation, and the
guard is load-bearing: a single-instance sub-asset's base *is* the live
prim and carries the set layer's placement, while the import node's layer
is stronger than that layer's overs — so where placement already composed,
only its order is applied and the identity values are skipped, which would
otherwise snap the asset to the origin. Note that order alone cannot tell
a clobbered prototype from a guarded one (both end up with five ops); only
the values distinguish them.

The placement op order comes from one shared rule
(`pipe.usd.composed_placement_op_order`) at all three points that
re-create instance prims and their prototype: the `import_asset`
metadata script (GUI), the `import_shot` duplicates subnet (also the
farm stage-task graph), and the batch-submit direct-render flatten,
which composes the staged stage at submission time to bake real orders
into its static defs.

The rule is: **an order that composed is returned verbatim.** Only when
none composed is one derived (ops with composed values, in
XformCommonAPI order, pivot inverted last, identity dup op as the
no-placement fallback) — that derivation exists solely for values the
stripped Duplicate defs left orderless, and it is not a second opinion
about an order that survived. Deriving unconditionally was a real
divergence, not a hypothetical one: the flatten authors its result into
the collapsed *root* layer, stronger than every department sublayer, so
a set whose duplicates kept a baked `xformOp:transform` had that order
replaced by a CommonAPI set applying the stale translate/rotate/scale
values sitting beside it, and every copy moved on the farm while the GUI
stayed put. `xformOp:transform` is not in `PLACEMENT_OPS`, so the
derivation cannot see it — which is exactly why it must not override.

`scripts/verify_tracked_asset_counts.py` sweeps a project for staged
counts that drifted from the department contexts.

## Dropped-metadata guard

Because a consumer only sees a tracked asset through its `customData`,
an *imported* asset that lost that tag would silently vanish from the
published layer and every downstream import. The export refuses to
publish when it finds such a drop, via two complementary checks: a
sibling scan (`util.list_dropped_asset_prims` — a metadata-less
`Scope`/`Xform` beside a real asset) and an upstream cross-check
(`export_layer._list_expected_asset_uris` — an import node's declared
URI missing from the stage). Re-running the import node (its metadata
step re-cooks) clears a genuine drop.

Geometry that never carried per-asset metadata — an extra group or
helper prim an artist authors directly, hand-modelled set dressing, or
department-authored shot content pulled from another department's shot
export (an FX sim, a set-dress cache) — is **not** a drop and does not
block the export. The sibling scan tells these apart from a real drop by
composition: a dropped asset still pulls its geometry from the *asset*
export tree (`export/assets/...`, since assets are referenced rather than
baked into shot-department layers), while artist geometry composes from
no pipeline layer and department shot geometry composes only from a
shot-department export (`export/shots/.../<dept>/...`). Only the first is
flagged; the rest pass through (and are logged). To bake a real imported
asset into the export instead of re-referencing it, set the import node's
*Import Mode* to Inline rather than leaving it untagged.

## Department exclusion

The *Exclude departments* setting on the import nodes filters the staged
layer stack per department, and applies through nesting: excluding
`lookdev` when importing a set also drops the lookdev layers of every
nested asset.

## Picking up new versions on open

A plain scene load does **not** re-resolve imports: the import nodes'
saved layers restore as-is, so a workfile shows the versions that were
baked in when it was last built or saved. Because an asset's staged
build pins each department (e.g. `lookdev`) to its latest-at-build-time
version (§*Department exports and staged files*), that pin sticks — a
department published *after* the staged build won't appear just by
reopening the workfile, and the `latest` label on an import node does
nothing on its own at load time.

Opening a workfile **through the Asset Browser** closes the gap: it
re-executes every `th::import_*` node in the scene
(`_pipeline_scene.refresh_scene_imports`), and each node's `latest`
reference re-resolves under resolver latest-mode, which ignores the
baked `version=` pins and floats to the newest published version — the
same cascade a fresh import would get. So a newly published upstream
department reaches a downstream workfile on its next catalogue open,
with no re-import. This is governed by the **Auto-import latest on
workfile open** preference (Asset Browser → pipeline settings, *on* by
default), persisted to
`$HOUDINI_USER_PREF_DIR/asset_browser/pipeline_prefs.json`; disabling it
restores load-time-frozen behavior.

The refresh only touches import nodes — `create_model` and `build_comp`
are deliberately excluded, so a plain open re-resolves references
without cooking the whole workgraph. It does not reach the farm until
the shot/asset is re-staged (see below — the farm pins `current`).

## Picking up new versions mid-session

An **already-open** scene has a second staleness layer beyond the node
parms: USD's layer registry keeps handing back the originally-opened
layer for an identifier, so a version-less `entity:` reference in a
composed stage never re-resolves on its own — no matter how often the
import button is pressed. `tumblepipe.resolver.refresh_context()`
(requested by every import-node `execute()`) closes it: it re-resolves
every loaded `entity://` layer and reloads the ones whose resolved path
changed, which recomposes every stage using them. A no-change refresh is
milliseconds; only genuinely new versions cost a reload.

The Asset Browser's **Update** quick action runs the same import refresh
as the on-open path against the current scene — no hip reload, no save
prompt — so a lighter can pull a mid-session model/anim publish without
reopening (or restarting) anything. **Reload** remains the heavier
variant that also reverts the scene to its on-disk state.

## Render staging

A submission takes one of two paths, chosen by the `standalone` setting.
**Direct render (`standalone=False`) is the default**; the stage task
below runs only when it is set.

### Direct render (default): the static flatten

`batch_submit` collapses the shot's latest staged build into a single
`collapsed_stage_<variant>.usda` at submission time
(`pipe.usd.collapse_latest_references`), and husk renders that. It walks
the staged file's sublayers recursively, resolves every `entity:` URI to
an absolute filesystem path, and emits them as one flat sublayer stack —
so the render needs no resolver at all. The instance defs described under
*Nested assets* are re-synthesized into this file's **root** layer, with
each order read off the staged stage composed at submission time.

Two things to know about it:

- The flat stack preserves the nested staged files' relative strength, so
  the sublayers alone compose exactly what `import_shot` shows. Anything
  the flatten *authors on top* is therefore the only thing that can make
  the farm disagree with the session — which is what makes the root-layer
  strength above load-bearing rather than incidental.
- It resolves every entity URI in **latest mode**, so it does *not* honour
  the `version=` pins the staged build froze into its sublayer URIs. For
  shot departments that lands close to the stage task's intent (point 3
  below refreshes them to `current` on purpose). For *assets* the two
  paths genuinely disagree: the stage task keeps them pinned via the
  staged build, while the flatten floats them. A shot pinning
  `Clash/SET?version=v0073` (lookdev `v0018`, model `v0055`) flattens to
  SET `v0075`'s layers (lookdev `v0019`, model `v0056`) — geometry the
  session never composed, and prims can appear or vanish accordingly.
  Because the flatten bakes absolute paths once at submission this is at
  least stable across workers and frames, so it avoids the hazard point 1
  warns about. Known divergence, not a settled decision.

### Stage task (`standalone=True`)

This path does not compose the staged file directly. The stage task
builds a dedicated LOP graph per render layer (variant) and exports it
to a `stage_<variant>.usd` that husk then renders. The graph — built by
`tumblepipe.pipe.houdini.render_stage.build_render_stage_graph`, and
identically by the `th::render_debug` HDA for in-session inspection —
composes, weakest to strongest:

1. the shot's staged build for that variant, **pinned**: the inner
   import shot runs at `version='current'`, which keeps the frozen
   `version=` parameter on every sublayer URI. (The interactive
   `latest` mode strips versions, and the resolver floats a
   version-less URI to whatever is newest on disk at resolve time —
   acceptable in a live session, wrong on a farm where frames resolve
   hours apart on different workers.)
2. the root default prims (`config:/usd/root_default_prims.usda`,
   render settings and RenderVar prims),
3. one import-layer per renderable shot department at its `current`
   export, so a render picks up department publishes made since the
   last shot build without a rebuild. A department with no export
   under the render's variant re-applies its default-variant export —
   the same fallback the staged build uses — so this pass refreshes
   the layer the staged stack actually contains,
4. the render-settings overrides from the submission's
   `render_settings.json`, then pruning of AOVs not selected for the
   render.

Each variant gets its **own** graph and export — variants are
alternative opinions on the same prims, so a single stage composing
several of them would just render the strongest one for every layer.

To preview what this path renders, drop a `th::render_debug` node and
pick the shot and variant; its dive target contains the same graph the
stage task exports. It previews the stage task specifically — a default
(direct-render) submission renders the flattened stage above instead, so
inspect that submission's `collapsed_stage_<variant>.usda` in the job's
`data/` directory to see what husk actually got.

## Performance note

A set-style asset's staged file composes the full geometry of all its
nested assets, so first loads pull considerably more data than the set's
own layers alone. This is expected; if it becomes a bottleneck, deferred
loading (payload arcs for tracked assets) is the design lever.
