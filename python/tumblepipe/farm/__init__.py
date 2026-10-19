"""The render farm: building Deadline jobs, and what they run.

- ``jobs/<family>.py`` — builds the one Deadline job that runs a worker
  (``deadline.farm_job``).
- ``workers/<name>.py`` — what a job runs on a render node, entered through
  ``worker.run``; ``<name>_houdini.py`` is its hython half.
- ``specs/<family>.py`` — the config schemas both sides validate against,
  on the primitives in ``checks``.
- ``pipelines/<name>.py`` — batches of jobs and their command lines;
  ``batch_submit`` is the Farm Submit dialog's per-entity submission.
- ``submit/`` — the Farm Submit dialog's Qt-free halves: ``settings`` (what
  each entity's settings resolve to), ``grid`` and ``status`` (the cells
  and their states), ``camera`` (the render camera probe), and ``plan``
  (running a submission out of process).
- ``env`` — the environment a job runs in; ``frames`` — frame-numbered paths.
"""
