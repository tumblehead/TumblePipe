# Building assets

The nodes that build an asset: the model and lookdev bodies that author
USD variants under the asset prim, the SOP-side rig import/export, and the
small helpers around them. All of the entity-aware nodes share the Entity /
Department / Channel block described in
[Pipeline nodes → Shared concepts](index.md#shared-concepts); publishing is
`th::export_layer`, described in
[Importing and exporting](import-and-export.md#thexport_layer-lop). None of
these HDAs carries built-in help text, so this page is the reference.

## The asset chain

A new project ships four asset departments
(`scripts/project_template/_config/db/departments.json`, summarised in
[Getting started → The default departments](../getting-started.md#the-default-departments)):

| Department | Composes into the staged asset? | What its template scaffolds in `/stage` |
|---|---|---|
| `model` | yes (independent, renderable) | `MODEL` (`th::create_asset_model`, one variant per channel with a starter box) → `EXPORT_MODEL` |
| `blendshape` | **no** (not a render layer) | `IMPORT_MODEL` (`th::import_layer`, Department `model`) → `BLENDSHAPES` (a SOP Create at `<asset>/blshp/`; inside: LOP Import of the model at frame 1001 → Unpack → `sculpt` → `cache` → `name` → `merge`, boxed as *BLENDSHAPE 1* to copy per shape) → `EXPORT_BLENDSHAPES` |
| `lookdev` | yes (renderable over-layer) | `IMPORT_MODEL` (`th::import_layer`, Department `model`) → `LOOKDEV` (`th::create_asset_lookdev`, one variant per channel; inside: `material_library` with a red `default_mtl` → `material_assigner` → every variant's output) → `EXPORT_LOOKDEV` |
| `rig` | **no** (not a render layer) | `rigging` (a SOP Create; inside: `import_model` (`th::import_model`), an unwired `import_blendshapes` (`th::import_model`, Department `blendshape`), and `export_rig` (`th::export_rig`) wired from the model import) |

Sources: [`templates/assets/model`](../../scripts/project_template/_config/templates/assets/model/template.py),
[`lookdev`](../../scripts/project_template/_config/templates/assets/lookdev/template.py),
[`blendshape`](../../scripts/project_template/_config/templates/assets/blendshape/template.py),
[`rig`](../../scripts/project_template/_config/templates/assets/rig/template.py).

Two things worth knowing before you start:

- The templates leave every `th::` node on `from_context`, so the network
  follows the workfile it lives in. A *Multi* (group) workfile is the one
  exception: it repeats the column per member, shifted along X, and pins
  each node to its member.
- The model and lookdev templates seed the **Variants** multiparm from the
  entity's *channel* list — one USD variant per channel name, `default` when
  the entity has none. A channel and a USD variant are different things
  ([Channels, and why they are not USD variants](../composition.md#channels-and-why-they-are-not-usd-variants));
  for an asset the two currently share a name, nothing more.

Rig and blendshape exports are real and versioned but never compose into
the staged asset — see
[Department exports and staged files](../composition.md#department-exports-and-staged-files).
The rig is read by `th::import_rig` / `th::import_rigs`; the blendshape layer
by `th::import_model` with Department `blendshape`.

### Walkthrough: model → lookdev → shot

1. **Model.** In the browser select the asset, right-click its `model` row,
   **New: Template**. Dive into `MODEL` (the Variants folder has a
   **Jump to Output** button per variant that lands you on that variant's
   `OUT_<name>` inside `variant_sopnet/create_variants`). Replace the
   starter box with your geometry and name the pieces — a primitive `name`
   or `path` attribute becomes the prim path under `<asset>/geo`, see
   [Where model geometry lands under the asset](../composition.md#where-model-geometry-lands-under-the-asset).
   **Save**, then **Publish** (or press **Export** on `EXPORT_MODEL`). The
   layer lands under `export/<asset>/default/model/v0001/` and the asset is
   staged.
2. **Lookdev.** Right-click the `lookdev` row, **New: Template**.
   `IMPORT_MODEL` has already been run for you and shows the model version
   it found. Dive into `LOOKDEV` → `lookdev_subnet`: build materials inside
   `material_library` (copy `default_mtl` or add *Karma Material Builder*
   nodes from the Tab menu), then on `material_assigner` press
   **Initialize Scene Materials** and give each row a **Primitives** pattern.
   Materials land under `<asset>/mtl`
   ([Where lookdev materials land](../composition.md#where-lookdev-materials-land-under-the-asset)).
   **Save**, **Publish**. What you published is an *over*-layer that
   composes over the model; opened on its own it looks empty, which is
   correct.
3. **Shot.** Open (or template) a shot workfile — `layout` is the usual
   first one — and drop a
   [`th::import_asset`](import-and-export.md#thimport_asset-lop), pick the
   asset, **Import**. You get the staged build: lookdev over model, at
   `latest`. Re-publishing the model or lookdev later is picked up as in
   [Picking up new versions on open](../composition.md#picking-up-new-versions-on-open).
4. **Rig (optional).** Right-click the `rig` row, **New: Template**, build
   the APEX rig inside `rigging` from `import_model`, and press **Export**
   on `export_rig`. The animation shot template's `th::animate` then lists
   the rig in its `th::import_rigs` —
   [`th::animate`](import-and-export.md#thanimate-lop).

## `th::create_asset` (LOP)

Author the asset's root prim on its own: an `Xform` at the asset's prim
path with a catalogue icon. `_TumblePipe/pipeline`. No template uses it —
`th::create_asset_model` creates the same prim itself — so it is only
needed when you assemble an asset out of plain LOPs.
Source: [`otls/lop_th.create_asset.1.0`](../../otls/lop_th.create_asset.1.0/th_8_8Lop_1create__asset_8_81.0/DialogScript),
[PythonModule](../../otls/lop_th.create_asset.1.0/th_8_8Lop_1create__asset_8_81.0/PythonModule).

| Label | Default | What it does |
|---|---|---|
| Primitive | Entity | *Entity*: derive the prim path from the Entity ([`effective_primpath`](index.md#effective_primpath)). *Path*: type it |
| Entity | `from_context` | The asset (button opens *Select Asset*; the label shows what resolved). Assets only |
| Primitive Path | `/Asset` | Only in Path mode |
| Icon | `COMMON_component` | Icon shown for the prim in the scene graph tree |

Gotchas:

- The node **ignores its input**: the internal Create Xform has nothing
  wired into it, so the output stage holds only the new asset prim. Put it
  first in the chain, not in the middle.
- It sets no kind. The structure validator only needs the asset prim to be
  an `Xform`, which this is.

## `th::create_asset_model` (LOP)

The model department body. It creates the asset prim, gives it a `model`
variant set, and fills every variant from its own SOP network, grafted
under `<asset>/geo`. `_TumblePipe/model`; the model template places one as
`MODEL`. The input stage passes through.
Source: [`otls/lop_th.create_asset_model.1.0`](../../otls/lop_th.create_asset_model.1.0/th_8_8Lop_1create__asset__model_8_81.0/DialogScript),
[PythonModule](../../otls/lop_th.create_asset_model.1.0/th_8_8Lop_1create__asset__model_8_81.0/PythonModule).

**Asset Definition**

| Label | Default | What it does |
|---|---|---|
| Primitive / Entity / Primitive Path | Entity, `from_context`, `lopinputprim('.', 0)` | As on `create_asset` |
| Create Sublayer | off | Activates an internal Configure Layer that starts a new layer with *Layer Save Path* `geo.usdc`. Leave it off — a bare relative save path is the trap described in [Layer save paths](../composition.md#layer-save-paths-and-export-portability) |

**Geometry Handling** (collapsed; these are the internal SOP Import's parms)

| Label | Default | What it does |
|---|---|---|
| Packed Primitives | Create Native Instances | How packed prims are imported (Xforms / Point Instancer / Native Instances / Unpack) |
| Treat Polygons as Subdivision Surfaces | off | Author meshes as subdivision surfaces |
| Import Path Prefix | `/geo` (enabled) | The scope under the asset the geometry lands in; the Configure Primitive that types it as `Scope` follows this parm |
| Path Attributes | `path,name` (enabled) | Attributes searched, in order, for each piece's prim path |
| Prefix Absolute Paths | off (enabled) | SOP Import's option; rarely matters because the node makes every path relative first (below) |

**Variants**

| Label | Default | What it does |
|---|---|---|
| Variant Set | `model` | Name of the variant set authored on the asset prim |
| Active Variant | first variant | Which variant the node's output selects (menu lists the rows below) |
| Variants | 1 row, `default` | One row per variant: a name and a **Jump to Output** button that takes the network editor to that variant's `OUT_<name>` output node |

What happens per variant: the node object-merges
`variant_sopnet/OUT_<name>`, runs it through the `NORMALIZE_PATHS` wrangle
(fall back `name` → `path`, strip the asset's own path if the value is
already rooted there, make it relative, clear `name`), imports it with SOP
Import under `<asset>` + Import Path Prefix with kind `component`, and adds
the result as a variant of the `model` set. The full rules, including what
Alembic and USD→SOP round trips do to `path`, are in
[The path attribute is normalised first](../composition.md#the-path-attribute-is-normalised-first).

Where you work: `variant_sopnet` is the node's editable network, and its
dive target is `variant_sopnet/create_variants`. Inside, each variant is an
Output SOP named `OUT_<name>` (output index = row index); whatever you wire
into it is that variant. The template drops a starter polymesh box on each.

Adding or removing a Variants row creates or destroys the matching
`OUT_<name>` output and its sibling null (the sync runs just after the
callback returns); renaming a row renames them. Orphaned outputs are
deleted, so **removing a row deletes that variant's output node** — move
your geometry first.

Gotchas:

- Keep variant names legal node names (no spaces, no leading digit). The
  per-variant fetch looks up `OUT_<name>` literally; a name Houdini cannot
  give a node yields an empty variant with no error.
- Keep **Variant Set** at `model`: `th::import_model`'s Variant menu reads
  the variant set named after its Department, so a renamed set shows no
  variants downstream.
- The export's structure validator
  ([`validators/asset_structure.py`](../../python/tumblepipe/pipe/houdini/validators/asset_structure.py))
  requires `<asset>` to be an `Xform` and `<asset>/geo` a `Scope`. Geometry
  that escapes the geo scope leaves the scope uncreated and the publish is
  rejected — that is the case the path normalisation exists for.

## `th::create_asset_lookdev` (LOP)

The lookdev department body: materials authored under `<asset>/mtl`, one
USD variant per look, composed as an over-layer on the incoming model.
`_TumblePipe/lookdev`; the lookdev template places one as `LOOKDEV` fed by
`IMPORT_MODEL`.
Source: [`otls/lop_th.create_asset_lookdev.1.0`](../../otls/lop_th.create_asset_lookdev.1.0/th_8_8Lop_1create__asset__lookdev_8_81.0/DialogScript),
[PythonModule](../../otls/lop_th.create_asset_lookdev.1.0/th_8_8Lop_1create__asset__lookdev_8_81.0/PythonModule).

| Label | Default | What it does |
|---|---|---|
| Primitive / Entity / Primitive Path | Entity, `from_context`, `lopinputprim('.', 0)` | As on `create_asset` |
| Create Sublayer | off | Same internal Configure Layer as the model node, save path `lookdev.usdc`. Leave it off |
| Variant Set | `lookdev` | Variant set authored on the asset prim |
| Active Variant | first variant | Variant selected on the node's output |
| Variants | 1 row, `default` | Name + **Jump to Output** per variant |

Where you work: the dive target is `lookdev_variant_subnet/lookdev_subnet`.
It ships with a `material_library` (Material Library LOP whose *Material
Path Prefix* is `<asset>/mtl/` and whose parent prim type is `Scope`)
holding `default_mtl`, a Karma Material Builder with a red base colour, and
one Output LOP per variant named `OUT_<name>`. The template inserts a
[`th::material_assigner`](#thmaterial_assigner-lop) between the library and
the outputs, so every variant reads the same assigned stage; a look that
needs different materials gets its own branch wired by hand.

Per variant the node fetches `lookdev_variant_subnet/VARIANT<n>_OUT`, adds
it as a variant of the `lookdev` set on the asset prim, and the Active
Variant is selected on the output. Why the published layer is all `over`s
and looks empty on its own is in
[Where lookdev materials land under the asset](../composition.md#where-lookdev-materials-land-under-the-asset).

Adding a Variants row creates `OUT_<name>` in `lookdev_subnet` and a
`VARIANT<n>_OUT` null wired to it. **Only the first output is wired for
you** (to `material_library`); every later `OUT_<name>` is created with no
input, and an unwired output publishes an empty variant. Wire it — usually
from `material_assigner` — before you publish.

Gotchas:

- Materials must be created *inside* `material_library` (or another
  Material Library with the same prefix) to land under `<asset>/mtl`. The
  structure validator requires `<asset>/mtl` to be a `Scope`.
- `IMPORT_MODEL` loads at Version `current`; a model published after the
  workfile was opened is picked up by the browser's **Update** or by
  reopening —
  [Picking up new versions mid-session](../composition.md#picking-up-new-versions-mid-session).

## Exporting

Publish through the template's `th::export_layer`
([reference](import-and-export.md#thexport_layer-lop)); the model export
runs the asset structure validator, and both departments are subject to
the [dropped-metadata](../composition.md#dropped-metadata-guard) and
[save-path](../composition.md#layer-save-paths-and-export-portability)
guards. `th::export_asset` is a pipeline-agnostic file writer and is not a
way to publish — see
[its entry](import-and-export.md#thexport_asset-lop).

## `th::create_model` (LOP)

A stand-alone model builder on top of a SOP Create: it authors the asset
`Xform` (kind `component`) at the asset's prim path, a `geo` Scope under it,
imports the geometry from its editable `sopnet/create` network and stamps
the prim with pipeline metadata. `_TumblePipe/pipeline`. No template uses
it; reach for it when you want one asset from a file on disk without the
variant machinery. It follows the same geometry contract as
`create_asset_model` (`NORMALIZE_PATHS` wrangle, geometry under `geo`).
Source: [`otls/lop_th.create_model.1.0`](../../otls/lop_th.create_model.1.0/th_8_8Lop_1create__model_8_81.0/DialogScript),
[`lops/create_model.py`](../../python/tumblepipe/pipe/houdini/lops/create_model.py).

| Label | Default | What it does |
|---|---|---|
| Asset | `from_context` | Plain menu of `from_context` plus every asset; the label beside it shows the resolved URI |
| Import Path Prefix | the asset's prim path (`/char/mom`) | Root of the authored hierarchy; geometry is imported under `<prefix>/geo`. Computed from **Asset**; empty when it resolves to nothing |
| Load As Reference, Sublayer Style, Adjust Transforms…, Bind Materials, Import Group, Layer Save Path, *Import from SOPs* folders | SOP Import defaults (save path **off**) | Promoted SOP Import parms, forwarded unchanged |
| Transform | identity | Placement of the asset prim, bound to the viewport handle |
| Materials: Auto-fill Materials / Number of Materials | 0 rows | **Auto-fill** scans the SOP geometry's `shop_materialpath` and fills rows (Material VOP, Material Path `<prefix>/mtl/<name>`, Geometry Path). Also runs on creation |
| Update | button | Regenerate the metadata script that writes `uri` and `instance` onto the asset prim |

Inside `sopnet/create`: `load_model_file` (File SOP) → `rest` → `OUT_GEO` →
the `default` output. Swap the File SOP for whatever produces the model.

Gotcha: this node's `NORMALIZE_PATHS` wrangle strips an already-rooted
`path` against **Primitive Path** (`/$OS`), not against **Import Path
Prefix**, so a USD → SOP round trip that hands back the asset's full prim
path (`/char/mom/geo/body`) is not re-anchored here the way
`create_asset_model` re-anchors it. Strip the prefix upstream, or use
`create_asset_model`.

## `th::import_model` (SOP)

Pull a published model (or blendshape layer) into a SOP network, one USD
variant at a time. `_TumblePipe/pipeline`; the rig template drops two into
`rigging` (`import_model`, and `import_blendshapes` set to `blendshape`),
and dropping an asset from the browser onto a SOP network creates one. It
embeds a `th::import_layer` followed by a Set Variant and a LOP Import.
Source: [`otls/sop_th.import_model.1.0`](../../otls/sop_th.import_model.1.0/th_8_8Sop_1import__model_8_81.0/DialogScript),
[PythonModule](../../otls/sop_th.import_model.1.0/th_8_8Sop_1import__model_8_81.0/PythonModule).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The asset (button opens *Select Asset*; picking one imports immediately) |
| Department | `model` | `model` or `blendshape` — which department's export to load |
| Version | `current` | `current` or a `v####` (the embedded `import_layer`'s list; there is no `latest`); the label shows what resolved |
| Variant | *(empty)* | Which USD variant to select before importing. The menu lists the variants of the variant set **named after Department** on the asset prim — `model` for a model. A blendshape layer has no such set, so the menu is empty there |
| Unpack To Polygons | on | Unpack the USD packed prims to polygons (adds `path`, `name`, `usdpath`; imports every primvar). Off leaves packed USD prims |
| Frame Mode | Static | Static samples one frame; Animated follows `$FF` |
| Import Frame | `1001` | The frame sampled in Static mode |

Buttons: **Import** runs the embedded import and mirrors its result onto
this node; **Open Location** opens the export folder.

The LOP Import only brings `%type:Boundable` prims, so scopes and xforms
never arrive as empty packed entries. With Department `blendshape` the
result is packed **by `name`** (one packed prim per blendshape) whatever
Unpack To Polygons says.

Gotchas:

- Nothing imports on creation; press **Import** (the template does it for
  you). When the department has nothing exported the node bypasses itself
  with the embedded `import_layer`'s comment (*NOTHING IMPORTED …*).
- With no resolvable entity the Variant menu falls back to the first prim
  in the incoming stage that carries a variant set, searching three levels
  deep — that is a fallback, not a feature.

## `th::import_rig` (SOP)

Load one published rig and add it to an APEX scene. `_TumblePipe/pipeline`.
Assets only: `from_context` inside a shot workfile resolves to nothing.
Source: [`otls/sop_th.import_rig.1.0`](../../otls/sop_th.import_rig.1.0/th_8_8Sop_1import__rig_8_81.0/DialogScript),
[`sops/import_rig.py`](../../python/tumblepipe/pipe/houdini/sops/import_rig.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The asset (button opens *Select Asset*; picking one imports) |
| Channel | `default` | Which channel's rig export |
| Version | `latest` | `latest` or a `v####` found under `export/<asset>/<channel>/rig/`; the label shows the version that resolved |
| Instances | `1` | Copies to add (1–10) |

**Import** loads
`export/<asset>/<channel>/rig/v####/<category>_<asset>_rig_v####.bgeo.sc`
through a File SOP, clears the dive network and rebuilds it: one
`apex::sceneaddcharacter` per instance, chained from the node's **first
input** — the APEX scene you are adding to — with *Character Name* the
asset name (`hero`) or, for several instances, `hero0`, `hero1`, …, each
preceded by a [`th::rename_packed`](#threname_packed-sop) rewriting
`/<category>/<asset>/*` to `/<category>/<asset><n>/*`. When it cannot
import it bypasses itself and passes the input through, with the comment
*Bypassed: No asset selected* / *No versions available* / *Rig file not
found*; on success *Imported: <asset>* and the version.

## `th::import_rigs` (SOP)

Several rigs in one node, one row each, with a version pin per row.
`_TumblePipe/pipeline`; the animation shot template places one inside
`th::animate`'s dive ([`th::animate`](import-and-export.md#thanimate-lop)).
Source: [`otls/sop_th.import_rigs.2.0`](../../otls/sop_th.import_rigs.2.0/th_8_8Sop_1import__rigs_8_82.0/DialogScript),
[`sops/import_rigs.py`](../../python/tumblepipe/pipe/houdini/sops/import_rigs.py).

**Rig imports** is a multiparm; each row has:

| Label | Default | What it does |
|---|---|---|
| *(Entity button)* + label | *(empty)* | The row's asset. No `from_context` here; the picker hides assets already on other rows |
| Channel | `default` | Per row; an invalid value snaps to the first channel |
| Version | `latest` | Per row, `latest` or `v####`; the label shows what resolved |
| Instances | `1` | Copies of that rig (1–10) |

**Import** rebuilds the dive network as a chain of `th::import_rig` nodes,
one per row, each pinned to the row's channel and version (`latest`
resolves per rig at import time), and comments *Imported: N rigs*. With no
rows the node bypasses itself with *Bypassed: No rigs configured*.

Gotchas:

- Picking an entity on a row updates its labels but does **not** import;
  press **Import**.
- A row with an empty entity is filled with the first unused asset when you
  press Import.

## `th::export_rig` (SOP)

Publish the rig department. `_TumblePipe/pipeline`; the rig template wires
one from `import_model` inside `rigging`. It is a sink — no output
connector — so put it at the end of the rig.
Source: [`otls/sop_th.export_rig.1.0`](../../otls/sop_th.export_rig.1.0/th_8_8Sop_1export__rig_8_81.0/DialogScript),
[`sops/export_rig.py`](../../python/tumblepipe/pipe/houdini/sops/export_rig.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | Plain menu of `from_context` plus every asset. `from_context` outside an asset workfile resolves to nothing and the export refuses rather than guessing |
| Channel | `default` | Channel to publish under |

**Export** opens the *Export Rig* task dialog (the same dialog as Publish).
The task writes the next version,
`export/<asset>/<channel>/rig/v####/<category>_<asset>_rig_v####.bgeo.sc`,
as a single-frame File Cache of the current frame, plus a `context.json`.
If the cook produced no file the task fails with *Rig export produced no
output file* instead of writing a context that points at nothing.

The department is always `rig` (there is no Department parm). A rig export
is a `.bgeo.sc`, never a USD layer, so it does not compose into the staged
asset — `th::import_rig` and `th::import_rigs` are its readers.

## `th::asset_thumbnail` (LOP)

Render a 256×256 thumbnail of the asset with Karma XPU.
`_TumblePipe/pipeline`.
Source: [`otls/lop_th.asset_thumbnail.1.0`](../../otls/lop_th.asset_thumbnail.1.0/th_8_8Lop_1asset__thumbnail_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Primitive Path | `lopinputprim('.', 0)` | Names the output file and the prims the wireframe material is assigned to |
| Override Output Image | `$HIP/thumbnails<Primitive Path>_thumbnail.png` | Where the render goes |
| Render to Disk | button | Renders the frame (16 path-traced samples, `/Render/rendersettings`) |
| type | wireframe | *wireframe*: a Karma MaterialX wire material assigned to Primitive Path, other materials unassigned. *lit*: three disk lights (`KEY`, `RIM`, `FILL`). *unlit*: camera only |

The camera is created for you at `/__HoudiniThumbnailCamera__`, framed on
the bounds of the input's last-modified prim (not on Primitive Path).

Gotcha: the node's output carries the camera, lights, materials and render
settings it added. Branch it off; do not leave it upstream of an export.

## `th::material_assigner` (LOP)

Assign the stage's materials to prim patterns, seeded from the materials
already on the stage. `_TumblePipe/lookdev`; the lookdev template places one
after `material_library` inside `lookdev_subnet`.
Source: [`otls/lop_th.material_assigner.1.0`](../../otls/lop_th.material_assigner.1.0/th_8_8Lop_1material__assigner_8_81.0/DialogScript),
[PythonModule](../../otls/lop_th.material_assigner.1.0/th_8_8Lop_1material__assigner_8_81.0/PythonModule).

Buttons:

- **Initialize Scene Materials** — one *Material Assignment* row per
  `Material` prim on the stage. **Ctrl-click** to pick which materials from
  a list instead.
- **Update Materials Paths** — re-resolve every row's material by its
  *name* against the stage, for when the material prefix moved (a renamed
  asset, a changed `mtl` path).

**Material Assignments** rows:

| Label | Default | What it does |
|---|---|---|
| Primitives | *(empty)* | Pattern to bind to; the menu offers *Meshes* (`%type:Mesh ^%instanceproxy()`) and *Volumes* (`%type:Volume`) |
| Material | *(empty)* | Menu of the stage's materials, by name (token is the full path) |
| Purpose | Render | Binding purpose: Render (`full`) or Preview |

Gotchas:

- A row's Primitives is empty after Initialize; nothing is bound until you
  fill it in.
- **Update Materials Paths** stops with a Python error (an undefined
  `current_name` in its warning) if any row names a material that is no
  longer on the stage. Delete the stale row first.

## `th::rename_packed` (SOP)

String-replace the path attributes inside an APEX character's packed
`Base.shp`: it unpacks that folder entry, runs an Attribute String Edit on
the primitive attributes `path`, `usdmaterialpath` and `usdprimpath` (and
point attributes) replacing **From** with **To**, and packs it back as
`Base` (`shp`). `_TumblePipe/utils`. `th::import_rig` uses it to give each
instance its own path (`/char/hero/*` → `/char/hero0/*`), which is the
pattern to copy: plain wildcards, not regular expressions, first match only.
Source: [`otls/sop_th.rename_packed.1.0`](../../otls/sop_th.rename_packed.1.0/th_8_8Sop_1rename__packed_8_81.0/DialogScript),
[`sops/rename_packed.py`](../../python/tumblepipe/pipe/houdini/sops/rename_packed.py).

## `th::geo_path_builder` (SOP)

One string field (default `road_lines`) with a read-only preview beside it.
It sets the primitive `name` attribute to what you type — which is what
`th::create_asset_model`'s SOP Import reads — and the preview shows the
resulting prim path as `<Import Path Prefix>/<name>`. `_TumblePipe/utils`.
Source: [`otls/sop_th.geo_path_builder.1.0`](../../otls/sop_th.geo_path_builder.1.0/th_8_8Sop_1geo__path__builder_8_81.0/DialogScript).

Gotchas:

- The preview reads `pathprefix` **four levels up**, so it is only right
  when the node sits directly inside `create_variants` of a
  `th::create_asset_model`. Anywhere else the prefix is missing.
- The `path` it computes for the preview is a *detail* attribute; the
  primitive `name` is what drives placement. Type a plain name, not a path.

## `th::maps_baker` (SOP)

Bake high-to-low normal and diffuse maps. **Project-specific as shipped**:
it exposes no parameters at all. Input 1 is the low-poly mesh, input 2 the
high-poly; the node computes normals and tangents, rasterises both in an
embedded COP network (`bake_maps2`), pads the result and previews it with a
Preview Material, and two Image ROPs inside write `All_normals_v01.png` and
`All_diffuse_v01.png` at 1024×1024 to a **hard-coded path under one
production project's** `texture/` folder. The output is the low-poly input
passed through. To use it elsewhere, allow editing of contents and change
the two Image ROPs' output paths. `_TumblePipe/lookdev`.
Source: [`otls/sop_th.maps_baker.1.0`](../../otls/sop_th.maps_baker.1.0/th_8_8Sop_1maps__baker_8_81.0/DialogScript).

## `th::copy_parms` (SOP)

Copy parameter values and keyframes from one node to another.
`_TumblePipe/utils`. Set **Source** and **Destination** (node paths,
relative to this node) and press **Transfer**: for every parm on Source the
same-named parm on Destination gets Source's value, its own keyframes are
deleted and Source's keyframes copied in. Parms that do not exist on
Destination are printed to the console (`missing parm …`) and skipped. It
does nothing to geometry.
Source: [`otls/sop_th.copy_parms.1.0`](../../otls/sop_th.copy_parms.1.0/th_8_8Sop_1copy__parms_8_81.0/DialogScript),
[PythonModule](../../otls/sop_th.copy_parms.1.0/th_8_8Sop_1copy__parms_8_81.0/PythonModule).
