# Project structure

Layout of the TumblePipe package (directories shown relative to the
package root, the directory containing `hpm.toml`).

```text
TumblePipe/
├── hpm.toml                     # HPM package manifest
├── README.md                    # Short project overview
├── LICENSE                      # MIT License
├── asset_browser_catalogs/      # TumbleTrove asset_browser catalog
│   ├── pipeline.py              #   factory entry point (discovered)
│   └── _pipeline_*.py           #   catalog implementation + helpers
├── radial_menus/                # tumbletrove radial menus (shipped JSON + startup-generated)
├── recipes/                     # Shipped asset-browser recipes (read-only)
├── desktop/
│   └── TumblePipe.desk          # Houdini desktop layout
├── ocio/                        # OpenColorIO configuration
├── otls/                        # Houdini Digital Assets (text format)
├── python/tumblepipe/           # Core pipeline Python modules
├── python3.11libs/              # Houdini 21 startup stubs
│   ├── pythonrc.py
│   └── uiready.py
├── python3.13libs/              # Houdini 22 startup stubs (byte-identical to 3.11)
├── python_panels/
│   └── icon_browser.pypanel     # Icon browser Python panel
├── resolver/                    # Pre-built USD asset resolver (native)
├── resolver-src/                # Source for the `entity://` USD resolver
├── resources/                   # Icons, UI resources, templates
├── scripts/                     # TumbleTrove hooks, node event scripts, maintenance CLIs
│   ├── tt_setup.py              #   project setup wizard (Qt6)
│   ├── project_template/        #   new-project scaffolding (also migration's source of truth)
│   ├── lop/                     #   per-node-type event scripts (Houdini scans these)
│   └── fix_*.py / verify_*.py   #   project maintenance / audit CLIs
└── docs/                        # This documentation (Sphinx)
```

## Key directories

### `otls/`

Houdini Digital Assets. In the source repository these are stored as
text-expanded directories (one directory per HDA) so they diff well in git.
The release pipeline compiles them to binary `.hda` files via `hotl` before
packaging.

### `python/tumblepipe/`

The pipeline's Python package. This is on Houdini's Python path; import
with `from tumblepipe import ...`. Subpackages:

- `api.py`, `startup.py`, `naming.py`, `migration.py`, `storage.py` —
  package root: the lazy `api` client, Houdini-startup registrations
  (radial menus/actions), naming/storage convention bases, config
  migration.
- `util/` — dependency-free primitives (`Uri`, io, logging, progress
  breadcrumbs, the single `hou`-availability gate).
- `config/` — the JSON config store and typed accessors (entities,
  departments, variants, timeline, farm, renderer, scene).
- `pipe/` — pipeline core: `paths/` (all filesystem addressing),
  `graph.py` (dependency graph), `build.py` (shot/asset build
  resolution), `context.py`, `usd.py`, `scene_build.py`, and
  `houdini/` (HDA node wrappers under `lops/`/`sops/`/`cops/`, shared
  `entity_node.py` base, `render_stage.py` — the single render-stage
  LOP graph builder shared by the farm stage tasks and the
  `th::render_debug` HDA — and task/process UI).
- `apps/` — external-tool launchers (Deadline, hython, ffmpeg/EXR
  tools, DaVinci Resolve).
- `farm/` — Deadline job/task builders (`jobs/` submit-side DAGs,
  `tasks/` per-task build + worker scripts).
- `config_editor/` — the Qt config-database editor application
  (launched from the shelf or the asset browser).
- `tools/`, `ui/` — shelf-tool helpers and the reusable Qt widget kit.

### `python3.11libs/` and `python3.13libs/`

Houdini's per-Python-version library locations (Houdini 21 → 3.11,
Houdini 22 → 3.13). Each holds the same two thin stubs, kept
byte-identical: `pythonrc.py` runs on Houdini Python startup (puts
`python/` on `sys.path`, then delegates all registrations to
`tumblepipe.startup`); `uiready.py` runs when the UI is ready (selects
the TumblePipe desktop). Everything with actual logic lives in the
package so the two interpreters cannot drift.

### `resolver-src/` and `resolver/`

The `tumbleResolver` USD asset resolver, which implements `entity://` URIs.
`resolver-src/` holds the Rust / C++ sources; `resolver/houdini<major>/`
holds the built binaries that ship in HPM release archives. The platform
slug is intentionally absent from this path: HPM produces a slim
per-platform archive (one OS per release asset), so the install layout is
flat by Houdini major. `hpm.toml [env]` then registers the resolver with
USD by prepending this path to `PXR_PLUGINPATH_NAME` (and the OS-specific
dynamic-linker search path), which Houdini applies before USD initializes
its plugin registry.

