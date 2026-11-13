# Export and publish

How a workfile's stage becomes a versioned, published layer — from the
toolbar quick actions, through the process dialog, to what lands on disk and
what refuses to.

## The quick actions

The Asset Browser's toolbar carries these quick actions (the **Open Timeline** button, which plays the selected shot's sequence in Cinedex, is described in the [toolbar table](index.md#quick-actions)); all but **Entity Editor** act on
the **currently loaded** `.hip`. The toolbar label is the loaded hip's filename; every action
first resolves the scene's pipeline context (entity, department, version) and
tells you when there is none.

| Button | Tooltip | What it does |
|---|---|---|
| **Save** | Save current scene | Saves the scene as the **next workfile version** of its own context (never in place). With *Ask for a version note on save* on (the default), a **Save Version** dialog asks "Note for the next `<dept>` version of `<entity>` (after `vNNNN`) — optional:". Cancel there aborts the save without burning a version. Status line: `Saved <file>`. |
| **Publish** | Publish exports | Opens the process dialog (title **Process: Publish**) with every export and build task in the scene for its entity, **whatever department each export node publishes into**: a model workfile that also holds the lookdev and rig exports publishes all three. See below. |
| **Render** | Farm Submit, with the current scene's Render ticked: publish, playblast and render any shots or assets | Opens the [Farm Submit dialog](submit-jobs.md) with the loaded scene's entity's **Render** cell ticked — or every member's, for a Multi workfile — and the Playblast / Render cut pinned to the workfile's department. Tick publish, playblast and render cells for any other shots or assets there. With no pipeline workfile loaded it opens with nothing ticked. |
| **Entity Editor** | The project's assets and shots as spreadsheets: add, edit and retire them | Opens the [Entity Editor](entity-editor.md) of the loaded workfile's project, on its shot or asset's tab and group with its row selected (in a Multi's workfile, the **Multis** tab with its Multi selected). An untitled or off-pipeline scene opens it on **Shots**, asking for the project when several are registered. |
| **Update** | Re-import latest published versions into the current scene (no scene reload) | Re-executes every import node in place (`import_shot`, `import_assets`, `import_asset`, `import_layer`, `import_rigs`, `import_rig`, `import_model`), so `current` and `latest` pick up the newest publish. Status line: `Imports updated to latest published versions (N node(s)).` If any node fails you get a warning dialog instead — the scene may still reference older versions. |
| **Reload** | Reload current scene | Reloads the hip from disk. Unsaved changes get the **Save Scene** prompt ("Save a new version before switching?" with **Save new version** / **Discard changes** / **Cancel**) — or a silent version-up when *Autosave (version up) on scene change* is on. After the load the timeline is re-applied and, if *Auto-import latest on workfile open* is on, imports are refreshed. |

Right-clicking **Save** offers **Emergency Save (off-thread)**: an inline
save for when Houdini is wedged behind its crash-report dialog and the
normal queued save never lands. It never prompts for a note.

**Publish** opens its window over the browser you clicked in — an undocked
browser included — kept on screen. The window is modal, but the browser
keeps working underneath it, and refreshes the published entity when the
window closes.

Save and Publish say so in a dialog when the scene has no
pipeline context, e.g. "Publish: the loaded scene has no pipeline context,
so there is nothing to publish. Save the scene through the pipeline first."
The background preferences these actions consult are described in
[Pipeline settings and files](settings.md).

### Export from a node instead

