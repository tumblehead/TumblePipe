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
| `TH_USER`          | Override the user identity in the pipeline.      | no       |

`TH_CONFIG_PATH` defaults to `$TH_PROJECT_PATH/_config` and `TH_EXPORT_PATH`
defaults to `$TH_PROJECT_PATH/export` when unset. `TH_USER` defaults to your
operating system username when unset.

## Project setup wizard

TumblePipe ships a `tt_setup` hook that TumbleTrove Desktop runs when the
user clicks **Configure** on the package card. The hook launches a small
Qt6 wizard with two flows:

- **Use an existing project** — browse to a project root that already has a
  `_config/` directory. The wizard verifies the layout and persists
  `TH_PROJECT_PATH` as a project-scope override.
- **Create a new project** — pick a parent directory, project name, and
  FPS. The wizard copies `scripts/project_template/_config/` into
  `<parent>/<name>/`, customises the JSON databases (farm pool default,
  fps), creates the standard top-level subdirs (`assets/`, `shots/`,
  `groups/`, `kits/`, `export/`), and persists `TH_PROJECT_PATH`.

The hook source is `scripts/tt_setup.py` and the bundled template lives
under `scripts/project_template/`. The wizard runs under an
hpm-managed `uv` venv (declared in `[scripts.tt_setup]` in `hpm.toml`)
that pins Python 3.11 and PySide6, so the hook works regardless of what
the user has on `PATH` — `tt_setup` runs out-of-process and can't reuse
Houdini's bundled `qtpy`.

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
Each migration is idempotent and refuses to overwrite a file you have
customised — re-running is always safe, it writes a `config_convention.py.bak`
before replacing the stock file, and `--dry-run` shows exactly what would
change. New projects created by the wizard already ship at the current
version. The migrations themselves are registered in `tumblepipe.migration`.

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
- `ocio/tumblehead.ocio` — OpenColorIO config shipped with the package;
  the package sets `OCIO` to this path on Houdini startup.
- `scripts/` — TumbleTrove hooks (`tt_setup.py`, plus the bundled
  `project_template/`) and any Houdini startup scripts that run when the
  package loads.
- `python3.11libs/` — Python-version-specific startup hooks (`pythonrc.py`,
  `uiready.py`) executed by Houdini.

## Next steps

- [Deadline and the render farm](deadline.md) — submitting jobs from
  TumblePipe.
- [Project structure](project_structure.md) — what ships in the package.
