# Deadline and the render farm

TumblePipe includes a `tumblehead.apps.deadline` module for submitting jobs
to [Thinkbox Deadline](https://www.awsthinkbox.com/deadline), and a custom
Deadline plugin (`UV`) for running Python tasks on workers with reproducible
dependencies via [Astral UV](https://docs.astral.sh/uv/).

## Farm worker prerequisites

Each worker needs the same environment as an artist workstation:

- **WSL2 / Ubuntu** (on Windows workers).
- **UV, ffmpeg, openimageio-tools, opencolorio-tools** — install via the
  commands in [Installation](installation.md).
- **Drive mappings** — `/etc/fstab` mounts must match the workstations
  exactly, so the worker can resolve the same project paths artists use.

Without matching drive mappings, jobs that reference `P:\...` on the
Windows side won't resolve on the worker and the job will fail.

## The Deadline UV plugin

The UV plugin is maintained as a separate repository:

- **Repo** — [tumblehead/deadline-uv-plugin](https://github.com/tumblehead/deadline-uv-plugin)
- **Install** — copy the plugin directory into
  `<DeadlineRepository>/custom/plugins/UV/` and restart the workers that
  should pick it up.

## Submitting a job from TumblePipe

`tumblehead.apps.deadline` wraps `deadlinecommand` with three primitives:

- `Job` — a single task (script + optional `requirements.txt` + args).
- `Batch` — a named collection of jobs with optional dependencies.
- `Deadline` — the submission client.

```python
from pathlib import Path

from tumblehead.apps.deadline import Batch, Deadline, Job

job = Job(
    Path("/path/to/script.py"),
    Path("/path/to/requirements.txt"),  # pass None if no UV env is needed
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

The plugin is always set to `UV`, so the worker runs the script inside a
UV-managed virtualenv built from `requirements_path`. Pass `None` for
`requirements_path` if the script has no third-party dependencies.

Use `Batch.add_dep(first, second)` to mark `second` as a dependency of
`first`, or `Batch.add_jobs_with_deps(jobs, deps)` to wire a whole graph
at once. See the farm job implementations in
`python/1x/tumblepipe/farm/jobs/` for complete examples.

## Further reading

- [Deadline plugin development](https://docs.thinkboxsoftware.com/products/deadline/10.1/1_User%20Manual/manual/manual-plugins.html)
- [Astral UV documentation](https://docs.astral.sh/uv/)
