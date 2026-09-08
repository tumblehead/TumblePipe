# Importing and exporting

The nodes that move published layers into and out of a workfile. All of them
share the Entity / Department / Channel / Version block described in
[Pipeline nodes → Shared concepts](index.md#shared-concepts); this page only
repeats what a specific node does differently.

None of these HDAs carries built-in help text, so this page is the reference.

## `th::import_asset` (LOP)

Reference one published asset into the stage. Tab menu
`_TumblePipe/pipeline`. Drop it in any LOP network — a shot workfile, or
another asset's workfile to build a
[nested asset](../composition.md#two-ways-to-build-multi-asset-environments).
Only assets: `from_context` inside a shot workfile resolves to nothing.
Source: [`otls/lop_th.import_asset.1.0`](../../otls/lop_th.import_asset.1.0/th_8_8Lop_1import__asset_8_81.0/DialogScript),
[`lops/import_asset.py`](../../python/tumblepipe/pipe/houdini/lops/import_asset.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The asset to import (button opens *Select Asset*) |
| Channel | `default` | Which channel's staged build to load |
| Version | `latest` | `latest` / `current` / `v####` of the staged build; the label beside it shows the version that resolved |
| Exclude departments | *(none)* | Checkable list; ticking re-imports immediately |
| Import Mode | Reference | Reference (metadata + layerbreak) or Inline (baked into your export) |
| Translate / Rotate / Scale / Uniform Scale | identity | Placement of the asset prim; bound to the viewport transform handle |

**Import** re-runs the node. It also runs automatically after picking an
entity, or changing Exclude departments or Import Mode.

What happens on import: the node resolves `export:/<asset>/_staged/<channel>`
to a staged file, sublayers it, places it, and writes the pipeline metadata
(`uri`, `instance`, `variant`, `inputs`) onto the asset's root prim — and
onto every sub-asset the staged `context.json` tracks, re-creating their
multi-instance copies as it goes. The prim lands at the asset's own path
(`/char/mom` for `entity:/assets/char/mom`). Inside a shot workfile the
metadata also records the shot and department as provenance.

Gotchas:

- The node bypasses itself with a comment when it cannot import: *No asset
  selected*, *No staged file found* (the asset has never been staged, or the
  channel has no build), *All departments excluded*. Read the comment before
  suspecting anything else.
- Excluding a department only drops what actually composes; a department
  that is not a render layer (`rig`, `blendshape`) is never in a staged file,
  so excluding it changes nothing. Exclusion applies through nesting and is
  never published — [Department exclusion](../composition.md#department-exclusion).
- The placement parms apply to the prim the node wrote; if the node is
  bypassed they apply to nothing.
- Keep Import Mode on Reference unless you mean to bake the asset in;
  see the [dropped-metadata guard](../composition.md#dropped-metadata-guard).

## `th::import_asset` (SOP)

The same node wrapped for SOPs (`_TumblePipe/pipeline` in a SOP network).
It embeds a LOP `import_asset` and mirrors its Entity, Channel, Version and
Exclude departments parms, then brings the result into SOPs through a LOP
Import of the asset's `Mesh` and `Boundable` prims (proxy purpose, animated
time samples at `$FF`).
Source: [`otls/sop_th.import_asset.1.0`](../../otls/sop_th.import_asset.1.0/th_8_8Sop_1import__asset_8_81.0/DialogScript),
[`sops/import_asset.py`](../../python/tumblepipe/pipe/houdini/sops/import_asset.py).

| Label | Default | What it does |
|---|---|---|
| Entity / Channel / Version / Exclude departments | as the LOP | Forwarded to the embedded LOP |
| Unpack To Polygons | on | Unpack the USD packed prims to polygons (adds `path`, `name`, `usdpath`; imports all primvars). Off gives packed USD prims |

There is no Import Mode and no Transform on the SOP version.

## `th::import_assets` (LOP)

Reference several assets in one node, one row each, with an instance count
per row and a single layout transform for all of them. `_TumblePipe/pipeline`.
Use it in a shot, or in a set-style asset's workfile to dress it.
Source: [`otls/lop_th.import_assets.2.0`](../../otls/lop_th.import_assets.2.0/th_8_8Lop_1import__assets_8_82.0/DialogScript),
[`lops/import_assets.py`](../../python/tumblepipe/pipe/houdini/lops/import_assets.py).

**Asset Imports** is a multiparm; each row has:

| Label | Default | What it does |
|---|---|---|
| *(Entity button)* + label | *(empty)* | The row's asset. There is no `from_context` row here — every row is a concrete asset. The picker hides assets already used on other rows |
| Channel | `default` | Per row |
| Version | `latest` | Per row |
| Instances | `1` | Copies of the asset (1–10). Copies are named `<Asset>0…N-1` |

Node-wide: **Exclude departments** (lists the union of all rows' departments),
**Import Mode**, the **Import** button, and a **Layout** folder that is a
full Edit LOP (Primitives, Translate/Rotate/Scale, pivot, Apply / Reset /
Remove Unused Transforms) so you can place the imported prims with the
viewport handles; those edits survive re-imports.

Gotchas:

- Picking an entity on a row does *not* re-import; press **Import**.
- A row with an empty entity is filled with the first unused asset when you
  press Import.
- Copies are instanceable unless the asset is marked `animatable` —
  [Instanceable copies and animatable assets](../composition.md#instanceable-copies-and-animatable-assets).
  Instance count is what the staged `context.json` records, so it survives
  export and re-import.
- Comment reads *Bypassed: No assets configured* or *Imported: N asset(s)*.

## `th::import_shot` (LOP)

Load a shot's staged build into a shot workfile, cut at the workfile's
department. `_TumblePipe/pipeline`; every shot department template starts
with one. Unlike the other nodes it resolves `from_context` from the
workfile's `context.json` for both Entity and Department, and only accepts
shots.
Source: [`otls/lop_th.import_shot.1.0`](../../otls/lop_th.import_shot.1.0/th_8_8Lop_1import__shot_8_81.0/DialogScript),
[`lops/import_shot.py`](../../python/tumblepipe/pipe/houdini/lops/import_shot.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | The shot (button opens *Select Shot*) |
| Channel | `default` | Which channel's staged build |
| Department | `from_context` | The cut: this department **and everything downstream of it** in the shot's pipeline order is excluded, so you compose what is upstream of your own work. The reserved value `none` (type it; it is not in the menu) excludes nothing |
| Version | `latest` | `latest` / `current` / `v####` |
| Include procedurals | off | Currently wired to nothing |
| Load Payloads | on | Currently wired to nothing (payloads always load) |

**Import** runs the node. It also sets the session's FPS and frame range
from the shot (keeping your current frame).

### Layer Stack

After an import, a **Layer Stack** folder appears listing every sublayer the
node loaded, weakest first. Each row is a checkbox named after the layer
(`Root`, a shot department such as `Animation`, or `Asset: <name>`) — untick
it to drop that layer from the stage in this session — followed by the
version that actually composed. Asset rows also carry a **`...`** button
that opens a read-only *Layers — <asset>* breakdown: the asset's department
layers with the version each one contributed and a status (*newer export not
composing*, *exported after this build*, *never exported*, *not a render
layer*), the sub-assets it brings in, and an **Open** button (in this session
or a new Houdini) for any department that has a workfile. The meaning of each
status is in [Seeing what composed](../composition.md#seeing-what-composed).

Gotchas:

- If the shot has never been staged the node loads nothing and says nothing
  — no comment, no bypass. Check the Layer Stack folder is present.
- With Version `latest` the versions shown in the stack are the ones that
  floated in, which can differ from the pins recorded in the build.
- Assets are dropped along with the department that introduced them, so a
  lookdev-only exclusion never removes a model.
- An invisible `Exclude Downstream Of` parm exists for the farm's stage task
  ([The department cut](../composition.md#the-department-cut)); it is not
  something you set by hand.

## `th::import_layer` (LOP)

Sublayer **one department's export of one entity** — asset or shot, any
department, at a channel and version. `_TumblePipe/pipeline`. This is how the
lookdev template brings the model in (`IMPORT_MODEL`), and how you pull
another department's shot export (an FX cache, a set-dress pass) into your
own.
Source: [`otls/lop_th.import_layer.1.0`](../../otls/lop_th.import_layer.1.0/th_8_8Lop_1import__layer_8_81.0/DialogScript),
[`lops/import_layer.py`](../../python/tumblepipe/pipe/houdini/lops/import_layer.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | Any asset or shot |
| Channel | `default` | Which channel's export; the entity's `_shared` layer is sublayered underneath when it has one |
| Department | `from_context` | Whose export to load. `from_context` is **your own** department — almost never what you want on an import |
| Version | `current` | `current` (highest export on disk) or a `v####`. There is no `latest`; the load is always pinned |
| Import Mode | Reference | As on `import_asset` |

Buttons: **Import**; **Open Location** opens the latest export folder in the
file browser.

Gotchas:

- When nothing is found the node's comment reads *NOTHING IMPORTED* with the
  reason, and, if Department is still `from_context`, tells you to pick the
  upstream department you meant. The input passes through untouched.
- Importing a foreign asset's layer tags its root prim like `import_asset`
  would; importing your own entity's or a shot's layer does not.

## `th::export_layer` (LOP)

Publish the department layer. `_TumblePipe/pipeline`; every department
template ends in one (`EXPORT_MODEL`, `export_shot`, …). Everything above it
in the network is what gets published; the node's output carries a
layerbreak so nothing after it leaks into another export.
Source: [`otls/lop_th.export_layer.1.0`](../../otls/lop_th.export_layer.1.0/th_8_8Lop_1export__layer_8_81.0/DialogScript),
[`lops/export_layer.py`](../../python/tumblepipe/pipe/houdini/lops/export_layer.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | Asset or shot to publish |
| Channel | `default` | Channel to publish under |
| Department | `from_context` | Department to publish; menu lists the entity's publishable departments |
| Frame Range | From context | From context (the entity's database range, roll included) or From settings |
| First/Last/Step-Frame, Pre/Post-Roll | `1001 1001 1`, `0 0` | Only with From settings. **Step is ignored** — exports always run every frame |
| Type | Local | Local export, or Farm submission |
| Downstream Exports | *(none)* | Farm only: departments after this one to re-export in the same job |
| Pool / Priority | first pool / `50` | Farm only |
| Batch | `0` | Local only: export in frame chunks and stitch (0 = one pass) |

Buttons:

- **Export** does not export directly — it opens the *Export Layer* dialog,
  the same task dialog as **Publish**, with this node's task pre-enabled. The
  dialog saves the scene first, so the workfile must be saved once already
  (*Cannot determine workfile context. Save the file first.*).
- **Validate** runs the department's validators on the incoming stage and
  reports pass, warnings, or a list of failures.
- **Open Location** opens the latest export folder.

The node embeds a USD ROP whose frame range, output path and Save to Disk
the pipeline drives during an export. Those parms are hidden (from the release after 1.46.0);
they used to show as an **export** folder whose Save to Disk fired the raw
ROP outside the pipeline.

What a local export writes: `export/<entity>/<channel>/<department>/v####/`
with a `<entity>_<channel>_<department>_v####.usd` and a `context.json`
recording every tracked asset on the stage —
[Department exports and staged files](../composition.md#department-exports-and-staged-files).
The node's comment then reads *last export: v#### / time / user*.

Aborts you can hit, in the order they are checked:

- *Frame range could not be determined* — the entity has no range in the
  database; set one, or switch to From settings.
- *channel '…' is not a channel of …* — the Channel parm names something the
  entity does not define, which would silently publish under `default`.
- No stage input connected — the task is **skipped** (not failed) so the
  rest of a multi-channel publish continues.
- *asset(s) on the stage carry no pipeline metadata* — the
  [dropped-metadata guard](../composition.md#dropped-metadata-guard).
  Re-run the import node; or set it to Inline if baking was the intent.
- *composes geometry from path(s) outside the export folder* — usually a LOP
  with *Layer Save Path* enabled;
  [Layer save paths](../composition.md#layer-save-paths-and-export-portability).
- *composes geometry from path(s) that do not exist* — a dangling payload
  or reference; same section.

A **Farm** export bundles the latest workfile and submits a publish job; the
metadata and path guards run on the farm, not before submission.

## `th::export_asset` (LOP)

A self-contained, pipeline-agnostic asset writer (`_TumblePipe/utils`): saves
the prim at **Primitive Path** to **Output File** (default
`$HIP/asset/test_asset/test_asset.usda`) with a chosen **Save Style**, an
optional **Payload Asset** sidecar (via `asset_payload`), and a thumbnail
(**Render to Disk**, **Set Thumbnail As Icon**). **Display** switches the
node's output between the live network and the exported file. It knows
nothing about entities or versions and is not used by any template or
Python code; publish through `th::export_layer` instead.
Source: [`otls/lop_th.export_asset.1.0`](../../otls/lop_th.export_asset.1.0/th_8_8Lop_1export__asset_8_81.0/DialogScript).

## `th::archive` (LOP)

Flatten the whole incoming stage into one `.usd`, with every referenced
asset and texture localized into an `extra/` folder beside it — for handing
a scene to someone outside the pipeline. `_TumblePipe/pipeline`.
Source: [`otls/lop_th.archive.1.0`](../../otls/lop_th.archive.1.0/th_8_8Lop_1archive_8_81.0/DialogScript),
[`lops/archive.py`](../../python/tumblepipe/pipe/houdini/lops/archive.py).

| Label | Default | What it does |
|---|---|---|
| Archive Path | *(empty)* | Destination `.usd`; required |
| Frame Range | Single Frame | Single Frame or From Settings |
| Start/End Frame, Pre/Post Roll | `$FSTART`/`$FEND`, `0 0` | Enabled with From Settings |

**Export Archive** writes the file. Gotcha: *Single Frame* exports the
**Start** frame shown in the (disabled) range field, not the current frame.

## `th::cache` (LOP and SOP)

Versioned caches in the pipeline's cache folders: the LOP writes USD
(`lops_cache/<name>/v####/v####.usd`), the SOP writes
`cache/<name>/v####/v####.$F4.bgeo.sc`. Both in `_TumblePipe/pipeline`.
The two share parms and behaviour; the SOP adds a **Simulation** toggle
(on by default, forces sequential cooking).
Source: [`otls/lop_th.cache.1.0`](../../otls/lop_th.cache.1.0/th_8_8Lop_1cache_8_81.0/DialogScript),
[`lops/cache.py`](../../python/tumblepipe/pipe/houdini/lops/cache.py),
[`otls/sop_th.cache.1.0`](../../otls/sop_th.cache.1.0/th_8_8Sop_1cache_8_81.0/DialogScript),
[`sops/cache.py`](../../python/tumblepipe/pipe/houdini/sops/cache.py).

| Label | Default | What it does |
|---|---|---|
| Entity | `from_context` | Whose cache folder to use |
| Department | `from_context` | Which workfile's cache folder; the menu lists **all** the entity's departments, so a node can load a cache another department wrote |
| Name | `$OS` | Cache name (menu lists existing names) |
| Version | *(empty)* | Version to load; **Latest** button fills in the newest |
| Location | Project | `project:` or `proxy:` storage |
| Frame Range | Single Frame | Single Frame, Playback Range, **From Entity (Database)** (the entity's range plus roll), or From Settings |
| First/Last/Step-Frame, Pre/Post-Roll | `1001 1001 1`, `0 0` | With From Settings |

**Cache** writes the next version and loads it; **Load** loads the chosen
version. The node turns green when the loaded version is the newest, orange
otherwise, and its comment says which is which. A single-frame cache is
time-shifted so it holds on that frame.

Gotchas:

- With **From Entity (Database)** and no authored range, the node refuses
  with the comment *No frame range: the entity has none authored in the
  database.*
- An empty Version on **Load** picks the **oldest** version, not the newest —
  press **Latest** first.
- A Department the entity does not have empties the menus, and Cache/Load
  silently do nothing.
- Caches publish **by reference**: the export leaves them where they are
  instead of copying them into the version folder, and a deleted cache
  version fails the export as a dangling arc —
  [Layer save paths](../composition.md#layer-save-paths-and-export-portability).

## `th::asset_payload` (LOP)

Wrap the incoming asset prim in a payload: a new layer saved as
`$HIP/payload_<prim path>.usd` with the prim as its default prim, re-composed
onto a fresh stage as a payload arc at the same path. `_TumblePipe/utils`.
Parms: **Color** and **Set Color** for the payload's display colour. On
publish `th::export_layer` copies the sidecar into the version folder so the
arc travels with the layer; why the path is keyed on the prim is explained in
[Layer save paths](../composition.md#layer-save-paths-and-export-portability).
Source: [`otls/lop_th.asset_payload.1.0`](../../otls/lop_th.asset_payload.1.0/th_8_8Lop_1asset__payload_8_81.0/DialogScript).

## `th::layer_split` (LOP)

Publish a `_shared` layer: content that every channel of an entity should
compose, written once to `export/<entity>/_shared/<department>/v####/`.
`th::export_layer` sublayers it into each channel's export whenever the
entity has more than one channel, and `th::import_layer` loads it under the
channel layer. `_TumblePipe/pipeline`. Same Entity / Department / Frame
Range parms and Validate / Export / Open Location buttons as `export_layer`
(the Export dialog is titled *Export Shared Layer*; in the Publish dialog it
appears as an *Export Shared* task when wired upstream of an export node).
Unlike `export_layer`, the frame step **is** honoured, and none of the
metadata or path guards run.
Source: [`otls/lop_th.layer_split.1.0`](../../otls/lop_th.layer_split.1.0/th_8_8Lop_1layer__split_8_81.0/DialogScript),
[`lops/layer_split.py`](../../python/tumblepipe/pipe/houdini/lops/layer_split.py).

## `th::sop_modify` (LOP)

Pull prims from the stage into an editable SOP network (dive in), then cache
named exports of the result to `$HIP/sop_modify/<name>/v####/<export>/` and
load them back as USD value clips over the incoming stage. `_TumblePipe/pipeline`;
the cfx shot template uses it.
Source: [`otls/lop_th.sop_modify.1.0`](../../otls/lop_th.sop_modify.1.0/th_8_8Lop_1sop__modify_8_81.0/DialogScript),
[`lops/sop_modify.py`](../../python/tumblepipe/pipe/houdini/lops/sop_modify.py).

| Label | Default | What it does |
|---|---|---|
| Import Prim Paths | *(empty)* | Prim pattern brought into the SOP network |
| Bypass Cache | on | Greys out the Caching folder. It does not change what the node outputs |
| Cache Name / Version / Latest | `$OS` / *(empty)* | As on `th::cache` |
| Frame Range | Playback Range | Playback Range or From Settings |
| Exports (multiparm) | 1 row | Per export: Export Name, Prim Path, the Point/Vertex/Primitive/Detail attributes to **keep**, Simulation toggle |

**Cache** writes every export and then loads it; **Load** re-attaches the
chosen version as clips. Gotchas: the cache lives under the workfile (`$HIP`),
not in the pipeline's cache folders, so it is not a substitute for
`th::cache`; the Cache Name menu does not list existing caches; an empty
Version loads the oldest.

## `th::set_kinds` (LOP)

Type a **Primitive** path and press **Apply**: the node fills its dive
target with Configure Primitive nodes that set the first path level to kind
`assembly`, the second to `component`, every deeper level (and everything
under the path) to a `Scope` with no kind. `_TumblePipe/utils`. Until Apply
is pressed with a path, nothing flows through the node.
Source: [`otls/lop_th.set_kinds.1.0`](../../otls/lop_th.set_kinds.1.0/th_8_8Lop_1set__kinds_8_81.0/DialogScript),
[`lops/set_kinds.py`](../../python/tumblepipe/pipe/houdini/lops/set_kinds.py).

## `th::shot_sequencer` (LOP)

Wire several shot stages into its inputs and it plays them back to back from
**Sequence Start Frame** (default `1001`), reading each input's
`startTimeCode`/`endTimeCode` to lay them out (`1001–1100` is assumed for a
stage that has none). `_TumblePipe/utils`.

| Label | Default | What it does |
|---|---|---|
| Mode | Root Switch | *Root Switch*: the active shot at its own prim paths. *Shot Primitives*: every shot grafted under `/shot_N`, only the active one active. *Sequence Variants*: shots as variants of a `shot` variant set on `/sequence` |
| Sequence Start Frame | `1001` | Where the sequence starts |
| Reload Shot Info | button | Re-read the inputs' time codes after they change |

Source: [`otls/lop_th.shot_sequencer.1.0`](../../otls/lop_th.shot_sequencer.1.0/th_8_8Lop_1shot__sequencer_8_81.0/DialogScript).

## `th::animate` (LOP)

A dive-in SOP network (`anim_dive`) imported as animated USD over the
incoming stage: topology animated, `P N visibility bounds Cd Alpha` only, no
material binding, transforms zeroed, and kinds set by depth
(assembly / component / scope, as `set_kinds` does). No parms.
`_TumblePipe/pipeline`. The animation shot template places one as
`animate_shot` with `th::import_rigs` and the APEX scene-animate nodes
inside its dive target; see [Building assets → `th::import_rigs`](assets.md#thimport_rigs-sop).
Source: [`otls/lop_th.animate.2.0`](../../otls/lop_th.animate.2.0/th_8_8Lop_1animate_8_82.0/DialogScript).
