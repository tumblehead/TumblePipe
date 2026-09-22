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
needs a shot to write to, so set **Entity** to From settings and pick the
member — see [`th::playblast` (LOP)](nodes/lighting-and-rendering.md#thplayblast-lop).
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
  are allowed by reference. See
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

### An imported asset is empty

Older publishes of `th::asset_payload` saved their payload to a bare
`payload.usd`, which USD resolved against the process working directory —
one file shared by every asset and every session, clobbered on each publish,
so an asset could compose another asset's geometry or nothing. The sidecar is
now anchored to `$HIP`, keyed on the asset prim, and copied into the version
folder. Re-export the asset with a current TumblePipe; a dangling or escaping
payload is now refused at export (previous entry). See
[Layer save paths and export portability](composition.md#layer-save-paths-and-export-portability).

### Two assets with the same name in different case

Windows storage is case-insensitive but entity URIs and prim paths are not,
so `Clash` and `clash` become two pipeline entities sharing one folder;
assets caught in the split lose their metadata and are blocked at publish.
Ask whoever maintains the project to run `scripts/verify_entity_casing.py`
and, if needed, `scripts/fix_case_duplicate_category.py`. See
[Entity casing audits](configuration.md#entity-casing-audits).

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

## Rendering and the farm

### Farm job failed: `No valid Houdini version was found`

The worker has no Houdini of the **major** the job was submitted from (the
submitting Houdini's full version travels with the job as
`TH_HOUDINI_VERSION`). Install that major on the workers before submitting
from it. See [Farm worker prerequisites](deadline.md#farm-worker-prerequisites).

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

`scripts/audit_discord_config.py` grades every project on the drive, which
separates the quiet projects from the ones a name is missing from. See
[Job families](asset-browser/submit-jobs.md#job-families).

### Playblast frames are black or missing on the farm

Farm playblasts render with husk's Storm (GL) delegate, which needs a real GPU
context. A headless worker produces nothing, and the task fails with
`Playblast produced no frames -- the GL (Storm) delegate likely has no usable
GPU/GL context on this worker. Confirm the playblast farm group has
GL-capable, non-headless workers.` Assign only GL-capable workers to the
`playblast` Deadline group. See [Playblast](compositing.md#playblast) and
[Farm worker prerequisites](deadline.md#farm-worker-prerequisites).

Before **1.52.2** that message was also what a *stage* problem looked like:
the playblast inherited the project's `UsdRender.Settings`, whose
`RenderProduct` orders Karma's LPE render vars (`beauty`, with
`sourceName = "C.*[LO]"`). Storm cannot fill those, so husk logged `All AOVs
bypassed or missing. Nothing to write` and wrote no image on a perfectly good
GPU. The submitter now authors a Storm-renderable settings prim for the
playblast instead — update and resubmit.

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
frame locally with the worker's own flags. The collapsed `.usda` it writes is
left behind and printed, so it can be opened in usdview.

In a farm log, the lines that answer "which camera?" are husk's own — the
playblast worker runs it with `--verbose a2`:

    Using stage default settings: /scene/Render/rendersettings   # named on the root layer
    Defaulting to use settings found at /Render/rendersettings   # found by husk's own search
    No camera in render settings, defaulting to /scene/cameras/render_camera   # neither — wrong view

### The render came back at project defaults — my overrides were ignored

The submit dialog's render overrides are applied to the shot's
`UsdRender.Settings` prim, and that prim's path is project-owned
(`/scene/Render/rendersettings` in the current template, `/Render/rendersettings`
in older projects). Releases up to and including 1.45.1 hardcoded the old
path, so on a project using `/scene/Render/rendersettings` every override
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
- **Versions.** **TumbleTrove ▸ About TumbleTrove...** in Houdini's menu bar
  lists every registered package with its version, plus the Houdini and
  Python versions. The Alt+T radial's **Project info** shows the project
  name, user and paths the session is actually running with.
- **Reporting a bug.** File it on
  [GitHub Issues](https://github.com/tumblehead/TumblePipe/issues) with the
  Houdini and TumblePipe versions, the OS, a minimal reproduction if you have
  one, and the log covering the failure. See
  [Reporting issues](development.md#reporting-issues).
