# Pipeline nodes

TumblePipe ships its Houdini tools as `th::` digital assets. They live in the
**Tab menu under `_TumblePipe/…`**, one submenu per area of the pipeline. This
page lists every node and explains the handful of parms that all the
entity-aware nodes share. The per-family pages describe each node's parms,
buttons and gotchas:

- [Importing and exporting](import-and-export.md) — `import_asset`,
  `import_assets`, `import_shot`, `import_layer`, `export_layer`, `cache`, and
  the smaller stage utilities.
- [Building assets](assets.md) — the `create_asset` chain, model and rig
  import/export, lookdev helpers.
- [Lighting and rendering](lighting-and-rendering.md)
- [Compositing](comp.md)
- [Tools](tools.md)

The deep mechanics — staging, channels, layer save paths, the metadata guard,
department exclusion, version floating — live in
[Asset composition and staging](../composition.md). The node pages link into
it rather than repeating it.

## Where the nodes are

Grouped by Tab submenu, as declared in [`hpm.toml`](../../hpm.toml)
(`[[operators]]`). *Category* is the Houdini network type the node is placed
in.

### `_TumblePipe/pipeline`

| Node | Category | Purpose |
|---|---|---|
| `th::import_asset` | LOP | Reference one published asset into the stage, at a chosen channel and version |
| `th::import_assets` | LOP | Reference several assets at once, with per-row instance counts and a shared layout transform |
| `th::import_shot` | LOP | Load a shot's staged build, cut at the workfile's department; shows the Layer Stack |
| `th::import_layer` | LOP | Sublayer one department's export of any entity (asset or shot) |
| `th::export_layer` | LOP | Publish the department layer; opens the Export/Publish dialog |
| `th::cache` | LOP | Versioned USD cache of the incoming stage, in the workfile's cache folder |
| `th::create_asset` | LOP | Root of the asset-building chain: the asset prim and its category scope |
| `th::create_model` | LOP | Standalone model builder (same geometry contract as `create_asset_model`) |
| `th::animate` | LOP | Dive-in SOP network imported as animated USD over the incoming stage |
| `th::archive` | LOP | Flatten the whole stage to one `.usd` with every asset localized beside it |
| `th::asset_thumbnail` | LOP | Render a 256×256 thumbnail of a prim with Karma XPU |
| `th::layer_split` | LOP | Publish a `_shared` layer that every channel of the entity sublayers |
| `th::sop_modify` | LOP | Pull prims into an editable SOP network and cache the result back as clips |
| `th::lpe_tags` | LOP | Seed LPE tag rows from the lights in the stage |
| `th::playblast` | LOP | Flipbook the stage through the pipeline's playblast folders |
| `th::render_settings` | LOP | Karma render settings with load/save presets; embeds `render_vars` |
| `th::cache` | SOP | Versioned `.bgeo.sc` cache in the workfile's cache folder |
| `th::import_asset` | SOP | The LOP `import_asset` wrapped for SOPs; optionally unpacked to polygons |
| `th::import_model` | SOP | Pull a published model's geometry into SOPs, one USD variant at a time |
| `th::import_rig` | SOP | Load one published rig |
| `th::import_rigs` | SOP | Load several rigs, one row each, with per-row version pins |
| `th::export_rig` | SOP | Publish the rig department |
| `th::playblast` | SOP | OpenGL flipbook from a SOP context |
| `th::build_comp` | COP | Build and submit the shot comp |

### `_TumblePipe/model`

| Node | Category | Purpose |
|---|---|---|
| `th::create_asset_model` | LOP | Model department body: USD variants, each a SOP network, grafted under the asset |
| `th::mesh_blender` | SOP | Brush-based mesh blending |
| `th::mesh_lasso` | SOP | Stroke/lasso extrusion tool |

### `_TumblePipe/lookdev`

