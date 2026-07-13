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

## Running the test harness

`tests/` ships a property-based test harness for the Python config
layer, using [minigun-soren-n](https://github.com/soren-n/minigun) for
QuickCheck-style generation and shrinking. It has its own uv-managed
venv pinned to Python 3.12+ (Houdini's bundled 3.11 is too old for
minigun) and is independent of `hpm.toml` and the package install. From
the repository root:

```bash
cd tests
uv sync
uv run minigun --test-dir . --time-budget 30
```

`tests/README.md` covers writing new properties and the design of the
project-fixture bootstrap (`_harness.py`).

## Qt widget harnesses

UI behaviour that pins a fixed bug lives in standalone harnesses under
`scripts/` that drive the real widgets — no Houdini required, since the
widget stacks involved are pure qtpy:

- `verify_database_editor_ux.py` — the config editor's commit model
  (click-away/Enter/Tab commit, key renames, reverts).
- `verify_process_dialog_ux.py` — the process dialog's execution UX
  (running-child status label, progress breadcrumbs, the
  cancel-leaves-steps-unrun warning).

They need a desktop session and a Qt binding:

```bash
cd scripts
uv run --python 3.12 --with pyside6 --with qtpy python verify_process_dialog_ux.py
```

## Resolver harness

`scripts/verify_resolver_refresh.py` pins the resolver-refresh contract
(`tumblepipe.resolver.refresh_context()` must float already-composed
stages to newly published versions — the "restart Houdini to see a new
publish" bug). It needs the tumbleResolver plugin, so run it under a
project hython (e.g. TumbleTrove Desktop's run_hython, with dev
overrides for a local build). It sandboxes `TH_EXPORT_PATH` to a
tempdir, so it never touches live project data.

## HDA spare-parm UI harness

`scripts/verify_import_shot_layer_stack.py` pins the import_shot Layer
Stack layout contract (the "parms changing places after import" bug):
calling `hou.Node.setParmTemplateGroup()` on an HDA *instance*
spare-ifies the definition's container folders under renamed names
(`selection` → `selection2`), so folder-name anchor lookups go stale
after the first rebuild. Code that inserts spare UI into an HDA
instance must anchor on definition *parm* names (never renamed) via
`ParmTemplateGroup.containingFolder()`, not on folder names. Run it
under any project hython (e.g. TumbleTrove Desktop's run_hython with
dev overrides); it drives the UI rebuild with synthetic layers and
touches no project data.

## Submit Jobs dialog harness

`scripts/verify_submit_jobs_entity_selector.py` pins the Submit Jobs
dialog's entity-selector contract: single-entity opens show a selector
listing the project's terminal entities (vetted with
`is_terminal_entity`, so empty seeded categories don't appear) that
defaults to the opened entity; picking another entity re-targets the
submission and reseeds the form from that entity's properties;
multi-select opens keep their fixed entity list. It reads a real
project config, so run it under a project hython whose project has at
least one shot and one asset (e.g. TumbleTrove Desktop's run_hython
with dev overrides). Qt runs offscreen; no project data is written.

## Farm task and job modules

Each farm task family under `python/tumblepipe/farm/tasks/<family>/` splits
into a worker-side CLI script (`<family>.py`, run on the render node), a
submit-side task builder (`task.py`), and — where the config schema is shared
between them — a `_spec.py` holding the canonical config validator. The
matching `farm/jobs/houdini/<name>/job.py` imports the same `_spec`.

Two rules keep these importable in the environments they run in:

- **Worker scripts must not import their family's `task.py`.** The task
  builder imports `tumblepipe.farm.deadline`, which needs `tomli_w` at module
  import time — present on submit machines, not guaranteed on workers or dev
  environments. Shared config schemas therefore live in `_spec.py` (which
  only depends on `tumblepipe.farm._common`), never in `task.py`.
- **Generic scaffolding lives in `tumblepipe.farm._common`** (config-check
  primitives, `valid_entity`, `run_task_cli`, `configure_logging`);
  `farm/jobs/houdini/_common.py` re-exports it and adds the job-side
  `submit_batch`/`run_cli`. Don't re-introduce per-module copies.

The farm modules cannot be run-imported outside the farm (Deadline,
`tomli_w`), so gate changes with a compile + lint pass instead. Use the
same selection CI enforces (`.woodpecker/ci.yml` runs it over every
Python tree on tag):

```bash
python -m compileall -q python/tumblepipe/farm/
ruff check --select E9,F python/tumblepipe/farm/
```

## Building the package locally

`hpm build` produces the install image: it runs the `[stage].prepack`
scripts (`build-resolver` cmake-builds the tumbleResolver USD plugin into
`resolver/houdini<major>/`; `compile-hdas` collapses the expanded
`otls/<name>/` sources into binary `.hda` files) and stages the tree per
`[stage].include`/`exclude`. Requirements: cmake, a Rust toolchain, and at
least one Houdini install.

By default the resolver builds every major listed in
`resolver-src/houdini_majors.toml`, which fails on a machine that doesn't
have them all installed. Restrict the build to the majors you have with
`HPM_HOUDINI_MAJORS` (bare space-separated majors, e.g. `22` or `21 22`):

```bash
HPM_HOUDINI_MAJORS=22 hpm build --platform windows-x86_64
```

`hpm build --houdini-majors "22"` (hpm ≥ 0.27) and dev-package launches
from TumbleTrove Desktop (≥ 0.38, which sets the variable from the Houdini
versions it discovers) do the same thing. Release CI always builds the
full matrix — see `.woodpecker/build-*.yml`.

## Building the documentation locally

The docs are written in [MyST Markdown](https://myst-parser.readthedocs.io)
and built with [Sphinx](https://www.sphinx-doc.org). From the package root:

```bash
python -m venv .venv-docs
source .venv-docs/bin/activate   # Windows: .venv-docs\Scripts\activate
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

Then open `docs/_build/html/index.html` in a browser.

## Documentation hosting

The rendered docs live at
[tumblepipe.readthedocs.io](https://tumblepipe.readthedocs.io). They are
rebuilt automatically on every push to `main` in the public mirror repo.
The RTD build configuration is `.readthedocs.yaml` at the repository root.

## License

TumblePipe is released under the [MIT License](https://github.com/tumblehead/TumblePipe/blob/main/LICENSE).
