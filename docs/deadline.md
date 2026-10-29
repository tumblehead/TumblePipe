# Deadline and the render farm

TumblePipe includes a `tumblepipe.apps.deadline` module for submitting jobs
to [Thinkbox Deadline](https://www.awsthinkbox.com/deadline), and custom
Deadline plugins for running its Python farm tasks on workers with the same
source and environment the job was submitted with.

Every farm submission goes through `deadlinecommand` from the **Deadline
Client** on the artist's machine, found through the `DEADLINE_PATH`
environment variable its installer sets. Without it, Submit stops with
"Deadline is not set up on this machine" before anything is submitted.
Install the client, restart Houdini, and submit again. For working without a
farm, see "Rendering without a farm" in `docs/compositing.md`.

## The HPM plugin

Farm workers live inside the TumblePipe package, which artists run via
[HPM](https://github.com/3db-dk/hpm) out of `~/.hpm/packages/<name>@<version>/`.
The [tumblehead/deadline-hpm-plugin](https://github.com/tumblehead/deadline-hpm-plugin)
runs them. A job bundles an `hpm.toml` whose dependency is the task package at its exact
  version and whose `[scripts.task]` runs `python -m <module>` with
  `package-env = true` (plus the task's own `requirements`, if any). Its
  `[runtime]` supplies `TH_PROJECT_PATH`, which TumblePipe's own manifest
  declares as a *required* placeholder — hpm resolves that against the bundled
  manifest, not the process env, so without it the install below fails with
  `Required env var 'TH_PROJECT_PATH' … has no value`. The worker
  `hpm install`s it against its **own** HPM store (resolving the package + its
  `[python_dependencies]` + the script's extra `requirements`, and pinning the
  Houdini-mapped CPython into a package-env venv), then `hpm run task` executes
  the task **in native python** inside that env — the package importable, its
  deps on `PYTHONPATH`. No node has to mirror the submitter's disk, and the
  plugin self-bootstraps the `hpm` CLI so render nodes need no TumbleTrove
  Desktop install. (Requires hpm ≥ v0.22.2 for `package-env`.)

Install the plugin by copying its directory into
`<DeadlineRepository>/custom/plugins/HPM/` and restarting the workers that
should pick it up.

The job's env file (`NN.env`, the plugin's `EnvironmentFile`) is written
UTF-8, so a value with a non-ASCII character (a user, a path) reaches the
task intact. The plugin reads UTF-8, else the worker's locale encoding, for
jobs written before 1.84 in the submitter's default encoding (cp1252 inside
Houdini). Update the plugin before TumblePipe 1.84 reaches the artists: an
older plugin reads the file in the worker's default encoding and garbles
such a value.

## Farm worker prerequisites

Each worker needs the same environment as an artist workstation:

- **Local scratch disk space.** Tasks stage their real payload through `temp:/`
  before publishing it — `render` writes its Karma tiles there ahead of the
  stitch, `denoise` and `mp4` their intermediates — and `temp:/` resolves to
  machine-local scratch (`<OS temp>/th_temp/<project>`), not the project drive.
  A worker whose system drive is small will feel that. `TH_TEMP` moves the
  root, and it has to be set in the **worker's own** environment: the job env
  built at submit deliberately does not carry it, so each worker decides its
  own scratch location. Nothing under `temp:/` is meant to survive the task, so
  the space is transient — but a task killed mid-run leaves its staging
  directory behind.

- **Houdini** — a build of the **same major** the job was submitted from. Under
  HPM the task runs in native Windows python and drives Houdini's bundled tools
  directly — `husk.exe`, the Windows USD resolver, `iconvert`, `idenoise` for
  denoising, and Houdini's `hoiiotool`/`hffmpeg` for image/video processing.
  No WSL2 or UV is needed.

  At submit the creating Houdini's full version is captured into the job env as
  `TH_HOUDINI_VERSION` (e.g. `22.0.368`); the worker selects a matching install —
  the newest build of that **major** (preferring one at or above the submitted
  build within the same minor). Because the USD/resolver ABI is per-major, this
  is what keeps husk and the tumbleResolver build in sync. A worker with **no
  Houdini of that major installed** fails the task with
  `No valid Houdini version was found` — so when the studio moves to a new major,
  install it on the workers before submitting from it.
- **Houdini licenses — for some tasks, not all.** The tasks that drive **hython**
  (`stage`, `export`, `composite`, `publish`, `collapse`) check out a **Houdini Engine**
  license, *falling back to Core/FX when no Engine seat is free* — so a busy farm
  can take seats artists are waiting for. Size the Engine pool with that in mind.
  hython cannot use a Karma Renderer or Renderer license; the Karma and Renderer
  licenses bundled with each Core/FX seat only serve husk and mantra.

  Every worker must stay **logged in to the SideFX Launcher** (login licensing).
  When that login lapses, the worker fails every hython *and* husk task a few
  seconds in with `No licenses could be found`, while the other workers render
  the same jobs fine. See
  [One worker fails every job with "No licenses could be found"](troubleshooting.md#one-worker-fails-every-job-with-no-licenses-could-be-found).

  The tasks that drive **husk** (`render`, `playblast`) pass
  `--check-licenses 'Karma Renderer'`, so they take a **Karma Renderer**
  license and never a Houdini seat. A husk run without that flag asks for Engine /
  Core / FX first; that is how early farm playblasts failed with
  `No licenses could be found` while Karma licenses sat free.

  The **`denoise`** task deliberately takes **no license at all**: it drives
  Houdini's `idenoise` CLI, which is a front-end onto the same Intel OIDN the
  `denoiseai` COP uses but needs no token (verified by running it with
  `SESI_LMHOST` pointed at a dead host, where hython cannot license at all). It
  still needs a Houdini *install* — that is where `idenoise` lives — but the
  `denoise` group never contends for seats.

  It also runs OIDN on the **CPU**, so `denoise` workers need no GPU. OIDN's
  CUDA device quantizes what it writes to a couple of hundred values per stop,
  which bands as soon as a grade pushes the image; the CPU keeps full float
  precision at a few seconds a frame.
- **Drive mappings** — workers must map the project drives to the same letters
  the workstations use, so jobs that reference `P:\...` resolve identically.
  Without matching drive letters, the job will fail to read project files.
- **An NVIDIA GPU for the `karma` group** — final renders and playblasts
  both run there. Playblast jobs render with
  Karma XPU in a preview mode (scene materials and lights off, a camera
  headlight with ambient occlusion, 4 samples). It draws through CUDA/OptiX
  with no GL context or display, and uses the worker's GPU memory (3.5 GiB
  peak on a HideAndReek shot). Hydra Storm (GL) used to be an option and was
  removed: it hung on the first frame and crashed husk on every real shot
  the farm tried.

For the **HPM** plugin specifically — **no per-node setup is required**:

- The `hpm` CLI is **self-bootstrapped** under `~/.deadline/hpm` on the first
  job (version from `HpmVersion` → `HPM_VERSION` env → the studio-pinned default
  `v0.22.2`). No manual install needed.
- Each job carries an `hpm.toml` built from the **project's own** `hpm.toml`:
  its `[dependencies]` (TumbleRig and anything else the project uses, at the
  project's pinned versions — TumblePipe itself pinned to the version that
  submitted), its `[runtime]` and its registries. The worker's `hpm install`
  sets those packages up and writes one Houdini package file per dependency
  into the job's `.hpm/packages/`, which is the `HOUDINI_PACKAGE_DIR` the
  task's hython loads. So a farm task runs with the packages the artist's
  Houdini had. The project manifest is found from `TT_PROJECT_DIR`, from a
  Desktop Houdini's `HOUDINI_PACKAGE_DIR` (`<project>/.hpm/packages`), or — for
  a job submitted from a worker, like a collapse submitting its playblast —
  from the running job's own manifest. With none found, the job carries
  TumblePipe alone and the submitter logs a warning.
- The job manifest declares its own `[[registries]]` (read from the submitter's
  hpm config at submit time), so a render node that was never
  `hpm registry add`-ed still resolves packages — no global hpm config on the
  node. The tumbletrove registry and its package archives are public, so no
  worker credentials are needed.
- Pre-warming `~/.hpm/packages` with the versions in flight keeps the render
  path entirely offline.

## Submitting a job from TumblePipe

`tumblepipe.apps.deadline` is a generic `deadlinecommand` wrapper:

- `Job` — one Deadline job (a package script and its arguments).
- `Batch` — a named collection of jobs with optional dependencies.
- `Deadline` — the submission client.

It is intentionally HPM-agnostic. A farm job is built by
`tumblepipe.farm.deadline.farm_job`, which makes a `Job` that runs one of the
package's workers (`farm/workers/<name>.py`) on a context dict it bundles, with
the farm's base environment, **and** generates the HPM `hpm.toml` manifest the
worker's machine installs to resolve the package. `submit()` writes that
manifest into the shared job dir and hands the plugin only its path.

```python
from pathlib import Path

from tumblepipe.apps.deadline import Batch, Deadline
from tumblepipe.farm.deadline import farm_job, run_once

# The package must be an installed HPM package (under
# ~/.hpm/packages/<name>@<version>/): its identity and manifest are derived
# from the worker's path. A dev/editable checkout is rejected, because it has
# no reproducible version to ship.
job = farm_job(
    "notify",                   # farm/workers/notify.py
    {"user_name": "me", "channel_name": "renders",
     "message": "hello", "command": {"mode": "notify"}},
    Path("/path/to/staging"),   # where the context is written before bundling
    name="My notify",
    pool="general",
    group="general",
    priority=50,
    max_minutes=10,
)
run_once(job, 1, 1)

batch = Batch("My batch")
batch.add_job(job)
Deadline().submit(batch, Path("/path/to/jobs-dir"))
```

A worker's third-party Python dependencies come from two places, both resolved
into the package-env: the package's own `hpm.toml` `[python_dependencies]`,
plus the worker's own `farm/workers/<name>_requirements.txt` when there is
one. `farm_job` writes that file's specs into the generated manifest's
`[scripts.task]` `requirements`, which the package-env merges on top of the
package deps as "extra requirements" (visible in the worker log as
`N manifest dep(s) + M extra requirement(s)`). `notify` needs `discord.py`,
which the package itself does not declare.

Use `Batch.add_dep(first, second)` to mark `second` as a dependency of
`first`, or `Batch.add_jobs_with_deps(jobs, deps)` to wire a whole graph
at once. The job builders in `python/tumblepipe/farm/jobs/` and the
pipelines in `python/tumblepipe/farm/pipelines/` are complete examples.

## Running farm jobs on this machine

Farm Submit's **Run on** choice (beside Close and Submit) sends a submission
either to the **Render farm** (Deadline) or to **This machine**. The choice is
remembered between submissions. With *This machine*, the very same batch is
built: the same job folder under `export:/other/jobs/`, the same env files,
the same `hpm.toml` manifests. It is then queued on this machine instead of
on Deadline, and runs in the background as a render node would run it.

- **One runner per machine** (`python -m tumblepipe.farm.local.runner`) works
  the queue one task at a time, below normal priority. It starts when work is
  queued and stops by itself once the queue is empty. Closing Houdini does not
  stop it.
- **Per task, it does what the HPM plugin does:** it loads the job's env file,
  sets `TH_FARM_DATA`, copies the manifest into a scratch dir, runs
  `hpm install` there, then runs `hpm run task -- <context> <first> <last>`.
  Before that it strips the session that started it (Houdini's variables,
  `PYTHONPATH`, `$HFS` on `PATH`, the pipeline's `TH_*`/`TT_*`/`HPM_*`). A
  task sees only this machine's environment plus the job's, as on the farm.
- **hpm:** the runner keeps its own `hpm` (artist machines have none on
  `PATH`). It downloads `$HPM_VERSION` (else the plugin's pinned default)
  from the hpm releases, as the plugin does on a render node.
  `$TH_LOCAL_FARM_HPM` names an executable outright.
- **Chained work stays local:** every local job's env file carries
  `TH_FARM_BACKEND=local`. A collapse that submits its playblast, or a stage
  that submits its render, queues them here too.
- **The queue** lives in `%LOCALAPPDATA%\TumblePipe\local_farm` (Windows),
  `~/Library/Application Support/TumblePipe/local_farm` (macOS) or
  `~/.local/share/tumblepipe/local_farm` (Linux); `$TH_LOCAL_FARM_DIR`
  overrides it. Each batch has a `batch.json`, the runner's `state.json`, and
  a log per task under `logs/`.

A local render takes this machine's licences: a `hython` beside the open
Houdini, and `husk` a Karma licence. It also shares the GPU with the
viewport.

**Every farm submit offers the same choice**, and they all share the
remembered one:

| Where | The choice |
|---|---|
| Farm Submit (Asset Browser) | **Run on** beside Close and Submit |
| Publish dialog | **In this session** (run now, in this Houdini), **This machine**, **Render farm** |
| `th::build_comp` **Submit to farm** | asked on press: Render farm / This machine / Cancel |
| `th::lookdev_studio` turntable submit | asked on press, the same |

The Deadline check runs only when the work goes to Deadline. After a
submit, the answer says where the work went and opens the Farm Manager. A
node's own settings (pool, priority, range, batch) are used wherever the work
runs. The publish dialog's farm build task, which has no node, takes the
project's default pool and priority.

In code, `tumblepipe.farm.backend.get_farm()` returns `LocalFarm` or `Deadline`
from `$TH_FARM_BACKEND`. Both take the same `submit(batch, jobs_dir)`.
Houdini-side submits set the variable for the call with
`farm.backend.using(backend)`, through `pipe.houdini.ui.farm_submit`.

## The Farm Manager

The **Farm Manager** shows the jobs on this machine and on the Deadline farm
in one list. Each batch is a row, with its jobs under it, showing state,
progress, task count, errors, user and submit time. To open it, use the
browser's **Farm Manager** quick action, the **Farm Manager** button of a
submission's status window, or the **Farm Manager** script in TumbleTrove
Desktop (`python -m tumblepipe.farm.manager`).

- **Filters:** *Show* (all, this machine, the farm), *Only mine*, *Only
  <project>*, *Hide finished*, and a name filter.
- **Tasks and logs:** selecting a job lists its tasks. Selecting a task shows
  its log. A local task's log is followed while it renders. A farm task's
  Deadline reports are listed newest first, error reports with their message.
- **Actions:** on this machine *Suspend*, *Resume*, *Retry failed*, *Cancel*
  and *Remove*. On the farm *Suspend*, *Resume*, *Retry failed*, *Requeue* and
  *Delete* (asks first). A selected batch row stands for all of its jobs.

The farm is read with `deadlinecommand -Json` (`GetJobs`, `GetJobTasks`, the
report listings) on a worker thread every few seconds. Without a Deadline
Client the window shows this machine's jobs alone.

## Further reading

- [deadline-hpm-plugin](https://github.com/tumblehead/deadline-hpm-plugin) — the default plugin and its options
- [Deadline plugin development](https://docs.thinkboxsoftware.com/products/deadline/10.1/1_User%20Manual/manual/manual-plugins.html)