| Node | Category | Purpose |
|---|---|---|
| `th::create_asset_lookdev` | LOP | Lookdev department body: materials under `<asset>/mtl`, one USD variant per look |
| `th::material_assigner` | LOP | Assign stage materials to prim patterns, seeded from the stage's materials |
| `th::cop_material_library` | LOP | Material library built from `cop_material` nodes |
| `th::lookdev_studio` | LOP | Turntable stage for look development |
| `th::mesh_outline` | LOP | Paint an outline stroke around prims |
| `th::projection_mapping` | LOP | Camera-projected material setup |
| `th::cop_material` | COP | Standard-surface material with triplanar textures |
| `th::gradient_map` | COP | Gradient-map an image |
| `th::lop_import` | COP | Bring LOP geometry into a COP network |
| `th::mask_painter` | COP | Paint a texture mask |
| `th::paint_scatter` | COP | Procedural brush-stroke texture generator |
| `th::tileable_texture` | COP | Make a texture tile |
| `th::maps_baker` | SOP | Bake high-to-low normal and diffuse maps (project-specific, see Building assets) |
| `th::mesh_carver` | SOP | Stroke-driven carving |
| `th::triplanar_projection` | VOP | MaterialX triplanar projection |

### `_TumblePipe/lighting`

| Node | Category | Purpose |
|---|---|---|
| `th::image_card` | LOP | Textured card |
| `th::karmafogbox` | LOP | Karma fog volume box |
| `th::light_blocker` | LOP | Translucent light-blocking card |
| `th::light_streaks` | LOP | Emissive streak geometry |
| `th::render_layer_setup` | LOP | Per-layer visibility, holdout and lighting overrides |

### `_TumblePipe/rendering`

| Node | Category | Purpose |
|---|---|---|
| `th::puzzlemattes` | LOP | Puzzle-matte AOVs from prim patterns |
| `th::render_vars` | LOP | Toggle the standard AOV set |

### `_TumblePipe/comp`

| Node | Category | Purpose |
|---|---|---|
| `th::a_b_slider` | COP | Wipe/blend between two images |
| `th::cop_paint` | COP | Paint strokes into an image |
| `th::depth_cull` | COP | Depth-based cull |
| `th::import_lop_camera` | COP | Bring a shot's camera into COPs (embeds `import_shot`) |
| `th::roto_mask` | COP | Draw and feather a roto mask |

### `_TumblePipe/utils`

| Node | Category | Purpose |
|---|---|---|
| `th::asset_payload` | LOP | Wrap the asset prim in a payload sidecar layer |
| `th::export_asset` | LOP | Pipeline-agnostic asset writer (no versioning) |
| `th::set_kinds` | LOP | Set assembly/component/scope kinds down a prim path |
| `th::shot_sequencer` | LOP | Play several shot stages back to back |
| `th::image_plane_painter` | LOP | Paint on an image plane in an external editor |
| `th::copy_parms` | SOP | Copy parm values and keyframes between two nodes |
| `th::geo_path_builder` | SOP | Set `name`/`path` on geometry for the asset's Import Path Prefix |
| `th::rename_packed` | SOP | String-replace `path` attributes inside a packed rig shape |

### `_TumblePipe/debug`

| Node | Category | Purpose |
|---|---|---|
| `th::render_debug` | LOP | Build the farm's stage-task graph in-session for a shot and channel |

### No submenu (recipes)

Five Data recipes ship in [`otls/Recipes.hda`](../../otls/Recipes.hda) and
are reached through the recipes radial menu rather than the Tab menu:
`th::th_configure_forest_gobo`, `th::th_configure_grass`,
`th::th_configure_lop_import`, `th::th_configure_material_override`,
`th::th_configure_render_layer_matte`.

### Radial menus

`Alt+T` in a network editor opens the TumblePipe radial
([`radial_menus/tumblepipe_pipeline.json`](../../radial_menus/tumblepipe_pipeline.json)):
an **Asset** submenu (Import Layer, Import Assets, Export Layer) and a
**Render** submenu (Mattes, LPE Tags, Render Settings, Render Vars), plus
project info and a cache refresh. See [Radial menus](../radial-menus.md).
The radial only registers when the `tumbleradial` package is installed.

## Shared concepts

Most pipeline nodes address a **pipeline entity** — an asset or a shot — and
share one block of parms to say which. The block is defined by the base
wrapper class
[`pipe/houdini/entity_node.py`](../../python/tumblepipe/pipe/houdini/entity_node.py);
the node pages point back here rather than describing it each time.

### Entity and Department default to `from_context`

