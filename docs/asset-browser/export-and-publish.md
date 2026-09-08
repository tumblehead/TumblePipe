# Export and publish

How a workfile's stage becomes a versioned, published layer — from the
toolbar quick actions, through the process dialog, to what lands on disk and
what refuses to.

## The quick actions

The Asset Browser's toolbar carries five quick actions for the **currently
loaded** `.hip`. The toolbar label is the loaded hip's filename; every action
first resolves the scene's pipeline context (entity, department, version) and
tells you when there is none.

| Button | Tooltip | What it does |
|---|---|---|
| **Save** | Save current scene | Saves the scene as the **next workfile version** of its own context (never in place). With *Ask for a version note on save* on (the default), a **Save Version** dialog asks "Note for the next `<dept>` version of `<entity>` (after `vNNNN`) — optional:". Cancel there aborts the save without burning a version. Status line: `Saved <file>`. |
| **Publish** | Publish exports | Opens the process dialog (title **Process: Publish**) with every export and build task for the scene's entity, current department **and all departments downstream of it**. See below. |
| **Render** | Submit render jobs for the current scene's entity | Opens the [Submit Jobs dialog](submit-jobs.md) for the loaded scene's entity, with the Render department seeded from the workfile's department. Only shots and assets can be submitted. |
| **Update** | Re-import latest published versions into the current scene (no scene reload) | Re-executes every `th::import_shot`, `import_assets`, `import_asset`, `import_layer` (LOP) and `import_rigs` (SOP) node in place, so `latest` references float to the newest publish. Status line: `Imports updated to latest published versions (N node(s)).` If any node fails you get a warning dialog instead — the scene may still reference older versions. |
| **Reload** | Reload current scene | Reloads the hip from disk. Unsaved changes get the **Save Scene** prompt ("Save a new version before switching?" with **Save new version** / **Discard changes** / **Cancel**) — or a silent version-up when *Autosave (version up) on scene change* is on. After the load the timeline is re-applied and, if *Auto-import latest on workfile open* is on, imports are refreshed. |

Right-clicking **Save** offers **Emergency Save (off-thread)**: an inline
save for when Houdini is wedged behind its crash-report dialog and the
normal queued save never lands. It never prompts for a note.

Save, Publish and Render all say so in a dialog when the scene has no
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

## The process dialog

Source: [`process_dialog.py`](../../python/tumblepipe/pipe/houdini/ui/process_dialog.py),
tasks collected by [`task_collection.py`](../../python/tumblepipe/pipe/houdini/ui/task_collection.py)
and built by [`task_factory.py`](../../python/tumblepipe/pipe/houdini/ui/task_factory.py).

### The task tree

Tasks are grouped under one row per entity (a Multi workfile lists every
member). Columns: **Task, Department, Channel, Version, First, Last,
Status**. *Version* shows the version currently on disk and is replaced by
the version a task wrote once it completes.

| Task row | Comes from | Notes |
|---|---|---|
| `Validate (<dept>)` | the department's export nodes | **Unchecked by default.** Runs the [validators](#validators) over each export node's input stage. |
| `Export (<dept>)` | parent row per department | Depends on the validation row — but only if that row is checked. |
| ⤷ `Export Shared` | a connected `th::layer_split` | Writes the shared layer under `_shared/<dept>/`. |
| ⤷ `Export (<channel>)` | each `th::export_layer` | One child per channel the department exports. |
| `Build USD` → `Build USD (<channel>)` | one per channel found on the export nodes | Department column reads `staged`. Depends on every export row of the entity. |
| `Export rig: <asset>` | `th::export_rig` (rig department only) | Local only — no farm mode. |

Bypassed nodes, nodes addressing another entity, and `layer_split` nodes not
wired into an export node are left out. Ticking a parent ticks its children;
a task whose parent is unchecked is drawn grey.

### Execution Mode

| Mode | Default | Effect on the tree |
|---|---|---|
| **Local** | yes | Executes in this Houdini session. Only the **current department's** export rows (and the builds) are enabled; downstream departments are unticked. |
| **Farm** | | Submits to Deadline. Every department listed — current and downstream — is enabled. |

The group is hidden when the tasks only support one mode (a Submit Jobs
batch is farm-only, a rig export is local-only).

### Running it

1. **Select All** / **Select None** adjust the checkboxes; the status line
   reads `Ready to execute N task(s) locally` (or `on farm`), or `No tasks
   selected`.
2. **Execute** first **saves the hip in place** (the "Preparing..." step),
   then runs tasks top to bottom. The status line shows
   `Running: <parent> — <child>: <progress>` as each task reports.
3. **Cancel** while running stops *after the current task*
   ("Cancelling after the current task..."). The run then ends with a
   **`<title> incomplete`** warning listing the steps that never ran and
   noting the result on disk is incomplete; if a Build USD was among them
   it adds that imports and render submissions will fail until Build USD
   has run. Re-open the dialog with only the unrun steps checked to finish.
4. When done, Cancel becomes **Close**.

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

## What Farm mode does

Farm mode submits instead of writing:

- each `Export (<channel>)` child submits a **publish** job for the
  department (plus the *Downstream Exports* ticked on the node), bundling
  the department's **latest saved workfile** — so an unsaved edit is not
  what the farm exports. It needs a Deadline pool and a priority (the node's
  **Pool** / **Priority** parms); you get a **Farm Export Submitted** dialog
  naming the department and downstream departments, and the node's comment
  reads `farm export submitted`. A submission error shows
  `Failed to submit farm job: …`.
- each `Build USD (<channel>)` child submits a **build** job (priority 50,
  pool `houdini`).

