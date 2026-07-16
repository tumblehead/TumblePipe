# Deadline and the render farm

TumblePipe includes a `tumblepipe.apps.deadline` module for submitting jobs
to [Thinkbox Deadline](https://www.awsthinkbox.com/deadline), and custom
Deadline plugins for running its Python farm tasks on workers with the same
source and environment the job was submitted with.

## Plugins: HPM (default) and UV (legacy)

Farm tasks live inside the TumblePipe package, which artists run via
[HPM](https://github.com/3db-dk/hpm) out of `~/.hpm/packages/<name>@<version>/`.
Two plugins can run them:

- **HPM** *(default)* —
  [tumblehead/deadline-hpm-plugin](https://github.com/tumblehead/deadline-hpm-plugin).
  A job bundles an `hpm.toml` whose dependency is the task package at its exact
  version and whose `[scripts.task]` runs `python -m <module>` with
  `package-env = true` (plus the task's own `requirements`, if any). The worker
  `hpm install`s it against its **own** HPM store (resolving the package + its
  `[python_dependencies]` + the script's extra `requirements`, and pinning the
  Houdini-mapped CPython into a package-env venv), then `hpm run task` executes
  the task **in native python** inside that env — the package importable, its
  deps on `PYTHONPATH`. No node has to mirror the submitter's disk, and the
  plugin self-bootstraps the `hpm` CLI so render nodes need no TumbleTrove
  Desktop install. (Requires hpm ≥ v0.22.2 for `package-env`.)
- **UV** *(legacy)* —
  [tumblehead/deadline-uv-plugin](https://github.com/tumblehead/deadline-uv-plugin).
  Bakes an **absolute** script path at submit time. This only works when every
  worker mirrors the submitter's `~/.hpm` store at the identical path, which is
  why it fails under HPM with `Script file not found: …/.hpm/packages/…`.

Install a plugin by copying its directory into
`<DeadlineRepository>/custom/plugins/<HPM|UV>/` and restarting the workers that
should pick it up. The two can coexist during migration; a job chooses via
`Job(..., plugin="HPM")` (see below).

## Farm worker prerequisites

Each worker needs the same environment as an artist workstation:

- **Houdini** — a build of the **same major** the job was submitted from. Under
  HPM the task runs in native Windows python and drives Houdini's bundled tools
  directly — `husk.exe`, the Windows USD resolver, `iconvert`, `idenoise` for
  denoising, and Houdini's `hoiiotool`/`hffmpeg` for image/video processing.
  **No WSL2 or UV is needed** for HPM jobs (the legacy UV plugin still requires
  both).

  At submit the creating Houdini's full version is captured into the job env as
  `TH_HOUDINI_VERSION` (e.g. `22.0.368`); the worker selects a matching install —
  the newest build of that **major** (preferring one at or above the submitted
  build within the same minor). Because the USD/resolver ABI is per-major, this
  is what keeps husk and the tumbleResolver build in sync. A worker with **no
  Houdini of that major installed** fails the task with
  `No valid Houdini version was found` — so when the studio moves to a new major,
  install it on the workers before submitting from it.
- **Houdini licenses — for some tasks, not all.** The tasks that drive **hython**
  (`stage`, `export`, `composite`, `publish`) check out a **Houdini Engine**
  license, *falling back to Core/FX when no Engine seat is free* — so a busy farm
  can take seats artists are waiting for. Size the Engine pool with that in mind.

  The **`denoise`** task deliberately takes **no license at all**: it drives
  Houdini's `idenoise` CLI, which is a front-end onto the same Intel OIDN the
  `denoiseai` COP uses but needs no token (verified by running it with
  `SESI_LMHOST` pointed at a dead host, where hython cannot license at all). It
  still needs a Houdini *install* — that is where `idenoise` lives — but the
  `denoise` group never contends for seats. See
  `designs/denoise-without-hython.md`.
- **Drive mappings** — workers must map the project drives to the same letters
  the workstations use, so jobs that reference `P:\...` resolve identically.
  Without matching drive letters, the job will fail to read project files.
- **A GL context for the `playblast` group** — playblast jobs render with
  husk's Hydra Storm (GL) delegate, so any worker assigned to the dedicated
  `playblast` group needs a GL-capable GPU context (a real display/session,
  not a headless service with no GPU). A worker without one produces empty
  frames; the task fails loudly with that hint rather than shipping a black
  MP4. The group is kept separate from `karma` so previews never take
  final-render slots.

For the **HPM** plugin specifically — **no per-node setup is required**:

- The `hpm` CLI is **self-bootstrapped** under `~/.deadline/hpm` on the first
  job (version from `HpmVersion` → `HPM_VERSION` env → the studio-pinned default
  `v0.22.2`). No manual install needed.
- The job manifest declares its own `[[registries]]` (read from the submitter's
  hpm config at submit time), so a render node that was never
  `hpm registry add`-ed still resolves packages — no global hpm config on the
  node. The tumbletrove registry and its package archives are public, so no
  worker credentials are needed.
- Pre-warming `~/.hpm/packages` with the versions in flight keeps the render
  path entirely offline.

## Submitting a job from TumblePipe

`tumblepipe.apps.deadline` is a generic `deadlinecommand` wrapper:

- `Job` — a single task (script + optional `requirements.txt` + args).
- `Batch` — a named collection of jobs with optional dependencies.
- `Deadline` — the submission client.

It is intentionally HPM-agnostic. Farm jobs use the thin factory
`tumblepipe.farm.deadline.Task`, which builds a `Job` **and** generates the
HPM `hpm.toml` manifest for it (the manifest the worker installs to resolve the
task's package). `submit()` writes that manifest into the shared job dir and
hands the plugin only its path — generation is owned by the job creators, not
the generic wrapper.

```python
from pathlib import Path

from tumblepipe.apps.deadline import Batch, Deadline
from tumblepipe.farm.deadline import Task

# The script must live inside an installed HPM package, i.e. under
# ~/.hpm/packages/<name>@<version>/...  — the package identity + manifest are
# derived from it. Submitting from a dev/editable checkout is rejected, because
# it has no reproducible version to ship.
job = Task(
    Path("/path/to/.hpm/packages/tumblepipe@1.12.2/python/.../script.py"),
    None,                       # per-task requirements.txt; None if the task has none
    "arg1",
    "arg2",
)

job.name = "My Render Job"
job.pool = "general"
job.group = "karma"
job.priority = 50
job.start_frame = 1
job.end_frame = 100
job.env.update({"MY_VAR": "value"})

batch = Batch("My batch")
batch.add_job(job)

farm = Deadline()
farm.submit(batch, Path("/path/to/jobs-dir"))
```

The default plugin is set by `DEFAULT_PLUGIN` in `tumblepipe.apps.deadline`
(pass `plugin="UV"` to `Task`/`Job` for the legacy path). Under **HPM**, the
task's third-party Python dependencies come from two places, both resolved into
the package-env: the package's own `hpm.toml` `[python_dependencies]`, plus any
per-task `requirements.txt` passed as `requirements_path`. `Task` reads that
file and writes its specs into the generated manifest's `[scripts.task]`
`requirements`, which the package-env merges on top of the package deps as
"extra requirements" (visible in the worker log as
`N manifest dep(s) + M extra requirement(s)`). A task with libraries the package
itself doesn't declare — e.g. `notify` needs `discord.py` — relies on this; the
file is `None` only when the task adds nothing beyond the package deps.

Use `Batch.add_dep(first, second)` to mark `second` as a dependency of
`first`, or `Batch.add_jobs_with_deps(jobs, deps)` to wire a whole graph
at once. See the farm job implementations in
`python/tumblepipe/farm/jobs/` for complete examples.

## Further reading

- [deadline-hpm-plugin](https://github.com/tumblehead/deadline-hpm-plugin) — the default plugin and its options
- [Deadline plugin development](https://docs.thinkboxsoftware.com/products/deadline/10.1/1_User%20Manual/manual/manual-plugins.html)
- [Astral UV documentation](https://docs.astral.sh/uv/)