There is no "Export" quick action. The per-node entry point is the
**Export** button on a `th::export_layer` (or `th::layer_split` /
`th::export_rig`) node, which opens the same process dialog under the title
**Process: Export Layer** — but with only the clicked node's task enabled
(plus any `layer_split` feeding it and the entity's Build USD task), while
every other task of the context is listed unchecked. Publish, by contrast,
enables everything. An unsaved hip is refused: "Cannot determine workfile
context. Save the file first."

When the scene holds no export node the workfile can run, both entry points
stop with a warning instead of an empty dialog: "No export tasks found for
the current context." (from a node) or "Publish: no export tasks found for
the current scene." Its details list every export node that was left out and
why — see [Why a node is left out](#why-a-node-is-left-out).

## The process dialog

Source: [`process_dialog.py`](../../python/tumblepipe/pipe/houdini/ui/process_dialog.py),
tasks collected by [`task_collection.py`](../../python/tumblepipe/pipe/houdini/ui/task_collection.py)
and built by [`task_factory.py`](../../python/tumblepipe/pipe/houdini/ui/task_factory.py).

### The task tree

Tasks are grouped under one row per entity (a Multi workfile lists every
member). An export node publishes the workfile it lives in, into the
department it names, so every department the scene's export nodes name is
listed, in pool order (the pipeline order the run follows), departments
before the workfile's own included. Up to TumblePipe 1.97.1 only the
workfile's department and those downstream of it were listed, and a lookdev
export built in the model workfile never published; up to 1.47.1 a Multi
workfile listed only its own department. Columns: **Task, Department, Channel, Current, First, Last,
Status**. *Current* shows the version currently on disk (not the one the run
will write) and is replaced by the version a task wrote once it completes in
this session; a group row such as `Export (model)` shows its children's
versions, so a collapsed group no longer reads `-`. Up to 1.101.0 the column
was headed *Version*, and read like the version the run was about to write.
*Status* gives a task finished in this session a tick; on *This machine* or
the *Render farm* a publish, build or render row that only submitted its job
gets an amber **↗** (queued) instead, with a tooltip pointing at the Farm
Manager. Validation rows still run in the session and keep the tick. *Channel* shows a task's own
channel; a row that covers several (the `Validate`, `Export (<dept>)` and
`Build USD` rows) lists them all, `default` first, so a collapsed entity
still shows every channel it publishes. Up to 1.66.0 those rows read `-` and
`default`, and a multi-channel publish looked default-only.

Clicking **Publish** on one export node ticks only that node's row, its
channel's Build USD and its upstream exports. The other channels are listed
but left unchecked; tick them to publish them in the same run.

| Task row | Comes from | Notes |
|---|---|---|
| `Validate (<dept>)` | the department's export nodes | **Unchecked by default.** Runs the [validators](#validators) over each export node's input stage. |
| `Export (<dept>)` | parent row per department | Depends on the validation row — but only if that row is checked. |
| ⤷ `Export Shared` | a connected `th::layer_split` | Writes the shared layer under `_shared/<dept>/`. |
| ⤷ `Export (<channel>)` | each `th::export_layer` | One child per channel the department exports. |
| `Build USD` → `Build USD (<channel>)` | one per channel found on the export nodes | Department column reads `staged`. Depends on every export row of the entity. |
| `Export rig: <asset>` | `th::export_rig` (any asset workfile) | At the rig department's place in the run: after the model and lookdev exports, before the rig department's `Export (rig)`. Local only — no farm mode. A cook that writes no rig file fails the task and removes its empty version folder. |

Bypassed nodes, nodes addressing another entity, and `layer_split` nodes not
wired into an export node are left out. Ticking a parent ticks its children;
a task whose parent is unchecked is drawn grey.

### Changing First / Last for one submit

*First* and *Last* open on the range the export node gives, rolls included.
Double-click a cell (or select it and press F2) to change it for this submit
only: a quick preview of a few frames, or an export over another range. The
node is not changed: its Frame Range parms stay as they are, and the next
publish window shows the node's range again
([`task_range.py`](../../python/tumblepipe/pipe/houdini/ui/task_range.py)).

- Export rows (`Export (<channel>)`, `Export Shared`) and `Build USD
  (<channel>)` rows can be changed. Editing a group row (`Export (<dept>)`,
  `Build USD`) changes each of its children; a group cell whose children differ
  on that frame reads `varies`. Validation and `Export rig` rows use no range and stay
  read-only.
- Changing *First* keeps the row's *Last*, and the other way round. A whole
  frame number only; an edit that would put *First* after *Last* is refused,
  the cell goes back, and the status line says why.
- A changed cell is drawn amber and in italics, and its tooltip gives the
  node's range. Right-click a row for **Reset Frame Range** (that row, or a
  group and its children) or **Reset All Frame Ranges**. Typing back the range
  the row would run unchanged (the node's, or for a build row the exports'
  span it follows) also counts as a reset. The status line adds *(frame range
  changed for this submit)* while any row is changed.
- `Build USD` follows its entity's exports: when an export row is changed and
  the build row is not, the build covers the span of the entity's export
  rows (lowest *First* to highest *Last*), so a preview export of 1001-1010
  stages 1001-1010. Its cells read amber and italic too, with a tooltip
  saying it follows. Change the build row itself to give it its own range;
  reset it to follow again.

The changed range reaches every mode. *In this session* the export and build
run over it. On *This machine* and the *Render farm* the publish job carries
it (`tasks.publish.frame_range` in the job's config, and the job's own
frames): the worker exports those frames, with every export of the job,
*Downstream Exports* included, and a carried build runs over the build rows'
range. Build rows changed to different ranges from each other are not
carried by the publish: they submit their own build batch, each over its own
range. Up to TumblePipe 1.103.0 the columns were read-only: a preview meant
changing the node's Frame Range and changing it back.

### Why a node is left out

Which export nodes a workfile runs is decided by the workfile's own entity,
recorded in its `context.json` — not by the node:

| Workfile belongs to | An export node is collected when |
|---|---|
| a shot or an asset | its **Entity** is that same shot or asset, and its **Department** is one the entity has — any of them, departments before the workfile's own included (up to TumblePipe 1.97.1, only the workfile's department or one downstream of it). |
| a Multi | its **Entity** is a member of the Multi, and its **Department** is in the pool of the Multi's context. |
| an asset that has a `rig` department, in any of its workfiles | it is an un-bypassed `th::export_rig` for that asset. Up to TumblePipe 1.97.1 only the asset's `rig` workfile collected them. |
| an asset Multi, likewise | it is an un-bypassed `th::export_rig` whose **Entity** is a member of the Multi — one *Export rig* task per node. `from_context` cannot pick a member, so set each node's Entity. Up to TumblePipe 1.51.0 a Multi collected no rig exports at all. |

