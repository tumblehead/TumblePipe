"""The Farm Manager: this machine's local jobs and the Deadline farm's, in one window.

- ``rows`` — what the window lists: :class:`~rows.JobRow` and
  :class:`~rows.TaskRow`, built from the local queue (``farm.local.queue``),
  and the filters over them.
- ``deadline_jobs`` — the same rows read from Deadline (``deadlinecommand
  -Json``), the job reports, and the job commands.
- ``window`` — the Qt window; ``python -m tumblepipe.farm.manager`` opens it
  on its own.
"""
