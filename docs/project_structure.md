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
├── desktop/
│   └── TumblePipe.desk          # Houdini desktop layout
├── ocio/                        # OpenColorIO configuration
├── otls/                        # Houdini Digital Assets (text format)
├── python/tumblepipe/           # Core pipeline Python modules
├── python3.11libs/              # Houdini 21 startup hooks + libraries
│   ├── pythonrc.py
│   └── uiready.py
├── python_panels/
│   └── project_browser.pypanel  # The project browser Python panel
├── resolver/                    # Pre-built USD asset resolver (native)
├── resolver-src/                # Source for the `entity://` USD resolver
├── resources/                   # Icons, UI resources, templates
├── scripts/                     # TumbleTrove hooks + project template
│   ├── tt_setup.py              #   project setup wizard (Qt6)
│   └── project_template/        #   new-project scaffolding
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
with `from tumblehead import ...`.

### `python3.11libs/`

Houdini's per-version Python library location. `pythonrc.py` runs on Houdini
Python startup; `uiready.py` runs when the UI is ready. The `external/`
subtree (bundled third-party wheels per platform) is excluded from the git
mirror — HPM resolves those dependencies on the user's machine via the
`[python_dependencies]` table in `hpm.toml`.

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
