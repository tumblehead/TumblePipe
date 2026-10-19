# Project structure

Layout of the TumblePipe package (directories shown relative to the
package root, the directory containing `hpm.toml`).

```text
TumblePipe/
├── hpm.toml                     # HPM package manifest
├── README.md                    # Short project overview
├── LICENSE                      # MIT License
├── radial_menus/                # Radial menus (shipped JSON + startup-generated)
├── stock/radial_menus/          # Stock department radials (Shift+Space)
├── desktop/
│   └── TumblePipe.desk          # Houdini desktop layout
├── otls/                        # Houdini Digital Assets (text format)
├── python/tumblepipe/           # Core pipeline Python modules
│   ├── asset_browser/           #   TumbleTrove asset-browser catalog (declared at startup)
│   └── vram/                    #   VRAM Inspector (TumbleTrove ▸ TumblePipe ▸ VRAM Inspector)
│   ├── pythonrc.py
│   └── uiready.py
├── python3.13libs/              # Houdini 22 startup stubs (byte-identical to 3.11)
├── bin/                         # Pre-built hook apps (tt_setup, tt_prepare; per-platform)
├── resolver/                    # Pre-built USD asset resolver (native, per Houdini major)
├── dso/                         # Pre-built VRAM Inspector viewport hook (per Houdini major.minor)
├── src/                         # Native (Rust/C++) sources — built, not shipped as source
│   ├── resolver/                #   `entity://` USD resolver (Rust core + C++ ArResolver shim)
│   ├── wizard/                  #   hook apps: core + tt_setup + tt_prepare (Rust/egui)
│   └── vram/                    #   VRAM Inspector: viewport hook DSO (C++) + size-model calibration (dev-only)
├── resources/                   # Icons, UI resources, templates
├── scripts/                     # TumbleTrove hooks, node event scripts, maintenance CLIs
│   ├── project_template/        #   new-project scaffolding (also migration's source of truth)
│   ├── lop/                     #   per-node-type event scripts (Houdini scans these)
│   └── fix_*.py / verify_*.py   #   project maintenance / audit CLIs
└── docs/                        # This documentation (Markdown; rendered offline by TumbleTrove, and by Sphinx on Read the Docs)
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

- `api.py`, `startup.py`, `naming.py`, `storage.py` — package root: the
  lazy `api` client, Houdini-startup registrations (radial
  menus/actions), the naming rules, and the storage table reader. The `_config`
  migrations are *not* here: they live in the `th_project_core` Rust
  crate and run from the `tt_prepare` binary.
- `util/` — dependency-free primitives (`Uri`, io, logging, progress
  breadcrumbs, the single `hou`-availability gate).
- `config/` — the JSON config store and typed accessors (entities,
  departments, channels, timeline, farm, renderer, scene).
- `pipe/` — pipeline core: `paths/` (all filesystem addressing),
  `graph.py` (dependency graph), `build.py` (shot/asset build
  resolution), `context.py`, `usd/` (USD text, sublayer refs, render
  settings, the farm collapse, composition arcs), `scene_build.py`, and
  `houdini/` (HDA node wrappers under `lops/`/`sops/`/`cops/`, shared
  `entity_node.py` base, `render_stage.py` — the single render-stage
  LOP graph builder shared by the farm stage tasks and the
  `th::render_debug` HDA — and task/process UI).
- `apps/` — external-tool launchers (Deadline, hython, ffmpeg/EXR
  tools).
- `farm/` — Deadline job/task builders (`jobs/` submit-side DAGs,
  `tasks/` per-task build + worker scripts).
- `config_editor/` — the Qt config-database editor application
  (launched from the asset browser's right-click menu).
- `vram/` — the VRAM Inspector ([user guide](vram-inspector.md)): reads the
  viewport hook's measurements (every memory client, and each viewer prim's
  exact bytes), scans the viewed stage, textures and cooked COP layers, and
  hangs the scan under the measured clients. `clients`, `hook`, `sizes`,
  `report` and `process` are pure Python; the rest need `hou`, `pxr` or Qt.
- `tools/` — network-editor helpers for the HDAs and the startup
  context menus. Named for the shelf tools that used to call them; the
  `toolbar/*.shelf` files were never in the package allowlist, so those
  helpers shipped unreachable and went when the shelves did.

