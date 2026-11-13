# Contributing and development

TumblePipe is developed privately and mirrored to the public GitHub
repository at [tumblehead/TumblePipe](https://github.com/tumblehead/TumblePipe).
We welcome issue reports and questions, but cannot guarantee response times
or feature requests.

## Reporting issues

File issues on
[GitHub Issues](https://github.com/tumblehead/TumblePipe/issues). When
reporting a bug, include:

- Houdini version.
- TumblePipe version (`hpm show tumblepipe`, or check `hpm.toml`).
- Operating system.
- A minimal reproduction — a small .hip file or a short script — if
  possible.
- The pipeline log covering the failure, if the report is "it did nothing":
  `$TH_PROJECT_PATH/export/other/logs/$TH_USER.log` (see *Where the logs
  are*). Error dialogs name the file they wrote to.

## Running the test harness

`tests/` ships a property-based test harness for the Python side of the
pipeline, using [minigun-soren-n](https://github.com/soren-n/minigun) for
QuickCheck-style generation and shrinking. It has its own uv-managed
venv pinned to Python 3.12+ (Houdini's bundled 3.11 is too old for
minigun) and is independent of `hpm.toml` and the package install. From
the repository root:

```bash
cd tests
uv sync
uv run minigun --test-dir . --time-budget 60 --jobs auto
```

`--jobs auto` runs one worker process per CPU; see `tests/README.md`.

On Windows that dies in the reporter, not the tests:

```
UnicodeEncodeError: 'charmap' codec can't encode character '∞'
```

minigun's execution-plan table prints `∞`, and the console lands on
cp1252. It says nothing about whether the suite passes. Force UTF-8:

```bash
PYTHONUTF8=1 uv run minigun --test-dir . --time-budget 60 --jobs auto
```

(`PYTHONUTF8=1`, matching `tests/README.md` and the hooks — the two pages
used to disagree, one saying `PYTHONIOENCODING=utf-8`.)

`tests/README.md` covers writing new properties and the design of the
project-fixture bootstrap (`_harness.py`).

Two modules also need **cargo** on `PATH` (or in rustup's default
`~/.cargo/bin`). `test_resolver_parity` checks that every path the Python
writers publish to is the path the Rust resolver in `src/resolver` resolves
the recorded URI to. `test_wizard_json_parity` checks that the `_config`
migrator in `src/wizard` rewrites JSON byte for byte as `json.dump` wrote it.
Each builds a small example from its crate on first use and drives it as a
subprocess (`tests/_cargo.py`). Without cargo they fail rather than skip.
The crates have their own tests, including `proptest` properties in the
resolver, in the wizard's migrations and in the delegate: run `cargo test`
in `src/resolver` and in `src/wizard`, and `cargo test --release` in
`src/delegate` (add `--features gpu` on a machine with a GPU, to hold the
GPU path to the CPU's pictures; the build workers do not run it). When
you break code on purpose to check that a property catches it, delete
the seed that run writes: it records a bug that never existed.

The asset-browser catalog tests (`test_catalog*.py`) and the docs link
check run against the **shipped TumbleTrove SDK**: `tests/_catalog.py`
puts the `python/` dir of the newest `tumbletrove` package hpm has
fetched (`~/.hpm/fetch/tumbletrove-tumbletrove-<version>/python`), else a
sibling `tumbletrove` checkout's `python/`, on `sys.path`, and refuses one
older than `hpm.toml [dependencies]` requires. `TUMBLETROVE_PYTHON` names
an SDK `python/` dir outright and skips that floor — the way to run a
branch against an unreleased SDK checkout, whose manifest still carries
the last release's version. The SDK's `asset_browser.api` and
`common` layers are pure Python, so the catalog
imports against the same base `Catalog`, `Collection` and `Asset`
the browser uses — the only way a property can see the
*host contract*. The lesson behind it: tumbletrove once routed every
operation on a Multi or Root through `Catalog.owns_collection`, and the
catalog never overrode it, so an artist could create a Multi and then do nothing else
with it — invisible to a stub whose base `Catalog` was `pass`. Launch a
project from TumbleTrove Desktop once so the SDK is fetched.

A second class of silent break lives *between* the catalog's own modules:
they reach each other through the catalog instance
(`self._catalog._containers.x`), so a helper that moves in a refactor is an
`AttributeError` only on the click that needs it — which is how "New:
Template" on a Multi row, the one action that creates a multishot
workfile, shipped broken for three months. `test_catalog_wiring.py` reads
every module as source and checks each cross-module reference against the
owning class, so that break now fails the suite instead of an artist. The
same file also bans loading one of these modules by file path, which is
how the Submit Jobs dialog lost its package (and with it the relative
import of its own resolver) in the move into `tumblepipe.asset_browser`.

A third class, and the one that has cost the most: **the base `Catalog`
is not the whole host contract.** TumbleTrove's browser also reaches
members on the catalog object that its abstract base does not declare,
so a rename on either side raises nothing — the call simply stops
happening. Their call sites wrap these in bare `except` handlers, which
is what turns a missing name into a feature that quietly does nothing.

It has bitten three times. `attach_network_thumbnail` stopped attaching
thumbnails during their v0.28.1 refactor. `delete_bucket`,
`list_root_assigned_shots` and `rebuild_root_assigned_shots` were called
but undeclared, two of them behind `hasattr` probes that convert a
drifted name into a no-op by design; they are declared on their base
class since tumbletrove 0.29.0.
Worst, `_entity_uri_for` and `_project_for_asset_id` left this catalog
in the 2026-05-26 DropRouter and AssetResolver extractions while their
deck-item drop path went on calling them, so dragging a department deck
item into a LOP network did nothing at all from v1.10.0 to v1.47.0.
It died a second time on 2026-10-13, when `Uri.parse_unsafe` (which
their browser called to build the node) was renamed `Uri.parse`.

TumbleTrove 0.48 ended this class of break at the source. The browser
reaches no private member of ours: the deck-item drop is
`DropRouter.on_deck_drop`, which builds the `th::import_layer` nodes
here, and the bucket-delete count is the declared
`Catalog.bucket_child_count`. The six private delegations it used to
call (`_activate_project`, `_entity_uri_for`, `_project_for_asset_id`,
`_project_for_hip_path`, `_count_for_project_category`,
`_count_for_project_sequence`) are gone, and their `test_catalog_calls`
now fails on any private reach. Refreshing the open browsers goes the
other way through the declared `Catalog.request_*_refresh` calls
(`refresh.py`), not through their widgets, and a catalog hook the
artist triggered may simply raise: the browser reports it.

`test_catalog_wiring` now guards this from our end and their
`test_catalog_calls` guards it from theirs. Neither is sufficient
alone, and the reason is worth keeping: each one first shipped scanning
a chosen list of their directories, and each missed a real surface the
other's list covered. A checker cannot report what its own scope left
out — it passes loudly while blind. Ours therefore reads the whole
package rather than a list.

## Linting

Python is linted with [ruff](https://docs.astral.sh/ruff/) under the
`E9,F` selection — syntax errors plus the full Pyflakes set (undefined
names, unused imports, f-strings without placeholders). That net is what
catches a shipped-broken import or a stray `f''` before it reaches a
release. Run it over the same trees the gate covers:

```bash
uvx ruff check python/ .ci/ python3.13libs/ \
  scripts/ tests/ --select E9,F
```

No git hook runs it: lint, like the tests, is yours to run before you push.
`uv run --no-project python .ci/preflight.py` runs it together with every
other gate, including `.ci/quality_gates/check_forbidden_files.py`, which
refuses an AI-generated report/summary `.md` (matched by content markers,
not filename). Given file paths it checks only those; with no arguments (as
preflight runs it) it scans the whole tree.

The hook **blocks** if `uv`/`ruff` isn't on `PATH`, rather than skipping. A
gate that silently passes when it cannot run is worse than no gate: it
prints the same shape as a real run, so an unlinted commit looks identical
to a linted one. `git commit --no-verify` bypasses it for a single commit.

## Running the tests before a push

No hook runs the tests: a pre-push hook used to, and it was removed because
it ran on every push, branch deletions included, and blocked pushes whenever
the shell lacked the test environment. Run the preflight below (its `tests`
stage is the minigun property suite, `rust` the Rust crates' own tests,
`hdas` the binary-HDA check, `qt` the Qt widget harnesses offscreen, and
`hython` the Houdini-side harnesses under hython) before pushing work. Work
that touches a window or dialog (anything under `asset_browser/`,
`config_editor/`, `farm/manager/` or `pipe/houdini/ui/`) needs at least
`preflight.py --only qt`, about 25 seconds. Work that touches an HDA
(`otls/`) or anything Houdini-side (`pipe/houdini/`, the templates, an HDA's
Python) needs `preflight.py --only hython` too, about 75 seconds with a
Houdini licence. `--only` takes several stages: `--only lint qt hython`.

The minigun suite runs no crate's `cargo test`, so a green suite says
nothing about the wizard, the resolver, the delegate or the player. Work
that touches `src/`, or anything a crate reads (such as the packaged
templates the wizard fingerprints), needs `preflight.py --only rust` too.

## Preflight

The release *tag* pipeline only builds and publishes (`.woodpecker/`) — it
does not lint and does not test. It starts only for version tags
(`ref: "refs/tags/v*"` in every workflow's `when:`), so another tag, such as
an `archive/<branch>` tag kept when a branch is deleted, starts nothing. So one command runs everything, and it is
what to run before tagging:

```bash
uv run --no-project python .ci/preflight.py
```

Stages: `lint` (ruff E9,F), `hdas`, `hdapy`, `hdapaths`, `reports`, `tests`,
`rust`, `delegate`, `harness`, `qt`, `hython`.

`rust` runs `cargo test --locked` in `src/resolver`, `src/wizard` and
`src/delegate` (on Windows inside vcvars64's environment), then
`.ci/build_player.py --test` for the player workspace: `cargo fmt --check`,
clippy with `-D warnings` and the whole workspace's tests, in the release
build's FFmpeg and OpenColorIO environment. The first run in a fresh
checkout builds FFmpeg into `build-ffmpeg-<platform>/`, which takes a while;
a worktree can junction (or symlink) that folder from the main checkout. It
is the local half of `.ci/test.sh`, which the CI workers run on a manual
`RUN=tests` pipeline.

`delegate` builds the playblast delegate (`.ci/build_delegate.py`, into
`plugins/houdini<major>/`) and renders `.ci/smoke_delegate.py`'s stages
through it with husk: culling (left-handed, mirrored and subdivided meshes
included), the transparency switch, subdivision, lights, motion, curves and points, on the CPU and,
on Windows, the GPU. The C++ shim compiles only against a real Houdini and
no `cargo test` reaches it, so work that touches `src/delegate/` needs
`preflight.py --only delegate` too. Majors come from `$HPM_HOUDINI_MAJORS`,
else 22; on a machine with no Houdini (husk) installed the stage prints
SKIPPED and passes. It takes a few minutes.

`hdapy` runs `.ci/quality_gates/check_hda_python.py`: the `PythonModule` and
event-script sections of every expanded HDA under `otls/` are extensionless,
so the tree-wide ruff run never sees them, and Houdini only evaluates them
when the button is pressed. The gate streams each one to ruff as a `.py`
(E9, F821, F823, with `hou` and `kwargs` as builtins), which is how a
`NameError` on an error branch of `th::material_assigner` would have been
caught before an artist hit it.

`hdapaths` runs `.ci/quality_gates/check_hda_project_paths.py`: an HDA is
saved from inside a project scene, so every embedded node ships with
whatever it pointed at when "Save Node Type" was pressed. That is how
`th::import_model` came to carry one project's
`P:/growth/export/assets/CHAR/Baby/.../v0003.usd` in its inner
`import_layer` with the import enabled, and loaded that Baby model into
every other project's rigging workfile until Import was pressed. The gate
greps the text parts of every `Contents.mime` and `DialogScript` for
drive-letter and UNC paths, `/Users/` and `/home/` paths, `entity:/` URIs
that name an entity, and the `th_temp` root; `op:/`, `opdef:`, `$HIP` and
`$HFS` forms pass. Findings that need an asset change rather than a string
edit (the paint COPs' textures) sit in `KNOWN_OPEN` and are printed
without failing the gate.
`--list` prints them, `--only <stage> [<stage> ...]` runs those.

Every `scripts/verify_*.py` is in exactly one list in `.ci/preflight.py`:
`HEADLESS_HARNESSES` (the `harness` stage), `QT_HARNESSES` (`qt`),
`HYTHON_HARNESSES` (`hython`) or `EXCLUDED`, which says why no stage runs
it (a live Houdini session, arguments naming a live project, a live
project's published asset, real shots). `tests/test_qt_harness_lists.py`
fails when a harness is in none or in two, and when one that imports `hou`
is put in the `qt` or `harness` stage, so a new harness is run or excluded
on purpose.

`harness` runs only the two `scripts/verify_*.py` harnesses that work with
no arguments (`verify_entity_from_context`, `verify_hda_callbacks`). The
rest need Houdini, a Qt display, or a live `<project_path>` — including
`verify_entity_casing` and `verify_tracked_asset_counts`, which look
headless from their imports but argparse-require a project. Preflight says
how many it skipped rather than implying full coverage.

`qt` runs the `scripts/verify_*.py` harnesses that drive real PySide6
widgets offscreen with no Houdini (`QT_HARNESSES` in `.ci/preflight.py`, 20
of 63), four at a time, and prints ok or FAILED per script with the tail of a
failure's output. Each runs as
`uv run --no-project --python 3.12 --with pyside6 --with usd-core python scripts/<name>`
with `QT_QPA_PLATFORM=offscreen`, this checkout's `python/` and
TumbleTrove's on `PYTHONPATH`, and `TH_PROJECT_PATH` at a fresh copy of
`scripts/project_template`, in its own temp dir (screenshots land there).
TumbleTrove is `TUMBLETROVE_PYTHON`, else the newest
`~/.hpm/packages/tumbletrove@X.Y.Z/python` (or fetched one). Without uv,
PySide6 or TumbleTrove the stage prints SKIPPED and why, and passes. It
takes about 25 seconds, most of it `verify_farm_manager`. A harness only
counts if it exits non-zero on a failure: `verify_cache_reference_fixes`
printed "ALL CHECKS PASSED" with every check skipped (no `hou`) and exited
0 whatever happened, so it now exits 1 on a failure (the `hython` stage
runs it).

`hython` runs the harnesses that import `hou` and need no arguments
(`HYTHON_HARNESSES`, 32 of 63) under the newest installed Houdini's hython
(22.x, or `$HPM_HOUDINI_MAJORS`; found as `delegate` finds husk), three at a
time since each takes a licence, and prints ok, FAILED (with the tail of
its output) or SKIPPED per script. It first compiles every expanded HDA
under `otls/` into a temp folder with `hotl` (the checkout is left alone;
a stale `otls/*.hda` there no longer matters) and runs one licence probe:
no Houdini, or no licence, prints SKIPPED and passes, and a harness that
fails on a licence message is SKIPPED, not FAILED. Each harness runs in its
own temp dir with a clean environment:

- its own `HOUDINI_USER_PREF_DIR` (named with `__HVER__`, or Houdini
  ignores it): the user's prefs dir carries packages, a stale tumblepipe
  that sets `TH_PROJECT_PATH=P:/growth` and a tumblerig-repro that loads
  TumbleRig twice (its CUDA SOPs then crash hython), which an empty
  `HOUDINI_PACKAGE_DIR` does not keep out;
- `HOUDINI_USE_HFS_OCL=0`: with a fresh prefs dir Houdini's bundled CPU
  OpenCL fails every Copernicus cook with "invalid string position" (the
  user's `houdini.env` sets it, which is why their prefs worked);
- `HOUDINI_PACKAGE_DIR` holding only the newest TumbleRig hpm installed
  (`~/.hpm/packages/tumblerig@X.Y.Z`), whose example rigs the animate and
  playblast harnesses stand published rigs in with; never
  `HOUDINI_PACKAGE_SKIP=1`, which also drops SideFX's APEX nodes;
- the compiled HDAs first on `HOUDINI_OTLSCAN_PATH`, this checkout on
  `HOUDINI_PATH` (its startup and OnCreated scripts run, as in a session),
  its `python/` and TumbleTrove's on `PYTHONPATH`, nothing inherited from a
  Houdini or pipeline session (`HOUDINI_*`, `TH_*`, `PXR_*`);
- `TH_PROJECT_PATH` at a fresh copy of `scripts/project_template`.

The three harnesses that resolve `entity:` URIs through USD
(`RESOLVER_HARNESSES`) also get the checkout's built tumbleResolver
(`resolver/houdini<major>/`, `.ci/build_resolver.py`), and are SKIPPED
without one. `PREFLIGHT_HYTHON="verify_a.py verify_b.py"` runs only those.
The stage takes about 75 seconds (the HDA compile 3 s, the slowest harness
`verify_comp` 30 s).

`check_binary_hdas.py` asks `git ls-files`, not the filesystem: `hpm build`
collapses every expanded HDA into a gitignored binary `otls/*.hda` beside
it, so a filesystem scan reported 64 violations on a clean checkout. The
question it means to ask is whether a binary HDA was *committed*.

## Qt widget harnesses

UI behaviour that pins a fixed bug lives in standalone harnesses under
`scripts/` that drive the real widgets — no Houdini required, since the
widget stacks involved are pure PySide6:

- `verify_database_editor_ux.py` — the config editor's commit model
  (click-away/Enter/Tab commit, key renames, reverts).
- `verify_process_dialog_ux.py` — the process dialog's execution UX
  (running-child status label, progress breadcrumbs, the
  cancel-leaves-steps-unrun warning, exported-version reporting, the
  skip-vs-fail split described below, and the footer states: a styled
  Execute, a green Done after a clean run, Close plus retry otherwise),
  and the up-front validation dialog (#56): its shot / department / issue
  tree, per-shot ticks, and a run that skips an unticked shot, starts
  nothing on Cancel Export and shows no dialog when clean. Offscreen, with
  Houdini's python313 (`QT_QPA_PLATFORM=offscreen`, Houdini's `bin` added
  as a DLL directory); `PREFLIGHT_SCREENSHOT=<png>` saves the dialog.
- `verify_submit_playblast_section.py` — the Farm Submit dialog's
  Playblast settings, offscreen: Materials is a `⟨per entity⟩` / Scene
  materials / Clay menu (not a tri-state box that reads as checked) that
  seeds, pins and unpins, and the rows the chosen Renderer ignores are
  greyed out. Needs tumbletrove's Python (`TUMBLETROVE_PYTHON`, else the
  newest `.tools/hpm-fetch` one):
  `uv run --python 3.12 --with pyside6 python scripts/verify_submit_playblast_section.py`.

- `verify_archive_app.py` — the archive window end to end, offscreen: read
  a small project, refuse a bad part size, write a split archive, check it,
  restore it under a new name. Run it with Houdini's bundled Python (it has
  PySide6, takes no licence):
  `PYTHONPATH="$HFS/python313/lib/site-packages-forced" "$HFS/python313/python.exe" scripts/verify_archive_app.py`.

All of these, and the other Qt harnesses that need no Houdini, run
offscreen in one go with `uv run --no-project python .ci/preflight.py --only qt`
(*Preflight* above). To run one alone the same way (on Windows separate the
`PYTHONPATH` entries with `;`; harnesses that read a project also want
`TH_PROJECT_PATH` at a copy of `scripts/project_template`):

```bash
PYTHONUTF8=1 QT_QPA_PLATFORM=offscreen PYTHONPATH="python:$TUMBLETROVE_PYTHON" \
    uv run --no-project --python 3.12 --with pyside6 --with usd-core python scripts/verify_process_dialog_ux.py
```

## Skipping a task instead of failing it

A task callback that finds it has **nothing to do** raises
`tumblepipe.util.errors.TaskSkipped` instead of returning or blowing up.
`ProcessExecutor` marks that task SKIPPED with the exception message as its
reason and carries on — crucially, a skipped *child* does not abort its
siblings, whereas any other exception fails the whole group. The canonical
case is an export node left disconnected in the network: its stage is `None`,
so there is nothing to publish, and the other render channels in the same
department must still export.

Two rules:

- Raise it only for a genuine no-op. Anything that would publish a wrong or
  empty layer must still fail loudly — a skip is quieter than a failure, so
  reaching for it to quieten a real problem buries it.
- Every skip carries a reason, and reasons are surfaced: after an otherwise
  clean run the dialog warns with the list of skipped steps. Without that,
  "All tasks completed" over a silently missing channel reads as success.
  (Tasks the artist unchecked are SKIPPED too, but carry no reason and stay
  out of the warning.)

`TaskSkipped` lives in `util.errors`, not in the `ui` package, so lops and
farm-side code can raise it without importing Qt. Note that the executor
tests for it *before* resolving `ValidationCancelled`, because that import
pulls in the validators and `pxr`.

The farm path deliberately does **not** skip: a worker hitting the same
disconnected node still fails its job, since nobody is watching a farm log to
notice a skip. It does report it legibly though — `farm/workers/
export_houdini.py` returns a named error rather than dying on
`None.GetPseudoRoot()`.

Note that `node.stage()` returning `None` is a recurring shape, and several
other call sites still dereference it unguarded (`lookdev_studio`,
`render_stage`, the two `playblast` nodes). They are not part of this fix;
each needs its own decision about whether a missing stage is a skip or an
error.

## Reporting a failed action to the artist

The sibling rule to *Skipping a task instead of failing it*, one layer out: an
action the artist **explicitly asked for** must never fail invisibly. An
action handler runs behind a toolbar button or a menu entry with no console in
sight, so `except Exception: log.exception(...)` and nothing more reads to the
artist as *"the button does nothing"* — which is how a dead export window got
reported rather than a stack trace.

Route those failures through `report_failure(action, exc)` in
`python/tumblepipe/pipe/houdini/ui/report.py` — the one path every node,
dialog and browser action uses. It logs the traceback, then
shows an error dialog naming the exception and the log file it landed in:

```python
try:
    ...
except Exception as exc:
    report_failure(f"Opening the workfile for {asset_id}/{dept}", exc)
    return
```

It logs for you — don't `log.exception` first as well — and it never raises: a
reporter that throws would replace the failure it was called to explain.

**It marshals to Houdini's main thread itself** (via `run_on_main_thread`).
Quick actions reach it from the browser's GUI thread as readily as from the
main thread, and `hou.ui` off the main thread is the same unsupported call
that `SceneManager`'s scene ops marshal to avoid. Callers do not have to know
which thread they are on.

Three rules:

- **Background upkeep raises; it is never swallowed** — detail/card repaints,
  cache invalidation, refresh hooks are not reported with a dialog (one the
  artist cannot act on is noise, and one raised from a repaint can fire every
  frame), but they do not catch either: a failure raises into the action that
  triggered it or into the browser's own boundary around the catalog hook.
  A `log.warning`/`log.debug` in an `except` is still a swallow. Only a
  genuinely expected condition is caught, by its specific type, with a
  one-line comment saying why it is expected (a file deleted between the glob
  that found it and its `stat`). A callback Qt calls — a menu entry, a button
  — is wrapped with `reporting(action, fn)`, which makes it its own boundary.
- **A status line is not a report.** `hou.ui.setStatusMessage` scrolls past,
  so for the *whole outcome* of a click — "Save: no pipeline context",
  "Publish: nothing to publish" — use `displayMessage`. A Save that saved
  nothing must not look like a Save that worked. Status messages stay for
  *success* notices.
- **A partial result is not a success.** The same trap as an unsurfaced skip:
  `refresh_scene_imports` deliberately survives a bad node, so `update_scene_imports`
  returns `(executed, failed)` and warns on a non-zero failure count rather
  than announcing "Imports updated" over a scene where every node blew up.
  Likewise a master scene naming the departments that would not merge, and a
  created workfile saying so when its frame range never landed.

A th:: node's parm **button** that fails because a parm does not resolve
(Entity on `from_context` in a Multi workfile, a camera not on the stage)
raises `NodeError` (`nodes.py`, built with `self._error(...)`), whose message
already says what to set. Wrap that callback in
`with ns.node_errors_as_message('<Node>'):` so the message shows as a plain
warning dialog. Left alone, Houdini puts it up as a Python traceback, which
artists report as "the node is broken" (Magnus on `th::playblast`). Any
other exception keeps its traceback, and without a UI the `NodeError` raises
too. The playblast LOP and SOP use it so far.

The one deliberate opt-out is the crash-time emergency save
(`_save_scene(..., report_failures=False)`): its whole premise is that
Houdini's event loop is wedged behind the crash-report dialog, so putting a
modal in front of it is exactly the wrong move. It logs and takes the status
bar instead.

### Where the logs are

`tumblepipe.util.logging` installs a rotating file handler on `import
tumblepipe`, so the traceback behind any of these dialogs is on disk:

```
$TH_PROJECT_PATH/export/other/logs/$TH_USER.log   # project-wide
<workspace>/_logs/$TH_USER.log                    # per-workspace
```

Warnings and errors are also echoed to the process console, which the
Desktop launcher captures into its per-project Houdini log
(`~/.tumbletrove/logs/houdini/houdini-<project id>.log`) — the file an
artist can find without the share. INFO stays file-only; `TH_DEV=1` or
`setup_logging(console=True)` echoes everything.

`get_log_paths()` returns the live handler paths — that is what the dialog
prints, rather than a recomputed guess. Two traps when triaging from them:

- The project handler is only attached when `TH_PROJECT_PATH` **exists**. A
  project that trips the required-placeholder contract gets no handler at
  all, so an empty log is not evidence that nothing failed.
- The filename is `$TH_USER` (never the OS username — these land in shared
  project storage). Identity-less writers share one `pipeline.log`.

## Qt and threads

A Qt object built, shown or destroyed off the GUI thread corrupts memory
rather than raising. The heap corruption ends in a fail-fast trap that
bypasses Houdini's crash handler, so the artist sees Houdini vanish with no
dialog, no recovery prompt and no `crash.*.txt`. That is the signature to
recognise: an ordinary crash produces all three.

`hou` is different. HOM calls take a lock that parks Houdini's main thread,
so they are serialised rather than racing. The lock gives mutual exclusion,
not thread affinity, and no lock makes a `QWidget` legal off its thread:

| From a worker thread | Safe? | Why |
|---|---|---|
| `hou.node()`, `hou.hipFile.path()`, `hou.ui.paneTabs()` | yes | the HOM lock serialises it |
| `hou.ui.addEventLoopCallback` | yes | the HOM lock guards the callback list |
| `hou.ui.displayMessage(...)` | **no** | builds a Qt dialog and a nested event loop |
| constructing or `.show()`ing any widget | **no** | thread affinity |
| a widget's destructor (including via the garbage collector) | **no** | thread affinity |

The rules that follow:

- **Workers do pure I/O**: bytes in, bytes or `QImage` out. Anything that
  touches Qt goes through `run_on_main_thread` (or `report_failure`, which
  marshals itself). A handler reachable from both the card menu (GUI thread)
  and the catalog's `execute_action` (a worker) checks `is_main_thread()`
  and re-dispatches itself, rather than trusting each call site.
- **Parent every Qt object.** A parentless object is Python-owned; trapped in
  a reference cycle, it is freed by the cyclic collector on whichever thread
  crosses the allocation threshold — and workers allocate constantly.
  A C++-parented object is C++-owned and out of the collector's reach.
- **Close a dialog before tearing down its owner.** Loading a scene deletes
  the node a dialog was opened from; a modeless dialog outliving its parent
  is a teardown crash.
- **Prefer Qt's own delivery** (queued connections, `QTimer.singleShot` on the
  GUI thread) to hand-rolled liveness guards: `~QObject` removes the posted
  events for the object, which is the invariant the guards try to re-create.
  `QTimer` can't be started from a non-Qt thread (the Python Shell is one).
- **Never `except Exception: pass` in a `paintEvent`**: it hides the
  `RuntimeError` a dangling wrapper raises, which is the one readable symptom
  this class of bug gives.

For a crash with no log, enable WER LocalDumps for `houdini.exe` (HKLM
`...\Windows Error Reporting\LocalDumps\houdini.exe`, `DumpType` 2) and
`faulthandler` to a file from a startup script; `HOUDINI_UI_COREDUMP=1` stops
the recovery save so the crash reaches WER unmediated. Don't also set
`HOUDINI_COREDUMP=1`, which enables JIT debugging and suppresses LocalDumps.

## Resolver harness

`scripts/verify_resolver_refresh.py` pins the resolver-refresh contract
(`tumblepipe.resolver.refresh_context()` must float already-composed
stages to newly published versions — the "restart Houdini to see a new
publish" bug). It needs the tumbleResolver plugin, so run it under a
project hython (e.g. TumbleTrove Desktop's run_hython, with dev
overrides for a local build). It sandboxes `TH_EXPORT_PATH` to a
tempdir, so it never touches live project data.

`scripts/verify_import_shot_asset_exclude.py` pins import_shot's
*Exclude Asset Departments*: excluding a department must mute exactly
the asset layers of that department the shot composes, no shot layers, and
clearing it must restore the stage. It reads **live project data** (read
only) and needs a licensed hython plus the tumbleResolver plugin
(`PXR_PLUGINPATH_NAME` / `PATH` as in its docstring). Choose the target
with `VERIFY_PROJECT`, `VERIFY_SHOT`, `VERIFY_VERSION` and
`VERIFY_DEPARTMENT`. Also run it on a project whose root layers sublayer a
scene (RND, Snail): that is the only path to scene assets, and paleindia
has none.

## VRAM Inspector harnesses

`scripts/verify_vram_scan.py` scans a hand-built USD stage with one of
everything the [VRAM Inspector](vram-inspector.md) must get right (indexed
and unread primvars, invisible and guide prims, native and point instances,
a texture only a stray shader names, a Karma `kma:surface` network) and
checks every itemised row. `scripts/verify_vram_collect.py` checks the `hou`
collectors: a cooked COP layer is itemised and an uncooked one is skipped
*without being cooked*, and a prim made inside a `sopcreate` is blamed on the
`sopcreate`. Both run under plain `hython` and touch no project data. The
pure modules (`clients`, `hook`, `sizes`, `report`, `process`) are covered by
`tests/test_vram_*.py` instead. `test_vram_no_gpumem` fails if anything in
`tumblepipe.vram` runs an hscript command other than the viewport hook's own
`ttvramhook_*`: Houdini's `gpumem` overcounts multi-part prims and segfaults
right after a prim is hidden.

The inspector's measurements come from the viewport hook, a C++ DSO in
`src/vram/hook/` (its README has the design and the spike's findings).
`.ci/build_vramhook.py` builds it into `dso/<major>.<minor>/` as the
`build-vramhook` prepack script, so every dev launch and `hpm build` compiles
it; `.ci/build_platform.py` refuses a Houdini 22 archive without it. The hook
can only be exercised in a GUI session (it measures during viewport
redraws): launch a dev session and read `ttvramhook_snapshot` over the
bridge.

The size constants in `vram/sizes.py` were measured in a live viewport;
`src/vram/calibration/README.md` has the protocol and the scripts to measure
them again.

## Cinedex, the editor

`src/cinedex/` is a stand-alone Rust app (no Houdini): Cinedex, TumblePipe's
editor (called `th_player` up to TumblePipe 1.103).
It edits *edits*, versioned and published pipeline entities
(`designs/edit-entity.md`), and plays them frame-accurately: playblasts and
other movies through FFmpeg, and EXR sequences shown through OCIO. Artists
start it from a shot card's **Play in Cinedex** entry ([Playing shots](asset-browser/play-shots.md)):
`tumblepipe.pipe.review_playlist.play_shots` starts
`bin/<platform>/cinedex` (or a dev build named by `$TH_CINEDEX`, else by
the former `$TH_PLAYER`) with
`--project <root> --ocio <config> --shots <uri>...`, and the editor finds
each shot's takes itself (`th_pipe::discovery`). It is its own cargo
workspace:

- `ffmpeg-sys`: FFmpeg bindings. They are generated once per platform and
  committed (`src/bindings/<platform>.rs`, one per platform), so builds
  need no libclang.
- `media`: decoding and the decoded-frame buffer. It covers the movie frame
  index with seek-and-decode, and EXR sequences through the `exr` crate:
  layers, channels, multipart files, data windows and gaps. An EXR frame is
  read whole, in 1 MB pieces, before it is decoded: the `exr` crate's own
  reads are 8 KB each, a round trip apiece on the file server (a 40 MB
  frame took 28 s from the NAS that way, 5 s read whole).
  `MovieDecoder::decode` and `ExrSequence::decode_while` stop once the frame
  is no longer wanted. `Media` is an opened take: a movie's frame index, or
  an EXR sequence with its first header read once and its colour space,
  which it must name (`oiio:ColorSpace`). A movie must have a frame rate and
  start on a keyframe. A movie frame stays Y'CbCr 4:2:0, except one with
  alpha or in RGB (`is_rgba_format`), which decodes to `Pixels::Rgba8`:
  display code values, straight alpha (planar Y'CbCrA such as ProRes 4444 is
  converted by its own matrix, not swscale's BT.601). `Media::Stack` is a
  shot's render layers opened together (all movies, or all EXRs of one
  colour space), and `composite` lays them "over" each other: movie layers
  straight-alpha over opaque black in display space, EXR layers
  premultiplied in scene linear (`ExrSequence::decode_with_alpha_while`
  reads a layer's own `A`). `composite::dissolve` blends frames of any
  size and kind through an edit's fades, in display code values. `sound`
  decodes a file's audio whole to stereo f32 (WAV read in Rust, the vendored
  FFmpeg having no WAV demuxer; a movie's track through FFmpeg, formats
  converted in Rust, no swresample) and `mix`es voices with in points, gain
  and fades, resampling linearly. It links no GUI, so it is tested
  headless.
- `playlist` (`th_playlist`): a shot's takes and their media, as
  discovery finds them and a published edit freezes them, as JSON through
  serde. It is strict: an unknown or missing field is an error. A take
  names its department, version and media: a movie's path, or an EXR
  sequence's layer and frames (directory, prefix, suffix, padding, first,
  last, step). A shot's takes come ordered by kind, department (pipeline
  order), render layer and version. A department (and a render's layer) is
  a *stream*. A `stack` take's media is
  `{"kind": "stack", "layers": [{"name", "media"}, ...]}`, bottom first,
  all movies or all EXRs of one frame range, never nested
  (`Media::problem`); its stream is `(kind, department, Some("layers"))`,
  right after the department's plain takes of that kind.
- `cinedex`: the editor's window, built on the in-house Primer UI crate.
  `main.rs` reads the arguments and opens the OCIO config; when it fails,
  or the edit does not open, the window says why. `display.rs` is the
  display and view the picture shows through, and opens a take's media off
  the UI thread. `engine.rs` decodes *sources* (a
  take's media each) placed as *clips* on a `Timeline` of tracks (position,
  in point, fades); frames are keyed by source and media index, so clips of
  one take share them and moving clips decodes nothing again. Where clips
  overlap, the top-most with media shows and the ones below show through its
  fades (`Timeline::layers`); every layer of the window is decoded and the
  playhead's are never evicted. `Engine::replace_source` swaps one
  source's content in place (a take opened, another view, other hidden
  layers), its frames going and every other source's staying. Its workers take frame jobs around the
  playhead and proxy (thumbnail) jobs, which go before the window but
  never before the playhead's own frame; seven workers,
  up to six EXR reads at once (while the playhead's EXR frame is read, no
  other starts), movie decoders kept in a small pool per take. A decode no
  longer wanted (the playhead moved off it, or its take changed) stops; a
  decode that panics is the frame's error, not a hung frame. A stack clip's
  job decodes each layer not hidden (`Content::hidden`, a bit per layer)
  and composites them on the worker; a stack of EXRs counts as EXR reads,
  of movies as one movie decode. `audio.rs` plays sound through `cpal` (the `sound`
  feature, default on; on Linux it builds against `libasound2-dev` and
  links `libasound.so.2`): a movie take's sound under its clip, and while there
  is any, playback keeps to the samples played, else to the wall clock.
  `colour.rs` bakes the chosen display/view into a 3D LUT through `ocio-rs` (which
  builds OpenColorIO from source); every render frame is drawn through it.
  `take_menu.rs` builds a shot clip's Source, Department and Version
  submenus from the take shown, its Layer submenu (#58: sets only the
  rule's `layer`, Best available by default) and the clip name's layer
  note (`SH030 · chars (+2)`). `layer_view.rs` is the viewer's AOV
  dropdown and stack layer toggles: they set the entry's `ExrView` and
  `Content::hidden`, view only (never in the edit), kept by name when the
  take resolves again. `render.rs` is Render Edit's renderer
  (`--render <edit>@vNNNN --out <dir>`, no window): each frame's layers
  (`th_edit::picture`) decoded and blended through `composite::dissolve`
  on the CPU, a render through the config's default display and view, as
  `edit.NNNN.png`, the mix as `edit.wav` and `render.json`; the farm worker
  encodes the movie with Houdini's hffmpeg. `export.rs` is File > Export
  Movie… (#61), on a thread of its own: the same sources, pictures
  (`render::picture`, through the display and view the viewer shows) and
  mix, each frame streamed as raw RGB into hffmpeg's stdin
  (`th_pipe::movie::edit_export_args`, the farm's encode from `-frames:v`
  on), written under `movie::partial_path` and renamed over the movie when
  done; a cancel or failure kills hffmpeg and deletes the partial file.
  `movie::find_hffmpeg` picks `$TH_HFFMPEG`, `$HFS/bin`, else the newest
  SideFX install. `editor/export_dialog.rs` is its dialog (path, Browse…,
  progress bar, Cancel, Play, Show in Folder); `save_dialog.rs` the system
  Save dialog (Windows' `GetSaveFileNameW` called directly, `osascript` on
  macOS, zenity or kdialog on Linux: no crate, so no GTK link).
  `burnin.rs` writes the burn-ins over the
  picture, placed by the same fit `video.wgsl` gives the frame. The
  controls are built `.focusable(false)`, so the player's keys keep working
  after a click on one.

Primer is a git dependency that cargo fetches from 3db Gitea
over plain HTTP (`http://10.100.36.15:3000/...`). It is readable without
credentials, so the studio network is all a build needs; CI needs no key.

**FFmpeg.** The player links its own FFmpeg, not Houdini's.
`.ci/build_ffmpeg.py` builds a decode-only, LGPL-only release into
`build-ffmpeg-<platform>/install/`. On Windows it needs MSYS2 and Visual
Studio; the Windows build worker has MSYS2 in `C:\msys64`. The player's
build looks for FFmpeg there, or in `TH_FFMPEG_DIR`. Because FFmpeg is LGPL, it ships as
separate shared libraries beside `cinedex`, never linked statically.

**Packaging.** `.ci/build_player.py --platform <platform>` runs as the
`build-player` prepack script. It builds FFmpeg when there is none, then
`cinedex` in release, and copies the exe and FFmpeg's shared libraries into
`bin/<platform>/`.

- The Linux and macOS binaries carry an rpath of `$ORIGIN` or
  `@executable_path`, so they find the libraries beside them. macOS FFmpeg is
  built with `@rpath` install names for this.
- Each platform builds from its committed bindings. A new platform has none
  and fails to compile until they are regenerated (below) and committed.
- Primer is used without its default `os` feature (native menus and tray
  icons) except on macOS. On Linux that feature links GTK and libxdo, which
  every artist machine would then need; on Windows the native menu bar
  ignores the editor's theme, so Primer draws the menu in the window.
- All three platforms have been built from a clean clone on the build
  workers, and the media tests pass on each.

**Building and testing on Windows.** Run cargo from a Visual Studio developer
environment (`vcvars64.bat`), because Git Bash's `link` is coreutils, not the
MSVC linker.

With both VS 2022 and VS 18 installed, use VS 2022's `vcvars64.bat`. The
OpenColorIO build (cmake) takes its own Visual Studio generator. VS 18's
vcvars mixed with that generator's compiler fails with `STL1001`.
`build_player.py` picks VS 2022 and names its generator for you.

From `src/cinedex/`:

```bat
set PATH=%CD%\..\..\build-ffmpeg-windows-x86_64\install\bin;%PATH%
cargo test --workspace
cargo run --release -p cinedex -- --project P:/show --ocio %OCIO%
cargo run --release -p cinedex -- --project P:/show --ocio %OCIO% --shots entity:/shots/A/010 entity:/shots/A/020
```

The tests:

- `media/tests/frame_accuracy.rs` decodes fixture movies whose frames encode
  their own number, in every order, and checks each frame, and that a decode
  no longer wanted stops. Set `TH_PLAYER_TEST_MOVIE` to a real playblast to
  also check that random access matches a straight decode byte for byte.
- `media/tests/exr_props.rs` writes random EXRs with the `exr` crate and reads
  them back. They cover layers, multipart files, data windows, f16 and f32,
  the default view, channel picking, a missing frame (an error, never a
  neighbour shown in its place), and a decode that stops once it is no
  longer wanted. `media/tests/stack_media.rs` decodes ProRes 4444 and QuickTime Animation (a layered playblast) movies
  with alpha to straight RGBA, reads an EXR layer's alpha only when asked,
  and refuses stacks that cannot composite; `media/src/composite.rs` checks
  that premultiplied over is associative, that a transparent top vanishes
  and an opaque one hides what is below, and that movie layers match a float
  reference within one code value. `media/tests/media_props.rs` checks that an opened take is
  shared across threads, describes itself while a decode runs, and that an
  EXR without a colour space does not open.
- `media/tests/buffer_props.rs` covers the buffer. The tests in
  `cinedex/src/engine.rs` cover decode scheduling: a state machine of
  playhead moves, view and hidden-layer changes, takes replaced and jobs finishing in any
  order shows that the job limits hold, that no stale frame is ever stored,
  and that a decode stopped for it is no failure; a decode that panics is
  the frame's error; and real workers fill the window around the playhead
  and stop when the engine drops; a stack's frame is exactly its shown
  layers that have the frame, composited in order, hidden ones never
  decoded. `colour.rs` covers the LUT layout and shaper.
  `media/src/thumbnail.rs` covers scaling and the colour of
  thumbnails. `media/src/exr_sequence.rs` reads files on and around the
  1 MB piece boundaries whole, and stops a read where asked. `burnin.rs`
  checks that its picture is placed as the video fits the frame: inside its
  bounds, its shape kept, centred. `take_menu.rs` checks that each submenu
  lists its choices once (every kind, every stream of the shown kind going
  to its newest version, the shown stream's versions newest first) and
  marks only what is shown, that the Layer submenu lists each layer of the
  shown kind once (a chosen layer not there as not ready) and its rows
  change only the layer, and the clip name's layer note; `layer_view.rs`
  that an AOV row shows its layer, a toggle changes one layer only and a
  resolve keeps both by name. `editing.rs` checks timecodes, edit points,
  inserting and overlaying a bin item (a movie with its sound), moving a
  selection and back, that a published document refuses changes and that
  undoing to what was opened is clean, and (a proptest) that every clip
  plays its own source's frames; `render.rs` that a frame written reads
  back as RGB and the mix's WAV reads back through `th_media`; `export.rs`
  that an export is a movie of the edit's every frame at its rate and size
  (hffprobe), that a cancel leaves nothing and a file already there as it
  was, and that a missing encoder or an empty edit is an error (the movie
  ones only where an hffmpeg is installed), plus the offered name and
  folder; `th_pipe::movie` the encode arguments, the hffmpeg search order
  and movie paths; `save_dialog.rs` the macOS script's quoting, the Linux
  dialogs' arguments and reading back the path chosen;
  `editor/export_dialog.rs` when Export may start and that a running
  export holds the window; `tracks.rs` checks row order, which
  part of a clip a point grabs, the selection's runs and the ruler's ticks.
- `cinedex`'s editor: `main.rs` opens it (`cinedex [--project <root>]
  [--ocio <config>] [--edit entity:/edits/<name>[@vNNNN] [--view <shot>
  [--at <frame>]] | --shots <uri>...]`; `--project` defaults to
  `$TH_PROJECT_PATH`, `--ocio` to `$OCIO`; neither `--edit` nor `--shots`,
  an untitled edit of every shot, `editing::project_fill`, as is an edit
  never saved; `--view` opens the edit as Live view shows a shot).
  Preview frames (#75): `th_pipe::previews` reads the local farm's
  batches as `tumblepipe.farm.local.previews` writes them (each batch's
  `state.json` `previews` entries, the farm folder as `queue.root` finds
  it), held to the Python by `test_previews_parity` through `pipe_lines`
  (change the reading rules on both sides, in its module docs);
  `cinedex/src/previews.rs` is what the editor makes of them;
  `cinedex --frames <path>` opens
  `frames_view.rs`, a window of stills (a batch's ready previews, a
  folder's frames, or one frame) that reuses `video.rs` and `colour.rs`
  and opens no project. The editor reads the batches every 5 s off the UI
  thread (`Message::PollPreviews`) and `tracks.rs` draws the **Preview
  ready** badge on a shot clip while one of its previews is of a
  department and version it has no take of. The Farm Manager's **Open in
  player** starts it through `review_playlist.open_preview_frames`.
  `editor.rs` is the
  window: the tracks, a Shots bin and the media library, Save (the next
  working version, with a note), Save As, Publish, Import and autosave,
  with an autosave newer than the working version offered back on open; a
  published version opens read-only. `editing.rs` is its document (every
  change a `th_edit` op through its history, one user action one step) and
  the pure helpers; `tracks.rs` draws the rows and turns drags into ops
  (move, trim, Alt for slip and roll, the top band for fades);
  `editor/clip_fields.rs` is the strip of the selected clip's fades and
  opacity as typed numbers (#60), its proptests that a typed fade or
  opacity makes exactly the op that sets it and shows back as typed, and
  that anything else is refused. `look.rs`
  holds every colour the window draws with (untinted greys, amber only for
  the present moment, a muted hue per department for clips); draw with its
  tokens, not colour literals, and its tests keep text readable on them;
  its `tick_box` is the window's checkbox (Primer's draws its tick in
  the theme's primary grey, too faint to tell from unticked). `reveal.rs`
  finds a take's folder and opens the file manager on it (Explorer takes
  only backslashes; given `C:/a/b` it opens Documents). A dialog field
  that offers a value until the user types one (New shot's name, Import's
  item) is an `editing::Offered`: it remembers that the user typed, rather
  than comparing the text with the offer.
  `editor/menu_bar.rs` declares the menu bar (`Application::menu`)
  and the window's key map (`on_key`), both read from one small `State` so
  its property tests hold them to each other: a shown shortcut is the key
  that sends the item's message, nothing acts or is enabled under a dialog,
  what writes is greyed on a read-only edit, and with Command or Alt held a
  letter acts only through a chord the menu shows. Add a key there, with
  its menu item, not in `editor.rs`; Primer's
  `os` feature is on for macOS only (player/Cargo.toml): the native Windows
  bar is a white Win32 menu that ignores the theme, and on Linux it would
  link GTK, so on both Primer draws the declaration inside the window
  instead, in the editor's theme (its `&` access letters, its shortcuts as
  hints); the keys
  are `on_key`'s on every platform, so the toolbar carries no actions. On macOS
  only Command chords are given to the menu as shortcuts, since the menu
  takes a key before the window sees it. Each distinct
  source of the edit is resolved off the UI thread to the take its rule
  picks today and played by the engine as one source.
  Live playblasts ([Live playblasts](asset-browser/play-shots.md#live-playblasts),
  `designs/shot-dashboard-live-playblast.md` §5): `live.rs` (`LiveWatch`)
  polls the live.json of the live render a shot plays (one local stat) and
  plays it over the clip's rule, never saved, published or exported, a frame not
  rendered yet showing the take under it (`th_media::Under`); **Keep as
  playblast** is `th_pipe::live::keep`, which runs the rendering Houdini's
  hffmpeg (the player's FFmpeg has no encoder). Live view: the editor
  serves `th_pipe::player_rpc` (one line of JSON-RPC on 127.0.0.1, port
  and token in `<live root>/players/<project key>/<pid>.json`), and
  `live_view.rs` turns a `view_shot` into the loop around the shot's
  first clip and raises the window without focus (Windows only). Houdini's
  side is `pipe/live_view.py`; `test_live_parity` drives it against the
  real endpoint through the `rpc_stand_in` example.
  New takes without looking (`journal.rs`, design §5.4, phase 6): the
  editor follows this machine's change journal (`th_pipe::events::
  JournalReader`, one local stat every 500 ms). A `playblast`, `render` or
  `live` line about a shot resolves that shot's entries again, playing on
  meanwhile (an entry being resolved is resolved again after); another
  process's `config` line reads the bins and board again, and of the
  `entity` purpose resolves every shot; a `workfile` line reads the board;
  a journal that may have lost lines does all of it. `journal::plan` is the
  pure part, property-tested. The writers are `pipe.review_events` in
  Python (the Houdini playblast nodes, Live, and the farm workers that
  write playblasts, review movies and render frames, which read the shot
  and department back out of the path) and `th_pipe::live::keep`.
  Playblasts and renders made on other machines (the farm) arrive through
  the project's shared journal, `<config>/journal/` (`util.shared_journal`
  writes one file per change, renamed into place; `th_pipe::shared_journal`
  reads it off the UI thread every 3 s; the same id on both journals is
  acted on once, #78). Anything else made elsewhere: **View > Refresh**.
  Agent commands (design §8, phase 7): `agent.rs` is the catalog (each
  command a JSON-RPC method with a JSON schema, listed by `tools/list` in
  MCP's shape; `edit`'s ops are `th_edit::ops::Op`'s serde form, tagged
  by `op`), `editor/agent.rs` answers them on the UI thread (the same
  `Document` and undo history as the user's; `list_takes` and `save`
  answer when their work is done, through `Message::Answer` and
  `agent_save`), and every edit, save, publish, resolved take and view is
  pushed to the endpoint's `Events`. `edit` with `dry_run` applies the ops
  to a copy and answers `th_edit::diff`; `publish` is a preview only.
  `mcp.rs` is `cinedex --mcp`: a stdio MCP server that finds the
  players through their endpoint files and relays each tool call
  (`player` picks one by pid, `list_players` lists them). Its proptests
  check that a call reaches a stand-in player exactly as made; `agent.rs`'s
  that the `edit` schema accepts every op serde writes, and rejects one
  with a field missing or added.
- `edit` (`th_edit`): the sequence editor's document, pure (no window, no
  decoding, no files). `document` is the edit as JSON (a working edit's
  take rules float; a frozen one, as a publish holds it, cannot hold a
  floating rule); `ops` are the editor's operations, each applied to a copy
  and refused, never clamped, when the result breaks the document's
  invariants or shows source frames its source has not (`Sources`); ripples
  move every track and the markers; `history` is undo/redo (a run of ops as
  one step, all or none); `diff` is what changed between two versions of
  an edit, clip by clip per track (property: it rebuilds the later one);
  `picture` is
  what is on screen and in the mix at a frame (top-most playable clip,
  blended through its opacity times its fades, a clip at no opacity
  transparent; a proptest, and `editing.rs`'s proptest that the engine's
  layers are its layers to the bit, so the editor and Render Edit blend
  alike); a video clip's `opacity` (#60) is written only when it is not 1
  and read as 1 when missing (`document`'s proptest: an edit at full
  opacity is byte for byte what it was; `serde_json`'s `float_roundtrip`
  so a number reads back as written), set by `SetOpacity` (0 to 1, video
  only, a proptest), and a join of clips at two opacities is refused;
  `takes` picks a take for a rule (a named
  layer no take fits falls back to the rule with its layer open, a
  proptest; `document`'s proptest round-trips a rule with and without a
  layer); `freeze`
  resolves rules (`Resolver`); `otio` writes OpenTimelineIO (plain
  dissolves as transitions; a fade with nothing below it, from or to
  black, as an `SMPTE_Dissolve` between the clip and a gap, a gap of no
  frames where the clip touches the track's start or the next clip, so
  every transition has an item each side; other fades and opacities as
  markers; a proptest checks exactly the fades over nothing become
  transitions at their clips' edges and every track stays well formed
  OTIO) and names each movie
  for the publish to link (`movie_links`, `relink`: Resolve takes
  same-named movies for one). Its properties: every movie gets one name no
  other has in any case, plain characters with its extension, the same
  each time; relinking changes nothing but movie references. The laws of
  `designs/edit-entity.md` §2.4 are its proptests. `th_pipe::edits`
  answers `Sources` and `Resolver` for a project.
- `pipe` (`th_pipe`): TumblePipe's pipeline in Rust, which the editor and
  tumblepipe-mcp share (the MCP takes it as a git dependency pinned by
  revision). It ports, with the Python as the specification: URIs
  (`util/uri.py`), storage (`storage.py`), the config database
  (`config/store.py`: schema defaults by position, deep merge root to leaf,
  verbatim own-property writes, the name and case rules), departments and
  channels, frame ranges, version folders, review media discovery
  (`review_playlist.py`, `paths/review.py`, `paths/render.py`, as
  `th_playlist::Shot`), and creating shots and retiring entities
  (`shot_batch.create`, `entity_files.delete_entity`,
  `groups.drop_member_everywhere`). Its JSON writes are Python's
  `json.dump(indent=4)` byte for byte (`ensure_ascii`, `\r\n` on Windows),
  and a rename over a file another process holds open retries for 5 s as
  `util.io.replace_file` does. `edit_files` is an edit's files (working
  versions with their sidecar, autosave, publishing with the movies
  linked into `media/` and `context.json` last: properties on real files
  check that the OTIO opens each clip's own movie and nothing else is in
  `media/`, and that a publish that cannot link a movie is not finished) and `media` the media library (`export:/media/<item>/vNNNN/`).
  `live` is the live playblast folder (live root per platform, the
  project's hashed folder, live.json and whether its husk still runs) and
  Keep as playblast, `player_rpc` the editor's endpoint for other
  programs (Live view's `view_shot`; `events`, a long poll on its
  `Events` log of the last 256; every other method handed to the editor
  as an agent command); Python parity in
  `tests/test_live_parity.py`. `events` writes the change journal as
  `util/events.py` does (every `Config` write, edit saves and publishes, a
  kept live render) and reads it (`JournalReader`, Python's reader poll for
  poll: `tests/test_journal_parity.py`).
  `db`, `uri` and `project` came from
  tumblepipe-mcp (mtime-guarded writes, the layout range). A `Project`
  holds a snapshot of the config: make a new one (`refresh`) to see later
  writes.
- `playlist/src/lib.rs` round-trips any take, refuses any unknown or
  missing field, numbers frame paths as Python does, and refuses stacks
  that cannot play (empty, nested, mixed, of different frames, or naming a
  layer twice).
- `tests/test_review_playlist.py` (minigun) checks take discovery against
  playblasts and renders written to the harness project (stack takes from
  layer movies and channel render layers among them), that a render take
  names its frames as renders write them, and that Play asks the editor for
  the shots in order. `test_pipe_parity` holds the Rust discovery to it.
- `tests/test_pipe_parity.py` (minigun) drives `th_pipe`'s `pipe_lines`
  example and the Python over the same projects: storage tables, random
  entity and schema trees (properties, schema, kind, listing order), the
  department pools, a shot's takes (playblasts of both namings, layer
  movies, render versions complete or not, channels), retiring a shot or
  its sequence (files, archive and Multis after), and creating a shot
  (refusals, and the database bytes written). `.ci/test.sh` runs the
  whole workspace through `.ci/build_player.py --test` (`cargo fmt
  --check`, `clippy -D warnings`, then `cargo test`), with the release
  build's FFmpeg, toolchain and OpenColorIO. Formatting follows
  `src/cinedex/rustfmt.toml`, so run `cargo fmt` before committing.

Commit any new seeds proptest writes under `proptest-regressions/`.

**Regenerating the bindings** (after an FFmpeg bump, for a new platform, or
when `th_media` starts using another FFmpeg item) needs libclang
(`LIBCLANG_PATH`). The bindings hold only what `th_media` uses: the
`FUNCTIONS`, `TYPES` and `CONSTANTS` lists in `ffmpeg-sys/build.rs`, plus
whatever those need. Add a new item to its list first.

```bat
cargo build -p th_ffmpeg_sys --features regenerate
```

## HDA spare-parm UI harness

`scripts/verify_import_shot_layer_stack.py` pins the import_shot Layer
Stack layout contract (the "parms changing places after import" bug):
calling `hou.Node.setParmTemplateGroup()` on an HDA *instance*
spare-ifies the definition's container folders under renamed names
(`selection` → `selection2`), so folder-name anchor lookups go stale
after the first rebuild. Code that inserts spare UI into an HDA
instance must anchor on definition *parm* names (never renamed) via
`ParmTemplateGroup.containingFolder()`, not on folder names. And it must
start from the *definition's* group, not the instance's: once
`setParmTemplateGroup()` has run on a node, the node keeps that interface
and an HDA update no longer reaches it (a parm the HDA gains is appended
at the bottom, a parm it hides stays shown). `layer_stack._base_group`
takes the definition's group and carries over only the artist's own spare
parms (`tests/test_layer_stack_spares.py`). Run it
under any project hython (e.g. TumbleTrove Desktop's run_hython with
dev overrides); it drives the UI rebuild with synthetic layers and
touches no project data.

`scripts/verify_import_assets_usd_variant.py` pins th::import_assets'
per-row **Variant** menu: the menu lists the row's composed variantSets,
a choice applies live (no re-import), lands in the layer below the HDA's
layerbreak (what the export keeps), reaches every instance copy and inline
mode, and an unknown variant falls back to the published selection. By
default it reads paleindia `Clash/goblin`; `TH_VERIFY_SYNTHETIC=1` patches
the resolver to serve a temp staged layer with a `model` variantSet
instead, so it runs under any project hython (name an asset the project
has with `TH_VERIFY_ENTITY` / `TH_VERIFY_PRIM`) and touches no project data.

## HDA dive-target harness

`scripts/verify_variant_dive_up.py` pins **U** from the
`th::create_asset_model` and `th::create_asset_lookdev` dive targets. Houdini
walks up past non-editable parents only (`nodegraphview.changeNetwork`), so
the only editable network in each node is its dive target, nothing is ever
created beside it, and the LOP side fetches the dive target's outputs
directly. The harness re-runs Houdini's walk and checks it lands on the
node's parent, then drives add / rename / remove through the variant sync
and cooks each variant to check it publishes its own output. Run it under
any project hython; it needs no project data. The static rule for every HDA
is `tests/test_hda_dive_targets.py`.

Changing an HDA's EditableNodes changes what an existing `.hip` keeps. A
node that stops being editable loads from the definition, and its saved
children are dropped with *Ignoring data for locked node* warnings. Children
of a node that is still editable are kept. Before shrinking the list, save a
scene with the old build (stash the change so the dev override compiles the
old HDA) and reopen it with the new one. Swapping definitions inside one
hython session with `hou.hda.uninstallFile` / `installFile` saves a broken
scene that reloads empty under *either* build, so it proves nothing.

## Department `from_context` in a Multi

A Multi's `context.json` names the group, so Entity `from_context` resolves
to nothing there, but Department `from_context` must still resolve: the
workfile's own department is unambiguous. `th::import_shot` read its
department through its shot-only entity lookup, got nothing, and imported
every department, its own included (fixed after 1.56.1).

`tests/test_multi_workfile_nodes.py` resolves Department `from_context` on
every node through a real Multi workfile on disk, and fails if any class
under `pipe/houdini/` defines `get_department_name` without being listed.
A new node with a Department parm goes in `_DEPARTMENT_FROM_CONTEXT`, or in
`_DEPARTMENT_EXEMPT` with the reason it has no `from_context`. Read the
department from `get_workfile_context`, never from a helper that filters
the entity.

## Farm Submit dialog harness

`scripts/verify_farm_grid.py` pins the Farm Submit dialog, its status scan and its
background runner against a real project, with no licence and no Houdini
session, and without submitting anything. Run it through the Desktop
launcher, which builds the environment a Houdini session would have for a
TumbleTrove Desktop project and uses that Houdini's bundled Python:

```bash
HOUDINI_PACKAGE_DIR=~/.tumbletrove/projects/<id>/.hpm/packages \
    python scripts/farm_launcher.py --run scripts/verify_farm_grid.py
```

**husk under `--run` cannot load Karma.** The launcher copies package
values of `HOUDINI_PATH` and `HOUDINI_DSO_PATH` as they are written, and
TumbleRig sets both without Houdini's defaults, which Houdini's own package
loader would keep. husk started from a `--run` script then fails with
`Unable to load render plugin: BRAY_HdKarmaXPU` (Storm still loads, because it
ships with USD). Farm jobs are not affected, since neither variable is in a
job's environment. Until the launcher keeps Houdini's defaults, a script that
runs husk (e.g. `scripts/debug_playblast.py --husk`) should
`os.environ.pop()` both before it starts.

`FARM_VERIFY_SCREENSHOT=<png>` also grabs the dialog to an image (offscreen
Qt has no fonts, so text renders as boxes; layout, colours and checkboxes are
true). It checks that the grid lists the context's terminal entities with one
publish column per publishable department in pool order; that an open for one
shot ticks only that shot's Render cell and the opened-from department pins
both preview cuts; that the status scan finishes with no cell pending; that
column headers, sequence rows, the filter and **Select stale** tick exactly
what they should; that a ticked row resolves to exactly its ticked publish
departments (`pub_departments`) over its own frame range; that a real staged
stage collapses in Houdini's bundled Python (the snapshot the runner takes
when a row publishes nothing); and that the runner starts, runs and reports
through its progress file — fed a Multi, which `submit_entity_batch` refuses
before it contacts Deadline; and that the mouse wheel over an unfocused
settings field scrolls the panel instead of editing (and pinning) the field.

The policy under the dialog is pure and has property tests:
`tests/test_farm_grid.py` (cell states, the tri-state checkboxes, stale
warnings, what a row submits), `tests/test_farm_submission.py` (explicit
publish departments, the collapse task's config, the worker-thread status
scan, the plan and progress files) and `tests/test_farm_launcher.py` (version
ranges, replaying the Houdini package files, picking Houdini's own Python).
`tests/test_farm_job_manifest.py` pins the job's `hpm.toml` (the project's
dependencies, runtime and registries, the task's package pinned; where the
project manifest is found) and the `.hpm/packages` dir the worker's hython
loads. The per-entity settings resolution keeps its own tests in
`tests/test_submit_settings.py`.

**Mutation-testing these, a trap.** Python reuses a module's `.pyc` when the
source's size and modification second are unchanged. A same-length mutation
(`NEVER` → `STALE`, `+` → `-`), or narrowing a test module's `spec` to a
property whose name is as long as the previous one, can therefore run the
*previous* bytecode and report a pass. Delete the module's `__pycache__/*.pyc`
after every write — the mutation, the narrowed spec, and the restore.

## Entity `from_context` audit

`scripts/verify_entity_from_context.py` pins the contract that every `th::`
HDA which addresses a pipeline entity leaves its `entity`/`shot`/`asset` parm
on the `from_context` sentinel, so the node resolves its entity from the
workfile it lives in every time it is evaluated. A concrete URI in that parm
pins the node to whichever entity it was *born* in: copy the scene to another
asset, rename the entity, or build a shot from a template, and it keeps
publishing to the old one.

It checks the four ways that contract has been broken:

1. **Parm defaults** — an entity-addressing parm whose default is a concrete
   URI, or the empty string. Empty is not neutral: an explicit value must
   name an entity, so `EntityNode.get_entity_uri()` raises `EntityNodeError`
   on an empty parm (it used to resolve it to the *first entity in the
   project*, silently addressing an arbitrary asset).
2. **`on_created` hooks** — a wrapper that writes a URI into the parm at node
   creation, defeating the sentinel it just defaulted to. Both shapes count:
   baking the workfile's own URI, and pinning the first entity in the project.
3. **Department templates** — the single-entity branch (`_create_entity`)
   stamping a specific entity URI. Only the multi-entity `_create_group`
   branch needs to: a group workfile holds several entities at once, so
   `from_context` cannot resolve to one of them.
4. **Group URIs** — a resolver that reads the workfile's `context.json` and
   uses its `entity_uri` as an entity without checking that it *is* one. A
   Multi's workfile records the Multi (`groups:/shots/<name>`), and the
   config layer raises *Not an entity URI* on it: that is how the LOP
   playblast's Department menu started throwing in a Multi's animation
   scene. This check walks the wrapper ASTs, follows one hop through a
   module's own reader helper (the playblast reached the pointer through
   `self._get_context()`), and accepts a `uri.purpose` comparison, an
   `is_group_uri()` call or an `entity:` prefix test as the guard. A reader
   that hands groups back on purpose is listed in `GROUP_AWARE`, and vouches
   for nobody: its callers must check for themselves.

It reads the expanded `otls/` DialogScripts and the Python wrappers as text,
so it needs no Houdini and no project:

```bash
cd tests
uv run python ../scripts/verify_entity_from_context.py
```

Prefer it over grepping — the baking shows up in several shapes that a
hand-written search misses.

## HDA callback audit

`scripts/verify_hda_callbacks.py` pins the contract that every HDA callback
reaches a function that exists. A pipeline HDA's callbacks call
`python/tumblepipe/…` directly, with `kwargs`:

```text
parmtag { "script_callback" "import tumblepipe.pipe.houdini.cache as th_cache; th_cache.select(kwargs)" }
```

A callback is a *string*, so nothing resolves it until an artist clicks and
gets an `AttributeError`. That is how `th::cache` shipped its Entity button
broken on both the SOP and the LOP (93e4dc1), back when a `PythonModule` of
one-line forwarders sat in between. No lint or build gate resolves callbacks,
so nothing else catches this — run this audit after editing one.

It checks:

1. **Dangling direct call** — a DialogScript callback or OnCreated script that
   imports a `tumblepipe` module and calls a name the module does not define.
2. **Dangling `hou.phm()` name** — the `PythonModule`s that remain (third-party
   HDAs, and those whose parm *expressions* saved scenes store verbatim) must
   define, or import from `tumblepipe`, every name `hou.phm()` asks for.
3. **Dangling forwarder** — a `PythonModule` call into a `tumblepipe` module
   whose function is gone (the `get_asset_uri` vs `get_entity_uri` near-miss).

A **menu** script constructs the wrapper itself and calls it, so it needs its
own check.

4. **Dangling menu call** — a menu block does

   ```python
   node = import_shot.ImportShot(hou.pwd())
   items = node.list_channel_names()
   ```

   and nothing resolves `list_channel_names` until an artist opens the menu
   and gets an empty list. Rename a wrapper method and every DialogScript
   saying the old name goes quietly stale — the variant → channel rename
   touched nine of them, plus two menu blocks buried in
   `Contents.dir/.OPdummydefs`, which grep reports as *binary* files and a
   plain search therefore misses.

   The class behind the wrapper is resolved through the *defining module's own
   imports*, never by bare name: `Cache` and `ImportAsset` each exist in both a
   LOP and a SOP module, and `EntityNode`'s base is written `ns.Node` while
   `Node` is defined twice in the package. When a class or a base cannot be
   resolved from the sources alone the lookup gives up rather than guess —
   a wrong guess would report a working menu as broken.

It reads the `otls/` sections and the wrappers as text, so it needs no Houdini
and no project:

```bash
cd tests
uv run python ../scripts/verify_hda_callbacks.py
```

Reading rather than importing is deliberate. Importing a `PythonModule`
outside a GUI Houdini fails for reasons unrelated to the callback —
`lop_th.image_plane_painter` imports `nodegraphutils`, which touches `hou.ui`
at import time — and that failure is indistinguishable from a missing name, so
an import-based check reports healthy HDAs as broken.

Pre-existing gaps live in `KNOWN_GAPS` with the reason they are not
regressions, so the audit stays green and any *new* break stands out.
`sop_th.mesh_blender.1.0` is the only entry: it has never had a `PythonModule`
(it shipped without one in 52df04b), so its Rename/Reset buttons have never
worked for anyone. Repairing it means reconstructing an attribute-paint module
from its internal network — a feature task, not a forwarder fix.

## Department pool audit

`scripts/verify_entity_departments.py` reports a project's department pool in
**pipeline order** (which is what the pool's key order *is* — see *Departments*
in [Departments](configuration.md#departments)), the entities scoped to a subset of it, assignments
naming a department the pool no longer has, and departments with work on disk
that their entity is not scoped to. It is read-only, so it is safe against a
live project; run it before and after migrating one to config v3.

```bash
cd tests
uv run python ../scripts/verify_entity_departments.py P:/paleindia
```

With no argument it audits `$TH_PROJECT_PATH`.

## Context chain audit

`scripts/verify_context_chain.py` checks — and with `--repair`, heals — a
department workspace's version bookkeeping. Three things must agree for a
workfile department: the `context.json` `version` pointer, the
`_context/vNNNN.json` lineage chain (each entry's `from_version` names a real
prior version), and the hip files on disk. Concurrent saves, a crash between
the three writes a save makes, or the old `from_version:"v0000"` re-anchor can
drift them apart — an "unhinged" shot.

The hip files are ground truth (their names encode the true version order), so
the tool diagnoses drift against them: a stale pointer, a `v0000` re-anchor
sitting above a real predecessor, a hip with no `_context` entry (or the
reverse), leftover reservation stubs, and an entry whose `extension` hint is
missing or names a channel the hip is not in (a `.hipnc` recorded as `hip`,
which older versions wrote on a licence that rewrites the extension on save).
`--repair` fixes only what is broken
— it never rewrites a valid, possibly non-consecutive `from_version` — and
backs `_context`/`context.json` up first. `--dry-run` reports what it would do.

**Reservation stubs are only reclaimed once they age.** A `_context/vNNNN.json`
claim is not bookkeeping *about* a reservation, it *is* the reservation:
`reserve_next_hip_file_path` allocates a version by creating that file
`O_CREAT | O_EXCL` and bumps to the next number when the create fails, so
between "version claimed" and "hip written" the claim is the only thing
keeping two savers off the same `vNNNN`. Deleting a live one therefore hands
one artist's version number to another, and the second save overwrites the
first — which the backup does not protect against, since it covers `_context`
and `context.json`, not the clobbered hip.

Age is the only evidence available that a claim is dead rather than in
progress, so a claim younger than `context_repair.RESERVATION_GRACE`
(30 minutes) is reported as an in-flight reservation and left strictly alone.
That also means an in-flight claim is **not** counted as drift: a workspace
whose only anomaly is a save in progress diagnoses healthy, so a sweep does
not report the artists who are working as failures. A stale claim costs
nothing while it waits out the grace — the reserver skips over it and takes
the next number — so there is no flag to shorten it.

Both unprovable cases are kept rather than deleted, because deletion is the
destructive direction: a claim that cannot be `stat`'d, and one whose mtime is
in the future (clock skew across the SMB share). Neither is silent — both log
and appear in the repair report.

The practical consequence: **`--repair` is safe to run while artists are
working.**

Unlike the audits above it parses version names via `api.naming`, so it needs
the pipeline environment configured (`TH_CONFIG_PATH` etc.):

```bash
python scripts/verify_context_chain.py "P:/paleindia/shots/000/sh020_Clash/animation"
python scripts/verify_context_chain.py --scan P:/paleindia/shots            # sweep a project
python scripts/verify_context_chain.py --scan P:/paleindia/shots --repair   # heal in place
```

The reservation, lineage, reader, and repair guarantees are pinned headlessly
by `test_context_chain` (see the test harness README).

## Dropped-metadata guard harness

`scripts/verify_dropped_asset_arc_guard.py` pins the asset-scoped export
drop-guard (see the Dropped-metadata guard section in `composition.md`):
a metadata-less prim that composes from the `export/assets/` tree is a
dropped asset and blocks the export, while artist-authored geometry
(composing from no pipeline layer) and department-authored shot geometry
(composing only from a `export/shots/.../<dept>/` export) both pass. It
builds an in-memory stage mixing a tracked asset, an asset-composed drop,
a session-authored prim, and a department shot-geometry prim and asserts
each verdict, plus the fail-closed fallback when no asset export root is
supplied.

Unlike the pure-Python and Qt harnesses above it exercises real USD
(`Usd.Stage.GetPrimStack`) through the `pxr`-importing pipeline module,
so it must run under **hython**, not the test venv:

```bash
hython scripts/verify_dropped_asset_arc_guard.py
```

Read-only; exits 1 on any failed assertion.

## Changelog

`CHANGELOG.md` is **generated — never hand-edit it**. It is derived from
the conventional-commit subjects between release tags, one section per
tag, newest first:

```bash
uv run --no-project python scripts/generate_changelog.py
uv run --no-project python scripts/generate_changelog.py --check  # stale?
```

The section *layout* lives in `.ci/_changelog.py`, shared with
`.ci/release_tumblepipe.py` so the file and the notes posted to the
github release / TumbleTrove version page can't drift in shape. Their
*ranges* deliberately differ — see **What a release's notes cover**
below. Commits are
bucketed by prefix (`feat` → Features, `fix` → Fixes, …); a `!` marks a
breaking change (`refactor!: …`) and leads the release; `ci`, `build`
and the `release` version-bump commit are excluded. Anything that
doesn't parse still lands under *Other Changes* rather than being
dropped, so a forgotten prefix can't lose a change — but it is worth
writing the prefix. A merge commit's `Merge branch …` subject lands there
too, which is why history on `master` is kept linear: merge a feature branch
with `git merge --ff-only` (rebase it first if needed), never `--no-ff`.

**Ordering gotcha:** the script reads the *tags*, so a release's section
can only be rendered once its tag exists. Regenerate and commit
**after** `git tag`, not before — that commit then rides along in the
next release. This is why `--check` is not a CI gate: it would
false-fail on the commit immediately after every tag.

In the same commit, refresh the record of what the release's HDAs shipped:

```bash
uv run --no-project python scripts/snapshot_hda_parms.py   # merges the newest v* tag
```

It adds the new tag's HDAs and parms to `tests/data/hda_parms_released.json`
(read from git, so the working tree does not matter), which
`tests/test_hda_parms_kept.py` holds every later DialogScript to (*Editing an
HDA definition*). Until it is refreshed the test reads the newest tag from
git itself, prints that the snapshot is behind, and still passes or fails on
the parms, so a forgotten refresh never breaks the suite and never opens a
gap; it only stops the record growing once a newer tag is cut.

### Superseded commits

A subject records what a commit *intended*; occasionally the release
overtakes it. `SUPERSEDED` in `.ci/_changelog.py` maps a sha to what
replaced it, and those commits are rendered by nobody — the file, the
github release and the version page all drop them together.

It is a hand-maintained list on purpose. Deriving notes from subjects is
the design, so every entry is a claim that the history and the shipped
code disagree, and that deserves a reviewer rather than a heuristic. The
two entries today are `7198fb7` and `484980c`, which announced refusals
that `6d87c59` inverted into tolerance before v1.44.0 shipped.

### What a release's notes cover

`CHANGELOG.md` renders one section per **tag**. The notes published to
the github release and the TumbleTrove version page range from the last
**released** version instead — the highest still-active version the
registry holds — because the registry is what users actually have.

The two agree whenever every tag publishes, and diverge exactly when one
does not. A tag whose pipeline fails never reaches anyone, so ranging
from it would silently drop everything that release was carrying:
v1.44.0 and v1.44.1 both died on the windows builder, and v1.44.2's
notes were published listing a single docs commit while shipping seven
breaking changes. `_notes_base_tag` refuses rather than guesses if the
registry's last released version has no matching ancestor tag here.

## LPE tag harness

`scripts/verify_lpe_tags.py` pins the contract that `th::lpe_tags` builds a
render var for every tag a light actually carries. The node does not read its
render vars off the multiparm: a foreach loop scrapes the lpetag attributes
back off the *cooked stage*, so anything the scrape cannot see produces no
`beauty_<tag>` AOV — silently, with no node error and no warning.

That is how mesh lights broke. `LightAPI` is an **applied** schema, so a mesh
light keeps the type name `Mesh`; the scrape filtered on
`GetTypeName().endswith('Light')` and dropped every one of them. A shot with
three configured tags rendered a single `beauty_fill` var and nothing said so.

The harness covers both halves — that mesh lights and light-typed prims alike
reach the loop, and that a tag nothing carries is named in the node's **Status**
field rather than vanishing. The empty-pattern case is the one that matters
most: an empty Lights pattern is the parm's own default, it makes the LPE Tag
LOP fall back to tagging everything `Untagged_Lights`, and the loop skips that
name by design, so *every* tagged AOV disappears at once.

It builds its stage in memory and touches no project data, but it drives real
nodes, so it needs Houdini with `otls/` on `HOUDINI_OTLSCAN_PATH` and `python/`
on `PYTHONPATH` (e.g. TumbleTrove Desktop's `run_hython`).

## Comp harness

`scripts/verify_comp.py` drives the comp nodes (`th::import_render`,
`th::light_mix`, `th::export_comp`) and the real composite worker in a
throwaway project (see [Compositing](compositing.md#the-comp-workfile)). It
stages synthetic renders with the install's `hoiiotool`: a two-channel shot
with a raw render and a denoise of `default`, and a `chars` render with
`beauty_key` / `beauty_rim` light groups, a one-channel matte and a pass the
comp does not read. It checks resolution (`best`, pins, refusals), the cable
fields, the light mix's pixels (defaults give back the beauty, Mute,
Exposure, an idempotent Sync), the unread-channel warning, the freeze plan,
and then runs `farm/workers/composite_houdini.py` in a second hython on the
config the job would pass: the merged frame is `chars` over `default`,
stamped ACEScg, with its receipts, and the version's context.json records
the renders it read. It then runs the worker again as Farm Submit's Comp
column does (no frozen imports, the export found by layer), which must write
the next version. A Multi's submit plans one version per member.

The child hython runs with `HOUDINI_PACKAGE_SKIP=1` and this checkout's
`otls/` on `HOUDINI_OTLSCAN_PATH`, so an installed tumblepipe package cannot
shadow it. Run it with hython (e.g. TumbleTrove Desktop's `run_hython`);
`VERIFY_DEBUG=1` prints the traceback of a failing resolve.

`scripts/verify_import_render_outputs.py` uses its staging for a shot with
three layers (a denoise read from the `C` plane, two raw renders) and cooks
every output of an all-channels `th::import_render` and of one import per
layer: each held beauty has its layer's pixels, every free `rgba` output is
empty and cooks, the `lpe`, `aov` and `matte` cables carry each layer's
fields by name with pixels, and the node's comment names the layer on each
output (`rgba: chars`, `rgba 2: bg`, `rgba 3: fx`) without replacing one
someone typed. Run it the same way (compile the HDAs first).

## Slap comp harness

`scripts/verify_slap_comp.py` drives the *TumblePipe Slap Comp* preset
([Slap comps](nodes/lighting-and-rendering.md#slap-comps)) in real Houdini:
it builds the preset in a COP network under `/stage` and `/obj`, reads back
`beauty` on every slap comp AOV row of the three stock nodes, the wiring,
the Block Path and the TumblePipe folder, puts a second preset beside the
first (numbered names that still find each other), cooks the block on a
fallback plate and compiles it to a program with `compile_program`. With
`--husk` (a Karma licence) it renders a 32x32 frame whose colour AOV is named
`beauty` without and with `--slap-comp` of a preset holding an Invert COP and
checks the slap comped beauty is one minus the plain one: husk ran the
compiled program on the `beauty` AOV. No project needed:

    HOUDINI_PACKAGE_DIR= PYTHONPATH=<repo>/python hython scripts/verify_slap_comp.py --husk

## Project HDA harness

`scripts/verify_project_hda.py` drives **TumblePipe ▸ Create Project
HDA...** ([Project HDAs](project-hdas.md)) end to end in a throwaway project:
the menu entry's context expression as `OPmenu.xml` has it, `create` on SOP
and LOP subnets (type, file, wiring, promoted spare parm, Tools.shelf
submenu, no backups), the version bump and kept spelling, the dialog
offscreen, and `show_dialog`'s Cancel, Create and failure paths. It then
starts a second hython whose only extra package carries `hpm.toml`'s own
`HOUDINI_OTLSCAN_PATH` entry and checks that session loads the files, keeps
Houdini's stock assets, and files the Tab entries under the project. The
naming rules themselves are `tests/test_project_hda.py`.

```bash
hython scripts/verify_project_hda.py
```

It sets `TH_*` and drops any already-imported tumblepipe before importing
this checkout's, so a stale Houdini package's project (one workstation's
sets `P:/growth`) is never written to; hython has no `hou.ui` or `hou.qt`,
so it installs stand-ins that record what would have been shown. File-name
checks read the directory listing, because `is_file()` ignores case on
Windows and macOS and would pass a wrong spelling. `VERIFY_KEEP=1` keeps the
temp project. Exits 1 on any failure.

## Rig discovery harness

`scripts/verify_rig_discovery.py` checks the `animatable` prim stamp and
`th::import_rigs`' **Discover** ([`th::import_rigs`](nodes/assets.md#thimport_rigs-sop))
in a throwaway project: the import nodes' metadata scripts stamp a rigged
character and an animatable prop but not a static one, `import_layer`
overwrites a stale stamp, and Discover, with Stage set and with it empty
inside a `th::animate`, makes one row per rigged animatable asset, keeps a
row's pinned version, judges an untagged prim by the rule and names an
animatable asset with no rig. The rule itself is
`tests/test_animation_template.py` and `tests/test_animatable_stamp.py`.

```bash
HOUDINI_PACKAGE_SKIP=1 HOUDINI_PATH="<repo>;&" PYTHONPATH=<repo>/python \
    TH_PIPELINE_PATH=<repo> hython scripts/verify_rig_discovery.py
```

With packages skipped, Houdini's APEX scene SOPs are not loaded, so the rig
import Discover runs stops at instancing; that is printed as a note. The
new parms are in the expanded HDA, so rebuild `otls/sop_th.import_rigs.2.0.hda`
(`hotl -l`) before running it against a checkout.

## Shared timeline harness

`scripts/verify_multi_timeline.py` drives a shots Multi's
[shared timeline](asset-browser/multis-and-roots.md#shared-timeline) in a
throwaway project: two shots in a Multi with shared timeline and bookmarks
on, the second placed at +10. It exports both through `th::export_layer`
from the Multi's workfile and checks each published layer sits on its shot's
own frames with the values cooked at its shared ones; checks the Time Offset
modes (From Multi, None, Manual) on `import_shot` and `export_layer`; checks
that the Sublayer LOP offset `import_shot` sets moves the published layer
back to its shared frames; and checks the playbar span, one temporary
bookmark per shot, bookmarks off leaving an artist's own bookmark, and 0
outside a Multi. The pure logic is `tests/test_multi_timeline.py`.

Compile the HDAs first, so the new parms exist:

```bash
hython .ci/compile_hdas.py
HOUDINI_PACKAGE_SKIP=1 HOUDINI_PATH="<repo>;&" PYTHONPATH=<repo>/python \
    TH_PIPELINE_PATH=<repo> hython scripts/verify_multi_timeline.py
```

Why the export rewrites samples instead of using a Time Shift LOP: Time
Shift cooks its input at the other frame but stamps the samples there too,
and a USD ROP writing through it collapses the range to a single sample.
`VERIFY_KEEP=1` keeps the temp project. Exits 1 on any failure.

## Changing a department template

A project copies `scripts/project_template/_config/templates/` when it is
created and runs its own copy from then on: "New workfile from template"
loads `<project>/_config/templates/<context>/<department>/template.py`, not
the package's. **Editing a packaged template changes new projects only.** An
existing project gets the change only when a numbered refresh step brings it
(v2, v9, v12, v14–v16, v18–v21, v24–v26 so far), so a fix nobody migrates is a fix nobody has: the
rig/blendshape dive fix shipped in 1.77.0 and the animation template's
`KeyError: 'uri'` fix both sat in the scaffold, unseen by every project past
v12, until v14.

So a template change comes with:

- **A `STOCK` row and a fixture.** `src/wizard/core/src/templates.rs` lists
  every version the scaffold has shipped, each one a fixture under
  `src/wizard/core/tests/fixtures/templates/<context>/<department>/`, named
  by the first ten characters of its git blob (`git hash-object`). The
  refresh steps replace only a template that matches one of them, so a
  version missing from the list reads as the studio's own and is never
  replaced. `cargo test` fails until the new version is listed.
- **A refresh step, when existing projects need the change.** Add the next
  numbered step to `src/wizard/core/src/migration.rs` that re-runs the stock
  refresh (as v14 does), bump `CONFIG_LAYOUT_VERSION` in
  `python/tumblepipe/config/layout.py` and the scaffold's
  `_config/version.json` to match, and describe the step in
  [Configuration](configuration.md).
- **An entry in `REMOVED_MODULES` / `REMOVED_CALLS`** when the change goes
  with removing an API the old templates call, so a customised template
  still calling it refuses the refresh by name instead of failing at
  new-workfile time.

## Renaming across the wire boundary

`channel` (the publish-tree fork, once called a *variant*) is the worked
example, but the rule is general: this tree serializes a lot of its own
identifiers, so a rename that looks purely internal can change data.

`docs/composition.md` lists where each spelling lives now. Three things worth
knowing before any similar sweep:

- **A `dict(...)` keyword *is* a dict key.** `pipe/context.py` wrote
  `context.json` as `dict(…, variant=…)`, and every farm job config was built
  as `dict(variant_name=…)`. Rename the identifier and you have silently
  renamed an on-disk sidecar field and a submit↔worker JSON key that the
  reader still looks up by its old string. The same goes for anything
  reached reflectively — `getattr(ctx, "variant_name")`, and
  `pipe/houdini/rebuild.py`, which drives the wrappers from a table of
  getter/setter **name strings**.
- **Check the new name is not already taken.** `channel_name` and `channels`
  were long-standing kwargs for Discord notify channels and for OIIO image
  channels (`farm/workers/notify.py`, `farm/pipelines/{playblast,composite}.py`,
  `apps/exr.py`). A blanket rename in either direction silently rewrites them.
  (The publish channel's job key is `channel_name` in `settings`; the Discord
  channel's stays `channel_name` in `tasks` — same word, different dicts.)
- **Where the data lives decides how the rename reaches it.** Project data
  sits on the shared drive while every reader ships per-machine in the hpm
  package, and TumblePipe has users outside the studio whose projects can be
  at any config version and whose published data already exists. Sort every
  stored spelling by who owns it before touching a reader:

  - *The project's config database* is the pipeline's to rewrite: add a
    numbered `_config` migration step (`src/wizard/core/src/migration.rs`;
    never edit a shipped step, never assume a minimum version) and let the
    readers take the new spelling only. Every launch offers the migration.
  - *Farm job configs* cross submitter → worker, but a job pins the exact
    package version that submitted it (`farm.deadline.job_dependencies`), so
    both ends always agree: rename them outright.
  - *HDA parm names* are baked into every saved scene and Houdini drops a
    value whose parm is gone before any script runs. Keep an invisible
    carrier parm under the old name, bump the definition's Version, and move
    the value in its `SyncNodeVersion` handler
    (`pipe/houdini/node_version.py`). A *button* has no value to move but
    is saved all the same: removing one makes every scene saved with it
    warn "Skipping unrecognized parameter" on open (hython 22.0.429), so a
    retired button stays as an invisible parm (`entity_select`, see
    `otls/README.md`).
  - *Published data* is immutable and is never rewritten. Its readers accept
    the old spelling forever — in one function per language, with the reason
    beside it — and writers emit only the new one.

  `channel` is the worked example of all four: migration v13, the
  `channel_name(s)` job keys, the eleven HDAs' `variant` carriers, and
  `config.channels.read_published_channel` / the resolver's
  `uri::channel_from_query` for published `context.json`, staged URIs and
  prim `customData`. Enumerate the boundaries first: leaving one out is not
  a smaller change, it is a hole. Where a reader does accept both, treat an
  absent key as the existing default and raise only when both spellings are
  present and *disagree* — that is unresolvable, and every other case has a
  right answer.

Rename identifiers with a `tokenize` pass that edits only `NAME` tokens —
never `STRING` tokens — so quoted keys survive by construction; a regex over
the source cannot tell the two apart. Then diff the serialized surface against
the previous commit: every `dict()` keyword, dict-literal key, subscript key
and `.get()`/`.setdefault()`/`.pop()` key should be unchanged. `ruff` with
`F821` over the whole tree is the backstop for the identifiers themselves.

### OnLoaded must not create or destroy nodes

An HDA's OnLoaded runs while Houdini is still loading the hip. Creating or
destroying nodes there crashes Houdini once the load finishes its last
pass over the loaded nodes (signal 11 in
`OP_Node::syncNodeVersionIfNeeded`): `th::light_mix` rebuilt its `mix`
network in OnLoaded in 1.101, and every comp with an older mix crashed on
open (Remy, 2026-11-11). Setting parms, comments and flags is fine.
Anything that builds or tears down nodes goes through
`pipe/houdini/after_load.py`: `after_load.run(fn)` runs `fn` at once
outside a load, else on the hipFile AfterLoad/AfterMerge event. Find the
node again by session id inside `fn` (it may be gone by then), as
`light_mix.on_loaded` does. `tests/test_onloaded_node_edits.py` checks
every OnLoaded statically: from the handler, following calls within its
module, nothing may reach `createNode`, `destroy` and the like except
inside a function handed to `after_load.run`.

## Editing an HDA definition

Reach for this when a fix lives inside an HDA rather than in `python/`. The
tracked source of truth is the expanded `otls/<name>/` directory; the `.hda`
beside it is a gitignored build artifact.

- **Never delete a parm, hide it.** A scene saves every parm of every node,
  buttons and folders included, and a scene loaded with an HDA that lacks
  one warns "Skipping unrecognized parameter" and drops the value. Make a
  retired parm `invisible` and keep its name (a rename is a delete). An HDA
  type is never removed either. `tests/test_hda_parms_kept.py` holds every
  HDA to the parms in `tests/data/hda_parms_released.json` (what the
  recorded releases shipped) and in the newest `v*` tag, and names the HDA
  and parm it misses; a removal that really is fine goes in its `ALLOWED`
  with the reason. The snapshot is refreshed after each release tag (see
  *Changelog*).
- **Expand with `hotl -t`, never `hotl -x`.** `-t` writes the uncompressed
  `Contents.dir/Contents.mime` the repo tracks and round-trips **losslessly**
  (zero diff), so an HDA edit produces a reviewable diff of just the change.
  `-x` writes a compressed binary `Contents.gz`, which still builds but is
  undiffable. `compile-hdas` collapses the other way with `hotl -l`.
- **The comp HDAs are generated, not hand-edited.** `th::import_render`,
  `th::light_mix` and `th::export_comp` are built from code by
  `hython scripts/make_comp_hdas.py`, which rewrites their
  `otls/cop_th.<name>.1.0/` directories. Change the script (or the Python
  the nodes call, `python/tumblepipe/pipe/houdini/cops/<name>.py`) and
  regenerate; an edit made only in the expanded otls is lost on the next
  run. Then run `scripts/verify_comp.py` (*Comp harness* above).
- **Edit the definition's interface, not an instance's.** Calling
  `setParmTemplateGroup()` on an HDA *instance* and then `updateFromNode()`
  silently fails to carry nested multiparm children. Use
  `definition.parmTemplateGroup()` / `definition.setParmTemplateGroup()`. And
  order matters: do `updateFromNode()` for *contents* first, since it replaces
  the whole definition and would clobber an interface edit made before it.
  (For inserting *spare* UI into an instance, see *HDA spare-parm UI harness*
  above — a different hazard with the same call.)
- **A locked instance hides its contents in hython.** `children()` on a fresh
  instance of a locked HDA can come back empty; call
  `allowEditingOfContents()` first, then edit and `updateFromNode()`.
- **Install the repo copy *after* loading a workfile.** Placed nodes bind to
  whichever definition is preferred when the hip loads, and the installed
  package's `.hda` wins at load. To check a repo edit against a real workfile,
  `hou.hipFile.load(...)` first, then `hou.hda.installFile(<repo dir>)` and
  `definitionsInFile(<repo dir>)[0].setIsPreferred(True)`; the placed nodes
  re-bind.
- **Expect a little re-serialisation noise.** `setParmTemplateGroup()`
  rewrites the whole DialogScript, so unrelated parms can shift cosmetically
  (a separator's label, a toggle's dropped `range`). Read the diff, but these
  are harmless.
- **Nested `folder.parmTemplates()` returns copies.** Mutating one changes
  nothing until you `folder.setParmTemplates(...)` and then
  `group.replace(group.find('<folder>'), folder)`.
- **Warnings do not escape an HDA.** A child's warning leaves the parent's
  `warnings()` empty, and `hou.pwd().parent().addWarning()` fails outright.
  Errors *do* propagate. So a failed parm expression becomes a node error that
  breaks the whole chain — any status or validation affordance must be fully
  guarded, including the no-input case where `stage()` returns `None`.
- **Python parm expressions on an HDA are unreliable.** `hou.pwd().path()` and
  `hou.pwd().type().name()` both evaluate to an empty string inside one, even
  though `hou.pwd().parm(...)` works. Compute the value on an internal node,
  where relative paths like `hou.node('../define_lpetags')` behave, and read it
  out from the interface with `chs("<child>/<parm>")`.
- **A new HDA needs an operator index entry.** `hpm.toml`'s `[[operators]]`
  block is generated, not hand-written: run `python .ci/gen_operators.py`
  and replace everything from `# Operator index.` to the end of the file with
  its output. On Windows, strip the CRs afterwards. The catalog's Nodes
  section reads this index, and `tests/test_operator_index.py` fails while it
  is stale. To build a new HDA from hython (`th::mattes` was made this way):
  - make a `subnet`, put the logic in an internal Python Script LOP that
    calls into `python/`, and run `createDigitalAsset()` on it;
  - set the interface with `definition.setParmTemplateGroup()`;
  - add `OnCreated` (set `ExtraFileOptions` `OnCreated/IsPython`) and a
    `Tools.shelf` copied from a sibling HDA, then `save()`;
  - expand the result into `otls/` with `hotl -t`.

## Export cache-reference harness

`scripts/verify_cache_reference_fixes.py` pins the publish-by-reference
contract for versioned `th::cache` files (see *Layer save paths and
export portability* in `composition.md`): arcs into a cache root are
pinned absolute (`export_checks.absolutize_cache_arcs`), skipped by the sidecar
localizer (`skip_roots`), and exempt from the escaping-path guard
(`allowed_roots`) — while a missing cache file still aborts as a
dangling arc. It needs `pxr` but no scene or project data (temp dirs
only), so run it under any project hython (e.g. TumbleTrove Desktop's
run_hython). The arc walk and the path classification are covered by
`tests/test_arcs.py`.

## Animated switch/blend export (track prim existence)

An animated Switch/Blend that changes **which prims exist** per frame
(switching between different geometry/assets/takes) only survives a USD
write if the writer authors visibility time samples — the ROP's *Track
Primitive Existence to Set Visibility* (`trackprimexistence`). With it
off, every switched branch is written as always-visible and all branches
appear at once, so the switch looks broken on the published layer.

The `filecache` LOP tracks prim existence inherently, so the
`filecache`-based farm export/stage tasks (and `th::cache`) were always
correct; the plain `usd_rop` export paths were not. `trackprimexistence`
is therefore baked **on** in the `export_layer` and `export_asset` HDAs'
internal `usd_rop` (their expanded `Contents.dir/Contents.mime` `.parm`
line).

Editing this on an HDA means editing the definition, not the driver: the
internal ROP is a locked asset node, so `native.node('export').parm(...)`
raises `PermissionError` — flip the value in the expanded `.parm` text
(`"off"`→`"on"`). To verify a repo HDA edit under `run_hython` (which
loads the *installed* package, not the working tree),
`hou.hda.installFile(<repo otl dir>)` then `definition.setIsPreferred(True)`
before instantiating.

Not fixable here: switching a **default-valued** attribute on a single
prim (e.g. a constant transform) is lost by `usd_rop`, the Cache LOP, and
`filecache`/`th::cache` alike — a USD default has no time coordinate, so
the animation must be authored as time samples upstream.

## Farm modules

`python/tumblepipe/farm/` is split by the side of the farm each module runs on:

- **`jobs/<family>.py`** builds the one Deadline job that runs a worker:
  `build(config, paths, staging_path)` validates its config and calls
  `deadline.farm_job(<family>, context, ...)`, which bundles the worker's
  context.json, sets the base environment and attaches the HPM manifest;
  `run_once` / `run_batched` schedule its frames.
- **`workers/<name>.py`** is what the job runs on a render node, as
  `python -m tumblepipe.farm.workers.<name> <context> <first> <last>`. The
  hython-side half of a worker, `workers/<name>_houdini.py`, is run by it as
  `hython <script> <config>`. Every one of them ends in
  `sys.exit(worker.run(spec.is_valid_context, main))` (`hou.exit` under
  hython): `farm.worker` parses the arguments, loads and validates the config,
  and owns the shared helpers (`error`, `headline`, `ensure_dir`); frame paths
  go through `farm.frames` (`frame_file`, `with_frame_token`, `frame_of`), with
  `####` the token in every config.
- **`specs/<family>.py`** holds the config schemas: `is_valid_config` for what
  the job builder takes, `is_valid_context` for what its worker reads (the
  same function when the job stores its config as is), `is_valid_houdini_config`
  for a hython half. They are built from the primitives in `farm.checks` and
  import nothing else, so both sides can load them.
- **`pipelines/<name>.py`** builds a batch of jobs:
  `build(config, paths, temp_path, jobs, deps, depends_on)` and
  `submit(config, paths)`, both on `pipelines/_batch.py`'s `submit_batch`.
  `post_render` is the review
  chain the render and composite pipelines end in; `batch_submit` is the Farm
  Submit dialog's per-entity submission. Every batch is made by
  `_batch.new_batch(title, entity)`, which stamps each of its jobs with the
  project and the entity URI (`farm.stamp`: Deadline extra info, or the local
  job's `extra_info`). The Farm Manager and the clean-ups
  (`manager/cleanup.py`) read the stamp, so a new pipeline must pass its
  entity too; `tests/test_farm_stamp.py` checks that every pipeline does.
- **`submit/`** is the Qt-free half of the Farm Submit dialog: `settings`
  (what each entity's settings resolve to), `grid` and `status` (its cells and
  their states), `camera` (the render camera probe) and `plan` (the
  out-of-process runner, `python -m tumblepipe.farm.submit.plan <plan.json>`).

The import graph runs one way: pipelines and the workers that chain more work
(stage, export, collapse, publish) import jobs and pipelines; jobs never import
a worker or a pipeline. A job references its worker by name only.
`tests/test_farm_layout.py` pins these rules, and imports every farm module
(the hython halves against the headless `hou` stub), so a broken import fails
the test run rather than a render node. Gate changes with the test suite and
the same lint preflight runs:

```bash
python -m compileall -q python/tumblepipe/farm/
ruff check --select E9,F python/tumblepipe/farm/
```

## Building the package locally

`hpm build` produces the install image: it runs the `[stage].prepack`
scripts (`build-resolver` cmake-builds the tumbleResolver USD plugin into
`resolver/houdini<major>/`; `build-delegate` builds hdThPlayblast, the
farm playblast's Hydra render delegate (`src/delegate`: a Rust rasteriser
behind a C++ Hydra shim, plus an opt-in wgpu path, the `gpu` cargo feature
and CMake's `TH_DELEGATE_GPU`, on by default on Windows only, which draws
and runs the post pass on the card (`gpu.wgsl`, `post.wgsl`) and keeps
unchanged meshes there between frames; `blur.rs` draws motion blur and
depth of field as several layers averaged together, on either path (on
the GPU the average is made on the card, `sum.wgsl`);
`cargo test --features gpu` holds it to the CPU path's picture on the
machine's GPU, which the build workers do not run;
`.ci/smoke_delegate.py` renders a test stage through a finished build with
husk, synchronously and through the viewer's background path), into `plugins/houdini<major>/`; `build-wizard` cargo-builds both hook apps
(`tt_setup` and `tt_prepare`) into `bin/<platform>/`; `build-player` builds the
sequence player and its FFmpeg into the same place ([Sequence
player](#cinedex-the-editor)); `compile-hdas` collapses the
expanded `otls/<name>/` sources into binary `.hda` files) and stages the
tree per `[stage].include`/`exclude`. Requirements: cmake, rustup (every
crate builds with the toolchain the root `rust-toolchain.toml` pins; CI
installs it up front, locally `rustup toolchain install` it once), and at least one
Houdini install. (The hook apps are
Houdini-independent — `build-wizard` needs only cargo, no HFS; `build-player`
also needs MSYS2 on Windows for FFmpeg's configure.)

The HDK builds (resolver, delegate, VRAM hook) configure from scratch each
time: the cmake build dir (`build-delegate-<os>-houdini<major>/`, say) is
deleted first. The resolver's and the delegate's cargo output is kept
beside it, in the same name with `-rust` added (`TH_RUST_TARGET_DIR`), so a
rebuild only compiles what changed: about 7 s for the delegate instead of
90 s. Deleting a `-rust` dir forces a full Rust rebuild.

The resolver and VRAM hook build for the Houdini majors in
`HPM_HOUDINI_MAJORS` (bare space-separated majors, e.g. `22`); without it
the build stops and says so:

```bash
HPM_HOUDINI_MAJORS=22 hpm build --platform windows-x86_64
```

`hpm build --houdini-majors "22"` (hpm ≥ 0.27) and dev-package launches
from TumbleTrove Desktop (≥ 0.38, which sets the variable from the Houdini
versions it discovers) do the same thing. Release CI always builds the
full matrix — see `.woodpecker/build-*.yml`.

### hdThPlayblast: transparent background

The delegate normally writes opaque frames: every pixel over the grey
backdrop gradient, alpha 1. `TH_PLAYBLAST_ALPHA` (`1`/`true`/`on`; unset,
`0`, `false` or `off` is off, the default), or the render setting
`th:alpha` (a bool or int on the render settings, which wins over the
variable, as `th:samples`, `th:shading`, `th:lighting`, `th:cull`, `th:transparency` and the
blur settings do), leaves
the background transparent. `viewport_env(..., alpha=True)`
(`apps/viewport_renderer.py`) sets the variable for husk.

What the delegate hands husk, in the colour AOV (linear, float RGBA):

- **Premultiplied by coverage.** A sample that hit nothing adds colour 0
  and alpha 0, one that hit a surface its shaded colour and alpha 1, and
  the pixel is their average: alpha is the pixel's coverage, through the
  same anti-aliasing samples and the same motion blur / depth of field
  layers as the colour. A pixel half covered by unlit white is
  `0.5, 0.5, 0.5, 0.5`; background is `0, 0, 0, 0`. Shading and ambient
  occlusion of the surfaces are unchanged, so an opaque pixel is the opaque
  render's pixel exactly.
- Off, the output is bit for bit what it was before the option existed
  (measured through husk: PNG and EXR pixels identical to HEAD's build, on
  the CPU and the GPU path).
- Both paths do it: `composite.rs` / `blur::average` on the CPU,
  `post.wgsl` (`accumulate` counts coverage into the sum's w, `finish`
  divides it) and `sum.wgsl` (`scale` averages alpha across blur chunks) on
  the GPU. The two agree on coverage the way they agree on depth: exactly
  from the same render buffer, and to within one sample at an edge the
  rasterisers decide differently.

What husk writes (Houdini 22.0.368, its default `--ocio 1`), measured:

- **EXR:** the AOV as it is: linear, **premultiplied** (associated) RGBA,
  A = coverage. Composite with `out = rgb + (1 - a) * background`, in linear.
- **PNG (8-bit RGBA, `sRGB` chunk):** husk converts the premultiplied
  linear colour to sRGB first, and OIIO's PNG writer then un-premultiplies
  the *encoded* values, as PNG's straight alpha requires, and clamps:
  `png.rgb = min(1, srgb(rgb_premult) / a)`, `png.a = a` (linear coverage),
  background `0, 0, 0, 0`. So it is **straight alpha in display space**:
  the usual display-space "over", `out = png.rgb * png.a + bg * (1 - png.a)`
  (all in sRGB-encoded values), gives `srgb(rgb_premult) + bg * (1 - a)`,
  which is the premultiplied composite done in display space, wherever
  `srgb(rgb_premult) <= a`. Where it is not -- bright anti-aliased edges and
  motion-blur trails: on a lit grey sphere 360 of 578 edge pixels -- the
  division passed 1 and was clamped, so such an edge composites with colour
  `a` (`png.rgb` = 1) instead of `srgb(rgb_premult)`: a little darker than
  the EXR's edge. Opaque pixels (`a = 1`) are the opaque render's exactly.
  husk `--ocio 0` does not change this. (Do not read these PNGs through
  OIIO's default reader to check them: it multiplies alpha back in linear;
  open with `oiio:UnassociatedAlpha=1` to see the stored bytes.)

husk and `PXR_PLUGINPATH_NAME`: husk processes the Houdini packages
itself, so an installed tumblepipe package's `hdThPlayblast` registration
comes ahead of the one `viewport_env` prepends. To render a local build of
the delegate through husk while a tumblepipe package is installed, run husk
with `HOUDINI_PACKAGE_SKIP=1` (or a dev-override launch, whose package is
the working tree).

### hdThPlayblast: scene lights

The delegate syncs the Hydra light sprims (`distantLight`, `sphereLight`,
`rectLight`, `diskLight`, `cylinderLight`, `domeLight`; `ThLightPrim` in
`cpp/delegate.cpp`) and hands the visible ones over as authored
(`ThFrame::lights`, `ThLight`): the transform, intensity x 2^exposure x
diffuse, colour, colour temperature (when enabled), normalize,
treatAsPoint, angle, radius, width, height, length. The frame's job keeps
a copy, so a light edit counts as a change (`_Changed`), not an edit that
stops the background render. The per-frame value only: motion-blur
sub-samples do not move the lights. `src/lights.rs` turns them into camera
space for the frame (`Lighting`), and `composite::light` lights each
sample with them on both paths (`post.wgsl` and `gpu.wgsl`'s `light`,
16 lamps in the uniform blocks); the GPU tests hold the two to the same
picture for any light set (`scene_lights()` in `gpu.rs`).

- **Shading.** Lambert's cosine from each light, no shadows (Houdini GL's
  "normal" lighting has none). Distant lights are directional; sphere,
  rect, disk and cylinder lights are points at their centre, the direction
  and the 1/d^2 falloff worked out per sample from its rebuilt position,
  d^2 held above the light's own size; rect and disk lights also take the
  cosine of their emitting side (their -Z). Dome lights add an even ambient
  of colour x intensity (textures ignored) in place of the rig's sky and
  ground. The highlight (`lights::SCENE_SPECULAR`, the rig's 0.05 over its
  key's 0.62) is of the light brightest as seen from the camera, tinted by
  the light it gives the sample. Clay is lit the same; albedo and occlusion
  are not lit.
- **Brightness is Karma's**, measured with Karma CPU (Houdini 22.0.429) on
  a 0.5 grey Lambert plane two units under each light: the table in
  `src/lights.rs`, held by `lights_are_as_bright_as_in_karma`. In short a
  surface of colour `albedo` comes out `albedo x k`, with `k = L cos / pi`
  for a normalized distant light (L = intensity x 2^exposure x colour), `L
  Omega cos / pi` for one of angle `a` not normalized (Omega the solid
  angle of a cone of angle `a`: a default Light LOP distant light,
  0.53 degrees, is nearly black in Karma, so here too; husk's own
  headlight is intensity 57225 for that reason), `L r^2 cos / d^2` for a
  sphere (normalized `L cos / (4 pi d^2)`; treatAsPoint `L cos / (pi d^2)`),
  `L w h cos cos_e / (pi d^2)` for a rect (normalized without `w h`), and
  `L` for a dome. Colour temperature is the Planckian locus at luminance 1
  (`lights::blackbody`, within 2% of Karma's tint from 3000 K to 9000 K).
- **Fallback.** No visible light of those kinds, or `th:lighting` =
  `headlight` (`TH_PLAYBLAST_LIGHTING`, read as `th:shading` is), is the
  fixed camera rig, bit for bit (`no_scene_lights_is_the_fixed_rig`).
  husk adds a distant light `/husk_headlight/__the_headlight` (libHUSD's
  default headlight) to a stage without lights; the delegate skips any
  light named `__the_headlight`, so such a stage keeps the rig.
- At most 16 lights (`lights::MAX_LIGHTS`): every dome is summed into one,
  and past that the brightest by their strength are kept.
- `TH_PLAYBLAST_LIGHTS=1` lists each frame's lights as read.
  `.ci/smoke_delegate.py` (`_check_lights`) renders a grey wall through
  husk under a distant and a sphere light on the CPU and the GPU and checks
  its centre against the mapping, and that an invisible light and the
  headlight setting give the rig.
- Artists set it as **Lighting** in the farm dialog's playblast (the
  look's `lighting`, `specs.playblast.PLAYBLAST_LIGHTINGS`, the entity
  property `playblast.lighting`) and on the LOP and SOP th::playblast
  (parm `viewport_lighting`; the LOP's `lighting` parm is Houdini GL's).
  The farm worker and `tumbleview.husk_env` pass it as
  `TH_PLAYBLAST_LIGHTING` (`viewport_env(..., lighting=)`). A look or node
  from before it existed reads `scene`.
- Not done: light linking, shadows, light filters, shaping (spot cones,
  IES), dome textures, mesh/portal/plugin lights, instanced lights.

### hdThPlayblast: see-through surfaces

Each mesh carries one opacity (`ThMeshView::transparency`, one minus it,
so a zeroed view is opaque; `raster::Mesh::opacity` in the core).

Where it comes from (the shim, `cpp/delegate.cpp`): the bound material's
surface shader when it says something about it, else the mesh's
`displayOpacity` primvar (its mean, whatever its interpolation; 1 when
unauthored). `ThMaterial::_ReadOpacity` reads, in the render context the
material colour comes from (`kma`, then `mtlx`, then the universal one):

- `opacity` (UsdPreviewSurface float; MaterialX standard_surface color3,
  taken as its mean) or OpenPBR `geometry_opacity`;
- `transmission` (standard_surface, what Karma's material builder writes)
  or OpenPBR `transmission_weight`, as see-through: the opacity times
  `1 - transmission` (clamped to [0, 1]);
- UsdPreviewSurface `opacityThreshold` above 0, a cut-out: an opacity under
  it draws nothing, at or over it opaque.

An `opacity` wired to a primvar reader of `displayOpacity` uses the
primvar (times the transmission factor). Wired to anything else (a
texture's alpha, a procedural) it is not evaluated and the surface stays
opaque, as before; so does a texture's own alpha channel.

How it is drawn (`raster.rs` `Surface`, `composite.rs` "See-through
surfaces", `gpu.wgsl` `fs_glass`, `post.wgsl` `accumulate`): opacity 1 (or
more, or NaN) is opaque and drawn exactly as before (the output of a scene
without see-through meshes is unchanged to the bit, and the GPU path then
allocates nothing more). Opacity 0 or less is not drawn and cannot be
picked. In between, after all the opaque surfaces, each sample a
see-through surface reaches in front of the opaque depth is lit there (the
same lights and shading mode as the post pass, with no ambient occlusion)
and added to that sample's **weighted blended order-independent
transparency** sums with every weight 1: `sum(a * colour)`, `sum(a)` and
the revealage `prod(1 - a)`. The post pass then makes the sample
`behind * reveal + sum(a * colour) / sum(a) * (1 - reveal)`, where behind
is the opaque surface's lit, occluded colour or the backdrop. One layer is
exact (`a * colour + (1 - a) * behind`); several are order independent
(the sums commute, so only rounding depends on the order), and the
revealage, so how much of what is behind shows, is exact. Limits of that
choice:

- Stacked layers of different colours blend to their opacity-weighted
  mean colour, not front-over-back: a red pane in front of a blue one looks
  the same as blue in front of red. For a cornea over an iris (one layer
  over an opaque surface) this does not arise.
- See-through surfaces write no depth, receive no ambient occlusion and
  cast none, and the depth AOV is the opaque surfaces' (Houdini's viewer
  composites its grid and handles by it).
- A mesh's front and back faces both blend (unless its cull style culls
  one, see backface culling below), so a closed see-through shell shows
  twice its opacity's worth.

**`th:transparency`** (a string render setting; unset,
`TH_PLAYBLAST_TRANSPARENCY`, read as `th:shading` is): `off` draws every
mesh opaque, whatever its material's opacity or transmission or its
`displayOpacity` (`_PassSettings::opaque`, handed to the core as
`ThFrame::opaque`; `ffi::opacity_of` then gives every view opacity 1, so
an opacity-0 view is drawn too); anything else, `on` by default, is the
blending above. A zeroed `ThFrame` is `on`, so the ABI's old callers draw
as before. It is part of the pass settings, so the viewer's frame key
changes with it. Artists set it as **Transparency** in the farm dialog's
playblast (the look's `transparency`,
`specs.playblast.PLAYBLAST_TRANSPARENCIES`, entity property
`playblast.transparency`) and on the LOP and SOP th::playblast (parm
`transparency`, read with Lighting and Backface Culling by
`tumbleview.node_look_settings`); `viewport_env(..., transparency=)`
passes it to husk. A look or node from before it existed reads `on`
(Discord, Martin 2026-11-12: the corneas are not needed once back faces
are culled). `props.rs`
(`the_abi_opaque_switch_draws_every_view_opaque`) holds `off` to the
picture of an opaque pane for any transparency (NaN, infinite and out of
range included) and `on` to the old `1 - transparency`;
`.ci/smoke_delegate.py` (`_check_transparency`) renders a display-opacity
and a material-opacity pane over a wall on the CPU and the GPU: see-through
by default and with `on`, the opaque pane's picture to the pixel with
`off`.

Alpha (`th:alpha`, a transparent background): a sample nothing opaque
covers gets coverage `1 - reveal`, so see-through surfaces add partial
alpha and the colour stays premultiplied.

Picking (`th_render_ids`, the viewer's selection): the nearest surface a
pixel's first sample sees, see-through or not, so clicking a window selects
the glass, as in Houdini's GL viewer. The CPU path keeps the nearest
see-through depth per pixel (`Samples::near`) for it; the GPU path draws
see-through meshes in its id pass too, after the opaque ones, with the id
pass's own depth.

Motion blur and depth of field need nothing more: each layer blends its own
samples, and the layers average as before.

Exactness: see-through surfaces are not depth-tested against each other,
so a sample on an edge two triangles share must be blended once, not
twice. The CPU rasteriser's coverage test for opaque surfaces (barycentric
weights stepped per sample, inclusive) can give such a sample to both
triangles (the depth test then keeps one) or, rarely, to neither (a
sub-sample crack, seen at 16 samples); see-through surfaces take an exact
edge test instead (`raster.rs` `Edges`: each edge evaluated in one fixed
corner order, with a tie rule), so every sample goes to exactly one
triangle. The GPU's rasteriser does that by itself. On the card the sums
are half floats (`Rgba16Float` / `R16Float`, the formats every adapter can
blend into), which keeps the GPU's picture within 2e-3 of the CPU's.

### hdThPlayblast: materials on faces (GeomSubsets)

A mesh whose faces are bound to materials of their own (a `GeomSubset` of
the `materialBind` family with a `material:binding`, which Hydra hands over
in the mesh topology, `HdGeomSubset::materialId`) is drawn in **parts**,
one `ThMeshView` per material (`ThMesh::_SplitBySubsets`, "Subsets" in
`cpp/delegate.cpp`). Each part is painted exactly as a whole mesh is
(`ThMesh::_Paint`): the material's base colour or texture wins, else the
mesh's display colour, else grey; its opacity the material's, else the
display opacity, so a subset can be see-through while the rest is opaque.
Faces in no subset, or in a subset bound to nothing, keep the mesh's own
material.

The design, and why:

- **Parts are runs of the triangles.** Once every per-corner value is made
  (normals, display colour, uvs), the triangles are sorted by material
  (stable), and each part is a run of them: a pointer and a count into the
  same arrays, so the points are shared and nothing is copied per part. The
  face each triangle came from decides its part, so triangulation (fan or
  ear clipping) and holes change nothing; a refined subdivision surface's
  faces carry their cage face (a uniform value carried down the refiner's
  levels, `_Refine` with `_Rate::Uniform`).
- **One mechanism for colour, texture and opacity.** A per-face colour
  through the corner colours would have covered constant colours only; a
  textured or see-through subset needs a view of its own anyway (a view has
  one texture set and one opacity), so every subset material gets one.
- **No crease where parts meet.** Normals worked out per part would smooth
  each over its own faces only, a visible seam along every material border
  on a smooth surface. So a mesh without normals of its own that is drawn in
  two or more parts gets the whole mesh's normals from the core,
  `th_mesh_normals` (cusp normals for a polygonal mesh, smooth point normals
  at each corner for a subdivision surface, the same functions the core
  uses), as explicit corner normals, remade whenever its points move
  (`_UpdatePoints`). `props.rs`
  (`a_mesh_drawn_in_parts_is_the_whole_mesh_coloured_by_part`) holds a mesh
  in parts to the bit to the whole mesh drawn once with each corner coloured
  by its part, cusp or smooth; without the whole mesh's normals it fails.
  `gpu.rs` (`a_mesh_in_parts_matches_the_cpu`) holds the GPU to the CPU.
- **Keys:** part 0 keeps the mesh's key, the others get keys of their own
  (`_partKeys`, never reused), so the GPU cache keeps each part's corners.
  Every part is picked as the mesh (its primId).
- A mesh whose subsets bind no material, or whose every face keeps the
  mesh's material, is one view exactly as before: a scene without subset
  materials renders bit for bit as before.

Limits: a moving mesh drawn in parts with motion blur takes the normals of
the frame's points at every moment of the shutter (a whole mesh's are
worked out from the shutter's opening); overlapping subsets (invalid USD)
give a face its first subset's material.

### hdThPlayblast: backface culling

Hydra's cull style is resolved as Storm resolves it (`ThMesh::_CullCode`):
the prim's style (`delegate->GetCullStyle`, synced on `DirtyCullStyle`), or
the render pass state's when the prim's is DontCare; `BackUnlessDoubleSided`
and `FrontUnlessDoubleSided` cull only a mesh that is not `doubleSided`
(`DirtyDoubleSided`). The result goes to the core as `ThMeshView::cull` (0
nothing, 1 back, 2 front; `raster::Cull`). The pass's style is part of the
viewer's frame key, so changing it redraws.

- **Front:** counter-clockwise triangles (the shim winds a left-handed
  mesh's the other way), seen through the instance's transform: a
  mirroring transform (negative determinant, `math::det3`) swaps front and
  back (`Cull::through`), so the front stays outside, as in Storm.
- **CPU:** `Raster::draw` drops a triangle by the sign of its screen area
  before any sample is touched; a culled triangle writes nothing, picking
  ids included. Per instance the side is worked out once (`job_culls`).
- **GPU:** the hardware culls (`cull_mode`, `FrontFace::Ccw`): every scene,
  id and see-through pipeline exists per side (`Culled`), and each mesh's
  instances are drawn in runs culling the same side (`cull_runs`), so a
  mirrored instance gets the other pipeline. No discard in the shader, so
  early depth testing stays.
- **What culls:** husk's pass says Nothing and USD prims DontCare, so
  playblasts and the farm cull nothing unless asked (below). A draw
  mode's cards ask for Back. What the Houdini viewer's pass asks for (its
  backface culling display option) has not been checked in the GUI.
- **`th:cull`** (a string render setting; unset, `TH_PLAYBLAST_CULL`, read
  as `th:shading` is): `back` gives every mesh view cull 1 (back) whatever
  Hydra's styles or `doubleSided` say (`_PassSettings::cullBack`, passed
  through `_Gather` to `ThMesh::Views`); anything else, `off` by default,
  is the resolution above. It is part of the pass settings, so the
  viewer's frame key changes with it. Artists set it as **Culling** in
  the farm dialog's playblast (the look's `cull`,
  `specs.playblast.PLAYBLAST_CULLS`, entity property `playblast.cull`)
  and **Backface Culling** on the LOP and SOP th::playblast (parm
  `cull`); `viewport_env(..., cull=)` passes it to husk. A look or node
  from before it existed reads `off`. `.ci/smoke_delegate.py`
  (`_check_cull`) renders a single-sided quad from the front and from
  behind on the CPU and the GPU: with `back` the one seen from behind is
  gone and the one seen from the front is the unculled picture to the
  pixel.

`props.rs` (`culling_drops_exactly_the_instances_showing_the_culled_side`)
holds the picture to the bit to the instances left drawn with nothing
culled, for turned, mirrored and reversed quads, which side faces the
camera judged from the normal carried through the transform (not from
screen winding); it fails with the winding rule inverted or without the
mirror swap. `cull_through_the_abi` holds the codes. `gpu.rs`
(`culling_matches_the_cpu`) holds the card to the CPU, ids included; it
fails with the faces swapped or without the per-instance runs.

### hdThPlayblast in Solaris's viewer

`UsdRenderers.json` (repo root, staged by `[stage] include`) puts the
delegate in the scene viewer's renderer menu as **TumbleView**. The name is
spelled three times and the three must agree: the entry's `menulabel`,
`displayName` in `src/delegate/cpp/plugInfo.json.in`, and the name
`delegate.cpp` registers the implicit-surface scene index under (Hydra
matches scene-index plugins by display name, so a mismatch silently drops
cubes and spheres from the frame). The plugin type,
`HdThPlayblastRendererPlugin`, is what husk's `-R` and farm jobs name and
does not change with it. `tests/test_viewport_renderer.py` holds the three
together. Houdini merges every
`UsdRenderers.json` on `HOUDINI_PATH`; the keys it reads (libHUSD) are
`valid`, `menulabel`, `menupriority`, `needsdepth`, `needsselection`,
`depthstyle`, `defaultpurposes`, `allowbackgroundupdate`, `pauseonupdate`,
`aovsupport`, `drawmodesupport`, `showhydraprocedurals`,
`showinviewportmenu`, `destroyifdeactivated` and `preloadlibraries`. Ours:
`depthstyle "linear"`: in the viewer (`houdini:viewport`) the shim sets
`ThFrame::linear_depth` and the core writes depth as distance along the
camera's view axis (`ffi::linear_depth`; nothing drawn is `FLT_MAX`), while
husk and the farm keep Hydra's window depth. `props.rs`
(`linear_depth_is_the_distance_along_the_view_axis`) holds it for any
camera, near and far plane (at infinity too) and orthographic views, on
both rasterisers (the viewer draws on the GPU by default);
`linear_depth_undoes_the_projection_and_keeps_the_order` holds the
conversion over the whole depth range, out to what f32 window depth can
still tell apart, and keeps near before far for what Houdini composites
over the scene. Houdini 22 decodes `"opengl"`
depth as near / d rather than through the projection it hands the delegate
(near 1, far at infinity), so every surface read as about one unit away and
the View tool's Set Pivot tumble pivoted on the camera; measured live, the
pivot sat at 1 / d of the depth written. `"linear"` is Karma's style and
leaves nothing to decode. A new depth buffer is cleared to nothing drawn
when bound, not left at zeros, so a pivot picked before the first frame is
published is not at the camera either. The rest:
`needsselection` (Houdini draws the outline from the primId/instanceId
AOVs, over a transparent background), guides and proxies by default.

`drawmodesupport true`: models with `model:drawMode` (bounds, origin,
cards) and `model:applyDrawMode` draw as their stand-ins in the viewer, as
in Storm, rather than as full geometry. The flag only asks Houdini's viewer
(libHUSD) to switch the draw mode scene index on; husk never does (no flag,
whatever the json says), so playblasts and the farm draw full geometry as
before. UsdImaging builds the stand-ins and the delegate draws what it is
handed: bounds and origin are linear basis curves named `boundsCurves` and
`originCurves` under the model, width 1 and meant as one-pixel lines, so
`ThStrokes` drops their widths (`_DrawModeStandin`) and draws them at the
minimum pixel width; drawn with their width they were a solid block. Cards
are a mesh `cardsMesh` with a GeomSubset per side, each bound to a
UsdPreviewSurface reading its `model:cardTexture<Side>` through the primvar
`cardsUv` (no texture-coordinate role), which is why `ThMesh` also reads
whatever uv primvar the mesh's or its subsets' materials name, and why cards
rely on materials on faces. They need the model's `extentsHint`; without it
UsdImaging makes every point zero. Checked with `usdrecord --renderer
TumbleView` (UsdImagingGL applies draw modes; husk does not): bounds a
one-pixel wire box, cards one texture per side. Not driven in the Houdini
GUI. Known gaps: a card texture given as a relative path is used unresolved;
a material whose uv primvar name changes does not rebuild the meshes using it.

The viewer reads the AOVs through `HUSD_RenderBuffer::map()`, CPU memory, so
the buffers stay host buffers. It sets `houdini:viewport` (bool) among the
render settings; husk does not (`TH_PLAYBLAST_SETTINGS=1` prints what a host
set). That decides `th:interactive`'s default:

- **Off (husk, farm):** `ThRenderPass::_ExecuteNow`, synchronous as before.
- **On (viewer):** `_ExecuteInBackground`. The pass keys a frame on size,
  view, projection, settings, render tags, the delegate's edit count and the
  buffers' identity and allocation generation. A changed key gathers a
  `_Job` (views plus owned copies of what they point to) and hands it to the
  pass's thread (`ThRenderThread::Request`): a draft (one sample, no
  occlusion or blur) when the last full frame took over `_draftAboveMs`
  (20 ms), then the full frame. The new request replaces the frames still
  queued, but the frame being drawn is finished, not cancelled, unless it is
  a full frame and the new request starts with a draft (or the size
  changed). Cancelling whatever was drawing starved the viewer: each camera
  move cancelled a draft that was nearly done, and nothing showed until the
  camera stopped. `IsConverged` is false until the newest request's full
  frame is published. A mesh or material `Sync` and every `Destroy*` call
  `_Workers::Edited()` first, which bumps the edit count and stops all
  background frames, so no frame reads a prim while Hydra changes it
  (HdEmbree's rule). A camera or instancer `Sync` only bumps the count
  (`_Workers::Changed()`): a frame copies the camera (`_MakeFrame`) and the
  instance transforms (`_Gather`), so gathering the next frame while one
  draws is safe, except across a shutter change, where gathering rewrites a
  moving mesh's shutter points (`ThMesh::_Motion`) and the pass stops the
  thread first. Frames serialise on the shared geometry cache through
  `cacheMutex`. The thread draws into its own image and swaps it with the
  newest finished one, and `Take` swaps that into the pass, so the buffers
  are allocated once; `_Publish` copies whole rows into the delegate's own
  AOV formats (Float32Vec4, Float32, Int32), flipping them.
- **GPU in the viewer:** `th:gpu` (bool; unset, `TH_PLAYBLAST_GPU`, else on
  for an interactive pass and off for husk) sets `ThFrame::gpu` on every
  frame, and the core draws on the GPU where it was built with it and an
  adapter works (CPU otherwise). husk and the farm stay on the CPU unless
  asked, so their frames match a worker with no GPU.
- **Cancellation** goes through `th_render_ids(..., cancel)`: the Rust side
  installs a thread-local `cancel::Token` for the call, the rasteriser
  checks it between band batches and inside the pool, the post pass skips
  occlusion, and a cancelled call returns `TH_CANCELLED` (3) without
  writing the caller's buffers.
- **Ids:** `th_render_ids` also writes, per pixel, the index of the caller's
  mesh and the instance seen by sample 0 of the first layer (-1 for none),
  remapped past the views `th_render` skips; the shim maps them to Hydra's
  primId and (for instanced meshes) instanceId. The shim asks for ids only
  when a primId or instanceId buffer is bound (the viewer's picking), so a
  husk or farm frame does not pay for them. The GPU path makes them too: a
  second pass over the first layer's draws writes mesh and
  instance into an `Rg32Sint` target with its own depth test, in the same
  order as the scene pass, so it keeps the same surface. (A third target
  on the scene pass would exceed the 32 bytes a sample wgpu allows.)
- **Cusp and smooth normals** are kept in the delegate's `ThCache`
  (`normals.rs`) for meshes with a key, while the key's version and the
  point and triangle arrays stay the same: cusp normals (per corner) for a
  polygonal mesh, smooth ones (per point) for a subdivision surface, either
  only where the mesh has no normals of its own. The misses are worked out a
  mesh at a time across the pool's threads, and meshes a call does not draw
  are let go. A draft of a still scene then no longer pays for them.
- **The CPU path's memory** is kept in `ThCache` too (`raster::Scratch`):
  the render buffers of the last two frame shapes (draft and full) and an
  `Arena` of the instances' transformed vertices, grown but never freed or
  cleared. A reused buffer clears only depth and ids (depth says what was
  drawn, and the occlusion blur tests drawn samples by their distance, not
  by a normal left from an earlier frame). Per-frame allocations, their
  page faults and their frees had been a quarter of a draft.
- **Culling:** an instance whose bounding box lies wholly beyond one plane
  of an unblurred pass's view is neither transformed nor binned. That is
  only a shortcut: the far plane is kept per sample on both paths (the
  CPU's depth test takes only `0 <= z < 1` against a buffer cleared to 1,
  the GPU's hardware clips at w and tests Less against 1), so of a
  triangle crossing it, the part beyond shows the backdrop and is never
  picked (`props.rs` `what_is_past_the_far_plane_is_not_drawn`, `gpu.rs`
  `the_far_plane_cuts_as_on_the_cpu`). The rasteriser fetches a triangle's corner attributes only once one of its
  samples passes the depth test, and clips on the stack.
- **The GPU path** reads colour, depth and ids back in one submission and
  one wait, each copied once out of the mapped buffer.

`TH_PLAYBLAST_TIMING=1` logs each viewer frame (`viewport draft|frame WxH,
N samples + AO, M meshes: X ms (core Y)`), each Execute that started or
published a frame (`execute WxH started published draft: X ms on the
viewer's thread (refine, stop, gather, take, publish), shown N ms after the
request; M ms since the last execute`) and the core's normals (`N meshes
worked out, M kept`).

`cargo run --release --example viewport_bench [--features gpu] -- --size
1065x599 --scene medium --only draft [--gpu] [--no-ids]` in `src/delegate`
times the core through the C ABI as the viewer calls it (kept cache, ids,
transparent background) on a generated set orbited by the camera (`small`,
`medium` with 4.5M triangles, 6.6M drawn, `large`). On an RTX workstation
at the viewer's 1065x599, medium: a draft takes 40 ms on the CPU (150 ms
before this pass) and 14.5 ms on the GPU (40 ms), a full frame 173 ms on
the CPU (480 ms).

To drive the viewer itself: orbit a Camera LOP the viewport looks through
(`vp.setCamera(path)`) from a `hou.ui.addEventLoopCallback`. A default
camera moved through HOM never reaches Hydra.

The viewer's render stats (Display Options) come from
`ThRenderDelegate::GetRenderStats`: `rendererName` (TumbleView), and as
`rendererStage` one line about the last frame put in the buffers (full or
draft, samples, meshes, triangles, and for subdivision surfaces the refine
level asked and drawn), with each value under its own key too.

### hdThPlayblast: subdivision surfaces

A mesh whose scheme is `catmullClark` or `loop` (USD's fallback is
`catmullClark`) is drawn refined; `none` and `bilinear` (what implicit
cubes become) are polygonal and drawn as authored. `delegate.cpp`
(Subdivision) refines uniformly with OpenSubdiv 3.7 through USD's
`PxOsdRefinerFactory`, which reads the scheme, creases, corners, holes and
boundary rules from the topology as Storm does, and draws the last level's
faces. Display colour and uvs are carried down at their own rate (uniform,
vertex, varying; each face-varying primvar is a channel of its own, its
indices the channel's topology). Authored normals are the cage's, so a
refined mesh drops them and the core smooths its own. The Windows build
links OpenSubdiv's static `libosdCPU_md.lib` from Houdini's dsolib; Linux
and macOS take its symbols from the shared library Houdini has loaded.

How far it is refined:

- **Asked:** `HdDisplayStyle::refineLevel`. The viewer's Level of Detail
  sets it (Level of Detail 1 asks for 0, the cage), and husk's
  `--complexity` (measured on 22.0.368: `low` 0, `medium` 2, `high` 4,
  `veryhigh`, the default, 8). `_kMostRefinedFaces` (16 M faces a mesh)
  lowers it for a dense cage.
- **Drawn, in Houdini's viewer** (`houdini:viewport`): the level asked, as
  Storm draws it.
- **Drawn, in husk:** no further than the frame shows,
  `ThMesh::ScreenLevel`: the levels that bring the cage's mean edge length
  down to `_kRefinedEdgePixels` (2) at the view depth of its bounds'
  nearest corner (the near plane where it crosses it), through the pass's
  projection and buffer size; 0 when every corner of its bounds is beyond
  one side of the frame (with a 25 % margin for blur) or in front of the
  near plane; for an instanced mesh, its nearest instance in view. Never
  past the level asked. Level 8 refined a 10-face grid into 1.2 M triangles
  and a 50 k-face character to the face cap; screen-sized, the smoke's cube
  draws at level 6, lit, 3 of 19,200 pixels more than 8/255 apart from it
  at level 8.

The level depends on the camera, and a still mesh is not synced again when
only the camera moves, so the pass picks it as it draws:
`ThRenderPass::_SizeRefinement` runs before gathering on both paths,
works out each drawn subdivision surface's level, and rebuilds the ones
whose level changed (`ThMesh::Resize`) through
`HdRenderIndex::GetSceneDelegateForRprim`, several at once
(`WorkParallelForN`, as Hydra syncs them), after `_Workers::Edited()`.
`Sync` itself rebuilds a mesh at the level last picked for it (the cage
until a pass has picked one), and a new refine level asked only records
it. A points-only update refines the new points at the mesh's level and
measures its cage again. `TH_PLAYBLAST_TIMING=1` ends each husk frame's
line with `refine level N, drawn at M` (the highest drawn).

`.ci/smoke_delegate.py` checks it through husk: the Catmull-Clark cube
refined at the default complexity and drawn as its cage at `low`; random
grids that refining must not change (flat Catmull-Clark lattices, uniform
and face-varying colours, holes; polygonal schemes never refined); random
cubes whose drawn level follows the frame (lit, the picture the full
level draws but for a few edge pixels; four times the focal length,
two levels more; four times as deep, two less; scene units scaled with the
camera, the same; out of view, none; an instancer, its nearest copy in
view; square to the camera, edges of at most two pixels); and, with a
RenderSettings prim setting `houdini:viewport`, the level asked on either
path. Mutation-checked: each of the sizing's parts, broken, fails one.

### hdThPlayblast: curves and points

`BasisCurves` and `Points` are drawn as the GL viewport draws them, so a
SOP or APEX playblast through TumbleView shows ropes, whiskers, rig curves
and particles. `delegate.cpp` (`ThStrokes`, as `ThCurves` and `ThPoints`)
hands them to the core as authored, in a `ThCurvesView` each: vertices
(curve indices applied), curve vertex counts, type, basis (`bezier`,
`bspline`, `catmullRom`; `centripetalCatmullRom` as `catmullRom`) and wrap
(`nonperiodic`, `periodic`, `pinned`), `widths` and `displayColor` with
their interpolation (constant, uniform, varying, vertex), the material's
colour in place of display colour as for a mesh, instances, and motion
(points at the shutter's opening and closing from sampled points or
velocities, transforms at keys across it). `th_render_scene` takes them
after the meshes; in its ids curves view `j` is `mesh_count + j`, so
picking and the viewer's highlight name the curves prim and instance.

The core (`src/curves.rs`) makes their geometry for each frame, because it
faces the camera and its thinnest size is in pixels: a curve is a ribbon
of its authored width, never under `MIN_CURVE_PIXELS` (1) across at its
depth; a point a disc of its width, never under `MIN_POINT_PIXELS` (2)
across; so a curve or point with no width is a line or a dot. A ribbon
runs on past each open end by half its width, and by at least
`CAP_PIXELS` / 2 (0.75 px) across the view: a line a pixel wide and about a
pixel long (a fine hair, a short rig curve) could otherwise sit between
pixel centres and draw nothing. A line pointing at the camera, a dot on
screen however long, makes up its cap across the view. It is mitred at
bends and broken at bends sharper than `BREAK_DEGREES` (90) as seen (a
line turning gently in depth can fold right back on screen), each side
capped past the bend; every point makes four vertices either way, so a
shutter's opening and closing line up even when a bend sharpens between
them. The pixel floor is taken where each vertex ends up, so a cap running
deeper into the picture does not thin out. Normals face
the camera, bent across a ribbon and out to a disc's rim like a tube's or
a sphere's, so lit and clay shading round them off and albedo is the flat
display colour. Cubic curves are evaluated to polylines (USD's basis
matrices; a pinned bspline or catmullRom gets USD's phantom end vertices,
`2 P0 - P1`), each span cut into one segment per `SEGMENT_PIXELS` (4) of
its control polygon on screen, at most `MOST_STEPS` (64); vertex widths
and colours follow the basis, varying ones run linearly per segment. The
geometry is world space, one mesh per instance, with no key: every frame
uploads it afresh on the GPU path rather than keeping view-dependent
geometry on the card. With motion blur an instance's geometry at the
shutter's closing (its closing points through its last transform key) is
cut as its opening was (`curves::Plan`), so the two line up vertex for
vertex and blur like a mesh's points. Everything after that (both
rasterisers, ids, cancel, alpha, both depth styles, blur and depth-of-field
layers) is the mesh path. Not drawn: authored curve `normals` (oriented
ribbons are drawn camera-facing), and hair a Houdini procedural would make
(husk does not run procedurals for the delegate).

`props_curves.rs` holds it: the bases' end and pass-through points, span
counts, unbroken, closed and pinned curves; a width-less curve an unbroken
line no more than about a pixel wide at any depth and direction (bent,
sub-pixel or of no length included), authored
widths drawn to size, discs covering their centres and nothing outside
their radius, display colours under albedo, ids naming the curves view and
instance through the ABI, a moving point smearing along its path, and a
closing cut as its opening. `gpu.rs`'s `curves_and_points_match_the_cpu`
and `random_curves_and_points_match_the_cpu` hold the GPU path to the
CPU's picture and ids. These properties are worth shaking with
`PROPTEST_CASES=2000` after a change to `curves.rs`: the sub-pixel cases
(a line about a pixel long between pixel centres, a hairpin seen in
depth, a line pointing at the camera) turned up only there. `.ci/smoke_delegate.py`
draws a wire, a rope and two point sets through husk, on the CPU, the GPU
and the viewer's background path. Mutation-checked: the pixel floor and a
basis matrix, broken, each fail a property.

## The package manifest

`hpm.toml` keeps its comments short; this is the reasoning behind its
less obvious entries.

**`[compat].houdini`** is a Cargo-style requirement whose upper bound is
exclusive: `<23` is the first *unsupported* major. (The pre-Manifest-2.0
`max_version = "22"` compiled to `houdini_version < '22'` and silently
disabled the package on every 22.x build.) Raise it only together with
`HPM_HOUDINI_MAJORS` in `.woodpecker/build-*.yml`; otherwise users on the new
major install cleanly and then fail at runtime, when USD finds no
`resolver/houdini<major>/` tree.

**Build outputs and staging.** `resolver/`, `bin/`, `dso/` and the collapsed
`otls/*.hda` are gitignored, so `.hpmignore` re-includes them for staging —
neither `[stage].include` nor place rules can re-include a gitignored path.
There are no `[stage.platform.*]` place rules: a place `to` is literal, so
`resolver/houdini*/**/` would collapse every file onto one path. The built
trees ship as ordinary content instead, and each worker only ever has its
own platform's build. Only `hpm build` and `hpm run <script>` run
`[stage].prepack`; `hpm pack` packs what is there, which is why
`.ci/build_platform.py` builds first and packs the staged image.

**`TH_PROJECT_PATH`** is a required placeholder with no `value`. A valueless
`required = true` entry means "the consuming project supplies this": an
unset project fails `hpm install`, and TumbleTrove Desktop shows the empty
required field beside the Configure button (`tt_setup`), which emits exactly
this key. The earlier `value = ""` was a *satisfied* entry, so Houdini got an
empty string and `TH_CONFIG_PATH`, `TH_EXPORT_PATH` and `TT_DOWNLOADS_PATH`
collapsed to `/_config`, `/export` and `/library`. A project-level `set`
override suppresses the package entry outright. Farm workers satisfy it
through the synthetic job manifest in `tumblepipe.farm.deadline`; the two
must stay in step, or every farm task fails at `hpm install`.

**`OCIO`** points into the project's `_config/ocio/`, scaffolded from
`scripts/project_template/_config/ocio/`. Houdini expands package variables
top to bottom, so `$TH_CONFIG_PATH` is already set. Keeping the config out of
the package also stops it being path-separator-joined with another package's
OCIO into an unreadable value.

**The resolver variables.** Houdini applies package env edits before USD's
`Plug.Registry` first scans, the only in-process timing that works
(`pythonrc.py` runs too late); `PXR_PLUGINPATH_NAME` is the load-bearing one.
`PATH`, `LD_LIBRARY_PATH` and `DYLD_LIBRARY_PATH` do *not* help the
in-process load on Linux and macOS — the loader reads them once at process
start — which the resolver's `-rpath '$ORIGIN'` / `@loader_path`
(`src/resolver/CMakeLists.txt`) covers instead. They stay for child
processes (husk, hython, farm tasks), which read them at their own start.
`$HPM_PACKAGE_ROOT` is rewritten by hpm at pack time; `$HOUDINI_MAJOR_RELEASE`
and `$HOUDINI_MINOR_RELEASE` pass through and are expanded by Houdini.

**The asset-browser catalog** is declared to TumbleTrove from
`python3.13libs/pythonrc.py` rather than found through
`ASSET_BROWSER_CATALOG_PATH`, so a renamed factory fails loudly and its
settings page is filed under TumblePipe.

**Desktop hooks.** `tt_setup` and `tt_prepare` run prebuilt binaries
directly; the old PySide6 wizard needed a ~100 MB managed venv before its
window could appear. Their program paths are absolute: Windows'
`CreateProcess` resolves a relative program against the *caller's* cwd (the
desktop app's), which failed with `os error 3` in v1.39.2. They are quoted
because HPM's package root contains `@` and may contain spaces. `tt_prepare`
is the migration hook because it runs on every launch — `tt_install` is
per-project and does not re-fire after an upgrade. Scripts with a `label`
are listed as buttons in the desktop's per-project Scripts panel; the hooks
deliberately have none.

## What a release publishes

A version tag starts `.woodpecker/build-{linux,macos,windows}.yml` and then
`release.yml`. Each is a thin wrapper around `.ci/ci.sh`, which logs into
Infisical (every secret comes from there, by its key name), installs hpm and
uv, and runs `.ci/build_platform.py` or `.ci/release_tumblepipe.py`.

To run the tests on all three workers without releasing, start a manual
pipeline with the variable `RUN=tests` (`tests/README.md`, "On the build
workers"). Use it before tagging anything that could behave differently on
Linux or macOS: the release pipeline itself does not test.

Two trees leave the repo, both from hpm.toml's `[stage].include`
(`.ci/_packaging.py`):

- the **hpm archive**, staged by `hpm build` per `[stage].include`/`exclude`;
- the **public source mirror** (`github.com/tumblehead/TumblePipe`): every
  tracked file under `[stage].include`, plus `MIRROR_EXTRAS` (`src/`,
  `.readthedocs.yaml`, `.gitattributes`, `.gitignore`). `[stage].exclude` is
  not applied: the expanded HDA sources and Sphinx files it carves out of the
  archive are what a source mirror is for. The mirror is replaced wholesale on
  each release, so a file removed here is removed there.

Both pass one deny guard (`_packaging.DENY`: CI machinery, assistant
context, credentials) before the archive is signed or the mirror pushed.

The version is then registered with TumbleTrove (`.ci/release_tumblepipe.py`):
one build per platform, the `[compat].houdini` range as display bounds, and
the registry entries of `[dependencies]` as the version's `dependencies`. The
registry serves those as `deps`, and TumbleTrove Desktop chooses a project's
unpinned TumbleTrove and Radial by them at every launch, so **the ranges in
`[dependencies]` are what keep a TumbleTrove release that breaks this version
away from projects on it**. Give `tumbletrove/tumbletrove` an upper bound
whenever the next TumbleTrove may change an API TumblePipe calls: 1.74 is
capped below 0.48 because 0.48 removed the browser API 1.74 used.

## Writing the documentation

`docs/` is plain Markdown and is read by two renderers:

- **Offline, in the package.** The folder ships in the release archive and
  `tumblepipe.startup.register_package` hands it to TumbleTrove as
  `docs=<package root>/docs`. TumbleTrove renders it into a static
  site under the Houdini prefs directory every time someone opens
  **TumbleTrove ▸ Documentation ▸ TumblePipe**, with a sidebar, a per-page
  outline and a top bar linking the other installed packages' docs. The online
  build stays reachable as **TumblePipe: Online documentation** in the same
  menu.
- **Online, on Read the Docs.** Sphinx + MyST builds the same files at
  [tumblepipe.readthedocs.io](https://tumblepipe.readthedocs.io), rebuilt on
  every push to `main` of the public mirror. `.readthedocs.yaml` at the
  repository root and `docs/conf.py` configure it; neither ships in the
  archive.

The offline renderer is
[python-markdown2](https://github.com/trentm/python-markdown2) (pipe tables,
fenced code with a language, heading ids, task lists, footnotes, inline HTML),
so the rules are:

- **No MyST directives or roles** — no `{toctree}`, `{doc}`, `{include}`; a
  fenced directive renders offline as a literal code block. Link pages with
  relative `.md` paths, fragments allowed (`configuration.md#departments`).
- **One `#` title per page**; `##` and `###` become the page outline.
- **`index.md` is the navigation.** A `## Heading` opens a sidebar section; a
  list item linking a `.md` file is a page, nested items nest. Every page
  must be listed there — an unlisted page still renders, filed under a
  trailing *Other pages* section. Sphinx gets its `toctree` from the same
  lists: the `source-read` hook in `docs/conf.py` derives one hidden
  `{toctree}` per section, so there is one navigation to maintain.
  `docs/changelog.md` gets its `CHANGELOG.md` include the same way; offline it
  links to the shipped file instead.
- **Source links** are relative paths climbing out of `docs/`
  (`../python/tumblepipe/startup.py`); offline they become `file://` links
  into the installed package, so they must point at files the archive ships.
- **Images** live inside `docs/`; folders starting with `_` or `.` are
  skipped (`_static/`, `_build/`).

`tests/test_docs_tree.py` renders the tree with TumbleTrove's own renderer
and follows every link, anchor and source link, and fails when a page is
missing from `index.md` or carries more than one title. It needs a
`tumbletrove` with the `docs` module (0.27.0+): the newest one hpm has
fetched, a sibling `tumbletrove` checkout, or `TUMBLETROVE_PYTHON`. It runs
with the rest of the suite (and so on the push hook).

To preview the offline rendering outside Houdini, with a TumbleTrove
`python/` on `sys.path`:

```python
from pathlib import Path
from tumbletrove.docs.render import Book, render_site

build = render_site([Book(key="tumblepipe", name="TumblePipe", root=Path("docs"))],
                    [], Path("/tmp/site"))
print(build.hub)   # open this in a browser
```

Or, in a dev-launched Houdini, open **TumbleTrove ▸ Documentation ▸
TumblePipe** — the site is re-rendered from the installed source on every
open, so an edit shows up on the next click.

To build the Sphinx site locally, from the package root:

```bash
python -m venv .venv-docs
source .venv-docs/bin/activate   # Windows: .venv-docs\Scripts\activate
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

Then open `docs/_build/html/index.html` in a browser.

## License

TumblePipe is released under the [MIT License](https://github.com/tumblehead/TumblePipe/blob/main/LICENSE).
