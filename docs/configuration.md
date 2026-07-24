# Configuration

TumblePipe is customized through a small set of environment variables and a
config directory of Python "convention" modules. The variables tell Houdini
where to find the pipeline and the project, and the convention modules tell
the pipeline how *your* studio names and organizes its files.

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

`TH_CONFIG_PATH` defaults to `$TH_PROJECT_PATH/_config` and `TH_EXPORT_PATH`
defaults to `$TH_PROJECT_PATH/export` when unset.

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
user clicks **Configure** on the package card. The hook launches a small
native wizard with two flows:

- **Use an existing project** — browse to a project root that already has a
  `_config/` directory. The wizard verifies the layout and persists
  `TH_PROJECT_PATH` as a project-scope override.
- **Create a new project** — pick a parent directory, project name, and
  FPS. The wizard copies `scripts/project_template/_config/` into
  `<parent>/<name>/` — the convention modules, the JSON databases, *and*
  the `templates/` department scaffolding — customises the JSON databases
  (farm pool default, fps), creates the standard top-level subdirs
  (`assets/`, `shots/`, `groups/`, `kits/`, `export/`), and persists
  `TH_PROJECT_PATH`.

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
stdout for TumbleTrove to apply, and exits non-zero on cancel.

## The convention framework

TumblePipe expects a config directory (pointed at by `TH_CONFIG_PATH`) that
contains these Python modules. The filenames are not optional — the pipeline
imports them by name:

- `config_convention.py` — workspace configuration (departments, shot
  scaffolding, default task names). This is a thin shim: the config database
  engine lives in the package (`tumblepipe.config.store.JsonConfigStore`),
  and the file just instantiates it (`def create(): return JsonConfigStore()`).
  Engine fixes therefore ship with the package and reach every project,
  rather than being frozen into this per-project copy.

  Reads are **coherent and cheap**: each public read validates the backing
  `db/*.json` stamp at most once (so an out-of-process write is visible on
  the very next read, with no manual refresh), and resolved
  properties/schemas are memoized until any db file changes. Two API notes
  for callers: `list_entity_uris()` lists URIs without resolving per-entity
  properties (use it whenever only `.uri` is consumed — HDA menus
  especially), and `with config.coherent():` batches a loop of reads into a
  single coherency check.
- `naming_convention.py` — how assets, shots, and work files are named.
- `storage_convention.py` — maps project URIs (`project://`, `entity://`, …)
  to concrete filesystem paths.

Render / AOV configuration lives in the schema and entity data
(`schemas.json`, `entity.json` under the `render` sub-object), not in a
Python module. A shot's render layers are its `variants` property — the
same variant list the rest of the pipeline uses (there is no separate
`render_layers` property; that name is retired).

### Departments

A project's departments live in `_config/db/departments.json`, one **pool** per
context (`shots`, `assets`, and `render` for the post-render stages). Each
carries capability flags — `publishable` (it exports a layer), `renderable` (it
composes into the render stage, and may be picked as a render's department
cut), `independent` (a publish upstream of it does
not propagate into it), `generated` (produced by Python, not a workfile) — plus
`enabled` and an optional `short` label.

Edit the pool from the Asset Browser's gear settings → **Departments…**, per
project: add, remove, reorder, retire, and set the flags. `enabled` is the safe
way to drop a department you no longer use — it disappears from every menu,
deck and job graph while its workfiles and exports stay on disk.

**A department's position in the pool is the pipeline order.** This is not
cosmetic:

- the staged build sublayers departments in *reversed* pool order, so later in
  the pool means a **stronger USD layer**;
- **downstream** is everything below a department in the pool — it drives the
  Downstream Exports menu, `import_shot`'s layer/asset exclusion, the publish
  task graph, and the propagate/update farm jobs;
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

### Department templates

`_config/templates/<context>/<department>/template.py` builds the node graph
of a **new department workfile** — the "New from template" action runs the
matching module's `create(stage, entity_uri, department_name)` against a
freshly saved, empty hip.

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

Each module exposes a specific interface that the pipeline calls into. The
[*Turbulence* tech demo](https://www.sidefx.com/tech-demos/turbulence/)
publishes a complete working example of these modules.

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
ship at the current version. The migrations themselves are registered in
`tumblepipe.migration`.

A migration never replaces a file without preserving the original. Which of
the two it does depends on how load-bearing the file is:

- **`config_convention.py` is refused, not clobbered.** If it is not the stock
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
  template, reconcile it from the `.bak`.
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

### Legacy OCIO retire

Color config is now project-owned (the v4 migration above), but the vestigial
per-project launch scripts at `<project>/_pipeline/_project_config.bat` still
set `OCIO` under the legacy `W:/_pipeline` tree. Only the old
double-click-the-bat entry point sources them — Desktop launches never do — but
any launch that does pins `OCIO` at the drive-side legacy config instead of the
project's own. A maintenance CLI retires them:

```
python scripts/audit_legacy_ocio.py <projects-root> [--apply]
python scripts/audit_legacy_ocio.py --projects P:/paleindia P:/Snail [--apply]
```

It scans each project's launch bat, resolves the `%VAR%` references against that
same bat's `SET` lines, and flags any whose effective `OCIO` lands under
`_pipeline` (read-only, exit 1 on findings). `--apply` repoints the setter at
`%TH_CONFIG_PATH%/ocio/tumblehead.ocio` — but only for a project that already
owns `_config/ocio/tumblehead.ocio`; one that doesn't is reported and skipped, so
run `migrate_config.py` on it first. The original line is backed up and left
commented above the rewrite, so the edit is reversible.

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
- `python3.11libs/` — Python-version-specific startup hooks (`pythonrc.py`,
  `uiready.py`) executed by Houdini.

## Next steps

- [Deadline and the render farm](deadline.md) — submitting jobs from
  TumblePipe.
- [Project structure](project_structure.md) — what ships in the package.
