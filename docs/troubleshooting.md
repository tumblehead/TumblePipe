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

If the button truly does nothing and no dialog appears, you are on a
TumblePipe older than **1.42.0**, which introduced the failure dialogs;
check the version under **TumbleTrove ▸ About TumbleTrove...** and update.

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

- **Opening the workfile** re-resolves imports only when opened **through
  the Asset Browser** with **Auto-import latest on workfile open** enabled
  (on by default, on the browser's TumblePipe settings page). A plain
  File ▸ Open restores the versions baked into the hip. See
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

### Playblast frames are black or missing on the farm

Farm playblasts render with husk's Storm (GL) delegate, which needs a real GPU
context. A headless worker produces nothing, and the task fails with
`Playblast produced no frames -- the GL (Storm) delegate likely has no usable
GPU/GL context on this worker. Confirm the playblast farm group has
GL-capable, non-headless workers.` Assign only GL-capable workers to the
`playblast` Deadline group. See [Playblast](compositing.md#playblast) and
[Farm worker prerequisites](deadline.md#farm-worker-prerequisites).

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
