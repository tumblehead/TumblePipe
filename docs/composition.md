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

The placement op order is derived by one shared rule
(`pipe.usd.composed_placement_op_order`: ops with composed values, in
XformCommonAPI order, pivot inverted last, identity dup op as the
no-placement fallback) at all three points that re-create instance
prims: the `import_asset` metadata script (GUI), the `import_shot`
duplicates subnet (also the farm stage-task graph), and the
batch-submit direct-render flatten, which composes the staged stage at
submission time to bake real orders into its static defs. GUI and farm
placement agree by construction.
`scripts/verify_tracked_asset_counts.py` sweeps a project for staged
counts that drifted from the department contexts.

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
without cooking the whole workgraph. It does not reach an already-open
scene until it is reopened, and it does not reach the farm until the
shot/asset is re-staged (see below — the farm pins `current`).

## Render staging

Farm renders do not compose the staged file directly. The stage task
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

To preview exactly what the farm will render, drop a
`th::render_debug` node and pick the shot and variant; its dive target
contains the same graph the stage task exports.

## Performance note

A set-style asset's staged file composes the full geometry of all its
nested assets, so first loads pull considerably more data than the set's
own layers alone. This is expected; if it becomes a bottleneck, deferred
loading (payload arcs for tracked assets) is the design lever.