When nothing is collected, the warning's details say which rule each node
failed ([`describe_missing_tasks`](../../python/tumblepipe/pipe/houdini/ui/task_collection.py)):

| Detail | Meaning |
|---|---|
| *This workfile publishes `<entity>` (`<dept>`).* | Always first: the entity and department the workfile belongs to. |
| *An export node only runs for the workfile's own entity. These address a different one …*, then one line per entity with its nodes | The nodes target other entities. Move them into that entity's workfile, or into a Multi that has it as a member. |
| *Multi `<name>` already lists every one of them as a member.* | Those entities are all in that Multi: work in the Multi's own workfile. |
| *An export node only runs for a member of this Multi. These address a non-member …* | Add the entity to the Multi, or move the node. |
| *Departments this workfile can publish: … These nodes export into one `<entity>` does not have:* | The node's department is not one of the entity's: retired, scoped away in the Entity Editor, or never in the pool. |
| *Bypassed:* … | Un-bypass the node. |
| *No entity resolved …* | The node's **Entity** names something that no longer exists, or is `from_context` in a workfile with no entity. In a Multi the detail says to set each node's Entity to the member it exports. |
| *It contains no `th::export_layer` nodes.* (or `th::export_rig`, in an asset workfile) | Nothing to export. |

A scene saved as a *shot* cannot become a Multi's workfile by adding
departments to the Multi: the file still belongs to the shot. See
[Troubleshooting](../troubleshooting.md#export-says-no-export-tasks-found-for-the-current-context).

### Execution Mode

| Mode | Default | Effect on the tree |
|---|---|---|
| **In this session** | yes | Executes now, in this Houdini session. Every row is enabled, whatever department it publishes into. Before each department, when an earlier department of the run wrote a version, the scene's imports float to it first, so the lookdev exports against the model this run just published. Up to 1.97.1 only the workfile's own department was enabled here. |
| **This machine** | | Submits the farm jobs to the background queue on this machine: they run one task at a time, as a render node would run them. Every row with a farm job is enabled; `Export rig` rows (session only) stay unticked. |
| **Render farm** | | Submits the same farm jobs to Deadline. The same tasks as *This machine* are enabled. |

Moving between *This machine* and *Render farm* keeps your ticks. The farm
choice is shared with Farm Submit's **Run on** and remembered: a farm-only
dialog opens on the one you used last. *Render farm* without the Deadline
Client says so on **Execute** and submits nothing. See
[Running farm jobs on this machine](../deadline.md#running-farm-jobs-on-this-machine).

The group is hidden when the tasks can only run in this session (a rig
export). A farm-only list (a Submit Jobs batch) hides *In this session*.

### Running it

1. **Select All** / **Select None** adjust the checkboxes; the status line
   reads `Ready to execute N task(s) in this session` (or `on this machine,
   in the background`, `on the render farm`), or `No tasks selected`.
2. **Execute** first **saves the hip in place** (the "Preparing..." step),
   then runs tasks top to bottom. The status line shows
   `Running: <parent> — <child>: <progress>` as each task reports.
3. **Cancel** while running stops *after the current task*
   ("Cancelling after the current task..."). The run then ends with a
   **`<title> incomplete`** warning listing the steps that never ran and
   noting the result on disk is incomplete; if a Build USD was among them
   it adds that imports and render submissions will fail until Build USD
   has run. Re-open the dialog with only the unrun steps checked to finish.
4. When the run finishes cleanly, the footer collapses to a single green
   **Done** button (Execute and the selection buttons disappear; the
   execution mode locks). After a failed or cancelled run the footer stays
   as it was: fix the selection and **Execute** again to retry, or
   **Close**. After a farm run (*This machine* or *Render farm*) a **Farm
   Manager** button opens the submitted jobs, their progress and logs.

Outcomes:

| Status line | Follow-up |
|---|---|
| `All tasks completed (N succeeded, M skipped)` | If any task **skipped itself with a reason** (e.g. a disconnected export node — see *Skipping a task* in [Contributing](../development.md#skipping-a-task-instead-of-failing-it)), a warning titled **`<title>: N step(s) skipped`** lists them under "These steps were skipped and wrote nothing". Tasks you unticked are not listed. |
| `Completed: X, Failed: Y, Skipped: Z` | An **Error Report** window opens with, per failed task, `TASK / Entity / Department / Type` and the full traceback under `ERROR:`. **Copy to Clipboard** copies the whole report. Tasks depending on a failed one are skipped with "Dependency '…' failed". |

Right-click a **completed** export or build row for **Open Location** (its
version folder in the file browser). Right-click an **entity** row for
**View Latest Export**, which opens the entity's latest staged file in an
external USD viewer (**No Published Export** when there is none).

A failure *opening* the dialog itself — as opposed to a failing task — goes
through `report_failure`: an error dialog reading `<action> failed:
<ExceptionType>: <message>` followed by the path of the log file the
traceback landed in (see [Where the logs are](../development.md#where-the-logs-are)).

## What Local export does

For each `Export (<channel>)` child, `th::export_layer` (source:
[`export_layer.py`](../../python/tumblepipe/pipe/houdini/lops/export_layer.py)):

1. Resolves entity, department, channel and frame range from the node
   (`from_context` reads the workfile's `context.json`; **From settings**
   uses the node's own frame/roll fields). The render *step* is ignored —
   a published layer always carries every frame.
2. Reserves the next version folder, `export/<entity>/<channel>/<department>/vNNNN/`
   (the `export:/` storage root plus the entity's URI segments), and writes
   the layer into a machine-local temp directory first (`temp:/`, see
   [settings](settings.md#temp-and-scratch)).
3. Scrapes the stage for every pipeline asset it composes and runs the
   [refusal checks](#what-can-stop-an-export) below.
4. Copies external sidecars (payloads, textures) into the folder, then
   copies the folder to its final location and writes `context.json`
   beside the layer — entity, department, channel, version, timestamp,
   user, the composed assets under `parameters.assets`, and the layer's
   inputs. See [Department exports and staged files](../composition.md#department-exports-and-staged-files).
5. Stamps the node's comment with `last export: vNNNN`, the time and the
   user.

`Export Shared` writes a `th::layer_split` layer to
`export/<entity>/_shared/<department>/vNNNN/`. `Build USD (<channel>)`
composes the entity's staged file, `_staged/<channel>/vNNNN/<Entity>_vNNNN.usda`,
which is what every import loads.

## What the farm modes do

*This machine* and *Render farm* submit the same jobs instead of writing;
only where they run differs:

- each `Export (<channel>)` child submits a **publish** job for the
  department (plus the *Downstream Exports* ticked on the node), bundling
  the **latest saved version of the workfile the node lives in** — whichever
  department it exports into, so a lookdev export in the model workfile
  sends the model workfile — and an unsaved edit is not what the farm
  exports. A version still being saved is passed over for the one before
  it. Up to 1.97.1 it bundled the target department's own workfile instead:
  lookdev's stale one, or none at all, which failed the task. It needs a pool and a priority (the node's **Pool** /
  **Priority** parms, used wherever the job runs). The node's comment reads
  `farm export submitted` (or `this machine export submitted`) with the
  downstream departments. A submission error fails the task with
  `Failed to submit farm job: …` in the Error Report.

When the run finishes, the status line says where the jobs went
(`… — submitted to the render farm`, or `… — queued on this machine`) and
the **Farm Manager** button opens them. A run no longer stops on a
"submitted" message per export.

The node's **Type** parm (*Local* / *Farm*) does not choose where an
export runs: the dialog's mode does. *Farm* only enables the node's
**Downstream Exports**. (Before 1.84, *In this session* on a node left on
*Farm* submitted a farm job instead, and a farm publish of such a workfile
submitted another publish instead of exporting.)
- the entity's `Build USD (<channel>)` rows ride in the batch of the
  entity's **last** publish: one **build** job per ticked channel, each
  after that batch's publish job, with the export node's pool and
  priority. The entity's other publishes queue no build, so each staged
  version is built once and only after its publishes land. (Through 1.98 the
  rows submitted a build batch of their own with no dependency on the
  publish, while the publish worker of a renderable asset department queued
  another build of the same version.) When no publish can carry them (the
  last row of the last export group is not a farm export, or a publish the
  build waits for did not submit), the rows submit their own build batch
  with the project's default pool and priority (`farm.default_pool`, the
  default priority preset); a project with no default pool fails the task
  with a message saying so.

The farm publish task runs the same export code headlessly, then posts a
Discord notify. It does **not** save a new workfile version: the export
records what was published, and the workfile stays yours. (Through 1.55.0 it
did, which gave a Multi's workfile one new version per shot published, each
of the scene its job had loaded — possibly older than your latest save.)
After a *renderable* asset department publishes it also queues the asset's
build, unless the publish dialog's run owns the build (above). Validation on the farm has no dialog: a failing
validator fails the job. What each job family chains is tabulated in
[Submitting to the farm](submit-jobs.md#job-families).

## Validators

Source: [`validators/`](../../python/tumblepipe/pipe/houdini/validators/).
Which validators run is keyed on (context, department); a project can
override the list with `config:/validators/<context>/<department>/validators.py`.
A department with no entry runs nothing.

| Context / department | Validators |
|---|---|
| assets / model | `model_structure`, `rest_geometry` |
| assets / rig | `rig_structure` (a LOP rig only; a `th::export_rig` gets its own `Validate rig: <asset>` row, the rig checks below) |
| assets / blendshape | `blendshape_structure` |
| assets / groom | `groom_structure` |
| assets / lookdev | `lookdev_structure`, `material_bindings` |
| shots / layout, environment, animation, crowd, cfx, effects, light | `shot_root_prims` |
| shots / render | `shot_root_prims`, `cameras`, `render_settings`, `render_products`, `render_var_names`, `render_var_twins`, `ordered_vars` |
| shots / composite | none |

**Errors** stop the export unless you continue; **warnings** never block
(they are logged).

**Validated up front.** When you press Execute, every ticked validation
row of the run (every shot of a Multi publish, every department) runs
first, against the stage as it is before anything exports, and before the
pre-run save. If any finds errors (or the rig check finds anything), one
**Validation Issues** dialog opens ("Validation found `<n>` issues in
`<m>` shots, before anything exported") listing the issues grouped by
shot (or asset), then department, each with its suggestion as a tooltip.
Each shot has a checkbox, ticked: untick one to skip all of that shot's
rows for this run (they come back unticked in the publish dialog).
**Continue Anyway** runs the ticked shots; **Cancel Export** (the default)
runs nothing and leaves the dialog ready to Execute again. A run whose
validations find nothing shows no dialog. This is the same for a
single-shot publish. On This machine / Render farm the up-front dialog is
the only one: up to 1.103.0 a failing validation there failed its row with
no way to continue.

**During the run** the validations still run in their place, because a
downstream department's validation (lookdev in the model workfile) sees
what an upstream export of the same run just wrote. An issue you already
accepted up front, for the same shot and department, passes silently
there; only an issue the run itself brought in opens the per-department
**Validation Issues - `<dept>`** dialog ("Validation failed for
`<entity>` (`<dept>`)"), listing just the new issues, with a checkbox
**Apply this choice to remaining validations** (ticked by default, so it
asks once rather than once per shot; untick it to answer shot by shot —
up to 1.101.0 it started unticked), and **Continue Anyway** / **Cancel
Export** (the default). Cancelling marks the department's tasks skipped
without an error report. Up to 1.103.0 there was no up-front pass: each
shot validated when the run reached it, so a failure on shot 1 of 4 sat
behind a modal and later shots' issues showed only after it was answered
(#56). The export node's own **Validate** button
runs the same set read-only and answers **Validation Passed**, **Validation
Passed With Warnings**, or the issue list with a **Close** button.

| Validator | Error messages (what fixes them) |
|---|---|
| `model_structure`, `blendshape_structure`, `groom_structure`, `lookdev_structure` | `Asset prim not found at path: <path>` (author the asset prim — `create_asset_model` does); `Asset prim must be of type 'Xform', found '<type>'`; `Missing required '<geo|blshp|groom|mtl>' child prim` (create the Scope under the asset); `'<name>' prim must be of type 'Scope', found '<type>'`. A wrong type with no transform on it only warns (`... it will be published as a Scope` / `as an Xform`) and the export retypes it in the published layer: an asset prim with no type or a Scope becomes an Xform; a geo/blshp/groom/mtl with no type or an Xform (what SOP Import makes) becomes a Scope. A transform on it, or any other type, stays an error. Warns `No entity URI provided` when the export node's Entity is unresolved. |
| `rig_structure` | `Asset prim not found at path: <path>`; `No APEX character at or below <asset>: nothing for an APEX Animate LOP to animate` (build the character on the stage — SOP Import APEX Scene in Character mode, APEX SOP Rig Builder, Configure APEX Rig — before the `export_layer`; a SOP rig publishes through `th::export_rig`); `APEX character outside the asset prim <asset>` (set the SOP Import APEX Scene's Import Path Prefix to the asset prim or below it: its default `/$OS` puts the character where no shot composes it). No `geo` Scope is required. Warns `No entity URI provided`. |
| `rest_geometry` | Warnings only: `Mesh missing rest positions (primvars:rest or primvars:Pref)`, `Mesh missing normals (primvars:normals or normals)`. |
| `material_bindings` | `No mesh under <asset> has a material bound (N mesh(es))`: the lookdev binds nothing (an empty material assigner); fill its Primitives. When some meshes are bound, each unbound one only warns `Mesh has no material binding`. A material counts when it resolves for the mesh: a binding on a parent or through a collection is enough. Meshes outside the asset prim (a lookdev studio's floor) are not checked. Up to 1.101.0 everything was a warning, so a lookdev with empty assigners published clean, and only a binding on the mesh itself counted. |
| rig checks (`Validate rig: <asset>`, on by default; the dialog opens for warnings too) | `Main_Viz is 1: the rig would publish with its viz on` — the Viz slider on the rig's Main control, which TumbleRig's `th::apex_joints` creates at 1 (the suggestion names the `th::apex_joints` nodes upstream; it is not a parameter on them): set it to 0 on the rig before `th::export_rig`. `No shape of the rig has a path attribute …` — `th::animate` would put the geometry at `/Geometry` and every animation publish would fail `shot_root_prims`; give the geometry a `path` under the asset prim before it is packed. `<shape> has geometry outside <asset> (…)` — a path that does not compose with the asset. Warns `<shape> (N points) has no normals …` for a shape of 50,000 points or more with no normals and no normals node in the graph. Up to 1.101.0 the path checks did not exist and the viz suggestion did not say where Main_Viz lives. |
| `shot_root_prims` | `Disallowed root prim '<name>'. Only asset categories, collections, lights, cameras, Render, and scene are allowed.` — reparent the content under an allowed root. Houdini's own `/HoudiniLayerInfo` is ignored. A `/_METADATA` (from an upstream layer published before metadata moved to customData) is disallowed like any other root: re-export that upstream department. |
| `cameras` | `No Camera prims found in stage` (add one under `/cameras/`); `Render camera path not found in stage: <path>` (fix Camera Path on the Render Settings LOP). Warnings for bad clipping range, non-positive near clip, missing or non-positive `focalLength`. |
| `render_settings` | `the stage carries no UsdRender.Settings prim …` (or several — exactly one is required); `RenderSettings missing 'camera' relationship`, `… has no target`, `RenderSettings camera target does not exist: <path>`. Warnings when `products` is missing or empty. |
| `render_products` | `No RenderProduct prims found on the stage`; `RenderProduct renders through no camera, and the RenderSettings names none to inherit` / `RenderProduct camera target does not exist: <path>`. Warnings for a missing or empty `productName`. |
| `render_var_names` | `Prim name '<prim>' does not match aov:name '<aov>'` — rename one to match. Warnings for a missing or empty `driver:parameters:aov:name`. |
| `render_var_twins` | `<var> and <var> are both the AOV '<name>'`: two RenderVars whose names differ only in case (Karma's own **Albedo** toggle beside the project's `albedo`). Switch Karma's toggle off and tick the AOV on [`th::render_vars`](../nodes/lighting-and-rendering.md#thrender_vars-lop); the farm refuses such a render too. |
| `ordered_vars` | `orderedVars references non-existent RenderVar: <path>`; `RenderVar not in orderedVars: <path>`. Warnings when `orderedVars` is missing or empty. |

A validator name in a project's `validators.py` that is not registered is
itself an error (`Unknown validator '<name>' - it is not registered, so it
did not run`), never a silent pass.

`render_settings`, `render_products` and `cameras` find the settings prim by
asking the stage, so they work whether a project keeps it at
`/Render/rendersettings` (the template) or `/scene/Render/rendersettings`
(projects made from the template before 2026-10-07) — see
[Where the render settings live](../composition.md#where-the-render-settings-live).

## What can stop an export

These are raised by the export itself, regardless of validation, and show
up in the Error Report:

| Message (abridged) | Cause and fix |
|---|---|
| `Entity URI is not set. Check 'Entity Source' setting or workfile context.` / `Department name is not set for entity: …` | The node cannot resolve its entity or department — save through the pipeline or set the parms explicitly. |
| `Frame range could not be determined for entity: …` | The entity's config resolves no frame range. Set one on the entity, or switch the node's frame range source to **From settings**. |
| `Export aborted: channel '<x>' is not a channel of <entity> (listed: …)` | The node's **Channel** parm names an unlisted channel; it would otherwise publish under `default`. Fix the parm or register the channel. See [Channels](../composition.md#channels-and-why-they-are-not-usd-variants). |
| `Export aborted: this workfile imports asset(s) that the shot's Root '<root>' already provides: …` | An import node in a shot workfile brings in an asset the shot's Root already gives it through `import_shot`. A Root's assets belong to the Root, so no export may claim them. Remove the asset from the import node, or change its count in the Root (Entity Editor, **Shots** tab, **Assets**). See [A shot's Root assets belong to the Root](../composition.md#a-shots-root-assets-belong-to-the-root). |
| *skipped* — `Nothing exported for channel '<x>': the node <path> has no stage input connected.` | Not a failure: a disconnected export node is skipped and its siblings still run. |
| `Nothing exported for channel '<x>': the input of <path> failed to cook.` followed by `Cook error:` | The node is connected, but what feeds it errored — look for the red node upstream; the quoted error names it. On the farm, a rig that cooks at your desk but not here usually means a package the project uses is missing from the job (see [Farm job: rig fails to compile](../troubleshooting.md#farm-job-failed-rig-fails-to-compile-failed-to-load-subnet)). |
| `Export aborted: asset(s) on the stage carry no pipeline metadata …` / `… asset(s) configured on upstream import nodes carry no pipeline metadata …` | An imported asset lost its `customData` tag and would vanish downstream. Re-run the import node (its **Import** button) and re-export; to bake an asset in on purpose set the import node's *Import Mode* to Inline. See [Dropped-metadata guard](../composition.md#dropped-metadata-guard). |
| `Export aborted: the exported layer composes geometry from path(s) outside the export folder …` | A sublayer/reference/payload escapes the version folder — usually a LOP with **Enable Layer Save Path** on (Houdini 22's default on new SOP Create / SOP Import nodes). Disable it and re-export. Versioned `th::cache` locations are exempt. If the paths are `export/assets/…/_staged/…` layers, an import node in *Import Mode* Inline is running on a TumblePipe without the inline flatten: update the package, or switch it to Reference. See [Layer save paths and export portability](../composition.md#layer-save-paths-and-export-portability). |
| `Export aborted: the exported layer composes geometry from path(s) that do not exist …` | A dangling arc (e.g. a payload anchored to a machine-local scratch path); the published asset would import empty. Same section as above. |
| `Failed to copy exported files to final location: …` | The temp → version copy failed (share unreachable, permissions). |

## After a publish

A **local** publish that wrote at least one version ends by re-resolving
every loaded `entity:` layer in the session (`resolver.refresh_context()`),
so an import node in the *same* scene that composes the published entity
floats to the new version without a restart. It cannot reach another
artist's session — there, **Update** (or reopening the workfile through the
browser) does the same thing. Farm submissions write nothing locally and
trigger no refresh. Details in
[Picking up new versions mid-session](../composition.md#picking-up-new-versions-mid-session).

The browser also drops its caches after the dialog closes, so the card's
version and *Latest Export* refresh on the next browse.
