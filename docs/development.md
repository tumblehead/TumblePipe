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

On Windows that dies in the reporter, not the tests:

```
UnicodeEncodeError: 'charmap' codec can't encode character '∞'
```

minigun's execution-plan table prints `∞`, and the console lands on
cp1252. It says nothing about whether the suite passes. Force UTF-8:

```bash
PYTHONIOENCODING=utf-8 uv run minigun --test-dir . --time-budget 30
```

`tests/README.md` covers writing new properties and the design of the
project-fixture bootstrap (`_harness.py`).

## Linting

Python is linted with [ruff](https://docs.astral.sh/ruff/) under the
`E9,F` selection — syntax errors plus the full Pyflakes set (undefined
names, unused imports, f-strings without placeholders). That net is what
catches a shipped-broken import or a stray `f''` before it reaches a
release. Run it over the same trees the gate covers:

```bash
uvx ruff check python/ asset_browser_catalogs/ .ci/ python3.11libs/ \
  python3.13libs/ viewer_states/ scripts/ tests/ tools/ --select E9,F
```

This runs automatically as a **pre-commit hook** (`.githooks/pre-commit`),
which blocks a commit that fails the lint. `core.hooksPath` is a local git
setting, so enable it once per clone:

```bash
git config core.hooksPath .githooks
```

The same hook also runs `.ci/quality_gates/check_forbidden_files.py` over
the **staged** `*.md` files, blocking a commit that adds an AI-generated
report/summary (matched by content markers, not filename). It checks only
staged files on purpose — an untracked scratch report in the working tree
must not block an unrelated commit. Run with no arguments (as CI/preflight
does) it scans the whole tree instead.

The hook skips (with a warning) if `uv`/`ruff` isn't on `PATH` rather than
blocking, and `git commit --no-verify` bypasses it for a single commit.
The release *tag* pipeline no longer lints — it only builds and publishes
(`.woodpecker/`) — so this hook, and a local run before tagging, are the
gate. The binary-HDA check (`check_binary_hdas.py`) is deliberately *not*
in the hook: it flags binary `otls/*.hda`, which are gitignored local
build artifacts, so it only makes sense from a clean clone.

## Qt widget harnesses

UI behaviour that pins a fixed bug lives in standalone harnesses under
`scripts/` that drive the real widgets — no Houdini required, since the
widget stacks involved are pure qtpy:

- `verify_database_editor_ux.py` — the config editor's commit model
  (click-away/Enter/Tab commit, key renames, reverts).
- `verify_process_dialog_ux.py` — the process dialog's execution UX
  (running-child status label, progress breadcrumbs, the
  cancel-leaves-steps-unrun warning, exported-version reporting, and the
  skip-vs-fail split described below).

They need a desktop session and a Qt binding:

```bash
cd scripts
uv run --python 3.12 --with pyside6 --with qtpy python verify_process_dialog_ux.py
```

## Skipping a task instead of failing it

A task callback that finds it has **nothing to do** raises
`tumblepipe.util.errors.TaskSkipped` instead of returning or blowing up.
`ProcessExecutor` marks that task SKIPPED with the exception message as its
reason and carries on — crucially, a skipped *child* does not abort its
siblings, whereas any other exception fails the whole group. The canonical
case is an export node left disconnected in the network: its stage is `None`,
so there is nothing to publish, and the other render variants in the same
department must still export.

Two rules:

- Raise it only for a genuine no-op. Anything that would publish a wrong or
  empty layer must still fail loudly — a skip is quieter than a failure, so
  reaching for it to quieten a real problem buries it.
- Every skip carries a reason, and reasons are surfaced: after an otherwise
  clean run the dialog warns with the list of skipped steps. Without that,
  "All tasks completed" over a silently missing variant reads as success.
  (Tasks the artist unchecked are SKIPPED too, but carry no reason and stay
  out of the warning.)

`TaskSkipped` lives in `util.errors`, not in the `ui` package, so lops and
farm-side code can raise it without importing Qt. Note that the executor
tests for it *before* resolving `ValidationCancelled`, because that import
pulls in the validators and `pxr`.

The farm path deliberately does **not** skip: a worker hitting the same
disconnected node still fails its job, since nobody is watching a farm log to
notice a skip. It does report it legibly though — `farm/tasks/export/
export_houdini.py` returns a named error rather than dying on
`None.GetPseudoRoot()`.

Note that `node.stage()` returning `None` is a recurring shape, and several
other call sites still dereference it unguarded (`lookdev_studio`,
`render_stage`, the two `playblast` nodes). They are not part of this fix;
each needs its own decision about whether a missing stage is a skip or an
error.

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

`scripts/verify_asset_payload_fixes.py` pins the two asset-payload fixes
against regression. It is the **only** coverage of either, so run it after
touching `th::asset_payload` or `export_layer`'s publish path:

- the `th::asset_payload` primpath duplication — it composes
  create_asset → create_asset_model → geo → asset_payload in a scratch
  subnet and asserts no duplicated `/char/test/test` prim appears. The fix
  it guards (`primpath1` = `` `lopinputprim('../payload_layer', 0)` `` rather
  than `` `@sourcename` ``) is still what ships.
- `export_layer._localize_external_sidecars` — it crafts a layer whose
  payload arc points at an external `payload.usd` and asserts the sidecar is
  copied beside the layer and the arc rewritten to the bare relative form.
  That function is live (called from `export_layer`'s publish), and the
  `tests/` suite does not cover it.

Unlike the audits below it needs a live Houdini: it imports both `hou` and
`pxr`, and `pxr` is not in the `tests/` venv, so it cannot run there. Drive it
through TumbleTrove Desktop's `sessions_exec_python`, or paste it into a
Houdini Python Shell and call `main()`. Each check prints PASS / FAIL / SKIP
and degrades to SKIP rather than failing when a prerequisite is absent. A
third end-to-end export check exists but is off behind `RUN_EXPORT = False`
because it publishes a real version — only enable it with a throwaway entity.

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

## HDA callback audit

`scripts/verify_hda_callbacks.py` pins the contract that every HDA callback
reaches a function that exists. A parm's callback does not call
`python/tumblepipe/…` directly — the HDA's own `PythonModule` section sits in
between as a hand-maintained shim of one-line forwarders:

```text
DialogScript            PythonModule            python/tumblepipe/…
hou.phm().select()  ->  def select():       ->  def select():
                            th_cache.select()       …
```

Three files, and the middle one is the one nobody edits. Add the parm and the
backing function and both ends look finished while the button is dead: a
callback is a *string*, so nothing resolves it until an artist clicks and gets
`AttributeError: 'module' object has no attribute 'select'`. That is how
`th::cache` shipped its Entity button broken on both the SOP and the LOP
(93e4dc1 touched only the two DialogScripts). No lint or build gate resolves
callbacks, so nothing else catches this — run this audit after editing one.

It checks both directions of the shim:

1. **Dangling callback** — a name a DialogScript calls that the `PythonModule`
   does not define, including the case of no `PythonModule` section at all.
2. **Dangling forwarder** — a forwarder whose backing function is gone from
   the module it delegates to. This is the near-miss class (`get_asset_uri` vs
   `get_entity_uri`): the wrapper gets renamed and the shim keeps calling the
   old name.

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
in {doc}`configuration`), the entities scoped to a subset of it, assignments
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
reverse), and leftover reservation stubs. `--repair` fixes only what is broken
— it never rewrites a valid, possibly non-consecutive `from_version` — and
backs `_context`/`context.json` up first. `--dry-run` reports what it would do.

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

