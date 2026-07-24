# TumblePipe project-setup wizard (tt_setup)

Native (Rust/egui) replacement for the PySide6 wizard that used to live at
`scripts/tt_setup.py`. It runs when a user clicks **Configure** on the
TumblePipe card in TumbleTrove Desktop.

## Why this exists

The old wizard was provisioned on demand through hpm's uv-managed venv
(`[scripts.tt_setup]` with `requirements = ["PySide6>=6.6"]`). The very first
Configure click had to fetch a CPython interpreter and build a ~100 MB PySide6
venv before the window could appear. This crate compiles to a single
self-contained binary shipped prebuilt in the package archive (like the
resolver), so launch is instant with no runtime download.

## What it does

Two flows, identical in behaviour to the Python original:

- **Use existing project** — verify a project root has `_config/db/entity.json`
  and emit its path.
- **Create new project** — copy `scripts/project_template/` into
  `<parent>/<name>/`, create the top-level dirs (`assets shots groups kits
  export`), and patch the config DBs (`entity.json` farm pools, `config.json`
  and `schemas.json` fps).

On accept it prints `{"envVars":{"TH_PROJECT_PATH":"…"}}` to stdout (the
contract TumbleTrove parses) and exits 0; on cancel it exits non-zero.

## Layout

```
src/wizard/
├── Cargo.toml
└── src/
    ├── lib.rs    # GUI-free core: validation, template copy, config-DB patching
    └── main.rs   # egui shell + rfd folder pickers + stdout/exit contract
```

`lib.rs` is kept GUI-free so the byte-for-byte JSON parity with the Python
`json.dump(indent=4)` output is pinned by unit tests (`serde_json`
`preserve_order` keeps human-edited key order; `arbitrary_precision` keeps
untouched numbers as their original tokens).

## Building

```sh
cargo test            # unit tests (JSON parity, validation)
cargo build --release # -> target/release/tt_setup[.exe]
```

CI builds it per platform via `.ci/build_wizard.py` (wired as the
`build-wizard` prepack step in `hpm.toml`), which drops the binary into
`bin/<platform>/tt_setup[.exe]` for packing. The binary takes
`--template-dir <path>` to locate the bundled `project_template/` (hpm passes
`scripts/project_template`; it also falls back to a cwd/exe-relative lookup for
standalone dev runs).
