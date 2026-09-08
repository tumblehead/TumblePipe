# Submitting to the farm

The **Submit Jobs** dialog sends publish, render and playblast jobs for one
or many shots or assets to Deadline. Source:
[`submit_jobs_dialog.py`](../../python/tumblepipe/asset_browser/submit_jobs_dialog.py)
(the form), [`submit_jobs_resolve.py`](../../python/tumblepipe/asset_browser/submit_jobs_resolve.py)
(how each field resolves per entity) and
[`batch_submit.py`](../../python/tumblepipe/farm/jobs/houdini/batch_submit.py)
(what gets built and submitted). Farm prerequisites are in
[Deadline and the render farm](../deadline.md).

## Opening it

| From | Menu / button | What is pre-checked |
|---|---|---|
| The toolbar, with a workfile loaded | **Render** quick action | The loaded scene's entity. Its workfile department seeds the Render and Playblast departments. Only shots and assets qualify. |
| A card's right-click menu | **Submit Jobs…** | That entity. |
| A multi-selection's right-click menu | **Submit Jobs for N selected…** | Every selected card in the same context (all shots, or all assets) as the one you right-clicked. |

The dialog is scoped to one context — shots or assets — because the
department lists differ between them.

## The entity tree

The header reads `Submit jobs for N entities (shots): <names…>`. Below it:

- a **Filter shots…** / **Filter assets…** box with **All** and **None**
  buttons that check or uncheck every *visible* entity;
- a checkable tree rooted at **Shots** or **Assets**, nested by sequence or
  category, plus a **Groups** root (when the project has groups) whose
  leaves mirror the same entities.

Checking a branch cascades to its visible leaves; branches roll up to a
partial state. An entity that sits under several groups is one entity — the
checkboxes mirror each other and it is submitted once. Filtering narrows the
view only; check state is untouched, so a branch checked while a filter is
active does not quietly submit what you cannot see.

## The form

Three checkable sections — **Publish** (off by default), **Render** (on) and
**Playblast** (shots only, off) — then **Pre-flight** and the **Submit** /
**Cancel** buttons.

### Fields follow each entity unless you pin them

Every field with a per-entity source is **tri-state**:

| State | How it looks | What is sent |
|---|---|---|
| Unpinned, entities agree | Italic, dimmed value | Nothing — each entity uses its own configured value (the one shown). |
| Unpinned, entities disagree | `⟨per entity⟩` (a spin box parks one step below its minimum, a checkbox shows partially checked, a combo shows the placeholder row, a line edit is empty) | Nothing — each entity keeps its own value. |
| Pinned | Upright, normal colour | Your value, for every checked entity. |

Editing a field pins it. To unpin, click the row's **↺** ("Use each
entity's own configured value"), pick the `⟨per entity⟩` row of a combo, or
click a checkbox round to partially checked. Growing the batch re-derives
only unpinned fields; pinned ones survive. A submission field that is a
choice *about the submission* rather than a property of the entity (Range,
Standalone, Copy to edit, Channels) is a plain widget with no per-entity
state.

### Publish

| Field | Per-entity source | Default when unset |
|---|---|---|
| **Department** | `submission.publish.department` | first publishable department |
| **Pool** | `farm.default_pool` | `general` |
| **Priority** | `farm.priority` | 50 |

The department combo lists the context's publishable, enabled,
non-generated departments. Tooltip: "Publishes every department up to and
including this one, in pipeline order."

### Render

