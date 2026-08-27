# TumblePipe hook apps (tt_setup, tt_prepare)

A cargo workspace holding the native (Rust/egui) apps TumbleTrove Desktop
invokes as package hooks, plus the core they share:

| crate | binary | hook | when it runs |
|---|---|---|---|
| `core/` | — | — | shared: validation, template scaffold, config-DB edits, `_config` migrations |
| `tt_setup/` | `tt_setup` | `tt_setup` | the **Configure** button, or an unfilled required var |
| `tt_prepare/` | `tt_prepare` | `tt_prepare` | **every `launch_project`** — migrates `_config` when the project is behind |

They are separate apps rather than modes of one binary because the hooks are
separate jobs: `tt_setup` sets a project up, `tt_prepare` brings its config
forward. Cargo shares one dependency set across every `[[bin]]` in a crate, so
member crates are also what lets each app carry only what it uses.

`core/` links no GUI, which keeps the byte-for-byte JSON parity and the
migration rules unit-testable without spinning up a window.

Both binaries ship prebuilt in the package archive as
`bin/<platform>/<name>[.exe]`, built together by `.ci/build_wizard.py`.

## tt_setup — the project-setup wizard

Native replacement for the PySide6 wizard that used to live at
`scripts/tt_setup.py`. It runs when a user clicks **Configure** on the
TumblePipe card in TumbleTrove Desktop.

### Why this exists

The old wizard was provisioned on demand through hpm's uv-managed venv
(`[scripts.tt_setup]` with `requirements = ["PySide6>=6.6"]`). The very first
Configure click had to fetch a CPython interpreter and build a ~100 MB PySide6
venv before the window could appear. This crate compiles to a single
self-contained binary shipped prebuilt in the package archive (like the
resolver), so launch is instant with no runtime download.

### What it does

Two flows, identical in behaviour to the Python original:

- **Use existing project** — verify a project root has `_config/db/entity.json`
  and emit its path.
- **Create new project** — copy `scripts/project_template/` into
  `<parent>/<name>/`, create the top-level dirs (`assets shots groups
  export`), and patch the config DBs (`entity.json` farm pools, `config.json`
  and `schemas.json` fps).

On accept it prints `{"envVars":{"TH_PROJECT_PATH":"…"}}` to stdout (the
contract TumbleTrove parses) and exits 0.

On cancel it exits **0** with nothing on stdout, printing the decline to
stderr. TumbleTrove reads "empty stdout + zero exit" as "no env var changes",
which is what a decline means — whereas any non-zero exit is surfaced to the
user as `Configure failed for tumblehead/tumblepipe`. It used to exit 1 here
(inherited from the PySide wizard), which reported the user's own choice back
to them as an error. That matters because TumbleTrove runs this wizard
*unprompted* when `TH_PROJECT_PATH` has no value, so a decline can be a
response to a window nobody asked to open. Failing to start the wizard at all
is a genuine failure and still exits 2.

### Layout

```
src/wizard/
├── Cargo.toml            # workspace root (members + release profile)
├── core/src/
│   ├── lib.rs            # GUI-free: validation, template copy, config-DB patching
│   └── migration.rs      # the _config migration registry, preflight and steps
├── tt_setup/src/main.rs  # egui shell + rfd folder pickers + stdout/exit contract
└── tt_prepare/src/main.rs # egui migration window + the --migrate CLI
```

`core/` is kept GUI-free so the byte-for-byte JSON parity with the Python
`json.dump(indent=4)` output is pinned by unit tests (`serde_json`
`preserve_order` keeps human-edited key order; `arbitrary_precision` keeps
untouched numbers as their original tokens). The release profile lives in the
workspace root — cargo ignores a `[profile]` table in a member manifest.

### Building

```sh
cargo test            # unit tests (JSON parity, validation, migrations)
cargo build --release # -> target/release/{tt_setup,tt_prepare}[.exe]
```

CI builds it per platform via `.ci/build_wizard.py` (wired as the
`build-wizard` prepack step in `hpm.toml`), which drops the binary into
`bin/<platform>/` for packing (both binaries; add one to `BINARIES` there to ship it). The binary takes
`--template-dir <path>` to locate the bundled `project_template/` (hpm passes
`scripts/project_template`; it also falls back to a cwd/exe-relative lookup for
standalone dev runs).

## tt_prepare — the launch-time migrator

Runs on **every** `launch_project`. It reads `_config/version.json`, and when
the project is already current it prints an empty payload and exits without
ever creating a window — the common case costs one small file read.

When the project *is* behind it shows the pending steps, asks, and migrates
only if the user agrees. It exists because nothing else brought migrations to
anyone: `migrate-config` is a Scripts-panel button somebody has to notice, and
no TumbleTrove hook fires on *upgrade* — `tt_install` is per-project rather
than per-version, and `tt_setup` is driven by the Configure button. Every
launch is the only reliable moment left.

### Rules the hook contract forces

- **One JSON object on stdout, everything else on stderr.** Console subsystem,
  like `tt_setup` — a `windows` subsystem build would detach stdout.
- **Never refuse a launch.** Every hook path exits 0. A failing hook is logged
  and the launch proceeds anyway, so failing loudly buys nothing.
- **Never open a window nobody can answer.** `TT_NONINTERACTIVE`, `CI`,
  `TH_FARM_WORKER`, or Linux with no `DISPLAY`/`WAYLAND_DISPLAY` log and
  return. The hook contract documents no timeout, so a modal on a headless
  host would hang the launch indefinitely.
- **Never migrate a shared project twice at once.** `_config` lives on a studio
  share and every artist's launch runs this, so the migration is taken under a
  lock that goes stale after 15 minutes rather than wedging the studio.

### Migration rules worth knowing

- A run is **refused as a whole** when preflight finds a pending step that
  cannot complete. Applying steps until one fails stamps the version after
  each, which is how five projects ended up part-migrated on 2026-08-26.
- Writes **preserve a file's existing line endings** (new files get LF). These
  files sit on a shared drive; rewriting CRLF to LF turns a migration into a
  whole-file diff on every project.
- Steps that touch `storage_convention.py` edit it **surgically**. A project's
  convention legitimately differs — several carry their own `_primary_path` —
  so replacing it would repoint where a show's assets and exports resolve.

### CLI

    tt_prepare --migrate [--dry-run] [<project>]

Headless, for a person or a bulk sweep: no window, a readable report on
stdout, non-zero exit on failure. `scripts/migrate_config.py` is a shim over
this that picks the right `bin/<platform>/` build.