The farm publish task runs the same export code headlessly, then posts a
Discord notify; after a *renderable* asset department publishes it also
queues the asset's build. Validation on the farm has no dialog: a failing
validator fails the job. What each job family chains is tabulated in
[Submitting to the farm](submit-jobs.md#job-families).

## Validators

Source: [`validators/`](../../python/tumblepipe/pipe/houdini/validators/).
Which validators run is keyed on (context, department); a project can
override the list with `config:/validators/<context>/<department>/validators.py`.
A department with no entry runs nothing.

| Context / department | Validators |
|---|---|
| assets / model, rig | `model_structure`, plus `rest_geometry` for model |
| assets / blendshape | `blendshape_structure` |
| assets / lookdev | `lookdev_structure`, `material_bindings` |
| shots / layout, environment, animation, crowd, cfx, effects, light | `shot_root_prims` |
| shots / render | `shot_root_prims`, `cameras`, `render_settings`, `render_products`, `render_var_names`, `ordered_vars` |
| shots / composite | none |

**Errors** stop the export unless you continue; **warnings** never block
(they are logged). When a validation row is ticked and errors are found,
the **Validation Issues - `<dept>`** dialog opens ("Validation failed for
`<entity>` (`<dept>`)") listing each issue with its suggestion, a checkbox
**Apply this choice to remaining validations**, and **Continue Anyway** /
**Cancel Export** (the default). Cancelling marks the department's tasks
skipped without an error report. The export node's own **Validate** button
runs the same set read-only and answers **Validation Passed**, **Validation
Passed With Warnings**, or the issue list with a **Close** button.

| Validator | Error messages (what fixes them) |
|---|---|
| `model_structure`, `blendshape_structure`, `lookdev_structure` | `Asset prim not found at path: <path>` (author the asset prim — `create_asset_model` does); `Asset prim must be of type 'Xform', found '<type>'`; `Missing required '<geo|blendshape|mtl>' child prim` (create the Scope under the asset); `'<name>' prim must be of type 'Scope', found '<type>'`. Warns `No entity URI provided` when the export node's Entity is unresolved. |
| `rest_geometry` | Warnings only: `Mesh missing rest positions (primvars:rest or primvars:Pref)`, `Mesh missing normals (primvars:normals or normals)`. |
| `material_bindings` | Warning only: `Mesh has no material binding`. |
| `shot_root_prims` | `Disallowed root prim '<name>'. Only asset categories, collections, lights, cameras, Render, and scene are allowed.` — reparent the content under an allowed root. |
| `cameras` | `No Camera prims found in stage` (add one under `/cameras/`); `Render camera path not found in stage: <path>` (fix Camera Path on the Render Settings LOP). Warnings for bad clipping range, non-positive near clip, missing or non-positive `focalLength`. |
| `render_settings` | `RenderSettings prim not found at /Render/rendersettings`; wrong prim type; `RenderSettings missing 'camera' relationship`, `… has no target`, `RenderSettings camera target does not exist: <path>`. Warnings when `products` is missing or empty. |
| `render_products` | `No RenderProduct prims found under /Render/Products`; `RenderProduct missing 'camera' relationship` / `… has no target` / `RenderProduct camera target does not exist: <path>`. Warnings for a missing or empty `productName`. |
| `render_var_names` | `Prim name '<prim>' does not match aov:name '<aov>'` — rename one to match. Warnings for a missing or empty `driver:parameters:aov:name`. |
| `ordered_vars` | `orderedVars references non-existent RenderVar: <path>`; `RenderVar not in orderedVars: <path>`. Warnings when `orderedVars` is missing or empty. |

A validator name in a project's `validators.py` that is not registered is
itself an error (`Unknown validator '<name>' - it is not registered, so it
did not run`), never a silent pass.

Note that `render_settings` and `cameras` still look for the prim at
`/Render/rendersettings`; on projects whose settings live under `/scene`
they mis-report — see [Where the render settings live](../composition.md#where-the-render-settings-live).

## What can stop an export

These are raised by the export itself, regardless of validation, and show
up in the Error Report:

| Message (abridged) | Cause and fix |
|---|---|
| `Entity URI is not set. Check 'Entity Source' setting or workfile context.` / `Department name is not set for entity: …` | The node cannot resolve its entity or department — save through the pipeline or set the parms explicitly. |
| `Frame range could not be determined for entity: …` | The entity's config resolves no frame range. Set one on the entity, or switch the node's frame range source to **From settings**. |
| `Export aborted: channel '<x>' is not a channel of <entity> (listed: …)` | The node's **Channel** parm names an unlisted channel; it would otherwise publish under `default`. Fix the parm or register the channel. See [Channels](../composition.md#channels-and-why-they-are-not-usd-variants). |
| *skipped* — `Nothing exported for channel '<x>': the node <path> has no stage input connected.` | Not a failure: a disconnected export node is skipped and its siblings still run. |
| `Export aborted: asset(s) on the stage carry no pipeline metadata …` / `… asset(s) configured on upstream import nodes carry no pipeline metadata …` | An imported asset lost its `customData` tag and would vanish downstream. Re-run the import node (its **Import** button) and re-export; to bake an asset in on purpose set the import node's *Import Mode* to Inline. See [Dropped-metadata guard](../composition.md#dropped-metadata-guard). |
| `Export aborted: the exported layer composes geometry from path(s) outside the export folder …` | A sublayer/reference/payload escapes the version folder — usually a LOP with **Enable Layer Save Path** on (Houdini 22's default on new SOP Create / SOP Import nodes). Disable it and re-export. Versioned `th::cache` locations are exempt. See [Layer save paths and export portability](../composition.md#layer-save-paths-and-export-portability). |
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
