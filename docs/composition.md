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
every import. The render-stage flatten generates the same instance
definitions.
`scripts/verify_tracked_asset_counts.py` sweeps a project for staged
counts that drifted from the department contexts.

## Department exclusion

The *Exclude departments* setting on the import nodes filters the staged
layer stack per department, and applies through nesting: excluding
`lookdev` when importing a set also drops the lookdev layers of every
nested asset.

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
   last shot build without a rebuild,
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
