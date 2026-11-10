# Submitting to the farm

The **Farm Submit** dialog sends publish, playblast and render jobs for any number
of shots or assets to Deadline. Its **Assets** and **Shots** tabs each show
every entity of that context as a row and every pipeline step as a column,
so you can see what is out of date and
tick what should run, from one cell up to the whole project. Submitting hands
the work to a separate process, so Houdini is usable again within seconds.
Farm Submit arrived in TumblePipe **1.54.1**; it replaced the Submit Jobs
dialog, which had one form for the whole batch and submitted from inside
Houdini.

Source: [`submit_jobs_dialog.py`](../../python/tumblepipe/asset_browser/submit_jobs_dialog.py)
(the dialog), [`submit_grid.py`](../../python/tumblepipe/farm/submit/grid.py)
(what cells, ticks and warnings mean), [`submit_status.py`](../../python/tumblepipe/farm/submit/status.py)
(reading each cell's status), [`submit_settings.py`](../../python/tumblepipe/farm/submit/settings.py)
(how each setting resolves per entity), [`submit_plan.py`](../../python/tumblepipe/farm/submit/plan.py)
(the background submission) and
[`batch_submit.py`](../../python/tumblepipe/farm/pipelines/batch_submit.py)
(what gets built and submitted). Farm prerequisites are in
[Deadline and the render farm](../deadline.md).

Every job here runs on a Deadline farm, and the machine you submit from
needs the Deadline Client. Without it, **Submit** says *Deadline is not set
up on this machine* and submits nothing. Working without a farm is covered in
[Rendering without a farm](../compositing.md#rendering-without-a-farm).

## Opening it

| From | Menu / button | Opens on | Ticked |
|---|---|---|---|
| The toolbar | **Render** quick action | The scene's context (**Shots** with no workfile loaded) | The loaded scene's entity's **Render** cell — or, in a [Multi](multis-and-roots.md#multis) workfile, every member's. Nothing with no workfile loaded. |
| A card's right-click menu | **Submit Jobs…** | The card's context | That entity's **Render** cell |
| A multi-selection's right-click menu | **Submit Jobs for N selected…** | The cards' context | Each selected card's **Render** cell |
| TumbleTrove Desktop, outside Houdini | the project's Scripts panel → **Farm Submit** | **Shots** | Nothing — see [From TumbleTrove Desktop](#from-tumbletrove-desktop) |

None of these needs a pipeline workfile: the rows are always every entity of
the tab's context, and the ticks are only a starting point — tick or untick
any cell, on any entity, before submitting.

When the dialog is opened from a workfile, that workfile's department pins
the **Up to** department of Playblast and Render (when it is renderable), so
submitting from a lighting workfile previews up to lighting.

The dialog is non-modal: Houdini stays usable while it is open. What you
ask of it before submitting (the review), why it cannot submit yet, and the
submission's progress all show on a card over the dialog itself rather than
in windows of their own, and the dialog stays open after a submission, so
you can go **Back to grid** and submit more.

### What it remembers

Closing the dialog keeps, per project and tab, for the next time it opens:

- the settings you **pinned** (set for the whole submission), except
  **Frames** (the Render's and the Comp's), which belong to the shots they
  were typed for;
- the Render section's **Range**, **Standalone** and **Copy to edit** choices,
  and the Comp section's **Preview frames** and **Copy to edit**;
- the columns you collapsed.

And for the dialog as a whole: which settings sections are collapsed, whether
empty columns collapse by themselves, **Clean up old jobs**, **Run on** and its
size. A remembered value the project no longer offers (a department since
removed, say) is dropped, not guessed at. The workfile you open it from still
wins: its department pins **Up to** over a remembered one, and that pin is
the workfile's, so it is not remembered for the next opening.

### Assets and Shots tabs

The tabs above the grid switch between assets and shots, whatever workfile
is open — so from an asset workfile you can still submit shots. Each tab has
its own grid and settings, because the department columns (and Playblast,
which is shots only) differ between them.

- The dialog opens on the tab in **Opens on** above. Only that tab starts
  with ticks and with the workfile's department pinned; the other tab lists
  every entity of its context with nothing ticked and nothing pinned.
- Each tab keeps its ticks, filter and settings while the dialog is open, so
  you can switch away and back. A tab holding ticks shows how many entities
  it has ticked, as in **Assets (2)**.
- **Submit** sends the tab in front only. Ticks on the other tab are not
  submitted: to send both, submit one tab, go **Back to grid**, switch tabs
  and submit the other.

### The Edits tab

The **Edits** tab lists the project's [edits](play-shots.md#saving-and-publishing),
one row each, with one step: **Render Edit**. An edit has no departments,
so this tab has no grid columns or status. Each row offers the edit's
finished published versions, the latest first; an edit never published
reads *not published* and cannot be ticked. A working version is never
offered, because it can change while the job runs. **Pool** and
**Priority** beside the list apply to every ticked edit.

Each ticked edit is one job, `render edit <name> <version>`, in the
`encode` group. A worker renders that version with the editor, headless and
on the CPU, sound included, and Houdini's `hffmpeg` encodes it as H.264 with
AAC sound and a timecode track starting at the edit's start timecode
(`01:00:00:00` unless the edit says otherwise). The movie is written to
`render:/edits/<name>/<name>_edit_vNNNN.mp4`; rendering the same version
again replaces it. No Discord notify follows. **Run on: This machine**
runs the same job here.

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
- **Playblast** (shots only), **Render** and **Comp** (shots only, see
  [Comp](#comp-shots-only));
- **Warnings**.

Each kind of step has a colour: blue for publishes, violet for Playblast,
pink for Render, cyan for Comp. It runs along the top of the kind's column headers, fills
its ticked cells, and edges the kind's settings section beside the grid, so
a section can be traced to the cells it speaks for. A heavier rule separates the kinds.

Hover a column's header for what the column does and what each mark in it
means (this replaced the legend above the grid); hover a cell for its own
state.

### Collapsing columns

A column can collapse to a thin sliver, to keep the grid to the columns you
are working with:

- **By itself**: once the status is read, a column with nothing to do on any
  row (a department no entity has a workfile in) collapses, unless a cell in
  it is ticked. It opens again by itself when a later **Refresh** finds work
  in it. **Columns ▸ Collapse empty columns by themselves** turns this off.
- **By hand**: hover a header and click the **‹** beside its checkbox,
  right-click a header, or untick the column in the **Columns** menu.

Click a collapsed column's header (its **›**) to open it again; **Columns ▸
Show every column** opens them all. Collapsing only changes the view: a
collapsed column's ticks stay and are submitted (its sliver shows them in the
kind's colour, and a dot where a cell is stale or never done), but **All**,
the row and sequence checkboxes and **Select stale** no longer reach it, and
a click on the sliver does nothing.

### What a cell shows

| Mark | Publish column | Playblast / Render column | Comp column |
|---|---|---|---|
| `·` dim | no workfile for this department — nothing to do | no frame range configured — cannot run | no composite workfile — nothing to comp |
| `○` | a workfile exists but was never exported | never made | never comped |
| `○` dim | the only workfile is a [Multi](multis-and-roots.md#multis)'s, and it has never exported this entity — Select stale skips it, but you can tick it | — | — |
| `●` amber | the workfile was saved after its latest export | older than the newest publish it composes | a render landed after the newest comp |
| `✓` green | exported since the last workfile save | newer than every publish it composes | newer than the newest render |
| `…` | status not read yet | status not read yet | status not read yet |
| filled in the kind's colour | **will be submitted** | **will be submitted** | **will be submitted** |

A preview "composes" the departments of its **Up to** cut: a playblast up to
animation is stale when layout or animation was published after it, not when
lighting was. Changing a Playblast or Render **Up to** re-reads the preview
columns. Hover a cell for what its state means.

A Multi covers every member for its departments, but its workfile need not
export them all: an environment Multi may export one shot of twenty. Until it
has exported a member, that member has no workfile of its own to be stale
against, so its cell is the dim `○` and **Select stale** and the stale
warnings leave it out. Tick it by hand for a Multi's first publish; once an
export exists the cell is stale or current like any other.

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
- **Select stale** ticks every visible cell that is stale or never done
  (not the dim `○` of a Multi that never exported that entity, and not in a
  collapsed column).
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
- `channel(s) … will be skipped (not defined on this entity)` — a checked
  render channel this entity does not define. The entity renders the checked
  channels it does define and leaves this one out; it does not render
  `default` in its place.
- `defines none of the checked channels (…), so it has nothing to render` —
  render is ticked but the entity defines none of the checked channels (for
  example `default` is unchecked and only another shot has the rest). This
  one **blocks** the submit: check one of its channels or untick its Render
  cell.
- `department '<x>' is not assigned to this entity` — the pool still
  composes it, so this is a warning, not a block.
- `Comp waits for the full render: …` — Comp and Render are ticked on the
  row, but the Render **Range** is Preview only, which makes no frames to
  comp. This one **blocks** the submit: tick **Full range**, or untick
  Render to comp the renders already on disk.
- `the comp frames <a>-<b> end before they start` — the Comp **Frames** you
  typed are the wrong way round. **Blocks**.
- `the comp frames <a>-<b> reach outside the render's <c>-<d>: …` — Comp and
  Render are ticked on the row and the Comp **Frames** go past the frames
  the render makes (its **Frames** with the rolls). **Blocks**: comp inside
  them, or untick Render to comp the renders on disk.
- `the comp frames <a>-<b> reach outside the shot's <c>-<d>: …` — a Comp
  ticked alone, over frames past the shot's range from the config (rolls
  included). A warning, not a block: the renders on disk may run past it,
  but a frame no render made is missing from the comp.

### Before it submits

**Submit…** first shows **Review submission** over the dialog: one row per
entity, its steps as chips in their kinds' colours, and what to check first
(`Ready` in green when nothing). The line under the title says how many steps
for how many entities, where they go and whether old jobs are cleaned up
first. Entities with a blocking problem come first, in red, under a banner
saying they can't be submitted, and **Submit** stays disabled until you go
**Back** and fix or untick them. Previews that would show stale, unticked
departments come next, in amber, under a banner with **Tick stale publishes**,
which ticks exactly those departments and takes you back to the grid. Other
warnings, including the channels each entity will skip, follow. **Submit N
steps** sends it (Enter does the same); **Back** (or Esc) returns to the grid
with nothing changed.

A single entity with nothing to check skips the review and is sent at once.

## The settings

The right-hand column holds the settings for the ticked cells, in four
sections: **Publish**, **Playblast** (shots only), **Render** and **Comp**
(shots only). A section
shows only while at least one cell of its kind is ticked — tick a Render cell
and the Render settings appear; with nothing ticked the column says *Tick a
cell to see its settings.* A pinned field in a hidden section keeps its
value, but only matters once a cell of that kind is ticked again.

Each section sits on a lighter frame edged in its kind's colour. Its headline
says what it covers (`3 shots`, or `5 publishes, 3 shots`) and collapses it
when clicked; a collapsed section still applies its settings.

### Fields follow each entity unless you pin them

Every field with a per-entity source is **tri-state**:

| State | How it looks | What is sent |
|---|---|---|
| Unpinned, entities agree | Grey value | Nothing — each entity uses its own configured value (the one shown). |
| Unpinned, entities disagree | `⟨per entity⟩` (a spin box parks one step below its minimum, a checkbox shows partially checked, a combo — **Materials** included — shows the placeholder row, a line edit is empty) | Nothing — each entity keeps its own value. |
| Pinned | White, with a jade edge on its left, and the row's **↺** | Your value, for every ticked entity. |

"The entities" are the rows with at least one tick (every visible row while
nothing is ticked). Editing a field pins it. The mouse wheel only changes a
field you have clicked into; over any other field it scrolls the settings,
so scrolling past a number cannot pin it by accident. To unpin, click the
row's **↺** ("Use each entity's own configured value"; it shows only while a
field in its row is pinned), pick the
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

The publish jobs themselves always run at priority 90, and so do the builds
and collapse that follow them on a row that also previews (see
[Previews after a publish](#submit)); this **Priority** field does not
currently change them.

### Farm priorities

Deadline hands an idle worker the highest-priority job in its pools, so every
job lands in a fixed band:

| Band | Priority | Jobs |
|---|---|---|
| Notify | 100 | every Discord notify (publish, preview, render, playblast) — it only starts once its work is done and takes seconds, so finished work is announced at once |
| Publish | 90 | publish jobs, and the builds and collapse that follow them |
| Preview | 80 | a render's **Preview** chain: first/middle/last render, its denoise (and, with **Standalone**, the stage job in front of it) |
| Render | artist's priority, at most 70 | the **Full range** chain: render, denoise, MP4s, slapcomp |

Playblasts keep the artist's **Priority** uncapped. Pool comes before
priority in Deadline's scheduling order, so the bands only hold among jobs a
worker could take from the same pools.

### Playblast (shots only)

| Field | Per-entity source | Default when unset |
|---|---|---|
| **Up to** | `submission.render.department` | last renderable department (the whole shot) |
| **Resolution** (W × H) | `playblast.res_x` / `playblast.res_y` | the shot's render resolution (`render.resolution`), else 1280 × 720 |
| **Renderer** | `playblast.renderer` | `viewport` |
| **Shading** | `playblast.shading` | `lit` |
| **Blur**: Motion blur, Depth of field, blur samples | `playblast.motion_blur` / `playblast.dof` / `playblast.blur_samples` | off, off, 16 |
| **Light** | `playblast.light` | `dome` |
| **Samples** | `playblast.samples` | 32 |
| **Materials**: Scene materials / Clay | `playblast.materials` | Clay |
| **Pool** | `farm.default_pool` | `general` |
| **Priority** | `farm.priority` | 50 |

Shading and Blur apply to TumbleView, Light, Samples and
Materials to Karma, and only the chosen **Renderer**'s rows show. While
Renderer is left `⟨per entity⟩` (the shots disagree), both sets show, each
label naming its renderer (`Shading (viewport)`, `Materials (Karma)`), under
a line saying so: Shading's `clay` and Materials' `Clay` are the two
renderers' own settings, not one setting twice. Up to TumblePipe 1.94 the
other renderer's rows were greyed out instead, which read as locked.
**Materials** is a three-row menu rather than a checkbox: a partially checked
box read as checked, so two submissions that looked the same rendered
differently.

**Up to** playblasts every department up to and including this one, in
pipeline order — the same cut the render uses. There is no frame or channel
field: a playblast always renders the shot's `default` staged stage over the
shot's configured range (rolls included), at the shot's fps. What it produces
is described in [Compositing → Playblast](../compositing.md#playblast).

**Renderer** picks what draws the frames: `viewport` is the viewport's look
(display colours and textures, shaded as **Shading** says) from
TumblePipe's own render delegate, on the CPU (husk still takes a Karma
Renderer licence, never an artist's Houdini seat); `karma` is
Karma XPU, lit and sampled by the fields below, which `viewport` ignores.
See [Compositing → Playblast](../compositing.md#playblast).

**Shading** is TumbleView's look, all with ambient occlusion:
`lit` is the scene's colours under lights that follow the camera; `clay` is
the same lights on plain grey; `albedo` is the scene's colours unlit;
`occlusion` is white with only the ambient occlusion. Karma ignores it.

**Blur** is TumbleView's motion blur (over the shot camera's
shutter) and depth of field (from its f-stop and focus distance; an f-stop
of 0 stays sharp), averaged over the blur samples (2–64). Both are off by
default, and off the frames are exactly as without them. Karma ignores it.
See [Compositing → Motion blur and depth of field](../compositing.md#motion-blur-and-depth-of-field).

**Light** picks the Karma look: `dome` switches the scene's lights off and lights
the stage with one white dome light (soft clay shading); `headlight` is a
light on the camera with ambient occlusion, the look before this setting
existed; `scene` renders with the shot's own lights. **Samples** is Karma's
pixel samples: more is cleaner and slower.

### Render

| Field | Per-entity source | Default when unset | Notes |
|---|---|---|---|
| **Up to** | `submission.render.department` | last renderable department (the whole shot) | Renders every department up to and including this one; departments after it are left out. Also names the render output. |
| **Camera** | — | read-only | The camera the render looks through: the `camera` of the RenderSettings prim in the latest published layer of the strongest department in the **Up to** cut that sets one, else the project's `root_default_prims.usda`. Read from the first checked channel; the tooltip names the department per entity, and `⟨per entity⟩` means the ticked entities disagree. A preview — the farm still asks the composed stage. |
| **Channels** | — | the entities' own channel lists | A dropdown of checkboxes (`(none — check at least one)` when nothing is checked) with **All**; click it to pick, it takes no typing. Lists the *union* of the rendering entities' channels, `default` first. Opens with the channels *every* rendering entity starts with checked: its own channels, plus `default` only when it has no others or `default` is one of its [render layers](../nodes/lighting-and-rendering.md#render-layers-from-channel_prims-render-passes) (the remainder, under the slapcomp). So a shot split into `heart`, `background` and `bubbles` by export branches does not also render its whole unsplit scene; check `default` to. When the entities share nothing it opens with `default` if one of them renders unsplit, else with nothing checked. Channels arriving with a later-ticked entity start unchecked. **All** checks every channel in the batch. The checks apply to the whole batch, but each entity renders only the checked channels **it defines** — one render per such channel — and the Warnings column and the review list what each entity skips. See [Channels the farm refuses](#channels-the-farm-refuses). |
| **Range** | — | Full range ticked | Two checkboxes. **Full range** renders everything and chains slapcomp + MP4; **Preview (first / middle / last)** renders three check frames and posts them to Discord. Tick both to get the preview first, with the full range queued behind it. One of the two must be ticked. |
| **Frames** (first → last) | `frame_start` / `frame_end` | *required* — omitted, and the entity fails with "no frame range configured" | |
| **Pre / Post roll** | `roll_start` / `roll_end` | 0 / 0 | **Extends** the rendered range: `first - pre … last + post`. |
| **Pool** | `farm.default_pool` | `general` | |
| **Pri / Tiles** | `farm.priority` / `farm.tile_count` | 50 / 4 | Priority orders full renders among themselves and is capped at 70; see [Farm priorities](#farm-priorities). A preview always runs at 80. |
| **Batch** | `farm.batch_size` | 10 | Frames per farm task. |
| **Samples** | — | `⟨from scene⟩` — not sent | Set a value to override `karma:global:pathtracedsamples`; ↺ goes back to the scene's. |
| **Denoise** | `render.enabledenoising` | on | Adds denoise jobs after each render. |
| **Motion blur** | — | partly checked — not sent | Checked / unchecked overrides `karma:object:mblur`. |
| **DOF** | — | partly checked — not sent | Checked / unchecked overrides `karma:global:enable_dof`. |
| **Standalone** | — | off | Off = direct render of the collapsed staged file. On = a farm **stage** job builds the render stage first. See [Render staging](../composition.md#render-staging). |
| **Copy to edit** | — | off | Adds an *edit* job that copies the finished `beauty` (RGBA — the alpha is its A), the `objid_*` / `holdout_*` mattes and the `ramp_*` distance ramps to the edit location (the other AOVs stay with the render). |

There is no step field: farm renders always submit `step_size = 1`.

Samples, Motion blur and DOF are the only render settings the dialog can
override, and only when you set them: left alone they send nothing, and the
render uses what the scene's render settings (th::render_settings) author.
Up to TumblePipe 1.68.2 they were seeded from the entity's `render.*`
properties (on, 64) and always sent, so turning motion blur off on the node
still rendered with motion blur. Everything else — resolution, ray limits, camera, engine — comes
from the shot's own stage: the project's `root_default_prims.usda`, overridden
by any department layer that authors the render settings prim (typically a
`render` department layer). The entity's `render.camera`, `render.resolution`,
`render.overscan`, `render.pathtracedsamples`, `render.enablemblur`,
`render.enabledof`, ray-limit and dicing properties are **not** read at submit;
setting them in the config changes nothing on the farm.

### Comp (shots only)

A **Comp** cell re-comps the shot on the farm: its newest composite workfile
(the one [made from the composite template](../compositing.md#the-comp-workfile);
a [Multi](multis-and-roots.md#multis)'s, when a Multi covers the shot's
composite department) over the shot's full frame range (or the frames you
set), written as a new version of the merged comp,
`render:/render/<shot>/composite/slapcomp/v####/`.
The workfile is bundled as it is when you submit; its `th::import_render`
nodes on `latest` resolve when the job runs, so it comps the newest renders,
and a pinned import stays pinned.

- **Ticked alone**, it comps the renders already on disk.
- **Ticked with Render**, the comp waits for that render: it starts after
  the full render's denoise (or the render, without Denoise) and comps the
  frames it made. The Render **Range** must include **Full range**; with
  Preview only, the row is blocked (see [Warnings](#warnings)).

The cell is `·` (it cannot be ticked) when the shot has no composite
workfile: make one from the composite template first. Otherwise it is `○`
until the shot has a merged comp, and `●` stale when a render landed after
the newest comp: the render the Render column measures, the newest version of
the Render **Up to** department's `default` channel, so changing that cut
re-reads it. A comp is not a preview of the publishes: a stale department
does not make it stale, and ticking Comp alone raises no
`stale and not ticked` warning (Render beside it still does).

The section offers what an `export_comp`'s own **Jobs** and **Farm** parms
do for its Submit (see [Compositing nodes](../nodes/comp.md)), in the
dialog's way: a field you leave alone follows each shot.

| Field | Per-entity source | Default when unset | Notes |
|---|---|---|---|
| **Range** | — | off | **Preview frames (first / middle / last)**: comp those three frames first and post them to Discord (`previews`); the full comp waits for them. The node's **Preview Frames**. Beside a render, the preview frames still wait for the whole render. |
| **Frames** | — | `⟨full range⟩` | Left on `⟨full range⟩`, the comp comps every frame of the render ticked beside it (its **Frames** with the rolls), or, ticked alone, the shot's range from the config with its rolls (the node's **From Config**). Type a first and/or last frame to comp from or to it instead (the node's **From Settings**); the other end keeps following. Beside a render the frames must lie inside the render's; past them, or the wrong way round, the row is blocked (see [Warnings](#warnings)). ↺ goes back to `⟨full range⟩`. Not remembered for the next opening. |
| **Step** | — | 1 | Comp every Nth frame of the range. |
| **Batch** | — | 5 | Frames per farm task of the comp. |
| **Pool** | `farm.default_pool` | `general` | A dropdown of the project's farm pools, the project default marked `(project default)`, as a `th::` node's **Pool** menu offers them. `⟨per entity⟩` leaves each shot on its own pool. A project that lists no pools gets a text field instead. |
| **Priority** | `farm.priority` | 50 | |
| **Copy to edit** | — | off | Adds an *edit* job that syncs the merged comp to the edit as its `comp` layer (mattes and ramps still per channel); see [Compositing → Farm submission and MP4s](../compositing.md#farm-submission-and-mp4s). |

The node's **Pre/Post-Roll** have no field of their own: **Frames** names
the frames the comp comps, rolls and all, and `⟨full range⟩` already
includes the shot's (or the render's) rolls.

#### Channels the farm refuses

A channel is only worth rendering where something was exported under it. Two
checks behind the dialog make sure a channel never quietly renders the
`default` scene again under its own name:

- **At submit**, an entity asked to render a channel it does not define
  fails before anything is sent to Deadline: `<entity> does not define render
  channel <x> (it defines: …)`. The dialog never sends one, so this catches
  other callers.
- **On the farm**, the staged build for a non-`default` channel fails when no
  department has exported that channel: `No department of <shot> has
  exported channel '<x>' …`. Departments that did not export it still fall
  back to their `default` layer; the build only fails when *every* layer
  would be a `default` one. A render layer with prims
  ([Render layers](entity-editor.md#render-layers)) that nothing exported
  is the exception: it is a pass of the `default` scene, so it renders
  from the `default` build instead of failing.

## Submit

Submit refuses, on a card over the dialog, when nothing is ticked ("Tick at
least one cell before submitting."), when a Render cell is ticked with no
channel checked or neither **Preview** nor **Full range**, or when the farm
is not set up on this machine. Otherwise it shows the
[review](#before-it-submits), and will not submit while any entity is blocked
(a Render cell on an entity that defines none of the checked channels, or a
Comp cell beside a Preview-only render).

It then does the cheap part in Houdini — each ticked row's settings resolved
into a **plan** — and hands the rest to a separate process running Houdini's
bundled Python (not hython: it takes no licence). Bundling workfiles,
snapshotting staged stages and talking to Deadline all happen in that
process, so Houdini is not locked for the length of the submission, however
many shots it covers. The process keeps going if the dialog or Houdini is
closed, or Houdini crashes, afterwards.

### Clean up old jobs

With **Clean up old jobs** ticked (beside **Run on**; on by default, and
remembered), the process first removes each submitted entity's **finished
and failed** jobs, everyone's and of every step, from where the new ones go
(the farm, or this machine), so the [Farm Manager](../deadline.md) shows
only what is current for it. Queued, rendering and suspended jobs are never
touched, nor are other entities'. The progress says how many were cleaned up
per entity, and says so in amber when a clean-up failed or stopped short, so
you know the old jobs are still there. Untick it to keep the old jobs.

### Following it

The progress shows on the same card: one row per entity with its steps
(`queued`, `submitting…`, `submitted, N jobs`, with `; N old jobs cleaned up`
when any were and `; old jobs not cleaned up: <why>` when the clean-up
failed, `failed: <error>`, or `cancelled, not sent`), and a bar below
the rows carrying the summary (`Submitting… 3 of 20`, then `20 of 20 sent`,
with `, N failed` or `, N cancelled` when any were; green once done cleanly,
red if anything failed, grey if cancelled). A row's status is cut short when
it is long; under the rows, each entity that failed, or whose clean-up did, is
listed with its whole error, which often says how to fix it (select it to
copy it). **Open log folder**, **Farm Manager**, **Cancel** and **Close
window** sit below it.

Once it is over, **Back to grid** (or Esc) returns to the grid with the sent
entities unticked and their status read again, to submit more; **Close**
closes the dialog (green after a clean run).

**Cancel** stops the submission between entities: the entity being submitted
when you click it finishes (so no entity is left with only some of its jobs on
the farm), and every entity after it is marked `cancelled` and never reaches
Deadline. Jobs already submitted stay on the farm; remove them in the Farm
Manager if you no longer want them. Closing the dialog does not stop the
submission — only **Cancel** does.

### Clearing an entity's old jobs first

A submission can clear each entity's old finished jobs from the farm before
it submits that entity, so the Farm Manager and Deadline Monitor do not fill
up with runs nobody needs any more. The plan records the choice as
`cleanup` (`true` or `false`). A plan without it, such as one written before
the option existed, clears nothing.

When it is on, the process does this for each entity just before submitting
it, on the side the submission goes to (the Deadline farm, or this machine's
local farm):

- It takes the entity's batches in this project, whoever submitted them and
  whatever their steps were (publish, playblast, render...).
- It removes only a batch whose every job has **completed or failed**. One
  job that is queued, rendering, pending or suspended keeps the whole batch.
  So does a job that an unfinished job is waiting on.
- On the farm the batches are deleted from Deadline. On this machine they
  are taken off the local farm's list. Rendered outputs stay either way.

Which project and entity a job is for comes from the stamp every job carries
(see [The Farm Manager](../deadline.md#the-farm-manager)). For a job
submitted before the stamp, the batch name says it.

A clean-up that fails never fails or holds up the submission. The entity is
submitted anyway, its row says why the old jobs stayed, and the failure is
written to `runner.log`. Each entity's
clean-up adds one line to `progress.jsonl`, after its `running` line:
`{"event": "cleanup", "uri": ..., "removed": <jobs>, "batches": <batches>,
"error": null or "<why not all were removed>"}`.

When a finished submission has failures, **Retry failed** appears beside them:
it submits just the failed entities again, as a new submission. After a
cancel the same button reads **Submit the rest** and also picks up the
cancelled entities.

Each submission keeps its files in its own folder under
`temp:/farm_submissions/` (machine-local): `plan.json` (the settings sent for
every entity), `progress.jsonl` (what the progress reads), `runner.log` (the
process's output — the first place to look when an entity fails) and, once
**Cancel** was clicked, an empty `cancel` file the process checks for before
each entity.

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
`<project> [publish] [render] [playblast] [comp] <entity uri> <user> <timestamp>`,
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
jobs itself, with that snapshot bundled. The builds and the collapse run at
the publish jobs' priority (90), not the render's: the collapse is what
creates the render batch, so at the render priority it waited behind other
shots' renders. Up to TumblePipe 1.53.0, previews were
snapshotted when the dialog submitted — *before* the publish had run — so a
publish + playblast submission played the previous version and nothing
flagged it. A **Standalone** render builds its stage on the farm anyway; after
a publish it now also waits for the rebuilt staged file.

**Previews with nothing published** — snapshotted at submit time, as before,
so a broken stage fails in the submission's progress rather than on the farm:

- *Render*, direct (Standalone off): each checked channel's latest staged
  file is collapsed into `collapsed_stage_<channel>.usda` with the cut
  applied and any Samples / Motion blur / DOF overrides you set baked onto the
  stage's render-settings prim; husk renders that. `No staged file found for <entity>
  channel '<x>' … Publish the entity first to create staged files.` and
  `Cannot collapse the staged stage …` (a layer the build records is missing
  on disk) fail that entity alone.
- *Render*, standalone: the overrides go into `render_settings.json`, a
  **stage** job builds `stage_<channel>.usd` on the farm, and the render
  chain depends on it.
- *Playblast*: the `default` staged file is collapsed with the playblast cut
  (no overrides) and one playblast job is submitted over the shot's
  configured range.

**Comp** — the shot's newest finished composite workfile
(`latest_complete_hip_file_path`, a Multi's when one covers the shot) is
bundled with the batch; a shot with none fails with `<shot> has no composite
workfile to comp; make one from the composite template first`. Without a
render on the row the composite jobs (named `comp_…`) run at once; with one,
they are part of the render chain and start after its full-range denoise.
Comp on an asset, or beside a render without the full range, is refused.

Either way the render department must be renderable (`Render department
'<x>' is not renderable. Renderable departments: …` otherwise), the
[department cut](../composition.md#the-department-cut) — every pool
department up to and including the chosen one — is applied, and AOV names are
gathered from every department's export `context.json` (own and referenced
assets), falling back to `config:/usd/context.json`. The override prim is
discovered on the stage, never assumed — see
[Where the render settings live](../composition.md#where-the-render-settings-live).

## Job families

Source: [`farm/pipelines/`](../../python/tumblepipe/farm/pipelines/).
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
| **render**, Full range | Render cells | per channel: `full_render_<ch>` (`karma`) → `full_denoise_<ch>` (`denoise`, if Denoise) → `layer_mp4_<ch>` (`encode`); then across channels: `edit` (`edit`, if Copy to edit), `slapcomp` (`houdini`) → `slapcomp_mp4` (`encode`) → `slapcomp_notify` (`general`) | `render:/render/<shot>/<dept>/<channel>/vNNNN/<aov>/`, denoised frames under the `denoise` department, slapcomp MP4 + daily. Each channel keeps its own version counter (a new `characters` channel starts at v0001 while `default` may be at v0008); the slapcomp reads each channel at its own version and stacks them in the shot's channel order |
| **render**, Preview | Render cells | per channel: `partial_render_<ch>` (`karma`) → `partial_denoise_<ch>` (if Denoise) → `partial_notify_<ch>` — three frames, no MP4 | same version folders, three frames only; the notify carries first/middle/last |
| **render**, Preview + Full range | Render cells | both chains above; each `full_render_<ch>` waits on its `partial_render_<ch>` and renders into the same version | the preview's three frames are posted, then the full range overwrites them in the same version folder |
| **render**, Standalone | Render cells with **Standalone** | `stage` (`houdini`) → the render chain above | `stage_<channel>.usd` in the job's data dir |
| **publish** | Publish cells; Farm mode in the process dialog | `publish_<dept>` (`houdini`) per department, chained → `notify` (`general`). A renderable asset publish then queues a **build** | `export/<entity>/<channel>/<dept>/vNNNN/` |
| **build** | after a farm publish (in its batch when the publish dialog's Build USD rides along); Build USD in Farm mode; before the previews of a row that also publishes | `build` / `build_<channel>` (`general`) | `_staged/<channel>/vNNNN/` |
| **collapse** | Playblast or (non-Standalone) Render cells on a row that also publishes | `collapse` (`houdini`), after the row's builds; it then submits the playblast and render families above with the fresh snapshot | nothing of its own; the previews' batches |
| **playblast** | Playblast cells | `playblast` (`playblast`) → `playblast_notify` (`general`) | `render:/playblast/<shot>/<dept>/<sequence>_<shot>_<dept>_vNNNN.mp4` + the daily |
| **composite** | the `th::export_comp` node's **Submit**; [Comp](#comp-shots-only) cells (jobs prefixed `comp_`: `comp_full_composite` → `comp_slapcomp_mp4` → `comp_slapcomp_notify`, and `comp_sync` with Copy to edit; after the render's denoise when Render is ticked too) | see [Compositing → Farm submission and MP4s](../compositing.md#farm-submission-and-mp4s) | `render:/render/<shot>/composite/…`; a Comp cell writes the merged comp, `composite/slapcomp/vNNNN/` |
| **export_render** | the `th::lookdev_studio` HDA | `export` (`houdini`) → `notify` | lookdev turntable renders |
| **edit_render** | the Edits tab's **Render Edit** | `edit_render` (`encode`), one task, no notify | `render:/edits/<name>/<name>_edit_vNNNN.mp4` |

Job priorities come from the settings, inside the
[Farm priorities](#farm-priorities) bands; every notify job runs at 100,
edit at 90. Playblasts run in the `karma` group with final renders
— a Karma playblast needs the same NVIDIA GPU workers, see
[Farm worker prerequisites](../deadline.md#farm-worker-prerequisites).
