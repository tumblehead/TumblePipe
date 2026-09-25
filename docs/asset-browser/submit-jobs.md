# Submitting to the farm

The **Farm Submit** dialog sends publish, playblast and render jobs for any number
of shots or assets to Deadline. It shows every entity of a context as a row
and every pipeline step as a column, so you can see what is out of date and
tick what should run, from one cell up to the whole project. Submitting hands
the work to a separate process, so Houdini is usable again within seconds.

Source: [`submit_jobs_dialog.py`](../../python/tumblepipe/asset_browser/submit_jobs_dialog.py)
(the dialog), [`farm_grid.py`](../../python/tumblepipe/asset_browser/farm_grid.py)
(what cells, ticks and warnings mean), [`farm_status.py`](../../python/tumblepipe/asset_browser/farm_status.py)
(reading each cell's status), [`submit_jobs_resolve.py`](../../python/tumblepipe/asset_browser/submit_jobs_resolve.py)
(how each setting resolves per entity), [`submit_plan.py`](../../python/tumblepipe/farm/submit_plan.py)
(the background submission) and
[`batch_submit.py`](../../python/tumblepipe/farm/jobs/houdini/batch_submit.py)
(what gets built and submitted). Farm prerequisites are in
[Deadline and the render farm](../deadline.md).

## Opening it

| From | Menu / button | Rows | Ticked |
|---|---|---|---|
| The toolbar | **Farm Submit** quick action | Every shot (every asset when the loaded scene is an asset's) | Nothing |
| The toolbar, with a workfile loaded | **Render** quick action | Every entity of the scene's context | The loaded scene's entity's **Render** cell — or, in a [Multi](multis-and-roots.md#multis) workfile, every member's |
| A card's right-click menu | **Submit Jobs…** | Every entity of the card's context | That entity's **Render** cell |
| A multi-selection's right-click menu | **Submit Jobs for N selected…** | Every entity of the context | Each selected card's **Render** cell |
| TumbleTrove Desktop, outside Houdini | the project's Scripts panel → **Farm Submit** | Every shot | Nothing — see [From TumbleTrove Desktop](#from-tumbletrove-desktop) |

When the dialog is opened from a workfile, that workfile's department pins
the **Up to** department of Playblast and Render (when it is renderable), so
submitting from a lighting workfile previews up to lighting.

The dialog is non-modal: Houdini stays usable while it is open. It shows one
context at a time — shots, or assets — because the department columns differ
between them.

A Multi is never a farm target itself: it has no staged stage, frame range or
channels of its own. Opened from a Multi, the dialog ticks the Multi's
members instead, so each member shot is submitted with its own settings.
`submit_entity_batch` refuses a Multi outright with `<uri> is a Multi, not a
shot or asset`.

## The grid

Rows are the context's entities, grouped under one row per sequence (or asset
category). Columns are the pipeline steps:

- one **publish** column per publishable department, in pipeline (pool)
  order;
- **Playblast** (shots only) and **Render**, after a heavier rule;
- **Warnings**.

### What a cell shows

| Mark | Publish column | Playblast / Render column |
|---|---|---|
| `·` dim | no workfile for this department — nothing to do | no frame range configured — cannot run |
| `○` | a workfile exists but was never exported | never made |
| `●` amber | the workfile was saved after its latest export | older than the newest publish it composes |
| `✓` green | exported since the last workfile save | newer than every publish it composes |
| `…` | status not read yet | status not read yet |
| jade fill | **will be submitted** | **will be submitted** |

A preview "composes" the departments of its **Up to** cut: a playblast up to
animation is stale when layout or animation was published after it, not when
lighting was. Changing a Playblast or Render **Up to** re-reads the preview
columns. Hover a cell for what its state means.

Statuses are read in the background after the dialog opens, visible rows
first; **Refresh** reads them all again. Only folder listings and one file
date per latest version are read — never a USD layer — so a project on a
network share fills in quickly. The status is advisory: the submission
checks everything again when it runs.

### Ticking

- Click a cell to tick or untick it. A `·` cell cannot be ticked.
- Every **shot row**, **column header** and **sequence row** carries a
  checkbox showing whether all (✓), some (–) or none of the cells it covers
  are ticked. Clicking it ticks them all, or clears them when all are
  ticked. **All** in the corner covers the whole grid.
- A **sequence row** also carries a checkbox per column, covering that column
  for every shot in the sequence, with the number of stale or never-done
  cells in its corner. Its ▶ / ▼ collapses and expands the sequence; a
  collapsed sequence's ticks still submit.
- **Select stale** ticks every visible cell that is stale or never done.
  **Clear** unticks everything.
- **Filter** narrows the rows by name, and the Multi menu beside it narrows
  them to one Multi's members. Filtering never unticks anything, but every
  header, sequence and Select stale action only reaches the rows you can see,
  so ticking a column under a filter cannot submit what you filtered out.

**Publish cells are individual departments.** Ticking animation and light on
a row publishes exactly those two, chained in pipeline order. (The form this
replaced published "every department up to X" and silently skipped the
up-to-date ones at the front; **Select stale** is now the one-click way to
publish what needs it.)

### Warnings

The Warnings column says, per row, what the submission would run into:

- `<departments> stale and not ticked` — a ticked playblast or render would
  compose departments inside its cut that are stale or never exported and not
  ticked for publishing. It does not block the submit: previewing what is
  published is sometimes the point.
- `no frame range configured` — the entity has no `frame_start` /
  `frame_end` and you pinned none.
- `channel(s) not defined here: …` — a checked render channel this entity
  does not define; it will fail on submit for that entity rather than
  silently render `default`.
- `department '<x>' is not assigned to this entity` — the pool still
  composes it, so this is a warning, not a block.

## The settings

The right-hand column holds the settings for the ticked cells, in three
groups: **Publish**, **Playblast** (shots only) and **Render**.

### Fields follow each entity unless you pin them

Every field with a per-entity source is **tri-state**:

| State | How it looks | What is sent |
|---|---|---|
| Unpinned, entities agree | Italic, dimmed value | Nothing — each entity uses its own configured value (the one shown). |
| Unpinned, entities disagree | `⟨per entity⟩` (a spin box parks one step below its minimum, a checkbox shows partially checked, a combo shows the placeholder row, a line edit is empty) | Nothing — each entity keeps its own value. |
| Pinned | Upright, normal colour | Your value, for every ticked entity. |

"The entities" are the rows with at least one tick (every visible row while
nothing is ticked). Editing a field pins it. The mouse wheel only changes a
field you have clicked into; over any other field it scrolls the settings,
so scrolling past a number cannot pin it by accident. To unpin, click the
row's **↺** ("Use each entity's own configured value"), pick the
`⟨per entity⟩` row of a combo, or click a checkbox round to partially
checked. Ticking more rows
re-derives only unpinned fields; pinned ones survive. A field that is a
choice *about the submission* rather than a property of the entity (Range,
Standalone, Copy to edit, Channels) is a plain widget with no per-entity
state.

### Publish

| Field | Per-entity source | Default when unset |
|---|---|---|
| **Pool** | `farm.default_pool` | `general` |
| **Priority** | `farm.priority` | 50 |

Which departments are published is what the grid's publish cells say.

### Playblast (shots only)

| Field | Per-entity source | Default when unset |
|---|---|---|
| **Up to** | `submission.render.department` | last renderable department (the whole shot) |
| **Resolution** (W × H) | `playblast.res_x` / `playblast.res_y` | 1280 × 720 |
| **Pool** | `farm.default_pool` | `general` |
| **Priority** | `farm.priority` | 50 |

**Up to** playblasts every department up to and including this one, in
pipeline order — the same cut the render uses. There is no frame or channel
field: a playblast always renders the shot's `default` staged stage over the
shot's configured range (rolls included), at the shot's fps. What it produces
is described in [Compositing → Playblast](../compositing.md#playblast).

### Render

| Field | Per-entity source | Default when unset | Notes |
|---|---|---|---|
| **Up to** | `submission.render.department` | last renderable department (the whole shot) | Renders every department up to and including this one; departments after it are left out. Also names the render output. |
| **Channels** | — | the entities' own channel lists | A checkable menu (`(none — check at least one)` when empty) with **All**. Lists the *union* of the rendering entities' channels, `default` first; opens with the *intersection* checked. Channels arriving with a later-ticked entity start unchecked. One render per checked channel. |
| **Range** | — | `Full range` | `Full range` renders everything and chains slapcomp + MP4; `First / Middle / Last` renders three check frames and notifies. |
| **Frames** (first → last) | `frame_start` / `frame_end` | *required* — omitted, and the entity fails with "no frame range configured" | |
| **Pre / Post roll** | `roll_start` / `roll_end` | 0 / 0 | **Extends** the rendered range: `first - pre … last + post`. |
| **Pool** | `farm.default_pool` | `general` | |
| **Pri / Tiles** | `farm.priority` / `farm.tile_count` | 50 / 4 | |
| **Batch** | `farm.batch_size` | 10 | Frames per farm task. |
| **Samples** | `render.pathtracedsamples` | 64 | Applied as a Karma override (`karma:global:pathtracedsamples`). |
| **Denoise** | `render.enabledenoising` | on | Adds denoise jobs after each render. |
| **Motion blur** | `render.enablemblur` | on | Override `karma:object:mblur`. |
| **DOF** | `render.enabledof` | on | Override `karma:global:enable_dof`. |
| **Standalone** | — | off | Off = direct render of the collapsed staged file. On = a farm **stage** job builds the render stage first. See [Render staging](../composition.md#render-staging). |
| **Copy to edit** | — | off | Adds an *edit* job that syncs the finished AOVs to the edit location. |

There is no step field: farm renders always submit `step_size = 1`.

## Submit

Submit refuses with a message when nothing is ticked ("Tick at least one cell
before submitting.") or a Render cell is ticked with no channel checked. With
more than one entity, or any warning, it asks first — the summary line
(`12 publishes · 8 playblasts · 2 renders — 9 shots`) and the warned
entities.

It then does the cheap part in Houdini — each ticked row's settings resolved
into a **plan** — and hands the rest to a separate process running Houdini's
bundled Python (not hython: it takes no licence). The dialog closes straight
away. Bundling workfiles, snapshotting staged stages and talking to Deadline
all happen in that process, so Houdini is not locked for the length of the
submission, however many shots it covers. The process keeps going if Houdini
is closed or crashes afterwards.

A small non-modal **Farm submission** window follows it: one row per entity
(`queued`, `submitting…`, `submitted · N jobs`, or `failed` with the error
underneath), a progress bar, **Open log folder**, **Retry failed** (submits
just the failed entities again, as a new submission) and **Hide**. Closing it
does not stop the submission.

Each submission keeps its files in its own folder under
`temp:/farm_submissions/` (machine-local): `plan.json` (the settings sent for
every entity), `progress.jsonl` (what the window reads) and `runner.log` (the
process's output — the first place to look when an entity fails).

## From TumbleTrove Desktop

The **Farm Submit** button in a project's Scripts panel in TumbleTrove Desktop opens
the same dialog **without starting Houdini**. The launcher
([`scripts/farm_launcher.py`](../../scripts/farm_launcher.py)) picks the
newest installed Houdini that satisfies both the project's and TumblePipe's
Houdini version range (or `$HFS`, when set), builds the environment a Houdini
session would have for the project from its package files, and opens the
dialog in that Houdini's bundled Python. Submitting works exactly as above.

Until TumbleTrove Desktop passes the user's name to scripts, submissions made
from Desktop carry **no user name** in their batch names and notifications.

## What is submitted per entity

`submit_entity_batch` builds one Deadline batch per entity named
`<project> [publish] [render] [playblast] <entity uri> <user> <timestamp>`,
staged through a temp directory and submitted via `export:/other/jobs`.

**Publish** — each ticked department, in pipeline order, is skipped when it
has no saved workfile (or the entity is a `000` placeholder). Each gets a
`publish_<dept>` job chained after the previous one, bundling the latest
workfile and its `context.json`; a Discord **notify** follows. Workfiles are
versioned and never overwritten, so the version bundled is the one that was
latest when the submission ran — saving again in the meantime changes
nothing.

Before it exports, a publish job points every import node in the workfile at
its newest publish and re-runs it, overriding any version the artist pinned
while working: a published department always composes against the newest
upstream. The node types are the ones the scene-load refresh uses
(`scene_imports.REFRESH_SPECS`).

**Previews after a publish** — when a row publishes *and* playblasts or
renders, the preview has to show what this submission publishes. So after
the last publish job the batch adds a staged **build** job per channel the
previews need (a first-ever department export, or a newly imported asset, is
absent from the old build), and then a **collapse** job (`houdini` group)
that snapshots the freshly built stage and submits the playblast and render
jobs itself, with that snapshot bundled. Up to TumblePipe 1.53, previews were
snapshotted when the dialog submitted — *before* the publish had run — so a
publish + playblast submission played the previous version and nothing
flagged it. A **Standalone** render builds its stage on the farm anyway; after
a publish it now also waits for the rebuilt staged file.

**Previews with nothing published** — snapshotted at submit time, as before,
so a broken stage fails in the submission window rather than on the farm:

- *Render*, direct (Standalone off): each checked channel's latest staged
  file is collapsed into `collapsed_stage_<channel>.usda` with the cut
  applied and the Samples / Motion blur / DOF overrides baked onto the stage's
  render-settings prim; husk renders that. `No staged file found for <entity>
  channel '<x>' … Publish the entity first to create staged files.` and
  `Cannot collapse the staged stage …` (a layer the build records is missing
  on disk) fail that entity alone.
- *Render*, standalone: the overrides go into `render_settings.json`, a
  **stage** job builds `stage_<channel>.usd` on the farm, and the render
  chain depends on it.
- *Playblast*: the `default` staged file is collapsed with the playblast cut
  (no overrides) and one playblast job is submitted over the shot's
  configured range.

Either way the render department must be renderable (`Render department
'<x>' is not renderable. Renderable departments: …` otherwise), the
[department cut](../composition.md#the-department-cut) — every pool
department up to and including the chosen one — is applied, and AOV names are
gathered from every department's export `context.json` (own and referenced
assets), falling back to `config:/usd/context.json`. The override prim is
discovered on the stage, never assumed — see
[Where the render settings live](../composition.md#where-the-render-settings-live).

## Job families

Source: [`farm/jobs/houdini/`](../../python/tumblepipe/farm/jobs/houdini/).
Each job runs in a Deadline **pool** from the settings' Pool field and a
fixed Deadline **group** per task type. Every family ends in a **notify**
job that posts to Discord — render and playblast notifies address the
channel named `renders`, publish and stage notifies the one named
`exports`; the name is looked up under `discord/channels` in the project's
`config` database (see [The config database editor](config-editor.md#the-databases)).

A project that has **no** Discord setup at all — no token and no channels,
which is what a project created from the template carries — posts nothing
and the notify job succeeds anyway, logging `Skipping discord notification:
this project has no discord configuration`. The notify is the last job in
its family, so failing it would red the whole batch over a message nobody
had asked for. Once a project *does* have a token and channels, a name with
no `channel_id` is a real misconfiguration and fails the notify job with
`Channel not found in discord config: <name> (configured channels: …)`.
Both behaviours are from **1.52.2**; before it, every notify on an
unconfigured project failed, and with it every render and playblast batch.
Output locations are in [Compositing → Where renders land](../compositing.md#where-renders-land).

| Family | Triggered by | Chain (job name → group) | Output |
|---|---|---|---|
| **render**, Full range | Render cells | per channel: `full_render_<ch>` (`karma`) → `full_denoise_<ch>` (`denoise`, if Denoise) → `layer_mp4_<ch>` (`encode`); then across channels: `edit` (`edit`, if Copy to edit), `slapcomp` (`houdini`) → `slapcomp_mp4` (`encode`) → `slapcomp_notify` (`general`) | `render:/render/<shot>/<dept>/<channel>/vNNNN/<aov>/`, denoised frames under the `denoise` department, slapcomp MP4 + daily |
| **render**, First / Middle / Last | Render cells | per channel: `partial_render_<ch>` (`karma`) → `partial_denoise_<ch>` (if Denoise) → `partial_notify_<ch>` — three frames, no MP4 | same version folders, three frames only; the notify carries first/middle/last |
| **stage_render** | Render cells with **Standalone** | `stage` (`houdini`) → the render chain above | `stage_<channel>.usd` in the job's data dir |
| **publish** | Publish cells; Farm mode in the process dialog | `publish_<dept>` (`houdini`) per department, chained → `notify` (`general`). A renderable asset publish then queues a **build** | `export/<entity>/<channel>/<dept>/vNNNN/` |
| **build** | after a farm publish; Build USD in Farm mode; before the previews of a row that also publishes | `build` / `build_<channel>` (`general`) | `_staged/<channel>/vNNNN/` |
| **collapse** | Playblast or (non-Standalone) Render cells on a row that also publishes | `collapse` (`houdini`), after the row's builds; it then submits the playblast and render families above with the fresh snapshot | nothing of its own; the previews' batches |
| **playblast** | Playblast cells | `playblast` (`playblast`) → `playblast_notify` (`general`) | `render:/playblast/<shot>/<dept>/vNNNN.mp4` + the daily |
| **composite** | the `th::build_comp` node's **Submit** | see [Compositing → Farm submission and MP4s](../compositing.md#farm-submission-and-mp4s) | `render:/render/<shot>/composite/…` |
| **export_render** | the `th::lookdev_studio` HDA | `export` (`houdini`) → `notify` | lookdev turntable renders |
| **propagate**, **update** | command line only (`propagate`: re-publish dependents of a department; `update`: republish every shot up to a department) | publish/build jobs → `notify` | exports and staged builds |
| **cloud_render**, **cloud_stage_render** | the cloud stage task, not the dialog | `stage` → render → denoise → slapcomp → mp4 → notify (`cloud_karma`) | as render |

Job priorities come from the settings; notify jobs run at 90, edit at 90,
stage notify at 60. The playblast group is kept separate from `karma` so
previews never contend with final frames — those workers need a GL-capable
GPU, see [Farm worker prerequisites](../deadline.md#farm-worker-prerequisites).
