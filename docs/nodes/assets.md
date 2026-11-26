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

A new project ships five asset departments
(`scripts/project_template/_config/db/departments.json`, summarised in
[Getting started → The default departments](../getting-started.md#the-default-departments)):

| Department | Composes into the staged asset? | What its template scaffolds in `/stage` |
|---|---|---|
| `model` | yes (independent, renderable) | `MODEL` (`th::create_asset_model`, one variant per channel with a starter box) → `EXPORT_MODEL` |
| `blendshape` | **no** (not a render layer) | `IMPORT_MODEL` (`th::import_layer`, Department `model`) → `BLENDSHAPES` (a SOP Create at `<asset>/blshp/`; inside: LOP Import of the model at frame 1001 → Unpack → `sculpt` → `cache` → `name` → `merge`, boxed as *BLENDSHAPE 1* to copy per shape) → `BLSHP_SCOPE` (a Configure Primitive making `<asset>/blshp` the Scope the export check requires) → `EXPORT_BLENDSHAPES` |
| `groom` | yes (renderable) | `IMPORT_MODEL` (`th::import_layer`, Department `model`) → `GROOM` (a SOP Create at `<asset>/groom/`; inside: LOP Import of the model at frame 1001 → Unpack (the skin) → `GUIDES` (Hair Generate, 500 guide curves) → `groom` (Guide Groom, guides + skin; holds the display flag) → `HAIR` (Hair Generate, 20000 hairs from the groomed guides) → `name` (`hair`), boxed as *GROOM 1* to copy per groom) → `GROOM_SCOPE` (a Configure Primitive making `<asset>/groom` the Scope the export check requires) → `EXPORT_GROOM`. The hair exports as `BasisCurves` at `<asset>/groom/hair` |
| `lookdev` | yes (renderable over-layer) | `IMPORT_MODEL` (`th::import_layer`, Department `model`) → `LOOKDEV` (`th::create_asset_lookdev`, one variant per channel; inside: `material_library` with a red `default_mtl` → `material_assigner` → every variant's output) → `EXPORT_LOOKDEV` |
| `rig` | **no** (not a render layer) | `rigging` (a SOP Create; inside: `import_model` (`th::import_model`), an unwired `import_blendshapes` (`th::import_model`, Department `blendshape`), `promote_name` (Attribute Promote: `name` primitive → point, original kept), `enumerate` (Enumerate, Group Type Points: integer point `index`; holds the display flag), and `export_rig` (`th::export_rig`) wired from `enumerate`) |

Sources: [`templates/assets/model`](../../scripts/project_template/_config/templates/assets/model/template.py),
[`lookdev`](../../scripts/project_template/_config/templates/assets/lookdev/template.py),
[`blendshape`](../../scripts/project_template/_config/templates/assets/blendshape/template.py),
[`groom`](../../scripts/project_template/_config/templates/assets/groom/template.py),
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
   **New from Template**. Dive into `MODEL` (the Variants folder has a
   **Jump to Output** button per variant that lands you on that variant's
   `OUT_<name>` inside `variant_sopnet/create_variants`). Replace the
   starter box with your geometry and name the pieces — a primitive `name`
   or `path` attribute becomes the prim path under `<asset>/geo`, see
   [Where model geometry lands under the asset](../composition.md#where-model-geometry-lands-under-the-asset).
   **Save**, then **Publish** (or press **Export** on `EXPORT_MODEL`). The
   layer lands under `export/<asset>/default/model/v0001/` and the asset is
   staged.
2. **Lookdev.** Right-click the `lookdev` row, **New from Template**.
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
4. **Rig (optional).** Right-click the `rig` row, **New from Template**. The
   network editor opens inside `rigging`'s SOP network; build
   the APEX rig there from `enumerate` (the model
   with `name` copied onto points and an `index` point attribute), and press **Export**
   on `export_rig`. The animation shot template's `th::animate` then lists
   the rig in its `th::import_rigs` —
   [`th::animate`](import-and-export.md#thanimate-lop). A rig built on the
   stage for the APEX Animate LOP publishes through `th::export_layer`
   instead — [LOP rigs](#lop-rigs-apex-characters-on-the-stage).

## `th::create_asset` (LOP)

Author the asset's root prim on its own: an `Xform` at the asset's prim
path with a catalogue icon. `_TumblePipe/pipeline`. No template uses it —
`th::create_asset_model` creates the same prim itself — so it is only
needed when you assemble an asset out of plain LOPs.
Source: [`otls/lop_th.create_asset.1.0`](../../otls/lop_th.create_asset.1.0/th_8_8Lop_1create__asset_8_81.0/DialogScript),
[`lops/create_asset.py`](../../python/tumblepipe/pipe/houdini/lops/create_asset.py).

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
[`lops/create_asset.py`](../../python/tumblepipe/pipe/houdini/lops/create_asset.py).

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
`variant_sopnet/create_variants/OUT_<name>`, runs it through the `NORMALIZE_PATHS` wrangle
(fall back `name` → `path`, strip the asset's own path if the value is
already rooted there, make it relative, clear `name`), imports it with SOP
Import under `<asset>` + Import Path Prefix with kind `component`, and adds
the result as a variant of the `model` set. The full rules, including what
Alembic and USD→SOP round trips do to `path`, are in
[The path attribute is normalised first](../composition.md#the-path-attribute-is-normalised-first).

Where you work: the dive target, `variant_sopnet/create_variants`, is the
node's only editable network. Inside, each variant is an Output SOP named
`OUT_<name>` (output index = row index); whatever you wire into it is that
variant. The template drops a starter polymesh box on each. **U** from there
goes straight back to the network the node sits in: `variant_sopnet` is
locked, so Houdini walks past it (an editable wrapper would stop the walk,
which is how it behaved before 2026-09-14).

Adding or removing a Variants row creates or destroys the matching
`OUT_<name>` output (the sync runs just after the callback returns);
renaming a row renames it. Orphaned outputs are
deleted, so **removing a row deletes that variant's output node** — move
your geometry first.

Gotchas:

- Nodes placed in `variant_sopnet` beside `create_variants` are not kept:
  the wrapper is locked, and a scene saved while it was editable drops them
  on open with an *Ignoring data for locked node* load warning. Everything
  inside `create_variants` is kept. Save once to clear the warning.
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
[`lops/create_asset.py`](../../python/tumblepipe/pipe/houdini/lops/create_asset.py).

| Label | Default | What it does |
|---|---|---|
| Primitive / Entity / Primitive Path | Entity, `from_context`, `lopinputprim('.', 0)` | As on `create_asset` |
| Create Sublayer | off | Same internal Configure Layer as the model node, save path `lookdev.usdc`. Leave it off |
| Variant Set | `lookdev` | Variant set authored on the asset prim |
| Active Variant | first variant | Variant selected on the node's output |
| Variants | 1 row, `default` | Name + **Jump to Output** per variant |

Where you work: the dive target is `lookdev_variant_subnet/lookdev_subnet`,
the node's only editable network (so **U** from it leaves the node, as on
`th::create_asset_model`). It ships with a `material_library` (Material Library LOP whose *Material
Path Prefix* is `<asset>/mtl/` and whose parent prim type is `Scope`)
holding `default_mtl`, a Karma Material Builder with a red base colour, and
one Output LOP per variant named `OUT_<name>`. The template inserts a
[`th::material_assigner`](#thmaterial_assigner-lop) between the library and
the outputs, so every variant reads the same assigned stage; a look that
needs different materials gets its own branch wired by hand.

Per variant the node fetches `lookdev_subnet` at output index `@ITERATION`, adds
it as a variant of the `lookdev` set on the asset prim, and the Active
Variant is selected on the output. Why the published layer is all `over`s
and looks empty on its own is in
[Where lookdev materials land under the asset](../composition.md#where-lookdev-materials-land-under-the-asset).

Adding a Variants row creates `OUT_<name>` in `lookdev_subnet` with output
index = row index. **Only the first output is wired for
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

**Retired.** Hidden from the TAB menu and the catalog since v1.80.0; use
`th::create_asset_model`, which does the same job with USD variants. It stays
installed so scenes that already contain one still load and cook.

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
[`sops/import_model.py`](../../python/tumblepipe/pipe/houdini/sops/import_model.py).

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

Load one published rig and add it to an APEX scene. The building block
[`th::import_rigs`](#thimport_rigs-sop) makes a chain of, one per row: add
rigs with that node. Hidden from the TAB menu (and the Asset Browser's
Nodes; `startup.UTILITY_NODE_TYPES`); placed nodes load and
work as before, and scripts still create it by name.
Assets only: `from_context` inside a shot workfile resolves to nothing.
Source: [`otls/sop_th.import_rig.1.0`](../../otls/sop_th.import_rig.1.0/th_8_8Sop_1import__rig_8_81.0/DialogScript),
[`sops/import_rig.py`](../../python/tumblepipe/pipe/houdini/sops/import_rig.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The asset (button opens *Select Asset*; picking one imports) |
| Channel | `default` | Which channel's rig export |
| Version | `latest` | `latest` (the newest finished rig export; a failed export's folder is passed over) or a `v####` found under `export/<asset>/<channel>/rig/`; the label shows the version that resolved |
| Instances | `1` | Copies to add (1–10) |

**Import** loads
`export/<asset>/<channel>/rig/v####/<category>_<asset>_rig_v####.bgeo.sc`
through a File SOP, clears the dive network and rebuilds it: one
`apex::sceneaddcharacter` per instance, chained from the node's **first
input** — the APEX scene you are adding to — with *Character Name* the
asset name (`hero`) or, for several instances, `hero0`, `hero1`, …, each
preceded by a [`th::rename_packed`](#threname_packed-sop) rewriting
`/<category>/<asset>/*` to `/<category>/<asset><n>/*`. Inside
`th::animate`, the node's `place` Python SOP, after the dive, starts each
character it added where layout placed it: the world transform of
`/<category>/<asset><n>` on the shot stage at the start of the shot (the
playbar start; in a Multi's workfile, the member's own first frame)
(`th::animate`'s `IN_stage_start`, which keeps the node from turning time
dependent) becomes the rig parms `Main_t` / `Main_r` / `Main_s` of TumbleRig's
top `Main` control. It reads the placement as it cooks, so a layout
published later moves the character, and a workfile imported before the
placement was read picks it up, without pressing **Import** again. It
recooks whenever the shot stage above `th::animate` changes (a camera
moved, say), and looks up the shot's start once per cook: up to 1.88 it
looked it up once per character, reading the workfile's `context.json` on
the project drive each time, about 3.5 s per upstream edit in a shot of
eight rigs. The animated output is then world space, which is what `th::animate`'s
zero_transforms expects. A rig without a `Main` control warns and starts
at the origin. A character a [`th::layout_keys`](#thlayout_keys-sop) row
copies `Main` keys to is left unplaced: those keys place it. When it cannot
import it bypasses itself and passes the input through, with the comment
*Bypassed: No asset selected* / *No versions available* / *Rig file not
found*; on success *Imported: <asset>* and the version.

Inside `th::animate`, the characters must be the copies the shot stage
holds: each lands on the stage prim of its name. The stage's copies of the
asset are the active children of `/<category>` whose pipeline metadata
names it (the source a Duplicate LOP deactivates is not one; a prim with no
metadata, from an inline import, counts by its name), whatever the
channel. When they differ (Instances set by hand to 3 while the shot's
scene has one Roommate, say), a character the stage lacks would land as a
new prim at the origin with no lookdev and a stage copy no character is
named for would stay unanimated, so the node warns, naming both sides:

> Roommate: the rig import makes 3 characters (Roommate0, Roommate1,
> Roommate2), the shot stage has 1 copy (Roommate). Roommate0, Roommate1,
> Roommate2 are not on the stage: they start at the origin with no lookdev.
> Roommate on the stage is not animated. Press Set Scene Copies on
> th::import_rigs to set the shot's scene to 3 copies of Roommate, or set
> its instances in the Entity Editor, then press Add Rigs from Scene on
> th::import_rigs.

It is a warning only; the rig still imports and cooks. **Import** adds it
to the comment, under *Imported*, and to the status bar, and the `place`
SOP inside raises it as a cook warning whenever the stage changes. Houdini
shows a warning only on the node that raised it, not on the subnet or
asset around it, so the comment is where to look. The fix is upstream: set
the asset's instances on the shot's scene (the Entity Editor's scene
assets) so the shot stage has the copies, then **Add Rigs from Scene** on
`th::import_rigs`, which sets each row's Instances from the stage. When
the stage has *more* copies than the rig makes, Add Rigs from Scene alone is the fix,
and the warning's last sentence says only that.

**Set Scene Copies** (beside **Import**) does the upstream fix in one
click when the rig makes more characters than the stage has copies.
[`sops/scene_copies.py`](../../python/tumblepipe/pipe/houdini/sops/scene_copies.py):

1. It finds the shot from the `th::import_shot` up the inputs of the
   `th::animate` the node sits in, and the scene (Root) the shot uses
   (set on it or inherited from its sequence).
2. It asks first: a dialog names the shot, its Root, the asset and the
   old -> new count (and, when the count comes from a parent Root, that
   the shot's Root gets its own entry), and the other shots using that
   Root, since every department reads it. Cancel writes nothing.
3. It writes the count into the Root through `config.scene`, the store
   the Entity Editor writes: the Root's own entry keeps its channel and
   place; an asset only a parent Root lists gets an own entry, which wins
   over the parent's. A parent Root is never written. An asset the Root
   does not list at all is refused (its stage copies come from somewhere
   else; adding it is the Entity Editor's call).
4. It stages the Root as the Entity Editor's Apply does
   (`root_staging.sync_roots` for the shot), re-imports the
   `th::import_shot` so the stage holds the new copies, and imports again
   (inside `th::import_rigs`, that node's **Add Rigs from Scene** runs once the click
   is done). A `th::import_shot` pinned to a version keeps the copies it
   was built with, and the status bar says to set it to `latest`.

When the Root already lists the count (the stage is only stale), nothing
is written and no dialog shows: it re-stages, re-imports and imports.

## `th::import_rigs` (SOP)

Several rigs in one node, one row each, with a version pin per row.
`_TumblePipe/pipeline`; the animation shot template places one inside
`th::animate`'s dive ([`th::animate`](import-and-export.md#thanimate-lop)).
Source: [`otls/sop_th.import_rigs.2.0`](../../otls/sop_th.import_rigs.2.0/th_8_8Sop_1import__rigs_8_82.0/DialogScript),
[`sops/import_rigs.py`](../../python/tumblepipe/pipe/houdini/sops/import_rigs.py).

**Discovery** finds the rows on a USD stage, or adds rigs you pick:

| Label | Default | What it does |
|---|---|---|
| Rig Sync | Follow Stage | **Follow Stage**: the rows follow the stage entering `th::animate` (see [Follow Stage](#follow-stage) below). **Manual**: the rows change only when you change them, as before Rig Sync. There is no project-level default; each node keeps its own |
| Stage | *(empty)* | LOP node whose stage **Add Rigs from Scene** reads. Empty: the stage fed into the `th::animate` this node sits in; outside one, it says to set Stage |
| Add Rigs from Scene | | Replaces the rows with one per asset whose prims are tagged `animatable` ([the stamp](../composition.md#instanceable-copies-and-animatable-assets)) and that has a rig published, **Instances** = its tagged prims, then imports. A row the stage still animates keeps its Channel and Version; a new row takes the channel the shot imports the asset in when that has a rig, else the first one that does, on `latest`. Animatable assets with no rig are named in the comment, *Animatable, no rig published: Ball*. On Follow Stage it is **Sync Now**: the rows follow the stage at once (rows are kept, never replaced; see below). Formerly labelled **Discover**; the parm is still `discover`, so scripts and saved scenes are unchanged |
| Browse Rigs... | | On Follow Stage: **Add Characters** (below); the picker lists only assets with a rig published. On Manual: opens the asset picker (*Add Rigs*) in multi-select mode, as `th::import_assets`' top [Browse...](import-and-export.md#thimport_assets-lop) does: click, **Ctrl-click**, **Shift-click** or **Select all in view**. **Select** adds a row per picked asset that has a rig published and no row yet, in the picker's order, on its `default` channel (else the first channel with a rig), `latest`, one instance, then imports, as **Import** does. Rows already there are left as they are. The status bar says how many were added and skipped and names picks with no rig, e.g. *rigs: Added 2 rigs, skipped 1 already in the list. No rig published: Tree.* Cancel changes nothing |
| Add Held to Stage | | Follow Stage: every row held because its asset is not on the stage (see below) gets the asset added, through the `th::import_assets` `th::animate` manages, then the rows follow |

An asset is animatable when its **Animatable** box is on or it has a rig
published in any channel (the Entity Editor's *No, rigged*), so a rigged
character is found with the box off and a prop needs the box and a rig.

**Rig imports** is a multiparm; each row has:

| Label | Default | What it does |
|---|---|---|
| **Browse...** + label | *(empty)* | The row's asset: **Browse...** re-picks it, then imports, as **Import** does (Cancel changes nothing). No `from_context` here. The picker lists every asset; an asset another row already imports is not refused there, but the row then errors ("already imported by another row"): remove one row or raise its Instances |
| Channel | `default` | Per row; an invalid value snaps to the first channel |
| Version | `latest` | Per row, `latest` or `v####`; the label shows what resolved |
| Instances | `1` | Copies of that rig (1–10). On Follow Stage the stage sets it; an edit goes to where the copies come from (see below) |
| Skip | off | Follow Stage: the row stays (and is kept on every sync) but imports no rig; the comment lists it under *Skipped*. Add Characters on its asset turns it off |

**Import** rebuilds the dive network as a chain of `th::import_rig` nodes,
one per row, each pinned to the row's channel and version (`latest`
resolves per rig at import time), and comments *Imported: N rigs*. A
`latest` row whose asset has no rig published is skipped and named on a
second line, *No rig published: Tree, Rock*; a pinned version must exist.
With nothing to import the node bypasses itself with *Bypassed: No rigs
configured*. A row whose characters are not the copies the shot stage
holds (Instances set by hand, not from the shot's scene) adds its
[`th::import_rig` warning](#thimport_rig-sop) to the comment, once per
asset, and to the status bar. Set the instances on the shot's scene in the
Entity Editor and press **Add Rigs from Scene**: the rows then take their
Instances from the stage.

**Set Scene Copies** (beside **Import**) does that in one click for every
row whose Instances makes more characters than the stage has copies:
after one confirm listing the shot, its Root and each asset's old -> new
count, it writes the rows' Instances into the shot's Root, stages it,
re-imports the shot's `th::import_shot` and runs **Add Rigs from Scene**,
so every character lands on a stage copy with its lookdev. See
[`th::import_rig`](#thimport_rig-sop) for what it writes and what it
refuses. Rows the stage already matches, or has more copies of, are left
alone (Add Rigs from Scene is their fix).

The animation shot template fills the rows when the workfile is created,
the way Add Rigs from Scene does from the imported shot.

### Follow Stage

On **Follow Stage** (the default) the rows follow the stage entering
`th::animate`: one row per asset on it that is animatable and has a SOP
rig, **Instances** its copies, and each character named after its stage
copy (`hero0`, `hero2` when a layout edit left a gap; a hidden `names`
parm on the row and on its `th::import_rig` carries them). The planner is
[`sops/rig_sync.py`](../../python/tumblepipe/pipe/houdini/sops/rig_sync.py)
(pure), the Houdini side
[`animate_sync.py`](../../python/tumblepipe/pipe/houdini/animate_sync.py).

The rows sync, and import again only when they changed:

- at the end of **Import** on a `th::import_shot` or `th::import_assets`
  that feeds the `th::animate`;
- when the workfile opens, after its imports refresh;
- on **Add Rigs from Scene** (Sync Now), **Add Characters** and Import.

A cook never changes the rows: when the stage changed since the last sync
(the shot re-imported by hand, an input rewired), the `place` SOP warns
*The shot stage changed since th::import_rigs last synced its rows*, and
Sync Now picks it up. A sync with nothing moved sets no parm, so it never
marks the workfile as changed. In a Multi workfile each member's
`th::animate` follows its own `th::import_shot`.

What a sync keeps:

- A row's **Channel**, **Version** pin and **Skip** stay.
- A row whose asset left the stage is **held** (kept, imported, named in
  the comment *Not on the stage (held)*) when it was there before Rig
  Sync or a character of it has keys in a Scene Animate; otherwise it
  goes. **Add Held to Stage** puts the asset back, or remove the row.
- A character with keys is never dropped: when its copy leaves the stage
  the row keeps the name, *Keyed, not on the stage (kept)*.
- When one copy becomes several (`hero` -> `hero0`, `hero1`: a layout
  duplicated an asset animated alone), `hero`'s keys move to `hero0`, the
  copy left where `hero` was. In every Scene Animate of the dive, all of
  `hero`'s keys (its own and those on each layer, channels exactly as they
  were) and anything Animate saved of the character become `hero0`'s;
  `th::animate`'s **Characters** row and the `th::layout_keys` row for
  `hero` are renamed `hero0`. The comment says *Keys moved to the first
  copy (one copy became several): hero -> hero0*, and a line under it
  names the Scene Animates the keys moved in. The rows and the keys move as one undo step:
  **Edit > Undo** puts both back (undo with Animate closed: Animate writes
  what it has loaded back when you leave it). A viewer with Animate open on
  that Scene Animate leaves Animate before the keys move, so they are not
  written back over; the comment says so, and Enter in the viewer opens it
  again. Keys are never overwritten: when `hero0`
  has keys of its own already, nothing moves, `hero` stays on the row
  beside `hero0` and `hero1`, and the comment says *hero kept beside
  hero0*; copy what `hero0` needs with Copy Animation, then remove
  `hero`'s keys. The move happens only in a sync (an import, the workfile
  opening, Sync Now), never in a cook, and a sync with nothing to move
  writes nothing.
- An animatable asset with no rig is named, *Animatable, no rig published*;
  one whose rig is a LOP rig is named too, as `th::import_layer` brings it.

**Add Characters** (**Browse Rigs...** here, or **Add Characters...** on
`th::animate`) picks assets with a rig published:

- one already on the stage gets its row (or, skipped, is un-skipped);
- one not on the stage is added to it, through a `th::import_assets`
  `th::animate` manages just above itself (made on first use, named
  `<animate>_characters`, on `latest`), and its row follows;
- with `th::animate`'s **Add Assets for Rigs** off, only the rig row is
  added (held, with its asset not on the stage), as Manual does;
- one in the shot's scene but missing from the stage says to re-import the
  shot.

**Instances** on Follow Stage goes to where the copies come from: an
asset of the managed `th::import_assets` sets its row there; one from the
shot's scene (Root) runs **Set Scene Copies** for it (with its confirm);
one a department upstream publishes is refused, naming the department to
change it in. The row's value then shows the stage's again.

An existing node (from before Rig Sync) switches to Follow Stage on its
first sync: rows matching the stage change nothing, others are held and
the comment says *Rig Sync is now Follow Stage* with what it held. The
stage is never changed by a sync; headless and farm runs never write the
Root.

Gotchas:

- On Manual, **Browse Rigs...** gives a new row one instance, whatever the shot's
  scene has; if the stage holds more copies, the row warns (see
  [`th::import_rig`](#thimport_rig-sop)) until **Add Rigs from Scene** or
  **Set Scene Copies** matches them.
- A row with an empty entity raises on Import, naming the row; it is never
  filled with the first asset.

## `th::layout_keys` (SOP)

Layout's keys for the animation, copied per character, with the project
setting *Layout in Animate* on. The animation shot template places it in
`th::animate`'s dive, between `th::import_rigs` (and the camera rig, rebuilt
with the settings layout published) and the APEX Scene Animate. Layout
publishes its keys beside its layer (see
[`th::export_layer`](import-and-export.md#thexport_layer-lop)); this node
copies them into the Scene Animate's own animation, the data the animator
keys into. From then on they are the animator's keys: edit, replace or
clear them. A newer layout publish changes nothing until a row is copied
again; layout is not a layer the animation adds to.
Source: [`otls/sop_th.layout_keys.1.0`](../../otls/sop_th.layout_keys.1.0/th_8_8Sop_1layout__keys_8_81.0/DialogScript),
[`sops/layout_keys_rows.py`](../../python/tumblepipe/pipe/houdini/sops/layout_keys_rows.py).

| Label | Default | What it does |
|---|---|---|
| Discover | | Adds a row per character the newest layout keys have, pinned to that version. Rows already there keep their choices. A character not in the incoming APEX scene (a prop layout animated with a control) comes in off |
| Copy All from Layout | | Copies every row that is on, as one undo step. A new animation workfile runs it once |
| Channel | `default` | The layout export channel |

**Rows** is a multiparm; each row has:

| Label | Default | What it does |
|---|---|---|
| On | on | Off: Copy All leaves the character alone |
| Layout Character | | The character's clip in layout's keys |
| Character | | The animation character the keys go onto (the layout name; `th::import_rig` names its copies the same) |
| Layout Version | `latest` | `latest` (the newest layout version that wrote keys) or a pinned `v####` |
| Controls | `*` | Which controls' channels are copied: space-separated globs on the control name (`Main_tx` is `Main`), `^` excludes. `Main` copies only the placement |
| Frame Range, First, Last | off | Only layout's keys in that range |
| Offset | `0` | Moves the keys by this many frames, after a cut change |
| Copy from Layout | | Copies the row into the Scene Animate |

With **Controls** `*` a copy replaces the character's whole entry in the
Scene Animate. With a narrower pattern it replaces only those controls'
channels and keeps the animator's keys on every other control, so `Main`
takes a new layout placement without touching the performance. The node's
comment lists what Copy All copied and from which version; each row's
copied version is kept on the node (user data
`tumblepipe_layout_keys_copied`).

The node passes the APEX scene through unchanged. While a row that copies
`Main` is on, `th::import_rig` does not place that character at layout's
first frame: its `Main` keys place it.

## `th::export_rig` (SOP)

Publish the rig department. `_TumblePipe/pipeline`; the rig template wires
one from `import_model` through `promote_name` and `enumerate` inside `rigging`. It is a sink — no output
connector — so put it at the end of the rig.
Source: [`otls/sop_th.export_rig.1.0`](../../otls/sop_th.export_rig.1.0/th_8_8Sop_1export__rig_8_81.0/DialogScript),
[`sops/export_rig.py`](../../python/tumblepipe/pipe/houdini/sops/export_rig.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | Plain menu of `from_context` plus every asset. `from_context` outside an asset workfile resolves to nothing and the export refuses rather than guessing. In an asset Multi's rig workfile, set it to the member this rig belongs to |
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

### LOP rigs (APEX characters on the stage)

A rig can also be built **on the stage** for Houdini's APEX Animate LOP
(SOP Import APEX Scene in Character mode, APEX SOP Rig Builder, Configure
APEX Rig) and published as a USD layer: end the rig workfile's stage in a
[`th::export_layer`](import-and-export.md#thexport_layer-lop) with
Department `rig`. **Publish** (and the node's **Export**) collects it
alongside any `th::export_rig` (before, a rig workfile only collected
`th::export_rig` and said there were no export tasks).

Neither needs the rig workfile: any workfile of the asset collects its
`th::export_rig` and its Department `rig` exports, so model, lookdev and rig
can live in one file and publish in one run, in pipeline order. Up to
TumblePipe 1.97.1 only the `rig` workfile collected `th::export_rig`.

- Put each character at or below the asset prim (`/CHAR/Ryan`): set the
  SOP Import APEX Scene's **Import Path Prefix** to it. Its default `/$OS`
  is refused by the `rig_structure` validator, since no shot composes it.
- The characters' rig, skeleton and shape libraries are Houdini geometry
  the prims point at (`houdini:apex:character:files`). The export copies
  them into `apex/` in the version folder, whatever the APEX LOPs' save
  paths say (their default `$HIP/usd` is the work area), so the version
  stands on its own:
  `export/<asset>/<channel>/rig/v####/<entity>_<channel>_rig_v####.usd`
  plus `apex/*.bgeo.sc` and `context.json`.
- `rig` is not a render department, so the layer does not compose into the
  staged asset. To animate it, bring it in with
  [`th::import_layer`](import-and-export.md#thimport_layer-lop) (Entity the
  asset, Department `rig`) and put an APEX Animate LOP after it; it finds
  the characters by their `HoudiniApexCharacterAPI` schema.
- Publish a rig one way per channel. When the newest rig version is a LOP
  rig, `th::import_rig`, `th::import_rigs`, `th::animate` and
  `th::layout_rigs` (which load SOP rigs) treat the asset as having no rig,
  and a row pinned to such a version says to use `th::import_layer`.

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
[`lops/material_assigner.py`](../../python/tumblepipe/pipe/houdini/lops/material_assigner.py).

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
| Purpose | Render | Binding purpose: All (one binding that every purpose falls back to), Render (`full`) or Preview |

Write Primitives against the workfile's asset rather than its literal path —
`` `@entity_path`/geo/Eyeballs `` instead of `/CHAR/Mom/geo/Eyeballs` — and a
row keeps working after the asset is renamed or duplicated (see
[Workfiles → Context options](../asset-browser/workfiles.md#context-options)).

Gotchas:

- A row's Primitives is empty after Initialize; nothing is bound until you
  fill it in.
- **Update Materials Paths** leaves a row whose material name is no longer
  on the stage as it is, and prints a warning to the console naming it.
- With no stage (nothing wired in, or an input that fails to cook) the
  Material menu is empty and both buttons stop with an error saying so.

## `th::material` (LOP)

One MaterialX material and its binding in one node: a Material Library
holding a `material` subnet (a standard surface with displacement and
geometry colour), then an Assign Material onto the prims the node
receives. `_TumblePipe/lookdev`. Dive in to edit the shader.
Source: [`otls/lop_th.material.1.0`](../../otls/lop_th.material.1.0/th_8_8Lop_1material_8_81.0/DialogScript).

| Label | Default | What it does |
|---|---|---|
| Material | `$OS` (the node's name) | The material prim's name |
| Assignment | `` `lopinputprims('.', 0)` `` | Prims to bind: by default every prim the input modified |
| Material Path | `` `lopinputprim('.', 0)`/mtl/ `` | Where the material is written: by default `mtl/` under the *first* prim the input modified |
| Purpose | *(empty)* | Binding purpose, as Assign Material's |

Wire a SOP Import (or any node that authored the geometry) in front and the
defaults bind its prims to a material under `<geo>/mtl/`.

**Stacking.** A th::material reports only the prims it assigned to as
modified, not the material it made, so a chain of them each reads the
geometry: three in a row on `/asset/geo/body` write
`/asset/geo/body/mtl/<name>` three times, and the last binding wins. Before
this, the second node's defaults picked up the first one's material prim,
bound a material onto it, and wrote to a path that was no prim.

Gotcha: after a node that modified several prims (a merge), every one of
them is bound, to one material under the first one's `mtl/`. Set Material
Path to keep it elsewhere.

## `th::import_texture` (LOP)

References a published [texture set](../asset-browser/textures.md)
version's MaterialX material onto the stage. `_TumblePipe/lookdev`. Dropping
a texture set card into LOPs makes one. Bind it with
[`th::material_assigner`](#thmaterial_assigner-lop) or an Assign Material.
Source: [`otls/lop_th.import_texture.1.0`](../../otls/lop_th.import_texture.1.0/th_8_8Lop_1import__texture_8_81.0/DialogScript),
[`import_texture.py`](../../python/tumblepipe/pipe/houdini/lops/import_texture.py).

| Label | Default | What it does |
|---|---|---|
| Owner | Asset | Whose sets to list: an asset's, or the project library's |
| Entity | `from_context` | The asset, when Owner is Asset |
| Texture Set | *(empty)* | The set to read, from the published sets |
| Version | the newest, when the set is picked | The pinned version. A newer publish changes nothing until **Update** |
| Status | | The pinned version, and whether a newer one is published |
| Material Path | `` `lopinputprim(".", 0)`/mtl/`chs("set")` `` | Where the material is referenced: `mtl/<set>` under the input's first prim, as `th::material` puts materials |

The reference reads the version's layer as `$TH_EXPORT_PATH/...`, so the scene
follows the project. A shared [library](../asset-browser/libraries.md)'s set
dropped from the browser is copied into the project library first, and the
node is pinned to that copy (Owner: project library).

## `th::export_texture` (COP)

Publishes Copernicus layers as the next version of a
[texture set](../asset-browser/textures.md): the images, a MaterialX material
reading them, and `context.json`. `_TumblePipe/lookdev`. It has one input per
map: **basecolor**, **roughness**, **metalness**, **normal**, **height**, **ao**
and **opacity**. Only connected inputs are written. The base colour passes
through, so the node can sit inline.
Source: [`otls/cop_th.export_texture.1.0`](../../otls/cop_th.export_texture.1.0/th_8_8Cop_1export__texture_8_81.0/DialogScript),
[`export_texture.py`](../../python/tumblepipe/pipe/houdini/cops/export_texture.py).

| Label | Default | What it does |
|---|---|---|
| Owner | Asset | Publish into an asset's sets, or the project library |
| Entity | `from_context` | The asset, when Owner is Asset |
| Texture Set | *(empty)* | The set's name: letters and digits, not a Windows device name (`CON`, `AUX`, `COM1`...). A name that differs from an existing set only by case is refused |
| Format | OpenEXR | The file format every map is written in (EXR, PNG, TIFF, JPEG) |
| Status | | The newest published version and which inputs are connected |
| Publish | | Write the connected inputs and publish them |

The base colour is converted into the colour space it is tagged with (`sRGB -
Texture`, or `ACEScg` for EXR). Every other map is written raw. Publishing runs
in the session; there is no farm job for it yet.

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
