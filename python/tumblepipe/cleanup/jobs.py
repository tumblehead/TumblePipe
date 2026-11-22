"""Farm job folders: ``tumblepipe.prune``, as a cleanup category.

Deadline is asked which jobs still use each folder, and this machine's local
farm queue which batches do. When Deadline cannot be asked nothing goes, and
the listing says why.
"""

from __future__ import annotations

from pathlib import Path

from tumblepipe import prune
from tumblepipe.cleanup.core import Category, CategoryPlan, Entry, Settings, export_path


def _ask_deadline() -> tuple[dict | None, str | None]:
    """``(jobs by folder, None)``, or ``(None, why Deadline gave none)``."""
    command = prune.deadline_command()
    if command is None:
        return None, 'Deadline is not installed on this machine: no job folder can be deleted safely.'
    try:
        return prune.query_deadline(command), None
    except (OSError, RuntimeError, ValueError) as error:
        return None, f'Deadline could not be asked ({error}): no job folder will be deleted.'


def plan(roots, _inventory, settings: Settings, *, workfiles) -> CategoryPlan:
    jobs_path = export_path(roots).joinpath(*prune.JOBS_DIR)
    out = CategoryPlan('jobs', 'Farm job folders', 'folder')
    if not jobs_path.is_dir():
        out.payload = (jobs_path, [])
        return out
    jobs, problem = _ask_deadline()
    if problem:
        out.notes.append(problem)
    try:
        local = prune.local_queue_folders()
    except OSError as error:
        local = set()
        out.notes.append(f"This machine's local farm queue could not be read ({error}).")
    verdicts = prune.plan_prune(
        jobs_path, jobs, min_age_days=settings.job_days,
        local_folders=local, local_min_age_days=settings.local_job_days,
    )
    out.payload = (jobs_path, verdicts)
    for verdict in verdicts:
        reason = verdict.reason
        if reason.startswith('a job that uses it is'):
            reason = prune.Decision.KEEP_ACTIVE.format(status='not completed')
        out.entries.append(Entry(
            verdict.folder.path, f'other/jobs/{verdict.folder.name}', 'other/jobs',
            verdict.folder.size, verdict.prune, reason,
        ))
    return out


def apply(category_plan: CategoryPlan, log_path: Path) -> tuple[int, int, list[str]]:
    jobs_path, verdicts = category_plan.payload
    return prune.apply(verdicts, jobs_path, log_path)


CATEGORY = Category(
    'jobs', 'Farm job folders',
    'the copies of workfiles made for farm jobs, once neither Deadline nor the local farm can run them again',
    (), plan, apply,
)
