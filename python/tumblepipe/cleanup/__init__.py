"""Clean up a project: delete what it no longer needs, one category at a time.

    python -m tumblepipe.cleanup [project] [--only renders,backups] [--apply]

A project only grows: every export, render and save adds a version and
nothing takes the old ones away. This reads the project once and lists, per
category, what can go and what stays, each with its reason:

- ``exports``: old export versions (``tumblepipe.prune_exports``);
- ``jobs``: farm job folders Deadline and the local farm are done with
  (``tumblepipe.prune``);
- ``renders``: old render versions and check renders (:mod:`.renders`);
- ``caches``: old ``th::cache`` versions their workfile can make again
  (:mod:`.caches`);
- ``backups``: Houdini's ``backup/*_bakN.hip`` of workfile versions that
  have since been saved again (:mod:`.backups`);
- ``deleted``: the ``_deleted/<stamp>/`` folders deleting an entity or an
  edit leaves behind (:mod:`.deleted`).

It lists by default and deletes only with ``--apply`` (or, in the window,
when confirmed), recording everything it removed in
``export/other/logs/cleanup-<date>.log``. The window is :mod:`.app`
(TumbleTrove Desktop's **Clean Up Project**).
"""

from tumblepipe.cleanup.core import (  # noqa: F401
    CATEGORY_KEYS, CategoryPlan, Entry, Plan, Settings, apply, categories,
    log_path_for, make_plan, summary,
)
