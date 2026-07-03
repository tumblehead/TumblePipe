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
   own layers, so its placement overrides win.

Shot staged files work the same way: shot department layers, then
shot-flow assets, then the root department (which carries the scene
reference).

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

## Department exclusion

The *Exclude departments* setting on the import nodes filters the staged
layer stack per department, and applies through nesting: excluding
`lookdev` when importing a set also drops the lookdev layers of every
nested asset.

## Performance note

A set-style asset's staged file composes the full geometry of all its
nested assets, so first loads pull considerably more data than the set's
own layers alone. This is expected; if it becomes a bottleneck, deferred
loading (payload arcs for tracked assets) is the design lever.