### `python3.13libs/`

Houdini 22's Python 3.13 startup hooks. `pythonrc.py` runs on every
startup, graphical or hython (`python/` is importable through `PYTHONPATH`
from `hpm.toml [runtime]`): it registers Radial's menus, the TumbleTrove
package and its scene-load callback, and the workfile context provider;
`uiready.py` runs when the UI is ready (selects
the TumblePipe desktop). Everything with actual logic lives in the
package so the two interpreters cannot drift.

### `src/resolver/` and `resolver/`

The `tumbleResolver` USD asset resolver, which implements `entity://` URIs.
`src/resolver/` holds the Rust / C++ sources; `resolver/houdini<major>/`
holds the built binaries that ship in HPM release archives. The platform
slug is intentionally absent from this path: HPM produces a slim
per-platform archive (one OS per release asset), so the install layout is
flat by Houdini major. `hpm.toml [env]` then registers the resolver with
USD by prepending this path to `PXR_PLUGINPATH_NAME` (and the OS-specific
dynamic-linker search path), which Houdini applies before USD initializes
its plugin registry.

### OCIO / color config

The OCIO config used by Houdini is owned by the *project*, not the package. It
ships in the config template (`scripts/project_template/_config/ocio/`) and
lands at `<project>/_config/ocio/tumblehead.ocio`; the package sets the `OCIO`
environment variable to `$TH_CONFIG_PATH/ocio/tumblehead.ocio` at startup.
Existing projects gain the file via the v4 config migration.

### `python/tumblepipe/asset_browser/`

A TumbleTrove `asset_browser` *catalog* — the integration that surfaces
pipeline assets, shots, Multis and Roots in the asset-browser pypanel.
It is an ordinary subpackage: `tumblepipe.startup.register_package`
**declares** `factory.create_catalog` to TumbleTrove at startup, so a
renamed factory is an ImportError at launch
rather than a catalog that quietly stops appearing. (It used to be a
top-level `asset_browser_catalogs/` directory that TumbleTrove globbed
for a `pipeline.py`; the `_pipeline_` prefixes and the `sys.path` tweak
that shape forced are gone with it.) Nothing is imported eagerly — the
catalog is built when the panel first opens, because importing it pulls
Qt and the pipeline clients onto the launch path.

- `factory.py` — `create_catalog()`: project registry + env bootstrap.
- `catalog.py` — the `PipelineCatalog` class: the host-facing hooks and
  the composition root. It builds each collaborator below with the narrow
  services it needs (never the catalog itself) and routes every hook to
  it; recipe, node and container ids are routed before anything
  entity-shaped sees them. It must override every collection hook the base
  `Catalog` declares, since a default means "not offered" and the feature
  would silently vanish (`tests/test_catalog_multis.py` pins this).
