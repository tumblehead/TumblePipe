# Troubleshooting

Symptoms an artist meets, what is behind each, and the fix. Every message
quoted here is the text the code produces. When the fix is "update
TumblePipe", do it from the package card in TumbleTrove Desktop; the version
that shipped a fix is in the [changelog](../CHANGELOG.md).

## Setup and launch

### The Asset Browser shows no Assets or Shots

The pipeline catalog could not be built, and TumbleTrove shows it as a failed
source with the reason on Houdini's status bar. Two messages:

- `TumblePipe is installed, but this project has not been configured yet:
  <path> has no _config/db/entity.json. Use Configure… on the TumblePipe card
  in TumbleTrove Desktop to set the project up.` — `TH_PROJECT_PATH` names a
  folder that was never set up. Run the wizard: **Configure** on the package
  card, *Use an existing project* or *Create a new project*.
- `TumblePipe cannot find this project: TH_PROJECT_PATH points at <path>,
  which does not exist. Check the path in the project's settings in
  TumbleTrove Desktop, or that the share is reachable.` — a mistyped path, or
  a network share that is not mounted on this machine.

Background in [If the project was never configured](configuration.md#if-the-project-was-never-configured);
the wizard screens in [Getting started](getting-started.md#2-configure-the-project).

### A window appears before Houdini at launch

Both come from the `tt_prepare` hook, which runs on every launch:

- **TumblePipe — project configuration** ("This project's configuration is
  out of date") — the project's `_config/` is behind. **Migrate now** applies
  the listed steps (replaced files are backed up as `.bak`); **Not now**
  launches with the project untouched and asks again next time. `Cannot
  migrate: …` with **Continue without migrating** means a step is blocked —
  the reason is on the window; the project keeps working as it is. See
  [Migrating at launch](configuration.md#migrating-at-launch).
- **TumblePipe — project not configured** — see the previous entry;
  **Launch anyway** opens Houdini without a pipeline.

### The radial menus are missing (Alt+T does nothing)

The `tumbleradial` package is not installed. Houdini's log carries
`tumbleradial is not installed, so TumblePipe's radial menus (pipeline
submenus, recipes, asset favorites, cop/vop network menus) will not
register`. Install it from TumbleTrove and restart. Alt+R and Alt+F are also
absent when fewer than two recipes / favourites exist. See
[Radial menus](radial-menus.md).

TumblePipe v1.48.0 and earlier fail at startup against Radial 0.4.0 with
`AttributeError: module 'tumbleradial' has no attribute 'register'`, which
takes Alt+T, Alt+R, Alt+F and the COP/VOP menus down with it. Update
TumblePipe. If only the COP/VOP menus are missing and the log says
`tumbleradial predates bind() (Radial 0.4.0)`, update Radial instead.

### Shift+Space does nothing, or opens the wrong department's menu

The department menu needs a TumbleTrove with the workfile context and a
Radial with department menus. With older versions, Shift+Space does nothing
and nothing is logged. When both are installed:

- **The scene isn't a workfile.** The department comes from the
  `context.json` beside the open .hip, so an unsaved scene or one saved
  outside the pipeline has none. Pick one under **TumbleTrove ›
  Department**; the pick is saved in the .hip.
- **A different menu opens.** A menu from a higher layer replaced
  TumblePipe's stock one for that department: yours, the project's
  (`_config/radial_menus/`), the organisation's or a package's, such as
  TumbleRig's rig radial. See
  [Radial menus → Shift+Space](radial-menus.md#shiftspace-the-department-menu).
- **The cursor is over the wrong kind of network.** Each stock menu is made
  for one network type: the model menu for SOPs, the light menu for LOPs,
  and so on (the table on the Radial menus page). Open it over that kind of
  network.

### The User column is blank

`TH_USER` is empty. The package wires it to `TT_USER_NAME`, which only the
TumbleTrove Desktop launcher injects — a Houdini started any other way has no
user, and the pipeline deliberately never falls back to the OS username. The
browser's **User** column, sidecars and farm attribution all read the same
variable. Launch through Desktop. See
[Environment variables](configuration.md#environment-variables).

## Saving, exporting, publishing

### Export or Publish "did nothing"

It no longer can: an action you clicked either succeeds, or tells you why it
did not.

- **Publish** on a scene with no pipeline context shows `Publish: the loaded
  scene has no pipeline context, so there is nothing to publish. Save the
  scene through the pipeline first.` — the hip was not opened or created
  through the browser. **Save** behaves the same way (`Save: the current
  scene has no pipeline context, so there is no version to save it as. Open
  or create the scene through the pipeline first.`).
- Any other failure produces an error dialog of the form
  `<action> failed: <ExceptionType>: <message>` followed by `Full traceback:`
  and the log file(s) it wrote to. Open the named log; the traceback is the
  bug report. See [Where the logs are](development.md#where-the-logs-are).
- A Publish that ran but skipped steps warns with the list of skipped steps
  when it finishes — a disconnected export node, for instance, is skipped
  rather than failing the other channels.

If the button truly does nothing and no dialog appears, check two things.
First, which button: up to 1.46.0 `th::export_layer` also showed the inner
USD ROP's own **export** folder, whose **Save to Disk** button sits a few
lines from the real **Export**. Save to Disk fires the raw ROP at whatever
output path the last pipeline export left behind (or none), so it writes a
stale temp file or errors on the node badge, and no dialog opens. That folder
is hidden in later releases; on 1.46.0 or older, use **Export**. Second, the
version: a TumblePipe older than **1.42.0** predates the failure dialogs;
check it under **TumbleTrove ▸ About TumbleTrove...** and update.

### Export says "No export tasks found for the current context"

The workfile's own entity decides which export nodes run. A node addressing
any other entity is left out, and when every node is left out there is
nothing to export. Open the warning's details: they list each dropped node,
the entity it asked for, and the entity the workfile belongs to. The rules
and every detail line are under
[Why a node is left out](asset-browser/export-and-publish.md#why-a-node-is-left-out);
TumblePipe 1.47.1 and older show the bare warning with no details.

The common case is a multi-shot scene that is not in a Multi. A workfile
made from a *shot's* department row belongs to that shot, even when the shot
is named like a Multi and its export nodes target the Multi's members. Adding
departments to the Multi does not change that file. Work in the Multi's own
department row instead (**New from Template** there, then bring the scene's nodes
across), and check the details line *Multi … already lists every one of them
as a member* to confirm which Multi.

Two related traps:

- **A Multi with no department rows.** Before 1.46.0 **New Multi…** created
  a Multi covering no departments, so it had no rows to open or create from.
  Add its departments through **Edit Multi…**; see
  [Multis and Roots](asset-browser/multis-and-roots.md#coverage-which-departments-the-multi-owns).
- **A Multi workfile that only exports its own department.** Up to 1.47.1 a
  Multi workfile never listed downstream departments; later releases do.
- **Rigs in an asset Multi.** Up to 1.51.0 an asset Multi's `rig` workfile
  never collected its `th::export_rig` nodes, even with the Entity set, and
  the details said *no th::export_layer nodes*. Later releases collect them;
  set each node's **Entity** to its character, since `from_context` cannot
  pick one member of a Multi.

### Publish from a Multi workfile only lists one member

The scene sits in the Multi's folder
(`<project>/groups/<context>/<name>/<department>/`) and its export nodes
target every member. But Publish only offers the nodes for one member shot,
and the warning details, if any, start with *This workfile publishes
`entity:/shots/…`* rather than the Multi. Copying export nodes in from
another department's workfile is not the cause, as long as each node's
**Entity** and **Department** are right.

The folder's `context.json` names a member instead of the Multi. Up to
TumblePipe 1.52.0, creating a workfile from a member's *covered* department
row wrote the file into the Multi's folder but recorded the member, and
every later Save kept it. Publish then treats the scene as that one shot
(see [Why a node is left out](asset-browser/export-and-publish.md#why-a-node-is-left-out)).

With a later release, **Save** once and reopen the scene: the save
records the Multi again. On an older release, change `uri` in that
folder's `context.json` to the Multi's `groups:/<context>/<name>`, keeping a
copy of the file, then reopen the scene.

### A node's menu raises "Not an entity URI: groups:/..."

Opening a Multi's workfile, or just clicking a parameter on
`th::playblast` (LOP) there, pops a traceback ending in

```
ValueError: Not an entity URI: groups:/shots/<Multi>
```

Nothing was pressed: the error comes from the node's **Department** menu.
A Multi's workfile records the Multi (`groups:/shots/<Multi>`) as its
entity, which is correct — but 1.52.1 and older handed that group straight
to the department lookup, which only accepts a single shot or asset.

Later releases resolve nothing from the context there instead, and the
Department menu lists the departments the Multi covers. The node still
needs a shot to write to, so pick the member with the **Entity** button —
see [`th::playblast` (LOP)](nodes/lighting-and-rendering.md#thplayblast-lop).
`th::create_model` and `th::render_debug` read the same pointer and now
turn a Multi away the same way.

### The export refused: "outside the export folder", "do not exist", "carry no pipeline metadata"

Each is a deliberate guard in `th::export_layer`, and the dialog names the
offending paths or prims:

- `Export aborted: the exported layer composes geometry from path(s) outside
  the export folder …` — a LOP in the scene has **Layer Save Path** enabled
  (Houdini 22 turns it on for new SOP Create / SOP Import nodes, pointing at
  `$HIP/usd/`). Disable *Enable Layer Save Path* on the node(s) named and
  re-export. Caches belong in a `th::cache` node, whose versioned locations
  are allowed by reference. If the paths listed are `export/assets/…/_staged/…`
  layers, an import node is in *Import Mode* Inline on a TumblePipe older
  than the fix that flattens inline imports: update the package, or switch
  the node to Reference. See
  [Layer save paths and export portability](composition.md#layer-save-paths-and-export-portability).
- `Export aborted: the exported layer composes geometry from path(s) that do
  not exist, so the published asset would import empty …` — a dangling arc,
  typically a payload anchored to machine-local scratch. Fix the
  `asset_payload` / export setup so the geometry is written into the version
  folder, and re-export.
- `Export aborted: asset(s) on the stage carry no pipeline metadata, so they
  would silently drop out of the published layer …` (or the variant naming
  the upstream import nodes) — an imported asset lost its `customData` tag,
  usually because a layerbreak stripped it or duplicates did not inherit it.
  Re-run the import node (its **Import** button) and re-export; to bake the
  asset in instead, set the import node's *Import Mode* to Inline. See
  [Dropped-metadata guard](composition.md#dropped-metadata-guard).

### A new publish does not show in my scene

Two different staleness layers:

- **Opening the workfile** re-resolves imports, however it is opened, while
  **Auto-import latest on workfile open** is enabled (on by default, on the
  browser's TumblePipe settings page). With it off, an open restores the
  versions baked into the hip. Before this was fixed, only an open through
  the Asset Browser refreshed; File ▸ Open did not. A node set to a specific
  version keeps that version on purpose. See
  [Picking up new versions on open](composition.md#picking-up-new-versions-on-open).
- **An already-open scene** needs the **Update** quick action, which
  re-executes every `th::import_*` node against the newest publishes without
  reloading the hip. Success is a status line (`Imports updated to latest
  published versions (N node(s)).`); if some nodes failed you get a dialog
  (`Update Imports: N import node(s) failed to update, M succeeded. The scene
  may still reference older published versions — see the log for which
  nodes failed.`). See
  [Picking up new versions mid-session](composition.md#picking-up-new-versions-mid-session).

Neither reaches the farm on its own — a render composes the shot's *staged*
build, so the shot has to be re-staged for a new asset publish to appear
there.

### A later department's work is missing from my shot (e.g. no camera)

`th::import_shot` leaves out your own department and, by default, every
department after it, so an upstream department such as environment does not
see the animation layer, or the camera that lives in it. Set the node's
**Exclude** to **This Department Only** to see them; the environment template
does this for new workfiles. Their opinions still win over yours in the final
shot, which is why it is not the default — see
[`th::import_shot`](nodes/import-and-export.md#thimport_shot-lop).

Up to 1.56.1, inside a **Multi** workfile, Department `from_context` showed
`from_context: none` and the node left out *nothing*: later departments
appeared, and so did your own department's last publish, underneath your
live edits. Later releases resolve the Multi's department, so a node left on
`from_context` now cuts as above — set Exclude if you relied on seeing the
later departments.

### My check import below the export doesn't show what I published

A `th::import_shot` placed under the export to review the publish still
leaves out its own department, so your animation (or whatever this workfile
authors) is missing from it. Set that node's **Exclude** to **Nothing** to
load every department, your own published layer included.

### An imported asset is empty

Older publishes of `th::asset_payload` saved their payload to a bare
`payload.usd`, which USD resolved against the process working directory —
one file shared by every asset and every session, clobbered on each publish,
so an asset could compose another asset's geometry or nothing. The sidecar is
now anchored to `$HIP`, keyed on the asset prim, and copied into the version
folder. Re-export the asset with a current TumblePipe; a dangling or escaping
payload is now refused at export (previous entry). See
[Layer save paths and export portability](composition.md#layer-save-paths-and-export-portability).

### Something I added from an asset library is empty or black in the next department

A model referenced from a library folder outside the project (a plain
Reference LOP) arrived as empty Xforms, or a dome light's HDRI went black,
with no error. Exports up to TumblePipe 1.62.1 copied only the referenced
file, not the geometry and textures it points at, and wrote textures
relative to a temporary folder. Re-export the department with a current
TumblePipe: library files now travel with the publish under `external/`.
See [Layer save paths and export portability](composition.md#layer-save-paths-and-export-portability).

### A set publishes with a shot's characters and props inside it

A set's staged file sublayers assets that belong to a shot (HideAndReek's
`SET/Park` picked up Ryan, Daughter, Toni, Koala and the PoliceCar). The set's
lookdev workfile imported a shot's light layer for context with an
`import_layer` node. That node tags the shot's assets on the stage, and up to
TumblePipe 1.62.1 the asset export recorded them as the set's own and the
asset build sublayered them. Exports and builds now ignore any tracked asset
that reached the stage through a shot layer, so **Build USD** on the set
again gives a clean staged file without re-exporting. Bypassing the shot
import before exporting also avoids it on older versions.

### A deleted or renamed asset came back with old work, or a renamed one is empty

Up to TumblePipe 1.62.1, deleting an asset left its folders, so a new asset
of the same name picked them up, and renaming one in the Database Editor
left its work under the old name. Delete now moves the folders to
`_deleted/<date-time>/`, renaming an asset with files is refused, and
**Duplicate…** on the card makes a copy under a new name. To recover old
work, move its folder back out of `_deleted/`. See
[Delete](asset-browser/index.md) and [Duplicate](asset-browser/index.md#right-click-on-a-card).

### Two assets with the same name in different case

Windows storage is case-insensitive but entity URIs and prim paths are not,
so `Clash` and `clash` become two pipeline entities sharing one folder;
assets caught in the split lose their metadata and are blocked at publish.
Ask whoever maintains the project to run `scripts/verify_entity_casing.py`
and, if needed, `scripts/fix_case_duplicate_category.py`. See
[Entity casing audits](configuration.md#entity-casing-audits).

### A shot opens as an asset, or "'X' is already a sequence"

A category and a sequence with the same name break the browser's addressing:
a card's id does not say which kind its middle part is, so the browser
decides from the name, and every shot in such a sequence resolves to an
asset that does not exist. **New Category**, **New Sequence**, **New Shot**
and **New Asset** now refuse a name the other kind already uses. A project
that already holds such a pair (created before the guard) still shows the
symptom, and there is no automatic repair: entity names are also folder
names under `export/` and the workfile tree, so renaming one side in the
config database alone would orphan its published work. Ask whoever
maintains the project.

### The column says "Channels" but paths and parms say "variant"

Intended. A **channel** is the pipeline's publish-tree fork (`default`,
`bg`, `fg`, …); *variant* is reserved for USD's own `variantSet`. Only labels
were renamed — the `variants` property key, the `<variant>` path segment and
HDA parm internal names keep the old spelling on purpose, and readers accept
both. A project whose column still reads *Variants* has not taken the v5
config migration. See
[Channels, and why they are not USD variants](composition.md#channels-and-why-they-are-not-usd-variants).

### Opening a model or lookdev scene warns "Ignoring data for locked node"

Expected once, on a scene saved before `MODEL` and `LOOKDEV` locked their
wrapper networks (`variant_sopnet`, `lookdev_variant_subnet`) so that **U**
from inside them leads back to `/stage`. The warning lists what was
dropped: the old per-variant nulls, plus anything you had placed in the
wrapper *beside* `create_variants` / `lookdev_subnet`. Everything inside
those two networks, which is where your geometry and materials live, is
kept. Rebuild anything you need from beside them inside the network, then
save to stop the warning. See
[`th::create_asset_model`](nodes/assets.md#thcreate_asset_model-lop).

### Multi actions do nothing (delete, edit, add members, drag-and-drop)

TumblePipe before **1.45.1** never claimed Multi and Root collections in the
browser, so every operation on one except creation silently no-oped. Update
to 1.45.1 or later. See [Multis and Roots](asset-browser/multis-and-roots.md)
and [Configuration → Multis](configuration.md#multis-multishot-workfiles).

### A Multi's version dropdown only shows the latest version

In TumblePipe 1.57.0 and earlier, a Multi's detail panel listed only the
newest workfile per department, and the row's play button and its user and
time stayed blank or did nothing. From 1.45.0 to 1.57.0, **Open Location** on
a Multi or Root card also did nothing. Update to a later version. Older
versions are still on disk, in `<project>/groups/<context>/<name>/<department>/`.

## Rendering and the farm

### An entity failed in the Farm submission window

The window shows the error under the entity's row; it is the same message the
submission raised, before anything for that entity reached Deadline (the
entities before and after it are unaffected). Most are about the entity, not
the submission — `No frame range for …`, `No staged file found for … Publish
the shot first`, `Render department '<x>' is not renderable` — and the
Warnings column of the [Farm Submit dialog](asset-browser/submit-jobs.md#warnings)
flags the common ones before you submit. Fix the entity and use **Retry
failed**, which submits just the failed entities again.

When every entity fails with `Could not load the submitter`, or the window
says `the submission process stopped early — see the log`, the background
process itself broke: **Open log folder** shows `runner.log`, next to the
`plan.json` of exactly what was sent. See
[Submit](asset-browser/submit-jobs.md#submit).

### Submit says "Deadline is not set up on this machine"

The submitting machine has no Deadline Client, so there is no
`DEADLINE_PATH` to submit through (older versions showed this as
`Invalid Deadline installation path: "%DEADLINE_PATH%"`). Install the
client and restart Houdini; see [Deadline and the render farm](deadline.md).
With no farm at all, see
[Rendering without a farm](compositing.md#rendering-without-a-farm).

### The Farm Submit button in TumbleTrove Desktop reports no Houdini

`No installed Houdini satisfies '>=22.0' and '>=21, <23'` means no Houdini in
the standard install location meets both the project's and TumblePipe's
version range. Install one, or set `HFS` to the Houdini to use.
`HOUDINI_PACKAGE_DIR is not set` means the launcher was run outside the
Desktop's Scripts panel, which is what provides the project's package files.
See [From TumbleTrove Desktop](asset-browser/submit-jobs.md#from-tumbletrove-desktop).

### Farm job failed: `No valid Houdini version was found`

The worker has no Houdini of the **major** the job was submitted from (the
submitting Houdini's full version travels with the job as
`TH_HOUDINI_VERSION`). Install that major on the workers before submitting
from it. See [Farm worker prerequisites](deadline.md#farm-worker-prerequisites).

The same assert raised by `th::playblast` (LOP) **inside Houdini**, after
the frames rendered and at the MP4 encode (`mp4.from_jpg` → `FFmpeg`), is a
different bug: 1.52.2 and older looked for Houdini's bundled `hffmpeg` under
the pipeline's default major (21) rather than the Houdini you are running, so
it failed on any machine with only Houdini 22 installed. Later releases use
the running Houdini's own version.

### Farm job failed: `Invalid .hip file header`

The job loads the workfile the submitter bundled into
`export/other/jobs/<id>/data/workfiles/`, and that copy was cut short: it was
taken while the version was still being saved. Through 1.55.0 every farm
publish saved a new workfile version when it finished, so a batch publishing a
Multi's shots kept writing new versions of that one workfile, and a later
submission could bundle one mid-write. Later releases bundle only a version
whose save has finished, and farm publishes no longer save a workfile at all.
Resubmit from a current TumblePipe.

### Farm job failed: rig fails to compile, Failed to load subnet

An animation publish on the farm whose rig compiles at your desk. Through
1.55.0 a farm job carried TumblePipe as its only package, so the worker's
hython loaded no other: a TumbleRig rig could not find its components
(`Failed to load subnet /fkik_01`, `/spline_01`, …) and every `th::animate`
node failed. It surfaced as `the node /stage/Export_<shot> has no stage input
connected`, which pointed at the wiring instead. Later releases build the job's
`hpm.toml` from the project's own, so the worker sets up the project's packages
(see [Worker prerequisites](deadline.md)); resubmit from a current TumblePipe.
If the job log warns *No project hpm.toml found*, the submitter could not find
the project — launch Houdini from TumbleTrove Desktop.

### Farm job failed: `Required env var 'TH_PROJECT_PATH' for package 'tumblepipe' has no value`

The job's bundled `hpm.toml` did not supply `TH_PROJECT_PATH`, which
TumblePipe's manifest declares as a required placeholder — hpm resolves it
against the job manifest, not the worker's environment. Resubmit from a
current TumblePipe (the submitter writes it into the job manifest). See
[Plugins: HPM and UV](deadline.md#plugins-hpm-default-and-uv-legacy).

### Farm job failed: `Script file not found: …/.hpm/packages/…`

The job used the legacy **UV** plugin, which bakes the submitter's absolute
package path; it only works when every worker mirrors that path. Submit with
the default **HPM** plugin. See [Deadline and the render farm](deadline.md).

### Farm job failed: `Channel not found in discord config: renders`

Every job family ends in a **notify** job that posts to Discord, so a notify
that fails reds the whole batch — even when the render or playblast it
trails went through perfectly.

The name (`renders` for render and playblast, `exports` for publish and
stage) is looked up under `discord/channels` in the project's `config`
database. Fill the missing name in under `config:/discord` in
[the config database editor](asset-browser/config-editor.md#the-databases),
copying it from a project that already posts. The message lists the names
the project does have.

**Before 1.52.2** this was also what a project with *no* Discord setup at
all looked like. A project created from the template ships that block
**empty** — `token: ""`, no users, no channels — and the token guard tested
`is None`, so the empty string read as a set token and only the channel
lookup failed. Every notify in such a project failed, and with it every
render and playblast batch. From 1.52.2 that case is quiet instead: the
notify logs `Skipping discord notification: this project has no discord
configuration` and succeeds, so the error above now means the project *does*
have a token and channels, but not this name.

**Up to 1.62.0** the error could also list the very name it failed on
(`Channel not found in discord config: Renders (configured channels:
Renders)`). Lookups lowercased the name they were asked for but matched the
stored key exactly, so any channel or user typed with a capital letter was
listed but unreachable. Names now match ignoring case, with an exact match
preferred when a project holds both spellings, and the audit script reads
them the same way.

`scripts/audit_discord_config.py` grades every project on the drive, which
separates the quiet projects from the ones a name is missing from. See
[Job families](asset-browser/submit-jobs.md#job-families).

### One worker fails every job with "No licenses could be found"

Every task that lands on one worker fails within seconds with
`No licenses could be found` (hython exits 3, or husk does), whatever the job,
while other workers render the same jobs. A busy licence server would hit every
worker; one worker failing everything means its own login has lapsed. Log in to
the **SideFX Launcher** on that machine again.

Deadline keeps counting those failures: jobs it touched can reach their error
limit and show as failed, and Deadline marks the worker bad on them so it no
longer gets those jobs even after the fix. Afterwards, resume the failed tasks
and clear the worker's bad marks on the affected jobs (Deadline Monitor: job →
right-click → *Modify Job Properties* → *Failure Detection*). Seen on
jacob-5090 in 2026-09: 187 failed tasks in a day, every render and playblast it
touched hit the error limit.

When the failures are spread over several workers instead, the Core/FX seats
are taken: hython tasks (`publish`, `collapse`, `stage`) need a Houdini Engine,
Core or FX seat, and with no Engine licenses they compete with artists for the
Core/FX ones. They succeed once a seat frees up. See
[Farm worker prerequisites](deadline.md#farm-worker-prerequisites).

### The farm playblast fails with "No licenses could be found"

husk exited 3 before rendering a frame, while Karma Renderer licenses were
free. The playblast's husk used to run without
`--check-licenses 'Karma Renderer'`, so it asked for a Houdini Engine / Core /
FX seat first, and those seats are shared with artists. The playblast worker
now passes the same flag as the render worker and takes a Karma license.
Update the pipeline and resubmit. See
[Farm worker prerequisites](deadline.md#farm-worker-prerequisites).

### The farm playblast hangs on its first frame, then husk exits 3765269347

If the task log says `Rendering playblast frames (storm)`, stops at
`ALF_PROGRESS 0%` right after `>>> Render`, and ends minutes later with
`husk exited 3765269347` (`0xE06D7363`, a crash), the job was submitted
from a pipeline that still rendered playblasts with Hydra Storm. Storm hung
like this on every real shot the farm tried (HideAndReek/010 on maria-2060,
030 on judas, and 030 locally), which is why it was removed. Update the
pipeline and resubmit; playblasts now render with Karma XPU. See
[Playblast](compositing.md#playblast).

### Playblast frames are black or missing on the farm

The task fails with `Playblast produced no frames`. Read the husk log above
it:

- `No licenses could be found` — licensing, see above.
- `Unable to load render plugin: BRAY_HdKarmaXPU`, or a CUDA/OptiX error —
  the worker has no usable NVIDIA GPU for Karma XPU. Keep such workers out of
  the `karma` Deadline group; see
  [Farm worker prerequisites](deadline.md#farm-worker-prerequisites).
- `All AOVs bypassed or missing. Nothing to write` — a *stage* problem, not a
  machine one. Before **1.52.2** the playblast inherited the project's
  `UsdRender.Settings`, whose `RenderProduct` orders Karma's LPE render vars
  (`beauty`, with `sourceName = "C.*[LO]"`), which the then-default Storm
  could not fill. The submitter now authors its own preview settings prim
  (one raw colour var) — update and resubmit.

To render one frame locally with the worker's own flags first:

    hython scripts/debug_playblast.py --shot entity:/shots/<seq>/<shot> --husk

### The farm playblast renders the wrong view

husk picks the camera from the stage's `UsdRender.Settings`. It only
*searches* `/Render` for one, and it only reads the `renderSettingsPrimPath`
metadatum off the **root** layer. A project whose settings live at
`/scene/Render/rendersettings` declares that path in
`_config/usd/root_default_prims.usda`, which reaches the collapsed farm stage
as a sublayer — so before **1.52.2** husk saw neither, logged `No camera in
render settings, defaulting to <first camera on the stage>` and rendered the
shot from the project template's placeholder camera (at the origin, with a
0.5mm lens). The submitter now names the settings prim on the collapsed
stage's root layer and refuses the submission when the stage does not say
which camera to render through.

To see what husk will resolve for a shot *without* submitting anything:

    hython scripts/debug_playblast.py --shot entity:/shots/<seq>/<shot> \
        --department <dept> --husk

It collapses the same staged stage the farm would, reports the settings prim,
the camera and its focal length and world position, the cameras and lights on
the stage, and which layers the department cut dropped; `--husk` renders one
frame locally with the worker's own flags (Karma XPU). The collapsed `.usda`
it writes is
left behind and printed, so it can be opened in usdview.

In a farm log, the lines that answer "which camera?" are husk's own — the
playblast worker runs it with `--verbose a2`:

    Using stage default settings: /scene/Render/rendersettings   # named on the root layer
    Defaulting to use settings found at /Render/rendersettings   # found by husk's own search
    No camera in render settings, defaulting to /scene/cameras/render_camera   # neither — wrong view

### The render came back at project defaults — my overrides were ignored

The submit dialog's render overrides are applied to the shot's
`UsdRender.Settings` prim, and that prim's path is project-owned
(`/Render/rendersettings` in the current template and most projects,
`/scene/Render/rendersettings` in projects made from the template before
2026-10-07). Releases up to and including 1.45.1 hardcoded `/Render/rendersettings`,
so on a project using `/scene/Render/rendersettings` every override
composed onto a prim that does not exist and the render was silently the
project default. Later releases find the `UsdRender.Settings` prim on the
stage and fail the submission when there is none or several. Update and
resubmit; on an older release, keep the overrides in the shot's own render
settings node rather than the dialog. See
[Where the render settings live](composition.md#where-the-render-settings-live).

Also check the department you submitted from: a render composes departments
**up to and including** the one picked, so a department downstream of it is
left out by design — [The department cut](composition.md#the-department-cut).

### A `beauty_<tag>` AOV is missing

`th::lpe_tags` builds one `beauty_<tag>` render var per tag that at least one
light carries. Two causes:

- The row's **Lights** pattern matches nothing. The node shows
  `No light carries: <tags>. Check the Lights pattern - no beauty_<tag> AOV
  is built for a tag nothing carries.`
- **Mesh lights** (a `Mesh` with `MeshLightAPI`) were dropped by a type-name
  filter before **1.37.2**, so their tags never produced a var. Update.

## Where to look

- **The status bar.** Success notices (`Saved …`, `Created …`, `Imports
  updated …`) and catalog failures land there; anything that needs a decision
  or explains a failure is a dialog instead.
- **The log files.** Every TumblePipe traceback is on disk, and error dialogs
  print the exact paths:

  ```
  $TH_PROJECT_PATH/export/other/logs/$TH_USER.log   # project-wide
  <workspace>/_logs/$TH_USER.log                    # per-workspace
  ```

  The file is named after `TH_USER`; a session with no user writes
  `pipeline.log`. The project-wide log only exists when `TH_PROJECT_PATH`
  points at a real folder, so an absent log on a broken project is not
  evidence that nothing went wrong. See
  [Where the logs are](development.md#where-the-logs-are).
- **Farm submissions.** Each one keeps its `plan.json`, `progress.jsonl` and
  `runner.log` in its own folder under `temp:/farm_submissions/` on the
  machine that submitted it; the Farm submission window's **Open log folder**
  opens it.
- **Versions.** **TumbleTrove ▸ About TumbleTrove...** in Houdini's menu bar
  lists every registered package with its version, plus the Houdini and
  Python versions. The Alt+T radial's **Project info** shows the project
  name, user and paths the session is actually running with.
- **Reporting a bug.** File it on
  [GitHub Issues](https://github.com/tumblehead/TumblePipe/issues) with the
  Houdini and TumblePipe versions, the OS, a minimal reproduction if you have
  one, and the log covering the failure. See
  [Reporting issues](development.md#reporting-issues).
