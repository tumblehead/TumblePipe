# Configuration

TumblePipe is customized through a small set of environment variables and a
per-project config directory of data files. The variables tell Houdini where
to find the pipeline and the project, and the config directory tells the
pipeline where the project's files live and how it is organised.

## Environment variables

TumblePipe reads these from the Houdini process environment. They are
typically set in a launcher script that then starts Houdini.

| Variable           | Purpose                                          | Required |
|--------------------|--------------------------------------------------|----------|
| `TH_PIPELINE_PATH` | Path to the TumblePipe package root.             | yes      |
| `TH_PROJECT_PATH`  | Path to the active project root on disk.         | yes      |
| `TH_CONFIG_PATH`   | Path to the studio config directory (see below). | no       |
| `TH_EXPORT_PATH`   | Path where the pipeline writes exports.          | no       |
| `TH_USER`          | The pipeline user identity (see below).          | no       |
| `TH_TEMP`          | Root for machine-local scratch (see below).      | no       |

`TH_CONFIG_PATH` defaults to `$TH_PROJECT_PATH/_config` and `TH_EXPORT_PATH`
defaults to `$TH_PROJECT_PATH/export` when unset.

`TH_TEMP` overrides the OS temp directory as `{local_temp}`, the root the
scaffold's `temp` storage entry uses (see *The project config* below). Scratch
is machine-local by default — `<OS temp>/th_temp/<project>` — because
everything that publishes
stages there and copies the finished result to its final home: an export
writes the whole version payload into a temp directory and then copies it into
`export/`, and a farm render writes its tiles there before the stitch. Putting
that on the shared project drive turns one write into two over the network.
Nothing under `temp:/` outlives the operation that wrote it.