- `container_menu.py` — the Multi / Root menu entries beyond tumbletrove's
  own (Open Location, the Root's export and rebuilds) and their
  confirmations.
- `projects.py` — the registry of projects the catalog spans (moved here
  from the TumbleTrove SDK, which no longer knows about TumblePipe).
- `houdini.py` — project activation (TH_* env / `default_client` reset)
  and the session's workfile extension.
- `clients.py` — per-project tumblepipe `Client` lifecycle.
- `resolver.py` — asset id → project, client, entity URI and the entity's
  own folder; a hip path → its project.
- `discovery.py` — `ProjectBuckets` (a project's categories and
  sequences) and `EntityIndex` (asset / shot cards and details, the
  discovery pass and its cache, the department workfile scan, the
  sidebar's per-bucket counts).
- `sidebar.py` — the collections tree.
- `dept_scope.py` — a context's department pool, and the departments one
  entity shows (its assignment plus any with work).
- `entity_ops.py` — creating, editing and deleting assets and shots, the
  description sidecar, Duplicate, Generate Master, and the per-entity
  actions (Departments…, Database Editor, todos, Submit Jobs…).
- `menus.py` / `deck.py` / `hover.py` — card and deck-item menus (every
  entry its own failure boundary), department decks, hover popups.
- `refresh.py` — asking the open browsers to repaint (a card, the grid,
  the sidebar, the detail panels, a thumbnail) through the SDK's
  `Catalog.request_*_refresh` calls, handed to the managers as one
  service.
- `containers.py` — `GroupContainer` / `SceneContainer` typed sum
  replacing the Multi-vs-Root `kind` branching, plus the
  `ContainerManager` that owns all container behaviour: collection
  discovery, cards and details, member/dept coverage, the collection
  lifecycle (delete, edit, add / remove members), and the Root
  context-menu actions (open location, rebuild assigned shots, export
  USD).
- `uris.py` — typed factories for tumblepipe URIs, and `asset_id_for`
  (entity URI → browser id).
- `drops.py` — the LOP / SOP / sublayer drop router.
- `workfiles.py` — workfile open / create lifecycle plus the per-row
  reads (user / mtime / note) behind a department row.
- `scene.py` — scene-state lifecycle: save / publish / reload /
  save-before-swap (prompt or silent version-up), the readonly
  hip-context helpers (including which browser id addresses the open
  .hip), and opening the Farm Submit grid.
- `detail.py` — Qt widget construction for every section in the
  right-hand detail panel, which the browser fills from the selected
  card in every scope (`session_panel_mode` is `"off"`, so tumbletrove's
  open-document *session* panel never takes the pane). A Multi's
  coverage editor is also hosted from the card menu, which reaches it
  without selecting the Multi first.
- `departments.py` — the entity Departments… dialog (per-entity pool
  scoping) and the project's department pool editor.
- `recipes.py` / `nodes.py` — the Recipes and Nodes card kinds.
- `submit_jobs_dialog.py` — the Farm Submit dialog (the grid of publish /
  playblast / render steps per entity). Its Qt-free halves live in
  `tumblepipe/farm/`: `submit_settings.py` (the per-entity settings resolver
  behind its form), `submit_grid.py` (cell states, tri-state ticks, stale
  warnings and what each row submits), `submit_status.py` (each cell's
  status: folder resolution on the main thread, listing and stats on a
  worker thread) and `submit_camera.py` (the render camera a row would use).
- `farm_submission_window.py` — the non-modal window that follows a
  submission running in its own process.
- `thumbnails.py` — sidecar thumbnail read / write / refresh.
- `widgets.py` — detail-panel custom QLabel / QComboBox.
- `types.py` — value-types and module-level constants.
- `prefs.py`, `settings_widget.py` — persisted preferences plus the
  gear-icon settings UI.
- `icons/` — the catalog's own SVG icons.

### `radial_menus/`

Menus for the radial (the Space-key Qt radial; the Houdini-native
`radialmenu/` system was retired in its favour). The radial ships as its
own package — python package `tumbleradial`. It lived inside tumbletrove
as `tumbletrove.radial` until tumbletrove v0.14.0 split it into its own
repo, so nothing below registers unless that package is installed.
`tumblepipe_pipeline.json` is authored, tracked and shipped (Alt+T:
pipeline HDA submenus). `tumblepipe_recipes.json` and
`tumblepipe_asset_favorites.json` are generated (or removed, when fewer
than the two entries a radial ring requires exist) at startup by
`tumblepipe.startup` from the live `Recipes.hda` and the asset-browser
favorites, so they cannot drift from what exists — they are gitignored
and never ship. The directory is registered via
`radial.add_custom_menu_dir()`. The `network.cop` / `network.vop`
context menus are built in Python in `tumblepipe.startup` rather than as
JSON, and bound with `radial.bind()` on Radial's own trigger key.

### `stock/`

Files TumblePipe ships as the baseline of a TumbleTrove layer.
`stock/radial_menus/<department>.json` is one radial per standard
department, each with a `"when"` naming its department. They are not
registered with Radial at startup. `tumblepipe.workfile_context` hands the
folder to `tumbletrove.context` as its `stock_dir`, and Radial opens the one
for the current department on Shift+Space. They live outside
`radial_menus/` because TumbleTrove reads that folder as the package layer,
where TumblePipe's rig menu would tie with TumbleRig's. See
[Radial menus → Shift+Space](radial-menus.md#shiftspace-the-department-menu).

### Recipes

Asset-browser recipes (saved node clusters) are project content, not
package content: they live under `<project>/recipes/<context>/<slug>/`
and are read and written by `python/tumblepipe/asset_browser/recipes.py`.
See [Recipes](asset-browser/recipes.md).