| Field | Per-entity source | Default when unset | Notes |
|---|---|---|---|
| **Department** | `submission.render.department` | first renderable department | Seeded — and pinned — from the workfile you opened the dialog from, when that department is renderable. Tooltip: renders every department up to and including this one; departments after it are left out. Also names the render output. |
| **Channels** | — | the entities' own channel lists | A checkable menu (`(none — check at least one)` when empty) with **All** / **None**. Lists the *union* of the checked entities' channels, `default` first; opens with the *intersection* checked. Channels arriving with a later-checked entity start unchecked. One render per checked channel. |
| **Range** | — | `Full range` | `Full range` renders everything and chains slapcomp + MP4; `First / Middle / Last` renders three check frames and notifies. |
| **Frames** (first → last) | `frame_start` / `frame_end` | *required* — omitted, and the entity fails with "no frame range configured" | |
| **Pre / Post roll** | `roll_start` / `roll_end` | 0 / 0 | **Extends** the rendered range: `first - pre … last + post`. |
| **Pool** | `farm.default_pool` | `general` | |
| **Pri** | `farm.priority` | 50 | |
| **Tiles** | `farm.tile_count` | 4 | |
| **Batch** | `farm.batch_size` | 10 | Frames per farm task. |
| **Samples** | `render.pathtracedsamples` | 64 | Applied as a Karma override (`karma:global:pathtracedsamples`). |
| **Denoise** | `render.enabledenoising` | on | Adds denoise jobs after each render. |
| **Motion blur** | `render.enablemblur` | on | Override `karma:object:mblur`. |
| **DOF** | `render.enabledof` | on | Override `karma:global:enable_dof`. |
| **Standalone** | — | off | Off = direct render of the collapsed staged file. On = a farm **stage** job builds the render stage first. See [Render staging](../composition.md#render-staging). |
| **Copy to edit** | — | off | Adds an *edit* job that syncs the finished AOVs to the edit location. |

There is no step field: farm renders always submit `step_size = 1`.

### Playblast (shots only)

| Field | Per-entity source | Default when unset |
|---|---|---|
| **Department** | `submission.render.department` | same as Render (renderable departments) |
| **Resolution** (W × H) | `playblast.res_x` / `playblast.res_y` | 1280 × 720 |
| **Pool** | `farm.default_pool` | `general` |
| **Priority** | `farm.priority` | 50 |

No frame or channel field: a playblast always renders the shot's `default`
staged stage over the shot's configured range (rolls included), at the
shot's fps. What it produces is described in
[Compositing → Playblast](../compositing.md#playblast).

### Pre-flight

Collapsed by default. Ticking it shows one row per checked entity, a column
for every setting the batch does **not** agree on, and a **Warnings**
column. The note below reads `All N agree on every setting shown here.`,
`Varies across the batch: …`, or `Nothing checked — …`, plus `N of M would
be submitted with a warning.` The rows are the exact settings the submit
sends. Warnings:

- `no frame range configured` — the entity has no `frame_start` /
  `frame_end` and you pinned none;
- `channel(s) not defined here: …` — a checked channel this entity does not
  define; it will fail on submit for that entity rather than silently
  render `default`;
- `department '<x>' is not assigned to this entity` — the pool still
  composes it, so this is a warning, not a block.

## Submit

Submit refuses with a dialog when nothing is checked ("Check at least one
shot in the tree before submitting."), no section is enabled ("Enable at
least one of Publish, Render or Playblast before submitting."), or Render is
on with no channel checked. With more than one entity, or any warning, it
asks first: `Submit render jobs for N shots?`, how many settings are pinned
to the whole batch, and the warned entities.

The batch then runs through the [process dialog](export-and-publish.md#the-process-dialog)
as **Process: Submit to Farm** — one farm-only task per entity, described
`publish+render [layout, lighting]` (the departments it cuts to), each
submitting its own resolved settings. A clean run closes the Submit Jobs
dialog; a failure keeps it open so you can fix and resubmit, with the
per-entity error in the dialog's Error Report. Outside Houdini the same loop
runs inline with a single summary box.

### What is submitted per entity

`submit_entity_batch` builds one Deadline batch named
`<project> [publish] [render] [playblast] <entity uri> <user> <timestamp>`,
staged through a temp directory and submitted via `export:/other/jobs`.

**Publish** — for every department from the top of the pool down to the
chosen one: skipped when it has no saved workfile (or the entity is a `000`
placeholder), and — for the first job in the chain — when its latest export
is already newer than its workfile. Each surviving department gets a
`publish_<dept>` job chained after the previous one, bundling the latest
workfile and its `context.json`; a Discord **notify** follows. Render and
playblast jobs depend on the last publish job when both are enabled.

**Render** — the render department must be renderable (`Render department
'<x>' is not renderable. Renderable departments: …` otherwise). The
[department cut](../composition.md#the-department-cut) — every pool
department up to and including the chosen one — is applied, and AOV names are
gathered from every department's export `context.json` (own and referenced
assets), falling back to `config:/usd/context.json`. Then:

- *direct* (Standalone off, the default): each checked channel's latest
  staged file is collapsed into `collapsed_stage_<channel>.usda` with the
  cut applied and the Samples / Motion blur / DOF overrides baked onto the
  stage's render-settings prim; husk renders that. `No staged file found for
  <entity> channel '<x>' … Publish the entity first to create staged files.`
  and `Cannot collapse the staged stage …` (a layer the build records is
  missing on disk) fail that entity alone.
- *standalone*: the overrides go into `render_settings.json`, a **stage** job
  builds `stage_<channel>.usd` on the farm, and the render chain depends on
  it.

Either way the override prim is discovered on the stage, never assumed —
see [Where the render settings live](../composition.md#where-the-render-settings-live).

**Playblast** — collapses the `default` staged file with the playblast
department's cut (no overrides) and submits one playblast job per shot over
the shot's configured range.

## Job families

Source: [`farm/jobs/houdini/`](../../python/tumblepipe/farm/jobs/houdini/).
Each job runs in a Deadline **pool** from the dialog's Pool field and a
fixed Deadline **group** per task type. Every family ends in a **notify**
job that posts to Discord — render and playblast notifies address the
channel named `renders`, publish and stage notifies the one named
`exports`; the name is looked up under `discord/channels` in the project's
`config` database (see [The config database editor](config-editor.md#the-databases)),
and a name with no `channel_id` there fails the notify job with
`Channel not found in discord config: <name>`.
Output locations are in [Compositing → Where renders land](../compositing.md#where-renders-land).

| Family | Triggered by | Chain (job name → group) | Output |
|---|---|---|---|
| **render**, Full range | Render section | per channel: `full_render_<ch>` (`karma`) → `full_denoise_<ch>` (`denoise`, if Denoise) → `layer_mp4_<ch>` (`encode`); then across channels: `edit` (`edit`, if Copy to edit), `slapcomp` (`houdini`) → `slapcomp_mp4` (`encode`) → `slapcomp_notify` (`general`) | `render:/render/<shot>/<dept>/<channel>/vNNNN/<aov>/`, denoised frames under the `denoise` department, slapcomp MP4 + daily |
| **render**, First / Middle / Last | Render section | per channel: `partial_render_<ch>` (`karma`) → `partial_denoise_<ch>` (if Denoise) → `partial_notify_<ch>` — three frames, no MP4 | same version folders, three frames only; the notify carries first/middle/last |
| **stage_render** | Render with **Standalone** | `stage` (`houdini`) → the render chain above | `stage_<channel>.usd` in the job's data dir |
| **publish** | Publish section; Farm mode in the process dialog | `publish_<dept>` (`houdini`) per department, chained → `notify` (`general`). A renderable asset publish then queues a **build** | `export/<entity>/<channel>/<dept>/vNNNN/` |
| **build** | after a farm publish; Build USD in Farm mode | `build` (`general`) | `_staged/<channel>/vNNNN/` |
| **playblast** | Playblast section | `playblast` (`playblast`) → `playblast_notify` (`general`) | `render:/playblast/<shot>/<dept>/vNNNN.mp4` + the daily |
| **composite** | the `th::build_comp` node's **Submit** | see [Compositing → Farm submission and MP4s](../compositing.md#farm-submission-and-mp4s) | `render:/render/<shot>/composite/…` |
| **export_render** | the `th::lookdev_studio` HDA | `export` (`houdini`) → `notify` | lookdev turntable renders |
| **propagate**, **update** | command line only (`propagate`: re-publish dependents of a department; `update`: republish every shot up to a department) | publish/build jobs → `notify` | exports and staged builds |
| **cloud_render**, **cloud_stage_render** | the cloud stage task, not the dialog | `stage` → render → denoise → slapcomp → mp4 → notify (`cloud_karma`) | as render |

Job priorities come from the dialog; notify jobs run at 90, edit at 90,
stage notify at 60. The playblast group is kept separate from `karma` so
previews never contend with final frames — those workers need a GL-capable
GPU, see [Farm worker prerequisites](../deadline.md#farm-worker-prerequisites).
