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
  A job carries the package **identity** (`tumblepipe@1.11.0`) plus a
  package-relative script path. The worker re-resolves that exact version
  against its **own** HPM store, so nodes don't have to mirror the submitter's
  disk. It also reconstructs the package's `[python_dependencies]` into the
  task venv (the deps HPM provisions in-Houdini), and self-bootstraps the `hpm`
  CLI so render nodes need no TumbleTrove Desktop install.
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

- **WSL2 / Ubuntu** (on Windows workers).
- **UV, ffmpeg, openimageio-tools, opencolorio-tools** — install via the
  commands in [Installation](installation.md). UV builds the task venv.
- **Drive mappings** — `/etc/fstab` mounts must match the workstations
  exactly, so the worker can resolve the same project paths artists use.
  Without matching drive mappings, jobs that reference `P:\...` on the
  Windows side won't resolve on the worker and the job will fail.

For the **HPM** plugin specifically:

- The `hpm` CLI is **self-bootstrapped** under `~/.deadline/hpm` on the first
  package cache miss (version from `HpmVersion` → `HPM_VERSION` env → `latest`,
  with the `latest` lookup TTL-cached). No manual install needed.
- `hpm` must be authenticated to the `tumbletrove` registry so it can pull
  packages on a cache miss. Pre-warming `~/.hpm/packages` with the versions in
  flight keeps the render path entirely offline.

## Submitting a job from TumblePipe

`tumblepipe.apps.deadline` wraps `deadlinecommand` with three primitives:

- `Job` — a single task (script + optional `requirements.txt` + args).
- `Batch` — a named collection of jobs with optional dependencies.
- `Deadline` — the submission client.

```python
from pathlib import Path

from tumblepipe.apps.deadline import Batch, Deadline, Job

# The script must live inside an installed HPM package, i.e. under
# ~/.hpm/packages/<name>@<version>/...  — the plugin derives the package
# identity + a package-relative path from it. Submitting from a dev/editable
# checkout is rejected, because it has no reproducible version to ship.
job = Job(
    Path("/path/to/.hpm/packages/tumblepipe@1.11.0/python/.../script.py"),
    None,                       # extra requirements.txt; usually unnecessary
    "arg1",
    "arg2",
    plugin="HPM",               # omit to use the default (DEFAULT_PLUGIN)
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

The default plugin is set by `DEFAULT_PLUGIN` in `tumblepipe.apps.deadline`.
Under **HPM**, the task's third-party Python dependencies come from the resolved
package's `hpm.toml` `[python_dependencies]`, so `requirements_path` is normally
`None`; pass a `requirements.txt` only for extras a package doesn't declare.

Use `Batch.add_dep(first, second)` to mark `second` as a dependency of
`first`, or `Batch.add_jobs_with_deps(jobs, deps)` to wire a whole graph
at once. See the farm job implementations in
`python/tumblepipe/farm/jobs/` for complete examples.

## Further reading

- [deadline-hpm-plugin](https://github.com/tumblehead/deadline-hpm-plugin) — the default plugin and its options
- [Deadline plugin development](https://docs.thinkboxsoftware.com/products/deadline/10.1/1_User%20Manual/manual/manual-plugins.html)
- [Astral UV documentation](https://docs.astral.sh/uv/)
