# Pipeline settings and files

The Asset Browser's settings page for TumblePipe, the two department
editors it reaches, and every file TumblePipe writes outside a project.
Sources: [`settings_widget.py`](../../python/tumblepipe/asset_browser/settings_widget.py),
[`prefs.py`](../../python/tumblepipe/asset_browser/prefs.py),
[`departments.py`](../../python/tumblepipe/asset_browser/departments.py).

## The Projects page

Open TumbleTrove's settings (the gear icon in the Asset Browser) and pick
the **Projects** page filed under TumblePipe. It manages the registered
projects the browser merges into one grid, plus three behaviour toggles.

### Project list

| Control | What it does |
|---|---|
| **Add** | Appends `new_project` (numbered if taken) and selects it for editing. |
| **Remove** | Drops the selected entry from the working copy. |
| **Name** | The project's registry name (placeholder `e.g. RND or growth`). |
| **Project Path** + **Browse…** | The project root (placeholder `P:/RND`). Browsing (**Select Project Folder**) fills a blank Name from the folder name and a blank Config Path from `<project>/_config` when it exists. |
| **Config Path** | Optional — where `_config` lives if not inside the project. |
| **Departments…** | Opens the [department pool editor](#the-department-pool-editor) for the selected project. A project you just added must be applied first ("Apply this project first — the department pool lives in its config…"). |
| **Apply Project Changes** | Validates (every entry needs a Name and a Project Path, names must be unique — `Fix these issues before applying: …`), writes the registry, drops the browser's discovery cache, and reports `Saved N project(s). The asset browser grid will repopulate on the next browse.` |

Edits to the list only take effect on **Apply Project Changes**.

### Behavior

Each checkbox is saved the moment you click it — no Apply needed.

| Checkbox | Default | Pref key | Effect |
|---|---|---|---|
| **Autosave (version up) on scene change** | off | `autosave_on_scene_change` | When opening another workfile from the browser while the current scene has unsaved changes: **on** saves a new version silently; **off** asks (**Save Scene**: *Save new version* / *Discard changes* / *Cancel*). Either way the current workfile is never overwritten in place. Off-pipeline hips fall back to Houdini's own prompt. |
| **Auto-import latest on workfile open** | on | `auto_refresh_on_open` | After opening (or reloading) a workfile through the browser, re-execute every `import_asset`, `import_assets`, `import_shot`, `import_layer` and `import_rigs` node so `latest` references pick up the newest publish. Runs in manual update mode and skips `create_model` / `build_comp`. See [Picking up new versions on open](../composition.md#picking-up-new-versions-on-open). |
| **Ask for a version note on save** | on | `prompt_note_on_save` | The **Save** quick action asks for a note that shows in the browser's Note column; Cancel aborts the save without burning a version. Off saves with a blank note. Autosave-on-scene-change and the emergency save never prompt. |

A failed write shows `Failed to persist … preference — see Houdini console`
but keeps the checkbox as clicked.

## The department pool editor

**Departments…** on the Projects page opens **Departments — `<project>`**.
The pool is per project and per context; the combo at the top switches
between **shots**, **assets** and **render** (the post-render stages). The
list shows the pool in **pipeline order** — later departments layer over
earlier ones in the staged build, and everything below a department is
downstream of it. Disabled departments are greyed and suffixed `(disabled)`.

| Button | What it does |
|---|---|
| **Add…** | **Add Department** asks for a name and inserts it *after the selection* (at the end when nothing is selected) with Publishable on, everything else off, Enabled on. Rejects a name already in the pool and the reserved pseudo-departments (see [Departments](../configuration.md#departments)). |
| **Remove** | Confirms (**Remove Department**): the department leaves the pool; its workfiles and exports stay on disk and entities scoped to it quietly drop it. Prefer unticking **Enabled** to retire one. |
| **Move Up** / **Move Down** | Reorders — this is the pipeline order. |

Flags for the selected department:

| Field | Meaning |
|---|---|
| **Short label** | Optional abbreviation (e.g. `mdl`) used on cards and decks. |
| **Enabled** | Off retires the department: gone from every menu, deck and job graph; files stay on disk. |
| **Publishable** | Exports a layer (appears in export/import department menus). |
| **Renderable** | Composes into the render stage. Leave off for tracking/notes-style departments — the update job treats the *last* renderable shot department as the final layer to re-import. |
| **Independent** | A publish upstream of it does not propagate into it. |
| **Generated** | Produced by Python, not a Houdini workfile — hidden from the Houdini export menus. |

**Apply** commits everything (adds, removals, flags, then the order) to the
project's `departments` database and refreshes the browser; **Cancel**
discards. Before committing it warns — and asks *Apply anyway?* — when you
**reordered existing departments** ("Order is the pipeline order: this
changes USD sublayer strength and what counts as downstream for every
existing shot and asset in this project") or appended a new **renderable**
department last in the shots pool ("… makes it the final layer the update
job re-imports. If it is not actually a render layer, untick Renderable").
Why order matters is spelled out in
[Department exports and staged files](../composition.md#department-exports-and-staged-files).

## Per-entity departments

Right-click a shot or asset card → **Departments…** opens **Departments —
`<name>`**: one checkbox per department of the context's pool (generated
departments excluded). A department that already has a workfile says so in
its tooltip. **Use all (inherit)** ticks everything, which is stored as an
*empty* assignment — the entity follows the pool, including departments
added later. **Apply** needs at least one tick ("An entity needs at least
one department. To hide a department from every entity, retire it in the
project's department pool instead.").

Unticking is scoping only: it hides the department from the entity's menus
and task lists; existing workfiles and exports stay, keep composing, and
keep showing (flagged). It can never change a render — see
[Per-entity assignment](../configuration.md#per-entity-assignment).

On a **Multi** card the same menu item opens the coverage editor instead —
which departments the Multi's workfile overrides for its members. See
[Multis and Roots](multis-and-roots.md).

## Files written outside the project

| File | Written by | Contents |
|---|---|---|
| `$HOUDINI_USER_PREF_DIR/asset_browser/pipeline_prefs.json` (or `~/.config/asset_browser/pipeline_prefs.json` when the variable is unset) | the Behavior toggles | The three prefs above plus `prefs_version` (currently 2; a file older than that re-defaults `auto_refresh_on_open` to on once). |
| `$HOUDINI_USER_PREF_DIR/asset_browser/projects.json` (same fallback) | Apply Project Changes | The registered projects: name, project path, config path. |
| `<package root>/radial_menus/tumblepipe_recipes.json` and `tumblepipe_asset_favorites.json` | Houdini startup (`tumblepipe.startup.register_radial`, `$TH_PIPELINE_PATH` is the package root) | Generated radial rings (Alt+R recipes, Alt+F asset favourites) for the `tumbleradial` package; `tumblepipe_pipeline.json` there is static and shipped. Regenerated every launch; a ring with fewer than two entries is deleted rather than written. |
| `$TH_PROJECT_PATH/export/other/logs/$TH_USER.log` | every session, on `import tumblepipe` | The pipeline log — rotating, 5 MB × 3 backups. Written into the **project** share, so it is only attached when `TH_PROJECT_PATH` exists; identity-less sessions share `pipeline.log`. This is the file the failure dialogs name. See [Where the logs are](../development.md#where-the-logs-are). |
| `$TH_PROJECT_PATH/export/other/jobs/` | farm submissions | The job manifests handed to Deadline. |

### Temp and scratch

Everything that publishes stages its output in a machine-local scratch root
first and copies the finished result to the project: `temp:/` resolves to
`$TH_TEMP/th_temp/<project>/` when `TH_TEMP` is set, otherwise the OS temp
directory (`%TEMP%`) plus `th_temp/<project>/`. Exports, farm submissions,
builds and MP4 encodes all create short-lived directories there and clean
them up; only the root persists. Set `TH_TEMP` on a machine whose system
drive is small. `TH_TEMP` is described under
[Environment variables](../configuration.md#environment-variables).

### Sidecars inside the project

Two per-entity files live beside the entity's folders, not in `export/`:

| File | Written by |
|---|---|
| `<project>/assets/<category>/<asset>/thumbnail.png` (or `shots/<sequence>/<shot>/`) | **Select thumbnail…** (any image, converted to PNG) and **Capture thumbnail** (the active scene viewer) on a card's right-click menu |
| `<project>/assets/<category>/<asset>/description.txt` (same for shots) | **Edit description…** on a card's right-click menu |

Workfile version notes and `context.json` sidecars belong to
[Workfiles](workfiles.md); the `_config/db/*.json` databases to
[The config database editor](config-editor.md).
