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

  **Render Edit** (`edit_render`, in the `encode` group) takes no licence
  either. It renders a published edit with the editor TumblePipe ships,
  `bin/<platform>/th_player`, run headless as `th_player --render`: on the
  CPU, with no window, display or GPU. The editor's own FFmpeg libraries are
  beside it in the package. Houdini's `hffmpeg` then encodes the frames and
  the mixed sound into `render:/edits/<name>/<name>_edit_vNNNN.mp4`, so the
  worker still needs a Houdini install for that. The job is one task: the
  whole edit is rendered and then encoded, so a long edit holds one worker
  for a while (the task may run four hours). On Linux the player links ALSA
  (`libasound.so.2`, package `libasound2t64` on Ubuntu 24.04) even headless,
  so a Linux worker needs it installed; every desktop install has it.
- **Drive mappings** — workers must map the project drives to the same letters
  the workstations use, so jobs that reference `P:\...` resolve identically.
  Without matching drive letters, the job will fail to read project files.
- **An NVIDIA GPU for the `karma` group** — final renders and playblasts
  both run there. A playblast with the default `viewport` renderer (TumbleView) draws on
  the CPU with TumblePipe's own render delegate; husk still checks out a
  Karma Renderer licence for it (without `--check-licenses` it would take a
  Houdini Engine/Core/FX seat first); with
  the `karma` renderer it renders with
  Karma XPU in a preview mode (by default scene materials off and the scene's
  lights replaced by one dome light at 32 samples; the Farm Submit dialog's
  playblast **Light** / **Samples** / **Materials** change it, see
  [Compositing → Playblast](compositing.md#playblast)). It draws through CUDA/OptiX
  with no GL context or display, and uses the worker's GPU memory (3.5 GiB
  peak on a HideAndReek shot). Hydra Storm (GL) used to be an option and was
  removed: it hung on the first frame and crashed husk on every real shot
  the farm tried.

For the **HPM** plugin specifically — **no per-node setup is required**:

- The `hpm` CLI is **self-bootstrapped** under `~/.deadline/hpm` on the first
  job (version from `HpmVersion` → `HPM_VERSION` env → the plugin's pinned
  default, `v0.36.1`). No manual install needed. The studio pins one hpm for CI,
  submitters and the farm with the Infisical `HPM_VERSION` knob (prod); bump the
  plugin's `DEFAULT_HPM_VERSION` and `farm/local/hpm.py`'s `DEFAULT_VERSION`
  with it, so a worker without the env var runs the same hpm.
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
  stop it. Low priority means a render waits for your own work: while the
  machine is busy, a task can take up to a minute just to start.
- **Per task, it does what the HPM plugin does:** it loads the job's env file
  (then the job entry's own `env`: a farm job run here with *Run on this
  machine* sets `TH_FARM_BACKEND=local` over its Deadline env file),
  sets `TH_FARM_DATA`, copies the manifest into a scratch dir, runs
  `hpm install` there, then runs `hpm run task -- <context> <first> <last>`.
  Before that it strips the session that started it (Houdini's variables,
  `PYTHONPATH`, `$HFS` on `PATH`, the pipeline's `TH_*`/`TT_*`/`HPM_*`). A
  task sees only this machine's environment plus the job's, as on the farm.
- **Time limit:** a job's task timeout counts per frame, as on Deadline
  (TumblePipe submits every job with frame timeouts on). The runner keeps one
  limit per job, so each task gets the limit times the frames in the job's
  largest task. A 10-frame render chunk with a 45-minute limit gets
  7.5 hours. Up to 1.101.0 a local task got the per-frame limit as its whole
  limit, so long chunks timed out here that the farm would have finished.
- **hpm:** the runner keeps its own `hpm` (artist machines have none on
  `PATH`). It downloads `$HPM_VERSION` (else the plugin's pinned default)
  from the hpm releases, as the plugin does on a render node.
  `$TH_LOCAL_FARM_HPM` names an executable outright. Houdini's Python on
  macOS has no CA certificates of its own, so when Python finds none (and
  `SSL_CERT_FILE` is not set) the download checks GitHub's certificate
  against the system's bundle (`/etc/ssl/cert.pem`), else certifi's.
- **Houdini:** a task runs the `hython`/`husk` of an install of the job's
  Houdini major (the same build or newer, else the newest of that major),
  found in SideFX's default place for the platform:
  `<drive>:\Program Files\Side Effects Software\Houdini <version>` (Windows,
  drives C–H), `/Applications/Houdini/Houdini<version>` (macOS) or
  `/opt/hfs<version>` (Linux). An install anywhere else is not found, and the
  task fails with *no installed Houdini 22.x for 22.0.457 (found: …)* naming
  what it did find. (Up to 1.91.0 only the Windows place was searched, so
  every local task on macOS or Linux failed this way.)
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

**Not from a dev session.** *This machine* builds the same `hpm.toml`
manifests as the farm, so it refuses a dev or editable checkout just as
Deadline does: *Cannot submit to the HPM farm plugin from a non-HPM
install*. To test a worker against the dev tree, run its Houdini half in
`hython` on the config its job would pass. For the composite, that is
`farm/workers/composite_houdini.py <config.json>`, where `config.json`
has `entity`, `first_frame`, `last_frame`, `frames`, `receipt_path`,
`input_path`, `node_path`, `layer_names` and `output_paths`
(`farm/workers/composite.py` writes it).

**Every farm submit offers the same choice**, and they all share the
remembered one:

| Where | The choice |
|---|---|
| Farm Submit (Asset Browser) | **Run on** beside Close and Submit |
| Publish dialog | **In this session** (run now, in this Houdini), **This machine**, **Render farm** |
| `th::export_comp` **Submit** | asked on press: Render farm / This machine / Cancel |
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
progress, task count, errors, user and submit time. A dot before each
batch, job and task name shows its state in colour, explained by the legend
at the bottom of the window (hover an entry for what it means), so the list
reads at a glance without the Status column in view. A failed one's name is
red, and so is its batch's; finished jobs are dimmed so the ones that need
you stand out. A batch is listed under its project, kind and entity (hover it for the
full Deadline batch name). To open it, use
**TumbleTrove ▸ TumblePipe ▸ Farm Manager** in Houdini's menu bar, the
browser's **Farm Manager** quick action, the **Farm Manager** button of a
submission's progress in the Farm Submit dialog, or the **Farm Manager** script in TumbleTrove
Desktop (`python -m tumblepipe.farm.manager`).

- **Filters:** *Show* (all, this machine, the farm), *Only mine*, *Only
  <project>*, *Hide finished*, and a name filter.
- **Layout:** drag the grey lines between the panes (the job list and its
  details, the tasks and the log, and the top and bottom halves) to resize
  them; a line lights up under the mouse.
- **Job details:** the pane beside the list shows the selected job's
  properties, the way the Deadline Monitor's job panel does:
  - *Job*: name, kind (the farm worker: render, playblast, publish...),
    entity and department, status, where it runs, who submitted it from
    which machine and when, the batch, the job ID.
  - *Renderer*: what renders it and on which devices. Renders and Karma
    playblasts say **Karma (husk), XPU: GPU and CPU together**. Farm and
    local renders always use Karma XPU, and XPU uses the CPU as well as the
    GPU: the CPU loads the scene, builds geometry and writes the images. So
    a busy CPU during a GPU render is expected. Viewport-look playblasts
    say *CPU only, no licence*, and so do Render Edit jobs (kind
    `edit_render`), which name the edit and version they render. Tiles per
    frame, shading and resolution are shown where the job has them.
  - *Time*: started, finished, elapsed wall-clock time, total render time
    (every task added up), average per task and per frame, and an estimate
    of the render time left.
  - *Frames*: the frame list, frame count, frames per task and the task
    counts. *Scheduling*: priority, pool, group, the jobs it waits on, the
    task timeout.
  - *Output*: each output path. Double-click one, or right-click ▸ *Open
    folder*, to open the folder it is written to. Right-click ▸ *Copy all
    details* copies the pane as text.

  Local batches queued before this version have no pool, group or chunk
  size recorded, so those rows are left out.
- **This machine's load:** while the local farm renders, the footer shows
  the machine's CPU, RAM, GPU and VRAM use. For a rendering local job, the
  details also have a *This machine* section with the task's own processes
  (hpm, hython, husk) and their CPU share and memory. The GPU line needs
  `nvidia-smi` (NVIDIA drivers); without it the GPU line is left out. The
  load is sampled every two seconds on a worker thread, and only while the
  local runner is working.
- **Tasks and logs:** selecting a job lists its tasks, each with its
  *Render time*. A finished task shows how long it took. A rendering task
  shows how long it has been rendering, in blue, counting up. Selecting a
  task shows its log. A local task's log is followed while it renders. A
  farm task's Deadline reports are listed newest first, error reports with
  their message.
- **Actions:** on this machine *Suspend*, *Resume*, *Retry failed*, *Cancel*
  and *Remove*. On the farm *Suspend*, *Resume*, *Retry failed*, *Requeue* and
  *Delete* (asks first). A selected batch row stands for all of its jobs.
  The footer answers each action on its right, and keeps the answer until
  the next one.
- **Clean up:** the **Clean up ▾** button beside *Refresh* removes finished
  batches. It works on the batches the filters show (*Show*, *Only mine*,
  *Only <project>* and the name filter). Finished batches count even while
  *Hide finished* hides them.
  - *Remove completed*: batches whose every job completed.
  - *Remove failed*: finished batches with a failed job (on this machine, a
    cancelled one counts too).
  - *Remove completed and failed*: both.
  - *Remove superseded*: older runs of the same work. For each set of
    batches with the same steps on the same entity in the same project (on
    the farm, or on this machine), the newest stays, finished or not. The
    older finished ones go.

  The menu shows how many batches each item would remove. A batch is
  removed whole, and only when every job of it has finished: one queued,
  rendering, pending or suspended job keeps the whole batch, and so does a
  job that an unfinished job is waiting on. It asks first, listing the
  batches. Farm batches are deleted from Deadline; local ones are taken off
  this machine's list. Outputs stay. The farm is read again just before the
  delete, and a batch retried in the meantime is left alone.
- **Run on this machine:** a farm job that cannot run on the farm (no
  licence free on the workers that take it, say) can run here instead.
  Right-click a single farm job ▸ *Run on this machine…*. It is enabled
  for a failed, queued, pending or suspended job. The job and every farm
  job waiting on it that has not completed (a notify waiting on an
  encode, say) go to this machine's queue as one batch named
  "… (on this machine)". They use the same job files (manifest, env file,
  data folder), so nothing is exported or rendered again, and what they
  submit goes to this machine. The dialog lists the jobs and asks whether
  to suspend the farm copies (the default, so the farm does not run them
  twice; *Resume* brings them back) or leave them. It is refused for a
  job that is rendering or completed, for a non-HPM job, and when one of
  the jobs waits on another unfinished farm job outside that batch.
- **Right-click menus:** on a job, the same actions plus *Open job folder*,
  *Open output folder*, *Copy job ID* and *Copy name*. On a task, *Open log
  folder*, *Copy log*, *Copy frames*, *Copy error*, and the job actions (they
  act on the whole job, as on Deadline).

The farm is read with `deadlinecommand -Json` (`GetJobs`, `GetJobTasks`, the
report listings) on a worker thread every few seconds. A job's
`context.json`, which the Renderer section reads, is on the project share and
is read on a worker thread too, once per job. Without a Deadline Client the
window shows this machine's jobs alone.

Every job TumblePipe submits carries a stamp saying which project and entity
it is for. On Deadline the stamp is the job's extra info `TumblePipeProject`
and `TumblePipeEntity` (Deadline Monitor can show them as columns). On this
machine it is the `extra_info` of the job in the batch's `batch.json`. The
*Only <project>* filter and the clean-ups read it. Jobs submitted before the
stamp have none: for those the project is the first word of the batch name,
and the entity is the entity URI the batch name contains. Failing that, the
clean-ups read the job's `context.json`, but only for finished jobs of this
project.

While you have jobs queued or rendering in this project, on this machine or
on the farm, the asset browser's **Farm Manager** quick action shows a ring
turning around its button. A background thread keeps the count: it reads
the local queue every two seconds and the farm every 20 seconds (the farm
only when this machine has a Deadline Client), and it stops reading while
the browser is hidden or closed. The ring needs a TumbleTrove with the
`get_quick_action_busy` hook; an older one shows the button without it.

Hover the **Farm Manager** button for a summary of your jobs in this
project, from the same count: those still queued or rendering plus those
submitted in the last 24 hours. It names how many shots (or assets) they
are for, the jobs running, queued, completed and failed, the tasks done of
all, and, while work is under way, about when it will be done. That time
is a rough guess: for each running batch, the time since it started scaled
by the share of its tasks still to do; it is left out until every running
batch has a task done. When some of those jobs have failed, the summary
names their shots, and the button shows a small orange dot at its top-right
corner (on the **⋯** button when the toolbar is too narrow for it). The dot
goes once you retry or remove the failed jobs in the Farm Manager, or a day
after they were submitted. The dot needs a TumbleTrove with the
`get_quick_action_alert` hook; an older one shows the hover without it.

## Further reading

- [deadline-hpm-plugin](https://github.com/tumblehead/deadline-hpm-plugin) — the default plugin and its options
- [Deadline plugin development](https://docs.thinkboxsoftware.com/products/deadline/10.1/1_User%20Manual/manual/manual-plugins.html)
