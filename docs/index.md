# TumblePipe

A small studio pipeline for animation and VFX projects in Houdini, developed
for the *Turbulence* short film at [Tumblehead](https://tumblehead.com).

TumblePipe is designed for individuals and small teams (up to ~20 artists).
It bundles Houdini Digital Assets, Python tools, a USD asset resolver, a
project configuration framework, and integrations for the Deadline render
farm. It plugs into [TumbleTrove](https://tumbletrove.com)'s Asset Browser,
which is where an artist opens workfiles, saves versions, publishes, and
submits renders.

This documentation ships inside the package. Open it any time from
**TumbleTrove ▸ Documentation ▸ TumblePipe** in Houdini's menu bar (TumbleTrove
0.27.0 or newer); it renders offline, and the top bar of every page links to
the other installed packages' documentation. The same pages are online at
[tumblepipe.readthedocs.io](https://tumblepipe.readthedocs.io).

## Using TumblePipe

- [Getting started](getting-started.md) — install, configure a project, launch, and make a first workfile
- [Installation](installation.md) — the three install paths and what the farm needs
- [The Asset Browser and the pipeline](asset-browser/index.md) — the Pipeline catalog: sidebar, cards, rows, menus, quick actions
  - [Workfiles and versions](asset-browser/workfiles.md) — opening, creating and saving department workfiles
  - [Export and publish](asset-browser/export-and-publish.md) — what Export and Publish do, the process dialog, validators
  - [Submitting to the farm](asset-browser/submit-jobs.md) — the Submit Jobs dialog and the jobs it creates
  - [Multis and Roots](asset-browser/multis-and-roots.md) — shared multi-shot workfiles and config-driven scenes
  - [Pipeline settings and files](asset-browser/settings.md) — every option, and every file TumblePipe writes
  - [The config database editor](asset-browser/config-editor.md) — editing entities, schemas and departments
- [Pipeline nodes](nodes/index.md) — the `th::` node families and the concepts they share
  - [Importing and exporting](nodes/import-and-export.md) — import_shot, import_asset, export_layer, cache, …
  - [Building assets](nodes/assets.md) — the model → lookdev chain, rigs, thumbnails
  - [Lighting and rendering](nodes/lighting-and-rendering.md) — render settings, AOVs, LPE tags, lookdev studio, playblast
  - [Compositing nodes](nodes/comp.md) — build_comp and the COP toolkit
  - [Modelling and utility nodes](nodes/tools.md) — mesh tools, recipes, the VOP menu
- [Radial menus and shortcuts](radial-menus.md) — Alt+T, Alt+R, Alt+F and the network context menus
- [Troubleshooting](troubleshooting.md) — symptoms, causes, fixes, and where the logs are

## Setting up a project

- [Configuration](configuration.md) — environment variables, the setup wizard, the convention framework, departments, migrations
- [Deadline and the render farm](deadline.md) — the farm plugins, worker prerequisites, submitting from Python

## How it works

- [Asset composition and staging](composition.md) — channels, staged files, nesting, versions, render staging
- [Compositing](compositing.md) — where renders land, colour space, comps, MP4s, playblasts
- [Project structure](project_structure.md) — what ships in the package and where

## Project

- [Contributing and development](development.md) — tests, harnesses, audits, releases, writing these docs
- [Changelog](changelog.md) — the release history

## Disclaimer

TumblePipe is free and open source — do with it as you please. The project was
made by and for a small studio, so design decisions reflect what we have
resources to maintain. We cannot offer tech support, we do not guarantee
backwards compatibility between releases, and we do not publish deprecation
warnings. Feedback and questions are always welcome via
[GitHub Issues](https://github.com/tumblehead/TumblePipe/issues).