Every entity-aware node is born with **Entity** and (where it has one)
**Department** set to the literal value `from_context`. That is not an entity
name — it means *follow the workfile I live in*. On every evaluation the node
reads the `context.json` next to the current `.hip` (which records the
workfile's entity URI and department) and resolves to that.

Why this matters to you:

- A node on `from_context` keeps working when the scene is copied to another
  workfile, when the entity is renamed, or when a template is instantiated
  for a new asset. A node with a baked URI keeps pointing at the entity it
  was born in.
- `from_context` needs a **saved pipeline workfile**. In an unsaved scene, or
  a hip outside the project, it resolves to nothing and the node bypasses
  itself (its comment tells you why).
- Some nodes only accept one side. `th::import_asset` and `th::import_rig`
  only ever import assets, so `from_context` inside a **shot** workfile
  resolves to nothing rather than to the shot.
- An **empty** Entity is *not* the same as `from_context`: the wrapper
  resolves an empty parm to the first entity in the project. Do not clear
  the field; put it back to `from_context` or pick an entity.

Department works the same way: `from_context` is the workfile's own
department. Note that on an *export* that is what you want, but on an
*import* it is usually not (you import the department upstream of yours) —
`th::import_layer` warns about exactly this.

### The Entity button and label

The Entity row is a button with a tree icon, followed by a read-only label.
The button opens the **Select Entity / Select Asset / Select Shot** dialog: a
tree of the project's entities with a `from_context` row at the top. The
label shows what the parm currently resolves to — `from_context: <uri>` when
following the workfile, the bare URI when pinned, or `from_context: none`
when nothing resolves. The URI itself lives in a hidden string parm named
`entity`, which is what scripts set.

### `effective_primpath`

The `create_asset` family (`th::create_asset`, `th::create_asset_model`,
`th::create_asset_lookdev`) can run in **entity mode** or **direct mode**.
In entity mode the asset prim path is derived from the entity URI by
dropping its first segment — `entity:/assets/char/mom` becomes `/char/mom`,
`entity:/shots/seq010/shot0010` becomes `/seq010/shot0010` — and that
derived path is what the nodes call `effective_primpath`. In direct mode it
is whatever you typed into **Primitive Path**. Every internal node reads the
resolved value, never the raw parm (which holds `from_context`, not a path).
The import and export nodes use the same URI-to-path rule for the prims they
write; see [Nesting is not containment](../composition.md#nesting-is-not-containment)
for why an asset's prim path never depends on who imports it.

### Channel

The parm labelled **Channel** (default `default`) picks the publish-tree fork
to read or write — the `bg`/`fg`/character-pass axis a project defines per
entity. The menu lists the entity's channels with `default` first. Its
*internal* parm name is still `variant`, so expressions and scripts refer to
`variant`; the label is the only thing that changed. What a channel is, and
why it is not a USD variant, is in
[Channels](../composition.md#channels-and-why-they-are-not-usd-variants).

### Version: latest, current, or pinned

Import nodes carry a **Version** menu listing `latest`, `current` and the
`v####` folders found on disk for the chosen entity and channel.

- **`latest`** puts the resolver in *latest mode*: the top-level reference is
  left unpinned, and every `entity:` reference nested inside the loaded layer
  also floats to the newest publish. This is the working default.
- **`current`** loads the highest staged version on disk but keeps the
  version pins the staged build froze into its sublayers, so what you see is
  exactly that build.
- **`v####`** pins to that folder.

`th::import_layer` has no `latest`; its default is `current` and it always
pins. A plain scene load does not re-resolve any of this — see
[Picking up new versions on open](../composition.md#picking-up-new-versions-on-open)
and [mid-session](../composition.md#picking-up-new-versions-mid-session).

### Import Mode

**Reference** (default) keeps the imported layer behind a layerbreak and tags
the asset's root prim with pipeline metadata, so the export re-references it
rather than baking it in. **Inline** drops the layerbreak and marks the prims
as inlined, so the geometry is baked into your department's export. Use
Inline deliberately; leaving a real asset untagged trips the
[dropped-metadata guard](../composition.md#dropped-metadata-guard) on export.

### Exclude departments

A checkable list of the entity's departments. Ticked departments are dropped
from the layer stack while you work — a working view that is never published;
see [Department exclusion](../composition.md#department-exclusion).