### `ocio/`

The OCIO config used by Houdini. The package sets the `OCIO` environment
variable to `$HPM_PACKAGE_ROOT/ocio/tumblehead.ocio` at startup.

### `asset_browser_catalogs/`

A TumbleTrove `asset_browser` *catalog* — the integration that surfaces
pipeline assets and shots in the asset-browser pypanel. `hpm.toml`
prepends this directory to `ASSET_BROWSER_CATALOG_PATH`, and
TumbleTrove's catalog registry loads `pipeline.py` from it via
`importlib.util.spec_from_file_location`. That loader globs top-level
`*.py` files only and skips underscore-prefixed names, so the catalog
itself is one `pipeline.py` (a small factory: `create_catalog()` plus
the `sys.path` tweak that lets the companions import each other
absolutely) and the implementation is split across `_pipeline_*.py`
companion modules:

- `_pipeline_catalog.py` — the `PipelineCatalog` class itself, which
  composes everything below.
- `_pipeline_houdini.py` — Houdini main-thread bridge + project-
  activation (TH_* env / `default_client` reset).
- `_pipeline_clients.py` — per-project tumblepipe `Client` lifecycle.
- `_pipeline_resolver.py` — asset-id → `(ref, project, client, uri,
  root)` resolution.
- `_pipeline_containers.py` — `GroupContainer` / `SceneContainer`
  typed sum replacing the Multi-vs-Root `kind` branching, plus the
  `ContainerManager` that owns all container behaviour: collection
  discovery, member/dept coverage, member add/remove, and the Root
  context-menu actions (open location, rebuild assigned shots,
  export USD).
- `_pipeline_uris.py` — typed factories for tumblepipe URIs.
- `_pipeline_drops.py` — the LOP / SOP / sublayer drop router.
- `_pipeline_workfiles.py` — workfile open / create lifecycle plus
  mtime / user-attribution helpers.
- `_pipeline_scene.py` — scene-state lifecycle: save / publish /
  reload / save-before-swap (prompt or silent version-up) and the
  readonly hip-context helpers.
- `_pipeline_detail.py` — Qt widget construction for every section
  in the right-hand detail panel.
- `_pipeline_thumbnails.py` — sidecar thumbnail read / write / refresh.
- `_pipeline_widgets.py` — detail-panel custom QLabel / QComboBox.
- `_pipeline_types.py` — value-types and module-level constants.
- `_pipeline_prefs.py`, `_pipeline_settings_widget.py` — persisted
  preferences plus the gear-icon settings UI.

None of the `_pipeline_*` files are loaded by TumbleTrove directly.

### `radial_menus/`

Menus for the tumbletrove radial system (the Space-key Qt radial; the
Houdini-native `radialmenu/` system was retired in its favour).
`tumblepipe_pipeline.json` is authored, tracked and shipped (Alt+T:
pipeline HDA submenus). `tumblepipe_recipes.json` and
`tumblepipe_asset_favorites.json` are generated (or removed, when fewer
than the two entries a radial ring requires exist) at startup by
`tumblepipe.startup` from the live `Recipes.hda` and the asset-browser
favorites, so they cannot drift from what exists — they are gitignored
and never ship. The directory is registered via
`radial.add_custom_menu_dir()`; the `network.cop` / `network.vop`
context menus are registered from Python in `tumblepipe.startup`
rather than as JSON.

### `recipes/`

Network-catalog entries (saved node-cluster recipes) that ship with the
package. `hpm.toml` prepends this directory to
`ASSET_BROWSER_NETWORK_PATH`; TumbleTrove's asset browser (> 0.9.1)
scans it as a **read-only** recipe root alongside the user's personal
library — entries browse and drop normally but cannot be edited or
deleted, and new recipes are never saved here. Layout and the authoring
workflow are documented in `recipes/README.md`.
