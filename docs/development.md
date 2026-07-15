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

`scripts/verify_submit_jobs_entity_tree.py` pins the Submit Jobs
dialog's entity-tree contract: every open shows a checkable tree scoped
to the dialog's context, listing that context's terminal entities
(vetted with `is_terminal_entity`, so empty seeded categories don't
appear) and starting with the opened entities checked. Checking more
entities fans the submission out to all of them; branch checks cascade
and roll up to a partial state; an entity that also appears under a
group is submitted once, not once per group; the filter narrows the
view without touching check state; and reseeding is keyed on the
primary (first checked) entity, so growing the batch doesn't clobber a
tuned form. It also pins the coherent-read contract: the sweep vets
every URI with `is_terminal_entity` (one read each), so it runs inside
a `config.coherent()` scope and the harness counts config stats to
catch a regression back into the stat-storm bug class (see the config
engine notes in `configuration.md`). It reads a real project config, so
run it under a project hython whose project has at least two shots and
one asset (e.g. TumbleTrove Desktop's run_hython with dev overrides). Qt runs
offscreen; no project data is written and nothing is submitted. It also pins
the shots-only **Playblast** section (present for shots, absent for assets,
department list = renderable shot departments, opt-in default).

`scripts/verify_playblast_job.py` pins the farm playblast job family:
the task/job config validators, and that the versioned playblast + rolling
daily paths resolve **under the shot's department** (the arity bug the
department fix closed stays closed). It builds the batch when run from an
installed package and skips that structural check on a dev checkout (hpm
won't ship a Task from an editable tree). Run it under a project hython with
at least one shot.

## Entity `from_context` audit

`scripts/verify_entity_from_context.py` pins the contract that every `th::`
HDA which addresses a pipeline entity leaves its `entity`/`shot`/`asset` parm
on the `from_context` sentinel, so the node resolves its entity from the
workfile it lives in every time it is evaluated. A concrete URI in that parm
pins the node to whichever entity it was *born* in: copy the scene to another
asset, rename the entity, or build a shot from a template, and it keeps
publishing to the old one.

It checks the three ways that contract has been broken:

1. **Parm defaults** — an entity-addressing parm whose default is a concrete
   URI, or the empty string. Empty is not neutral:
   `EntityNode.get_entity_uri()` resolves an unset parm to the *first entity
   in the project*, so the node silently addresses an arbitrary asset and
   looks in the UI exactly like a deliberate choice.
2. **`on_created` hooks** — a wrapper that writes a URI into the parm at node
   creation, defeating the sentinel it just defaulted to. Both shapes count:
   baking the workfile's own URI, and pinning the first entity in the project.
3. **Department templates** — the single-entity branch (`_create_entity`)
   stamping a specific entity URI. Only the multi-entity `_create_group`
   branch needs to: a group workfile holds several entities at once, so
   `from_context` cannot resolve to one of them.

It reads the expanded `otls/` DialogScripts and the Python wrappers as text,
so it needs no Houdini and no project:

```bash
cd tests
uv run python ../scripts/verify_entity_from_context.py
```

Prefer it over grepping — the baking shows up in several shapes that a
hand-written search misses.

## Department pool audit

`scripts/verify_entity_departments.py` reports a project's department pool in
**pipeline order** (which is what the pool's key order *is* — see *Departments*
in {doc}`configuration`), the entities scoped to a subset of it, assignments
naming a department the pool no longer has, and departments with work on disk
that their entity is not scoped to. It is read-only, so it is safe against a
live project; run it before and after migrating one to config v3.

```bash
cd tests
uv run python ../scripts/verify_entity_departments.py P:/paleindia
```

With no argument it audits `$TH_PROJECT_PATH`.

## Changelog

`CHANGELOG.md` is **generated — never hand-edit it**. It is derived from
the conventional-commit subjects between release tags, one section per
tag, newest first:

```bash
uv run --no-project python scripts/generate_changelog.py
uv run --no-project python scripts/generate_changelog.py --check  # stale?
```

The section layout lives in `.ci/_changelog.py`, shared with
`.ci/release_tumblepipe.py` so the file and the notes posted to the
github release / TumbleTrove version page can't drift. Commits are
bucketed by prefix (`feat` → Features, `fix` → Fixes, …); a `!` marks a
breaking change (`refactor!: …`) and leads the release; `ci`, `build`
and the `release` version-bump commit are excluded. Anything that
doesn't parse still lands under *Other Changes* rather than being
dropped, so a forgotten prefix can't lose a change — but it is worth
writing the prefix.

**Ordering gotcha:** the script reads the *tags*, so a release's section
can only be rendered once its tag exists. Regenerate and commit
**after** `git tag`, not before — that commit then rides along in the
next release. This is why `--check` is not a CI gate: it would
false-fail on the commit immediately after every tag.

## Export cache-reference harness

`scripts/verify_cache_reference_fixes.py` pins the publish-by-reference
contract for versioned `th::cache` files (see *Layer save paths and
export portability* in `composition.md`): arcs into a cache root are
pinned absolute (`_absolutize_cache_arcs`), skipped by the sidecar
localizer (`skip_roots`), and exempt from the escaping-path guard
(`allowed_roots`) — while a missing cache file still aborts as a
dangling arc. It needs `pxr` but no scene or project data (temp dirs
only), so run it under any project hython (e.g. TumbleTrove Desktop's
run_hython). The pure path-classification half is covered by
`tests/test_usd_paths.py`.

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
line) and `set(1)` on the `usd_rop` in `farm/jobs/houdini/update`.

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
