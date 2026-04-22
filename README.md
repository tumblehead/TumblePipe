# TumblePipe

A small studio pipeline for animation and VFX projects in Houdini, developed
for the [*Turbulence*](https://www.sidefx.com/tech-demos/turbulence/) short
film at [Tumblehead](https://tumblehead.com).

**Full documentation:** [tumblepipe.readthedocs.io](https://tumblepipe.readthedocs.io) —
installation, configuration, the convention framework, render farm setup,
and project structure reference.

## Install

Pick one:

- **[TumbleTrove Desktop](https://tumbletrove.com/desktop)** — browse,
  install, and update Houdini packages from a GUI. No command line required.
- **[HPM](https://hpm.readthedocs.io)** — the Houdini Package Manager:
  ```bash
  hpm add tumblepipe --git https://github.com/tumblehead/TumblePipe
  ```
- **Manual** — grab an archive from
  [Releases](https://github.com/tumblehead/TumblePipe/releases).

A Linux environment (WSL2 on Windows) with `uv`, `ffmpeg`,
`openimageio-tools`, and `opencolorio-tools` is required for the farm
scripts. See the
[installation guide](https://tumblepipe.readthedocs.io/en/latest/installation.html)
for the full prerequisite list.

## Configure

TumblePipe is customized through a config directory pointed at by
`TH_CONFIG_PATH`, containing four Python convention modules
(`config_convention.py`, `naming_convention.py`, `storage_convention.py`,
`render_convention.py`). The
[configuration guide](https://tumblepipe.readthedocs.io/en/latest/configuration.html)
documents each one; the [*Turbulence* tech demo](https://www.sidefx.com/tech-demos/turbulence/)
ships a working example.

## Package layout

- `otls/` — Houdini Digital Assets (HDAs), text-format for version control.
- `python/1x/tumblepipe/` — pipeline Python modules.
- `python3.Xlibs/` — per-Python-version libraries and Houdini startup hooks.
- `scripts/` — Houdini startup scripts.
- `python_panels/` — Python panels (project browser).
- `desktop/` — Houdini desktop layout.
- `resources/`, `ocio/` — resource files and OpenColorIO configuration.
- `resolver-src/` — source for the `entity://` USD asset resolver.
- `docs/` — documentation source (hosted on Read the Docs).
- `hpm.toml` — HPM package manifest.

## Disclaimer

- Free and open source — do with it as you please.
- Built by and for a small studio; design choices reflect our resources.
  Works well for individuals and small teams (up to ~20 artists); not
  currently appropriate for larger teams.
- We cannot offer tech support, but feedback and questions via
  [Issues](https://github.com/tumblehead/TumblePipe/issues) are welcome.
- We update the project as long as we use it ourselves. No deprecation
  warnings, no backwards-compatibility guarantees between releases.

## License

[MIT](LICENSE).
