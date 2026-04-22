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
| `TH_CONFIG_PATH`   | Path to the studio config directory (see below). | yes      |
| `TH_EXPORT_PATH`   | Path where the pipeline writes exports.          | yes      |
| `TH_USER`          | Override the user identity in the pipeline.      | no       |

`TH_USER` defaults to your operating system username when unset.

## The convention framework

TumblePipe expects a config directory (pointed at by `TH_CONFIG_PATH`) that
contains these Python modules. The filenames are not optional — the pipeline
imports them by name:

- `config_convention.py` — workspace configuration (departments, shot
  scaffolding, default task names).
- `naming_convention.py` — how assets, shots, and work files are named.
- `storage_convention.py` — maps project URIs (`project://`, `entity://`, …)
  to concrete filesystem paths.
- `render_convention.py` — render layer / AOV configuration.

Each module exposes a specific interface that the pipeline calls into. The
[*Turbulence* tech demo](https://www.sidefx.com/tech-demos/turbulence/)
publishes a complete working example of these modules.

## Where configuration lives in the codebase

- `hpm.toml` — Houdini package manifest and HPM metadata (dependencies,
  supported Houdini version, native resolver slots).
- `ocio/tumblehead.ocio` — OpenColorIO config shipped with the package;
  the package sets `OCIO` to this path on Houdini startup.
- `scripts/` — Houdini startup scripts that run when the package loads.
- `python3.11libs/` — Python-version-specific startup hooks (`pythonrc.py`,
  `uiready.py`) executed by Houdini.

## Next steps

- [Deadline and the render farm](deadline.md) — submitting jobs from
  TumblePipe.
- [Project structure](project_structure.md) — what ships in the package.