## Editing an HDA definition

Reach for this when a fix lives inside an HDA rather than in `python/`. The
tracked source of truth is the expanded `otls/<name>/` directory; the `.hda`
beside it is a gitignored build artifact.

- **Expand with `hotl -t`, never `hotl -x`.** `-t` writes the uncompressed
  `Contents.dir/Contents.mime` the repo tracks and round-trips **losslessly**
  (zero diff), so an HDA edit produces a reviewable diff of just the change.
  `-x` writes a compressed binary `Contents.gz`, which still builds but is
  undiffable. `compile-hdas` collapses the other way with `hotl -l`.
- **Edit the definition's interface, not an instance's.** Calling
  `setParmTemplateGroup()` on an HDA *instance* and then `updateFromNode()`
  silently fails to carry nested multiparm children. Use
  `definition.parmTemplateGroup()` / `definition.setParmTemplateGroup()`. And
  order matters: do `updateFromNode()` for *contents* first, since it replaces
  the whole definition and would clobber an interface edit made before it.
  (For inserting *spare* UI into an instance, see *HDA spare-parm UI harness*
  above — a different hazard with the same call.)
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
same selection the pre-commit hook enforces (see *Linting* below — the
`E9,F` set over every Python tree):

```bash
python -m compileall -q python/tumblepipe/farm/
ruff check --select E9,F python/tumblepipe/farm/
```

## Building the package locally

`hpm build` produces the install image: it runs the `[stage].prepack`
scripts (`build-resolver` cmake-builds the tumbleResolver USD plugin into
`resolver/houdini<major>/`; `build-wizard` cargo-builds the tt_setup
project-setup wizard into `bin/<platform>/`; `compile-hdas` collapses the
expanded `otls/<name>/` sources into binary `.hda` files) and stages the
tree per `[stage].include`/`exclude`. Requirements: cmake, a Rust
toolchain, and at least one Houdini install. (The wizard is
Houdini-independent — `build-wizard` needs only cargo, no HFS.)

By default the resolver builds every major listed in
`src/resolver/houdini_majors.toml`, which fails on a machine that doesn't
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
