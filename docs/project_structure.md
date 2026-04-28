# Project structure

Layout of the TumblePipe package (directories shown relative to the
package root, the directory containing `hpm.toml`).

```text
TumblePipe/
├── hpm.toml                     # HPM package manifest
├── README.md                    # Short project overview
├── LICENSE                      # MIT License
├── desktop/
│   └── TumblePipe.desk          # Houdini desktop layout
├── ocio/                        # OpenColorIO configuration
├── otls/                        # Houdini Digital Assets (text format)
├── python/1x/tumblepipe/        # Core pipeline Python modules
├── python3.11libs/              # Houdini 21 startup hooks + libraries
│   ├── pythonrc.py
│   └── uiready.py
├── python_panels/
│   └── project_browser.pypanel  # The project browser Python panel
├── resolver/                    # Pre-built USD asset resolver (native)
├── resolver-src/                # Source for the `entity://` USD resolver
├── resources/                   # Icons, UI resources, templates
├── scripts/                     # Houdini startup scripts
└── docs/                        # This documentation (Sphinx)
```

## Key directories

### `otls/`

Houdini Digital Assets. In the source repository these are stored as
text-expanded directories (one directory per HDA) so they diff well in git.
The release pipeline compiles them to binary `.hda` files via `hotl` before
packaging.

### `python/1x/tumblepipe/`

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
