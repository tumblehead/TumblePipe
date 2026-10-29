"""The local farm: farm batches run on this machine, the way a render node runs them.

A batch submitted with the local backend (``farm.backend``) is written to the
same job folder a Deadline submission writes (``export:/other/jobs/<uuid>``),
and queued on this machine instead of on Deadline. One runner process per
machine works the queue, one task at a time, doing what the Deadline HPM
plugin does on a render node: load the job's env file, ``hpm install`` the
job's manifest in a scratch dir, and ``hpm run task -- <context> <first>
<last>``. So a local render is the farm job itself, only on this machine.

- ``queue`` — batches, task state and control requests on disk.
- ``task`` — running one task: its environment, its command, its process.
- ``hpm`` — the hpm executable the runner drives (bootstrapped like the plugin's).
- ``runner`` — the machine's queue worker, and starting it.
- ``backend`` — :class:`~tumblepipe.farm.local.backend.LocalFarm`, the
  submit side that ``Deadline`` stands beside.

Import-light: stdlib only (``backend`` aside), so the runner starts in any
Python that has the package on its path.
"""
