# Getting started

A guided first session: install TumblePipe, point it at a project, launch
Houdini, and take one shot from an empty department to a published layer.
Each step links out to the page that covers it in depth.

## 1. Install TumblePipe

Install the package through [TumbleTrove Desktop](installation.md#tumbletrove-desktop-recommended):
search for **TumblePipe**, install it, and add it to the project you want to
work in. Desktop handles the Houdini package registration; there is nothing
to edit by hand. (Command-line and manual installs exist too — see
[Installation](installation.md) — but the rest of this page assumes Desktop.)

TumblePipe needs Houdini 22 (`hpm.toml` declares `houdini = ">=22, <23"`).

## 2. Configure the project

A freshly installed TumblePipe does not know where your project lives.
Click **Configure** on the TumblePipe package card in TumbleTrove Desktop —
Desktop also opens the same wizard on its own when the package's required
`TH_PROJECT_PATH` variable has no value yet. The wizard is a small native
window titled **TumblePipe — Project Setup** with one question: **TumblePipe
Project Folder**. Type a path or click **Browse…**, and the window says what
it will do with that folder:

| Status line | Meaning |
|---|---|
| `Existing project — TumblePipe will use <path>.` | the folder already holds a TumblePipe project; **Use project** is enabled |
| `A new project will be created in <path>.` | the folder is empty or doesn't exist yet; an **FPS** field (default 24) appears and **Create project** is enabled |
| `This folder isn't a TumblePipe project, and it isn't empty. …` | you are one level too deep, or in the folder *above* the project; pick the folder that contains `_config`, or an empty one |
| `That folder doesn't exist.` / `That is a file, not a folder.` | fix the path |

There is no name to type. A new project takes the name you gave it in
TumbleTrove Desktop (Desktop 0.58.0 and later; older versions fall back to
the folder's name), keeping only its letters and digits. The name becomes
the project's default farm pool.

**Create project** copies the bundled template (`scripts/project_template/`)
into the folder — `_config/` with the storage table, the JSON databases and
the department templates — patches the databases with your fps and farm
pool, and creates the top-level `assets/`, `shots/`, `groups/` and
`export/` directories. If the copy fails, a **Project creation failed** modal
shows the reason, the partial copy is removed, and you stay on the page.

Either way, the button hands `TH_PROJECT_PATH` back to Desktop, which stores it as
a project-scope override. **Cancel** is a real choice: nothing is written and
Desktop does not report an error. Details and the exit contract are in
[Configuration → Project setup wizard](configuration.md#project-setup-wizard).

## 3. Launch

Launch the project from TumbleTrove Desktop. Two things can appear before
Houdini does, both from the `tt_prepare` hook that runs on every launch:

- **TumblePipe — project configuration** ("This project could not be
  migrated") — the project's `_config/` is behind the version this
  TumblePipe expects, and the launch could not bring it forward on its own.
  (Normally it does, silently, backing up anything it replaces as `.bak`.)
  The window says why; TumblePipe will not open the project until it is
  migrated, so fix what it names and launch again.
  See [Migrating at launch](configuration.md#migrating-at-launch).
- **TumblePipe — project not configured** ("This project has not been
  configured for TumblePipe yet … No _config/db/entity.json was found
  there.") — `TH_PROJECT_PATH` names a folder that was never set up. **Launch
  anyway** opens Houdini, but the Asset Browser will have no pipeline until
  you run step 2. See
  [If the project was never configured](configuration.md#if-the-project-was-never-configured).

A project that is current and configured shows neither; Houdini just opens.

## 4. What you see in Houdini

**The TumblePipe desktop.** The package ships one desktop layout,
`desktop/TumblePipe.desk`, and `python3.13libs/uiready.py` makes it the
current desktop when the UI comes up (only if a desktop named `TumblePipe`
exists and is not already current — switching desktops mid-session is
respected until the next launch). It is a Solaris layout:

- far left: a details pane pinned to `/stage` (the Scene Graph Tree);
- middle, top: a Scene Viewer on `/stage`;
- middle, bottom: a Python Panel running the **Asset Browser**;
- right: the Network Editor, open at `/stage`.

The Scene Viewer and Network Editor open with their bars expanded: pane tabs,
the viewer's toolbars including the color correction toolbar (only the view
memory toolbar stays hidden), and the network editor's menu bar.

The shelf carries the two Solaris shelf sets.

**The Asset Browser.** TumbleTrove's browser panel, with TumblePipe's
*pipeline* catalog registered into it. The sidebar has **Assets**, **Shots**
and **Roots** sections for each project (Multis, once there are any, sit in a
**Multis** subheader under Assets and Shots). Selecting an entity shows its card and one row per
department. The bar above the browser carries quick actions that act on
the scene currently loaded — **Save**, **Publish**, **Render**, **Update** and
**Reload** — and **Farm Submit**, which publishes, playblasts and renders any shots
on the farm. See [Asset Browser](asset-browser/index.md); the browser's
TumblePipe settings live on the **Projects** page of TumbleTrove's settings
dialog ([Settings](asset-browser/settings.md)).

**The TumbleTrove menu** in Houdini's main menu bar (next to Help) gives every
registered package a submenu of its items, **Settings...**, a **Documentation** submenu (the
TumblePipe docs are listed there) and **About TumbleTrove...**, which shows
the installed version of every registered package.

**Radial menus.** With the `tumbleradial` package installed, **Alt+T** opens
the TumblePipe pipeline radial in a network editor, **Alt+R** the recipes and
**Alt+F** your asset favourites. See [Radial menus and shortcuts](radial-menus.md).

## 5. Your first workfile

1. **Pick a shot** in the browser: open **Shots** in the sidebar and select
   the shot. Its department rows appear; a department with no workfile yet
   reads *missing*.
2. **Create the first workfile.** Double-click the department row (say
   `light`), or right-click it and choose **New from Template**, the first
   entry on an empty row's menu. Below it are **New from Current** (a new
   version from whatever scene is loaded) and **Open Folder**; once a version
   exists the menu leads with **Open Latest (v0001)** instead and adds
   **View Latest Export** and **Open in New Houdini**.

   New from Template clears the session, saves an empty hip at the next version
   path (`v0001` for an empty department), records the workfile's context,
   runs the department's template against `/stage`, applies the shot's frame
   range and fps from the project config, and saves again. The status bar
   confirms with `Created <file>`; if the template could not be found it
   appends `(no template)`, and if the timeline did not land it appends
   `(frame range/fps NOT applied — check the log)`.
3. **What the template built.** For `light`
   (`_config/templates/shots/light/template.py`) the network in `/stage` is a
   single column:

   - `import_shot` — a `th::import_shot` node that composes everything the
     shot's upstream departments have published;
   - `environment_light` — a dome light;
   - `key_light` — a rect light (`UsdLuxRectLight`) to start from;
   - `light_linker` — a Light Linker for per-light include/exclude sets;
   - `export_shot` — a `th::export_layer` node with the display flag, which
     is what Publish will run.

   Every entity-aware `th::` node is left on its `from_context` default, so
   the nodes resolve the shot and department from the workfile they live in
   rather than having a URI baked in. The `animation` template is shaped
   differently — `import_shot` → a `camera_edit` on `/cameras/render_camera`
   → `animate_shot` (a `th::animate` whose dive already imports the rigs the
   shot uses and wires APEX scene-animate/invoke nodes) → `export_shot`, plus
   an `anim_camera` LOP Import Camera under `/obj`. Every department has its
   own template; see [Department templates](configuration.md#department-templates)
   and [Workfiles](asset-browser/workfiles.md).
4. **Work, then Save.** The **Save** quick action writes the *next* version
   (`v0002`, …) and confirms `Saved <file>` on the status bar; it never
   overwrites the version you opened. Houdini's own File ▸ Save writes the
   open file in place; the browser's Save is what advances the version.
5. **Publish.** With the light workfile loaded, click **Publish**. The
   **Publish** dialog lists the scene's export tasks (one per export node,
   grouped by entity) with a Local/Farm choice; run it and the department's
   layer lands under `export/` as a new version. If the loaded scene has no
   pipeline context the button says so in a dialog rather than doing
   nothing: `Publish: the loaded scene has no pipeline context, so there is
   nothing to publish. Save the scene through the pipeline first.` What an
   export checks before it writes, and why it may refuse, is in
   [Export and publish](asset-browser/export-and-publish.md) and
   [Composition](composition.md#layer-save-paths-and-export-portability).
6. **Open the next department.** Select the row of the department downstream
   of yours and use **New from Template** (first time) or **Open Latest**. A
   workfile re-executes its import nodes when it opens, so the layer you
   just published is composed — the
   **Auto-import latest on workfile open** preference, on by default. An
   already-open scene picks up a new publish with **Update**. See
   [Picking up new versions on open](composition.md#picking-up-new-versions-on-open).

From here: rendering and farm submission are covered in
[Submitting to the farm](asset-browser/submit-jobs.md) and
[Deadline and the render farm](deadline.md); the shipped nodes in
[Nodes](nodes/index.md).

## The default departments

A new project starts with these departments
(`scripts/project_template/_config/db/departments.json`). Position in the
pool is the pipeline order; the flags are explained under
[Departments](configuration.md#departments).

**Assets**

| Department | Notes |
|---|---|
| `model` | independent; publishes and composes into the asset (renderable) |
| `blendshape` | publishes, but is **not** a render layer — it never composes into the staged asset |
| `lookdev` | publishes an over-layer of materials that composes over the model (renderable) |
| `rig` | publishes, **not** a render layer; its export is read by `th::import_rigs` (inside the animation template's `th::animate`), not by composition |

**Shots**

| Department | Notes |
|---|---|
| `layout` | independent; publishable, renderable |
| `environment` | independent; publishable, renderable |
| `animation` | publishable, renderable; template imports the shot's rigs into a `th::animate` node |
| `crowd` | publishable, renderable |
| `effects` | publishable, renderable |
| `cfx` | publishable, renderable |
| `light` | publishable, renderable; template scaffolds a dome light, a key light and a light linker |
| `render` | publishable, renderable — the last render layer |
| `composite` | neither publishable nor renderable — the comp workfile; its frames come from the farm's composite job, not a USD layer (see [Compositing](compositing.md)) |

**Render** (the post-render stages, produced by farm jobs rather than
workfiles): `render`, `denoise`, `composite`. `denoise` sits above `render`,
so denoised frames win over raw when both exist — see
[Compositing](compositing.md).

"Independent" means a publish upstream does not propagate into that
department; every department above is enabled. Edit the pool per project
from the browser's **Departments…** editor.
