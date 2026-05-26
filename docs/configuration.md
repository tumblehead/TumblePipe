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
  scaffolding, default task names).
- `naming_convention.py` — how assets, shots, and work files are named.
- `storage_convention.py` — maps project URIs (`project://`, `entity://`, …)
  to concrete filesystem paths.

Render layer / AOV configuration lives in the schema and entity data
(`schemas.json`, `entity.json` under the `render` sub-object), not in a
Python module.

Each module exposes a specific interface that the pipeline calls into. The
[*Turbulence* tech demo](https://www.sidefx.com/tech-demos/turbulence/)
publishes a complete working example of these modules.

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