`TH_TEMP` is read from the process environment of whatever is running, so it is
per-machine: set it where the OS temp sits on a small system drive. It is
deliberately **not** part of the environment a submission builds for its farm
jobs (`farm.env.get_base_env`), so a worker resolves scratch against its
own environment rather than inheriting the artist's — see
[Farm worker prerequisites](deadline.md#farm-worker-prerequisites). It is also
the one variable here the package manifest does not declare: `[runtime]` edits
are applied on top of the process environment, so a machine that sets `TH_TEMP`
is honoured without one, and declaring it with an empty value would make it
look *supplied* (the trap `TH_PROJECT_PATH` fell into). An empty value falls
back to the OS temp anyway.

`TH_PROJECT_PATH` is required in the enforced sense: the package manifest
declares it as an hpm *required placeholder* (`required = true` with no value),
so a project that never supplies it fails `hpm install` with `Required env var
'TH_PROJECT_PATH' for package 'tumblepipe' has no value` instead of installing
and then behaving as though every project path were rooted at the drive root.
Supply it in the project's own `[runtime]` section — which is what the
**Configure** wizard below writes for you, and what TumbleTrove Desktop prompts
for in the project editor.

`TH_USER` identifies who saved or published a version (it is what the Asset
Browser's User column and farm job attribution show). The package manifest
wires it to `$TT_USER_NAME` — the TumbleTrove account name the Desktop
launcher injects — so under a Desktop launch it is your TumbleTrove username
automatically. When it is unset or resolves empty (a launch outside the
Desktop), attribution is simply blank: the pipeline never falls back to the
operating system username or hostname, so machine-local identity does not
leak into project files.

## Project setup wizard

TumblePipe ships a `tt_setup` hook that TumbleTrove Desktop runs when the
user clicks **Configure** on the package card. The hook opens a small
native window that asks for one thing, the project folder:

- **A folder that is already a project** (it has `_config/db/entity.json`) —
  TumblePipe uses it and persists `TH_PROJECT_PATH` as a project-scope
  override.
- **An empty folder, or one that doesn't exist yet** — the window asks only
  for the FPS, then copies `scripts/project_template/_config/` into it — the
  storage table, the JSON databases, *and* the `templates/` department
  scaffolding — customises the JSON databases (farm pool default, fps),
  creates the standard top-level subdirs (`assets/`, `shots/`, `groups/`,
  `export/`), and persists `TH_PROJECT_PATH`. The project's name, which
  becomes its default farm pool, is the TumbleTrove project's name when
  Desktop passes it as `TT_PROJECT_NAME` (Desktop 0.58.0 and later), and
  the folder's name otherwise, with anything other than letters and digits
  dropped.
- **Any other folder** is refused, with the reason shown. Usually it is a
  sub-folder of a project, or the folder above one, so the window offers a
  one-click fix: the enclosing project, the projects inside the picked
  folder, or a new folder there named after the TumbleTrove project.

The folder picked is the project itself: the files go straight into it, and
no sub-folder is made.

The wizard is a self-contained native binary (Rust/egui, source in
`src/wizard/`) built per platform into `bin/<platform>/tt_setup` by the
`build-wizard` prepack step, and the bundled template lives under
`scripts/project_template/`. That scaffold is not only a one-shot copy at
creation: migration reads it back out of the installed package to bring an
existing project's `templates/` forward (see below).

`[scripts.tt_setup]` in `hpm.toml` invokes the prebuilt binary directly,
so there is nothing to provision at run time — the wizard opens instantly.
It replaces an earlier PySide6 wizard (`scripts/tt_setup.py`) that ran
under an hpm-managed `uv` venv: the first Configure click had to download a
CPython interpreter and build a ~100 MB PySide6 venv before the window
could appear. The native binary carries its own GUI toolkit, so that
first-run download is gone. The wizard runs out-of-process (it can't reuse
Houdini's bundled Qt), emits `{"envVars": {"TH_PROJECT_PATH": …}}` on
stdout for TumbleTrove to apply. Cancelling exits 0 with empty stdout — the
contract's way of saying "no env var changes" — so declining is not reported
to the user as a failed hook. See `src/wizard/README.md` for the full exit
contract.

### If the project was never configured

A project can carry TumblePipe and a `TH_PROJECT_PATH` that names a folder
with no `_config/db/entity.json` — the wizard was skipped, or the path was
typed by hand. Houdini still launches, but the pipeline has nothing to read,
and for a while that was invisible: the catalog factory returned "no pipeline
here" and the asset browser simply had no Assets or Shots. Two things now say
so, in words that name the fix:

- **At launch**, the `tt_prepare` hook opens a "project not configured"
  window pointing at Configure… on the TumblePipe card (headless launches
  log the same line and continue). It deliberately does *not* show the
  migration window: a missing `_config` reads as layout v0, and "your
  configuration is out of date" sends someone looking for a migration when
  what they are missing is the setup step. `tt_prepare --migrate` refuses
  such a project for the same reason.
- **In the asset browser**, the pipeline catalog fails to build with
  "TumblePipe is installed, but this project has not been configured yet",
  which TumbleTrove shows as a failed source with the message on Houdini's
  status bar. A `TH_PROJECT_PATH` that does not exist at all gets its own
  message (mistyped path, or an unreachable share).

"Configured" means the same thing to both: `<config>/db/entity.json`
exists. The marker is shared with the wizard's *existing project*
check.

## The project config

TumblePipe expects a config directory (pointed at by `TH_CONFIG_PATH`) holding
the project's data. Nothing in it is code: the pipeline reads it, it never
imports it.

- `db/*.json` — the config database: entities, schemas, departments, scenes,
  groups and project settings. The engine that reads and writes it is the
  package's (`tumblepipe.config.store.JsonConfigStore`, the client's
  `api.config`), so fixes ship with the package and reach every project.

  Reads are **coherent and cheap**: each public read validates the backing
  `db/*.json` stamp at most once (so an out-of-process write is visible on
  the very next read, with no manual refresh), and resolved
  properties/schemas are memoized until any db file changes. Two API notes
  for callers: `list_entity_uris()` lists URIs without resolving per-entity
  properties (use it whenever only `.uri` is consumed — HDA menus
  especially), and `with config.coherent():` batches a loop of reads into a
  single coherency check.
- `storage.json` — where each storage purpose lives on disk (below).
- `version.json` — the layout version (see *Layout versioning and migration*).

Naming is not configurable per project: entity names are ASCII letters and
digits, versions are `v` plus at least four digits (`v0001`, `v10000`), and the
second instance of an asset is `<asset>1`. The resolver and every path builder
spell versions that way, so `tumblepipe.naming` is a plain module.

### Storage

`storage.json` maps each storage purpose to a path template:

```json
{
    "assets": "{project_path}/assets",
    "shots": "{project_path}/shots",
    "groups": "{project_path}/groups",
    "export": "{project_path}/export",
    "cache": "{project_parent}/{project_name}_cache",
    "render": "{project_parent}/{project_name}_render",
    "turntable": "{project_parent}/{project_name}_turntable",
    "temp": "{local_temp}/th_temp/{project_name}",
    "proxy": "{project_parent}/{project_name}_proxy",
    "preset": "{config_path}/presets",
    "edit": "{project_path}/edit"
}
```

That is the scaffold's table. A URI resolves by joining its segments under its
purpose's path: `export:/assets/CHAR/Hero/default/model` is
`<project>/export/assets/CHAR/Hero/default/model`. Edit the file to move a
purpose; a show whose edit sources sit on another drive sets `"edit":
"E:/Sources/Show"`. The table must list exactly these eleven purposes — a
missing or unknown one stops the project from opening, with the file named —
and a template may use only these placeholders:

| Placeholder | Value |
|---|---|
| `{project_path}` | `TH_PROJECT_PATH` |
| `{project_parent}` | the folder that contains the project |
| `{project_name}` | the project folder's name |
| `{pipeline_path}` | `TH_PIPELINE_PATH` |
| `{config_path}` | `TH_CONFIG_PATH` |
| `{home}` | the user's home directory |
| `{local_temp}` | `TH_TEMP`, else the OS temp directory |

`project:/`, `pipeline:/` and `config:/` are not in the table: they are the
directories TumblePipe is launched with. Resolving any other purpose, or a
wildcard URI, raises rather than returning nothing.

Until layout v11 these were three Python modules (`naming_convention.py`,
`storage_convention.py`, `config_convention.py`) that the client imported and
ran. Only the storage paths ever differed between projects, so they became
this table, and v11 converts an existing project (see below).

Render / AOV configuration lives in the schema and entity data
(`schemas.json`, `entity.json` under the `render` sub-object), not in a
Python module. A shot's render layers are its **channels**, stored in the
`channels` property — the same channel list the rest of the pipeline uses.
(Projects from before v13 stored it as `variants`; v13 renamed the key.
There is no separate `render_layers` property; that name is retired.) How a render workfile produces each layer is in
[Render layers (channels)](nodes/lighting-and-rendering.md#render-layers-channels).

### Departments

A project's departments live in `_config/db/departments.json`, one **pool** per
context (`shots`, `assets`, and `render` for the post-render stages). Each
carries capability flags — `publishable` (it exports a layer), `renderable` (it
composes into the render stage, and may be picked as a render's department
cut), `independent` (a publish upstream of it does
not propagate into it), `generated` (produced by Python, not a workfile) — plus
`enabled` and an optional `short` label.

Edit the pool from the Asset Browser's gear settings, under **Project
defaults ▸ Department pool** on the Projects page, per project: add, remove,
reorder, retire, and set the flags. Organisation owners and admins can edit
it; everyone else sees it read-only (see
[Who can edit it](asset-browser/settings.md#who-can-edit-it)). `enabled` is the safe
way to drop a department you no longer use — it disappears from every menu,
deck and job graph while its workfiles and exports stay on disk.

**A department's position in the pool is the pipeline order.** This is not
cosmetic:

- the staged build sublayers departments in *reversed* pool order, so later in
  the pool means a **stronger USD layer**;
- **downstream** is everything below a department in the pool — it drives the
  Downstream Exports menu, `import_shot`'s layer/asset exclusion (in its
  default *Exclude* mode), the publish task graph, and the propagate/update
  farm jobs;
- AOV precedence ranks by pool index.

So reordering an established pool restages composition for every existing
entity, and the editor warns before it does. One trap worth naming: the update
job treats the **last renderable shot department** as the final layer to
re-import, so a department that is not a render layer (tracking, notes,
references) must have `renderable` off.

`root`, `staged` and `none` are reserved — they are pipeline pseudo-departments
(the resolver's root layer, the build task's synthetic department,
`import_shot`'s exclusion sentinel) — and are refused at creation.

#### Per-entity assignment

A shot or asset may be scoped to a subset of its context's pool through its
`departments` property, so a shot that only carries tracking work stops
advertising eight departments it will never have. Set it from the entity's
**Departments…** dialog on the card's right-click menu.

A Multi's **Departments…** opens a different editor, because its departments
are a *coverage* decision — which departments this Multi's workfile overrides
for its members — rather than an assignment. Both live on the same menu item;
it forks on what you right-clicked.

An empty list — the default — means **inherit the whole enabled pool**, so an
entity that was never scoped behaves as it always did, and picks up departments
added to the pool later. The property inherits like any other, so it can be set
on `entity:/shots`, on a sequence, or on the single shot, nearest override
winning. The assignment is a *set*, never an order: it is always resolved by
filtering the pool, so pipeline order stays the pool's business.

Scoping states what an entity is *expected* to have. It deliberately does not
reach composition: the staged build keeps iterating the pool and skipping
departments with no export, so unticking a department in the browser cannot
change a render, and a department that has work but is not assigned still
shows in the browser (flagged) rather than vanishing.

That boundary is about the *checkbox*. It is not a claim that composition
ignores departments generally — a submission's explicitly chosen render (or
playblast) department does cut the composed stack, up to and including
itself; see [Composition → The department cut](composition.md#the-department-cut).
The distinction is intent: a scoping checkbox is a statement about what an
entity has, and must not silently black out a render; a department picked in
the submission dialog is a direct instruction about what to render.

`scripts/verify_entity_departments.py` audits a project: the pool in pipeline
order, which entities are scoped, assignments naming a department the pool no
longer has, and work that its entity is not scoped to.

### Multis (multishot workfiles)

A **Multi** is one workfile whose node graph covers several shots (or
several assets) at once — the multishot workflow. Instead of an artist
opening ten lighting hips for ten shots of the same sequence, the ten shots
join a Multi, and for the departments the Multi *covers* every member's
workfile is the Multi's hip.

In the configuration a Multi is a **group**: `groups:/shots/<name>` or
`groups:/assets/<name>` in `db/groups.json`, carrying a `members` list of
entity URIs and a `departments` list. A Multi is locked to one context —
shots and assets never mix — and its workfiles live under
`<project>/groups/<context>/<name>/<department>/`.

What coverage means, mechanically:

- Opening a member's workfile for a covered department resolves to the
  Multi's hip (`pipe/paths/workspace.py` redirects the workspace to
  `groups:/`); the member's deck row shows the Multi's name instead of a
  version. Departments the Multi does not cover keep the member's own
  workfiles.
- The department template's `_create_group` branch scaffolds the Multi's
  first workfile with one pinned entity graph per member, laid out in
  columns — a Multi holds several entities, so `from_context` cannot stand
  in for any one of them.
- Export and publish from the Multi's hip collect the export nodes per
  member and publish each member's layer under its own entity; the Multi
  itself composes nothing (it is not a Root).

In the asset browser, Multis sit in a **Multis** subheader at the top of
the Assets and Shots sections of each project (a side with no Multi has
no subheader). **New Multi…** creates one
(name + context), and a new Multi **covers its context's whole department
pool** from the start — coverage is what gives the Multi its department
rows and what points a member's rows at the Multi's workfile, so a Multi
that covered nothing had no rows, opened nothing, and read as a folder of
shots. Trim the coverage through **Edit Multi…** (or the card's
**Departments…**, the coverage editor described under *Per-entity
assignment*) when a Multi is meant for fewer departments.

The Multi is an entity, not a folder: its sidebar leaf lands on the Multi's
own card, in list view, with one row per covered department, exactly as a
shot's rows work. A row with a workfile opens it; a row without one reads
*missing*, and a double-click or its right-click **New from Template** creates
the first version from the department template (one pinned graph per member, laid out in
columns) — the same menu a shot's row has. Its members are
one step away — **double-click** the Multi card to drill into them (the
breadcrumb shows the Multi), and a member's own rows carry a **ⓜ** badge
on every department the Multi covers (the tooltip names the Multi), and
open the Multi's hip from there too. Drag cards onto the Multi's sidebar leaf or use
**Add selected to Multi** to add members. The leaf's right-click menu is the
card's (**Open Multi**, **Open Location**, **Edit Multi…**, **Delete Multi**)
plus **Add selected to Multi** and **Remove selected from Multi** (see
[Multis and Roots](asset-browser/multis-and-roots.md)).

TumbleTrove routes every one of those operations to the catalog whose
row or card was clicked, and asks it what the menu holds beyond its own
entries (`container_kind_label`, `get_container_actions`,
`execute_container_action`). Until TumbleTrove 0.48 it routed them through
an `owns_collection` hook instead, and did nothing when no catalog claimed
the id — which is exactly what happened until the catalog overrode it: an
artist could create a Multi and then never delete, edit, or populate it.
`tests/test_catalog_multis.py` pins the contract against the shipped SDK.

### Department templates

`_config/templates/<context>/<department>/template.py` builds the node graph
of a **new department workfile** — the **New from Template** action on a
department row runs the matching module's
`create(stage, entity_uri, department_name)` against a freshly saved,
empty hip. By the time `create` runs, the unpinned network editors are
already at `/stage`. A template that wants the artist to start deeper can
move them itself: the `rig` and `blendshape` templates open the editor
inside their SOP network.

Each template splits on the URI it is handed:

- `_create_entity` — an ordinary single-entity workfile. It wires the graph
  and **leaves every entity-aware `th::` HDA on its `from_context` default**,
  so each node resolves its entity from the workfile it lives in. Writing a
  concrete URI here would pin the nodes to the entity the file was born in,
  and a copied scene or a renamed entity would keep publishing to the old one.
- `_create_group` — a group workfile, which holds several entities at once.
  `from_context` cannot resolve to one of them, so this branch (and *only*
  this branch) pins each node to its member via `_pin_entity`, which routes
  through the HDA's `_apply_entity` so the visible Entity label stays in step
  with the parm.

`scripts/verify_entity_from_context.py` enforces that split. Templates are
versioned with the rest of `_config` and refreshed by migration (below), so
a fix here reaches live projects instead of only new ones.

### Layout versioning and migration

The `_config/` layout is versioned in `_config/version.json` (a project with
no such file predates the system and counts as version 0). When the layout
changes, an existing project is brought forward **in place** rather than being
recreated:

Run it as a project script from TumbleTrove — open the project and click
**Migrate Project Config** in the Scripts panel — or from a shell:

```
python scripts/migrate_config.py [project-or-_config-path] [--dry-run]
```

The path defaults to `$TH_PROJECT_PATH`, so the launcher (which runs project
scripts with the project's environment) needs no argument. A project is thus
migrated by whoever opens it with a newer pipeline, not by a central batch.
Every migration is idempotent — re-running is always safe — and `--dry-run`
shows exactly what would change. New projects created by the wizard already
ship at the current version.

The package reads exactly the layout its newest migration writes
(`tumblepipe.config.layout.CONFIG_LAYOUT_VERSION`): opening a project on any
other layout raises `ProjectLayoutError` naming the fix, instead of reading
the old layout wrongly. Older layouts are never refused by the migration
itself — every one still migrates forward.

You no longer have to remember to run it: the `tt_prepare` package hook runs
on every project launch, notices when a project is behind and offers to bring
it forward. The command above stays as the deliberate route — a bulk sweep,
or a project you want migrated without launching it.

The migrations themselves live in the `th_project_core` crate and execute from
the prebuilt `tt_prepare` binary. `scripts/migrate_config.py` is a shim that
picks the right `bin/<platform>/` build and runs it, so the launch hook and
the Scripts-panel button can never disagree about what a migration does.

Whichever route runs it, a migration is refused **as a whole** when any
pending step cannot complete — a project that cannot be fully migrated is
left exactly as it was, rather than stamped part-way at the last step that
happened to succeed.

A migration never replaces a file without preserving the original — the one
exception is a template v12 recognises as a version the scaffold shipped, which
holds nothing of the project's. Which of the two it does depends on how
load-bearing the file is:

- **`config_convention.py` is refused, not clobbered (v1).** If it is not the stock
  engine, the migration raises rather than overwrite a customisation; the
  stock file is backed up to `config_convention.py.bak` before being replaced
  by the thin shim.
- **`_config/templates/` is refreshed.** The department templates are copied
  from the packaged scaffold, and any project copy that differs is preserved
  as `template.py.bak` alongside it. They are refreshed rather than refused
  because a frozen template is the very thing this closes: the templates build
  a new department workfile's node graph, so a fix there (leaving entity-aware
  HDAs on their `from_context` default instead of baking an entity URI in)
  would otherwise never reach a live project. If you *had* hand-tuned a
  template, reconcile it from the `.bak`. The refresh runs only as a
  numbered step (v2, then v9 as `template.py.v9.bak`, then v12 for stock
  templates only), so a later scaffold template change reaches existing
  projects only once another refresh step ships.
- **`_config/db/schemas.json` gains the `entity.departments` default (v3).**
  Additive and data-neutral — it declares the per-entity department
  assignment property (default `[]` = "inherit the pool") so the store can
  resolve and sparsely store it. Without it, per-entity department scoping
  can't be stored; the rewrite is trivially reversible, so back the file up
  yourself first if you're editing a live share.
- **`_config/ocio/` is seeded (v4).** Color management moved out of the package
  and into the project: the package points `OCIO` at
  `$TH_CONFIG_PATH/ocio/tumblehead.ocio`, so an existing project needs the file
  present. Seeded **if absent** and never clobbered — a project that has
  hand-tuned its color config keeps it. (Owning the config per project stops the
  *package* from shipping a second copy of its own; retiring the drive-side
  legacy `_pipeline` setters that also define `OCIO` is a separate sweep — see
  the *Legacy OCIO retire* section below.)

- **The `variants` column is relabelled `Channels` (v5).** The publish-tree
  fork is called a **channel** now — "variant" is reserved for USD's own
  `variantSet` (see *Channels* in `composition.md`). Only the label and its
  tooltip move here; v13 moves the key. Data-neutral, and it leaves a project
  that has renamed the column itself alone.

- **The retired `kits` entries leave `storage_convention.py` (v6).** `kits`
  was in the scaffold from the first commit and nothing ever resolved a
  `kits:` URI, so every project has carried a dead path and an empty
  directory. This removes exactly those two lines and leaves every other one
  byte for byte — it does **not** refresh the file from the package, because
  a project's convention legitimately differs (several carry their own
  `_primary_path`), and replacing it would silently repoint where a show's
  assets and exports resolve. The empty `kits/` directory is left alone;
  delete it at your leisure.

- **The convention modules are pointed at the renamed package (v7).** The
  package was renamed `tumblehead` → `tumblepipe`, and the packaged scaffold
  was corrected in 0bef314 (2026-05-26) — but a project's
  `naming_convention.py` and `storage_convention.py` are copied at creation
  and then frozen, so every project made before that date still imports a
  package that no longer exists. This is not a cosmetic staleness:
  `Client.__init__` *executes* all three convention modules at construction,
  so those projects raise `ModuleNotFoundError` and cannot be opened at all.
  Only the module prefix on import statements moves — a path or comment
  mentioning tumblehead is left alone — so per-project customisation
  survives.

- **`temp:/` leaves the project drive (v8).** The scaffold anchored scratch to
  `<project>_temp`, a sibling of the project. Every export stages its whole
  version payload there and copies it into `export/`, and every farm render
  writes its tiles there before the stitch — so on a shared drive that is two
  network writes for data that nothing keeps. It also left an empty
  `<project>_temp` beside the project for good: the callers create the root
  eagerly and only the inner `TemporaryDirectory` cleans itself up, so a
  session that dies mid-export strands its staging directory there too. The
  step rewrites exactly the `self.temp_path` line to call
  `tumblepipe.storage.default_temp_path()` and adds the import it needs,
  leaving every other path where the project put it. A project that already
  keeps scratch somewhere other than the project drive is left alone.

- **The department templates are refreshed again (v9).** v2 was the only
  template refresh, so a project past it never received a later scaffold
  template change. v9 re-runs the same refresh, picking up the animation
  template's Scene Invoke outputting unpacked geometry (packed output drops
  the rig geometry's `path`/`name` attributes), the rig template promoting
  `name` to points, and the MODEL/LOOKDEV dive-target fix. A template that
  differs from the scaffold is replaced, with the original kept as
  `template.py.v9.bak` — a separate name so a `.bak` left by v2 is not
  overwritten. Existing workfiles are not touched; only new ones pick up the
  change.

- **The separate alpha AOV goes; the beauty renders RGBA (v10).**
  `_config/usd/root_default_prims.usda` is every shot's weakest sublayer and
  every RenderVar in it renders, so its `alpha` RenderVar (Karma `ray:hit`,
  which holdouts do not affect) kept rendering whatever the render nodes
  said. The step removes that RenderVar and its `orderedVars` target and
  turns the `beauty` RenderVar's `dataType` and
  `driver:parameters:aov:format` from `color3f` to `color4f` (`color3h` to
  `color4h`). It reads the file's structure -- strings, comments, `( … )`
  metadata, `{ … }` bodies -- rather than matching lines, and edits only
  those spans, so a project's own settings path (`/Render` or
  `/scene/Render`) and everything else stay byte for byte.
  `_config/usd/context.json` loses `alpha` from its `aov_names`, also as a
  text edit, so the file keeps its layout. Originals are kept as
  `*.v10.bak`; a project without the files, or already through, is left
  alone. Templates are not refreshed: a render template still listing
  `alpha` in `RENDER_VARS_ENABLED` sets a hidden, inert parm. See
  [Compositing → Alpha lives in the beauty](compositing.md#alpha-lives-in-the-beauty).

  It **refuses rather than guesses**, and a refusal blocks the launch's
  migration run with a message naming the file and the reason (nothing is
  written). It refuses when there is no `def RenderVar "beauty"` to carry
  the alpha (removing the alpha would make every comp opaque), when the
  beauty's `dataType` is unauthored or not a colour it knows, when more than
  one prim is named `alpha` or it is not a `def RenderVar`, when an
  `orderedVars` target names the alpha by a path that is not the alpha's own
  (a product pointing at `/scene/Render/...` while the vars live at
  `/Render/...`), or when removing it would leave the product with no target.
  Fix the file by hand -- remove the `alpha` RenderVar and its `orderedVars`
  target, make the beauty RenderVar `color4f` -- and launch again.

- **The convention modules become data (v11).** `naming_convention.py`,
  `storage_convention.py` and `config_convention.py` are retired, each kept
  as `<name>.v11.bak`, and the storage paths are written to
  `_config/storage.json` (see *Storage* above). The storage module is *read*,
  not run: the step understands every shape the scaffold has shipped and the
  live projects carry — literal and WSL `/mnt/...` roots, `Path.home()`,
  locals such as `_primary_path`, f-strings on `project_name` — and writes
  each purpose's path exactly as it resolved. A purpose the module never
  routed (some old projects have no `groups`) gets the scaffold's location;
  `kits` is dropped. A project with no storage module gets the scaffold's
  table, and one that already has a `storage.json` keeps it.

  It **refuses rather than guesses**, before anything is written, when the
  storage module does something it cannot read (a condition, an environment
  lookup, a helper call, a placeholder other than `{project_name}`, a purpose
  of its own, a moved `project_path`), when `naming_convention.py` is not one
  of the three versions the scaffold shipped, or when `config_convention.py`
  is not v1's shim. The message names the file and the line. Write
  `_config/storage.json` by hand from the scaffold's, or reconcile and delete
  the naming/config module, then launch again.

- **The stock department templates are refreshed for the 2026-10 refactor
  (v12).** "New workfile from template" runs the *project's* copy of
  `_config/templates/<context>/<department>/template.py`, and the refactor
  removed APIs those copies call: `tumblepipe.pipe.houdini.util` (split into
  `nodes`, `menus`, `playbar`, `prim_metadata` and `prim_queries`),
  `pipe.houdini.sops.cache` (now `pipe.houdini.cache`), `lops.animate` and
  `lops.render_vars` (now `nodes.create_node(...)`), `lops.puzzle_mattes` and
  `th::puzzlemattes` (now `th::mattes`), `set_shot_uri` (now
  `set_entity_uri`), and the create_asset / import_model `hdaModule()`
  helpers (`_apply_entity`, `_sync_variants`, `_update_labels`, `execute`;
  now `create_asset.wrap(node)` and `ImportModel(node)`). The full list is
  `REMOVED_MODULES` and `REMOVED_CALLS` in `src/wizard/core/src/templates.rs`.

  A template that is *stock* — byte for byte, bar line endings, one of the
  versions the scaffold has ever shipped for that department (every one is
  listed in `templates.rs`) — is replaced by the packaged one, with no
  backup, since nothing in it is the project's. A missing one is put in
  place. Anything else is the studio's own and is never written: a customised
  template, or a `template.py` for a department the package does not ship,
  is left alone when it calls nothing that was removed.

  It **refuses**, before anything is written, when a customised template
  calls something that was removed — replacing it would lose the studio's
  work, keeping it leaves new workfiles from it broken. The message lists
  each file and each removed import or call with what replaced it. Port the
  changes onto the packaged template of the same department (or delete the
  file, and the next launch puts the packaged one in its place; rename it
  first to keep a copy), then launch again. A project still to run v9 is
  judged as v9 will leave it: v9 replaces every packaged template whatever it
  holds (keeping `template.py.v9.bak`), so only the project's own departments
  can block v12 there.

- **The publish channel is stored under its own name (v13).** The config
  database still spelled the channel "variant": the entity property
  `variants` (and its schema default), the `variant` key of each scene asset
  entry, and the browser column whose key and property path name that
  property. v13 renames them to `channels` / `channel` throughout
  `schemas.json`, `entity.json`, `scenes.json` and `config.json`, in place —
  same position, so each file's diff is the renamed lines — keeping each
  rewritten file's original as `<file>.v13.bak`. From this version the
  package reads and writes only the new keys, so a project that has not
  taken v13 reads as having no custom channels. A record carrying both
  spellings with the same value keeps one; with *different* values the step
  refuses and names every such record — delete whichever is wrong, then
  launch again. Published data (`export/**/context.json`, staged `.usda`
  URIs) is not touched: published versions are immutable and are read in
  either spelling (see *Channels* in `composition.md`).

### Migrating at launch

You do not have to remember to run the command above. The `tt_prepare` package
hook runs on **every project launch**: it reads `_config/version.json`, and
when the project is already current it does nothing visible at all. When the
project is behind, it migrates it, backing up anything it replaces alongside
as `.bak` first, and the launch carries on.

There is no "not now": the package reads exactly one config layout and
refuses any other (see the "is on layout vN" entry in
[Troubleshooting](troubleshooting.md)), so a project left behind is one this
TumblePipe cannot open. A window appears only when the migration could not
run — a step that cannot proceed, which writes nothing, or one that stopped
part way — and says what to fix.

Two cases where the window deliberately never appears:

- **A launch nobody is watching** — a farm worker, CI, or a Linux session with
  no display. There is no timeout on a package hook, so a dialog nobody can
  answer would hang the launch forever. It logs what it found and returns.
- **A project it cannot fully migrate** — the run is refused as a whole and
  the window says which step is blocked and why, rather than migrating
  part-way. A project that cannot take every pending step is left untouched.

And one case where a *different* window appears: a project that was never
configured at all (no `_config/db/entity.json`) gets the "not configured"
notice described under *Project setup wizard*, never the migration plan.

**How the hook finds the project.** `TH_PROJECT_PATH` is a package-declared
`[runtime]` variable, and TumbleTrove Desktop does not put those into a hook's
environment — it defers them to Houdini's package file and hands a hook only
the `TT_*` context block. The first releases of this hook read the variable
anyway, so every launch logged "TH_PROJECT_PATH is unset — nothing to
migrate" and the window never opened for anyone. The hook now reads the path
out of the project's own manifest, `$TT_PROJECT_DIR/hpm.toml`, where the
desktop records it; the variable still wins when it is present (the
`--migrate` CLI and a hand-set shell rely on that). Only a literal path is
accepted from the manifest — a value that refers to another variable is
refused rather than misread.

Because `_config` lives on a shared drive and every artist's launch runs this,
the migration itself is taken under a lock, so two people launching at once
cannot both rewrite the same files.

**Un-migrated projects degrade, they don't crash.** `list_departments`
reads each department's `independent`/`publishable`/`renderable` via schema
defaults, so a project whose schema predates those defaults returns a
consistent pool instead of raising `KeyError` (which the asset-browser
catalogue used to swallow into a differently-shaped fallback pool — the
source of an intermittent list-view/deck crash). This is a safety net, not
a substitute: run `migrate_config.py` to bring the project's schema and
templates properly forward.

### Entity casing audits

Entity URIs and USD prim paths are case-sensitive, but Windows storage is
not — so two config categories differing only in casing (e.g. `Clash` and
`clash`) share one folder on disk while the pipeline treats them as two
entities. Assets caught in the split lose their pipeline metadata silently
and are blocked at publish. Two maintenance CLIs cover this:

```
python scripts/verify_entity_casing.py <project> [--usd] [--crates]
python scripts/fix_case_duplicate_category.py <project> --from clash --to Clash [--apply]
```

The verifier is read-only (exit 1 on findings): it flags case-duplicate
config keys, sidecar URIs and filename prefixes whose casing disagrees with
config, and — with `--usd`, run via `uvx --with usd-core python …` — export
root prims that are case-variants of their config entity. The fixer merges
the duplicate category in `entity.json` (backing it up first), rewrites the
wrong-case URIs in `.json`/`.usda` sidecars, and case-renames affected
files; it is a dry-run report unless `--apply` is passed. Geometry authored
under a wrong-case root prim, and import parms stored in workfiles, cannot
be text-patched — fix the workfile in Houdini, re-export, and re-pick the
entity on affected import nodes.

## Where configuration lives in the codebase

- `hpm.toml` — Houdini package manifest and HPM metadata (dependencies,
  supported Houdini version, native resolver slots).
- `_config/ocio/tumblehead.ocio` — OpenColorIO config, owned by the project
  rather than the package. It ships in the config template
  (`scripts/project_template/_config/ocio/`) and lands at `<project>/_config/`
  per project; the package points `OCIO` at `$TH_CONFIG_PATH/ocio/tumblehead.ocio`
  on Houdini startup. Keeping it project-local (one file, not a package-shipped
  copy) means the package no longer contributes its own copy to an `OCIO` that
  could be pathsep-concatenated with a legacy `_pipeline` source into an
  unreadable multi-path value; the legacy setters themselves are retired
  separately (*Legacy OCIO retire*, above). Existing projects gain the file via
  the v4 config migration (`scripts/migrate_config.py`).
- `scripts/` — the bundled `project_template/`, the `migrate_config.py`
  maintenance CLI, and any Houdini startup scripts that run when the
  package loads. (The `tt_setup` wizard is a native binary built from
  `src/wizard/` into `bin/<platform>/`.)
- `python3.13libs/` — the startup hooks Houdini runs (`pythonrc.py`,
  `uiready.py`).

## Next steps

- [Deadline and the render farm](deadline.md) — submitting jobs from
  TumblePipe.
- [Project structure](project_structure.md) — what ships in the package.
