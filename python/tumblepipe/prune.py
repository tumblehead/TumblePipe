"""Prune a project's farm job folders once Deadline can no longer need them.

    python -m tumblepipe.prune [project] [--apply] [--min-age-days 2]

Every farm submission writes a folder under ``export/other/jobs/<uuid>/``: a
copy of the workfile and the job's environment, manifest and arguments. The
Deadline workers read the job from there — when it first runs, and again
whenever someone requeues it, days later if need be. On HideAndReek these
were 559 folders and 25 GB, mostly copies of one 150 MB hip.

A folder is pruned only when nothing in Deadline can run from it again:

- every Deadline job that names it (its plugin info points into
  ``…/jobs/<uuid>/``) is **Completed**; or
- no Deadline job names it any more (the jobs were deleted from Deadline)
  and it is older than ``--min-age-days``, so a submission still being
  written is never touched.

A folder any job names as Suspended, Failed, Queued, Rendering or Pending is
kept: that job can be requeued. When Deadline cannot be asked, nothing is
pruned.

This is not the archive tool and the archive tool never calls it: archiving
only reads a project (``export/other/jobs`` is simply left out of every
archive). Pruning deletes, so it lists by default and deletes only with
``--apply``, recording what it removed in
``export/other/logs/prune-<date>.log``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

#: Where farm submissions write their job folders, under the export root.
JOBS_DIR = ('other', 'jobs')
LOGS_DIR = ('other', 'logs')

_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_JOB_FOLDER_REF = re.compile(r'[\\/]other[\\/]jobs[\\/](' + _UUID.pattern + r')[\\/]', re.IGNORECASE)

#: The one Deadline status from which a job cannot run again by itself.
DONE = 'Completed'


class Decision:
    PRUNE_COMPLETED = 'every job that used it completed'
    PRUNE_ORPHANED = 'no Deadline job uses it any more'
    KEEP_ACTIVE = 'a job that uses it is {status} in Deadline and may be requeued'
    KEEP_RECENT = 'no Deadline job uses it, but it is too recent to be sure'
    KEEP_UNKNOWN = 'Deadline could not be asked'


@dataclass(frozen=True)
class JobFolder:
    name: str
    path: Path
    size: int
    mtime: float


@dataclass(frozen=True)
class Verdict:
    folder: JobFolder
    prune: bool
    reason: str


# ── Deadline ───────────────────────────────────────────────────────────────


def parse_jobs(text: str) -> dict[str, list[str]]:
    """``deadlinecommand -GetJobsFilter`` output -> {folder uuid: [statuses]}.

    The output is one ``Key=Value`` block per job, blocks separated by blank
    lines. A job names its folder in ``PluginInfoDictionary`` (``StartupDirectory``,
    ``EnvironmentFile``, ``Manifest`` all point into ``…/other/jobs/<uuid>/``),
    under whatever drive the farm maps the project to, so folders are matched
    by uuid alone.
    """
    folders: dict[str, list[str]] = {}
    for block in re.split(r'\r?\n\s*\r?\n', text):
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.partition('=')
            if sep and key and key not in fields:
                fields[key.strip()] = value
        status = fields.get('JobStatus') or fields.get('Status')
        info = fields.get('PluginInfoDictionary', '')
        if not status or not info:
            continue
        for uuid in {m.group(1).lower() for m in _JOB_FOLDER_REF.finditer(info)}:
            folders.setdefault(uuid, []).append(status.strip())
    return folders


def deadline_command() -> Path | None:
    from tumblepipe.apps.deadline import _get_deadline_path, DeadlineNotInstalled
    try:
        path = _get_deadline_path()
    except (DeadlineNotInstalled, OSError, RuntimeError):
        return None
    return path if Path(path).is_file() else None


def query_deadline(command: Path, timeout: float = 600) -> dict[str, list[str]]:
    """Every TumblePipe (HPM plugin) job Deadline holds, by job folder."""
    import subprocess
    result = subprocess.run(
        # The field is PluginName (or JobPlugin): `Plugin=HPM` matches no job,
        # silently, which would make every folder look orphaned.
        [str(command), '-GetJobsFilter', 'PluginName=HPM'],
        capture_output=True, text=True, timeout=timeout,
        encoding='utf-8', errors='replace',
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'deadlinecommand exited {result.returncode}: {result.stderr.strip()[:300]}'
        )
    jobs = parse_jobs(result.stdout)
    if not jobs:
        # An empty answer cannot be told apart from a filter or parse that
        # matched nothing, and it would mark every folder orphaned.
        raise RuntimeError('Deadline reported no TumblePipe jobs at all')
    return jobs


# ── folders and decisions ──────────────────────────────────────────────────


def _size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def list_job_folders(jobs_path: Path) -> list[JobFolder]:
    """The submission folders: uuid-named directories, nothing else."""
    if not jobs_path.is_dir():
        return []
    folders = []
    for entry in sorted(jobs_path.iterdir()):
        if entry.is_dir() and not entry.is_symlink() and _UUID.fullmatch(entry.name.lower()):
            folders.append(JobFolder(entry.name, entry, _size(entry), entry.stat().st_mtime))
    return folders


def decide(
    folder: JobFolder,
    statuses: list[str] | None,
    *,
    now: float,
    min_age_days: float,
    deadline_known: bool,
) -> Verdict:
    if not deadline_known:
        return Verdict(folder, False, Decision.KEEP_UNKNOWN)
    if statuses:
        live = [s for s in statuses if s != DONE]
        if live:
            return Verdict(folder, False, Decision.KEEP_ACTIVE.format(status=live[0]))
        return Verdict(folder, True, Decision.PRUNE_COMPLETED)
    if now - folder.mtime < min_age_days * 86400:
        return Verdict(folder, False, Decision.KEEP_RECENT)
    return Verdict(folder, True, Decision.PRUNE_ORPHANED)


def plan_prune(
    jobs_path: Path,
    jobs: dict[str, list[str]] | None,
    *,
    now: float | None = None,
    min_age_days: float = 2.0,
) -> list[Verdict]:
    """A verdict per job folder; ``jobs`` None means Deadline was unreachable."""
    now = time.time() if now is None else now
    return [
        decide(
            folder,
            None if jobs is None else jobs.get(folder.name.lower()),
            now=now, min_age_days=min_age_days, deadline_known=jobs is not None,
        )
        for folder in list_job_folders(jobs_path)
    ]


def apply(verdicts: list[Verdict], jobs_path: Path, log_path: Path) -> tuple[int, int, list[str]]:
    """Delete the folders marked prune; returns (folders, bytes, errors)."""
    removed = removed_bytes = 0
    errors = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as log:
        log.write(f'# prune {dt.datetime.now().isoformat(timespec="seconds")} '
                  f'by {os.environ.get("TH_USER") or "unknown"}\n')
        for verdict in verdicts:
            if not verdict.prune:
                continue
            path = verdict.folder.path
            # Only ever a uuid folder directly inside the jobs folder.
            if path.parent.resolve() != jobs_path.resolve() or not _UUID.fullmatch(path.name.lower()):
                errors.append(f'{path}: not a job folder, left alone')
                continue
            try:
                shutil.rmtree(path)
            except OSError as error:
                errors.append(f'{path}: {error}')
                continue
            removed += 1
            removed_bytes += verdict.folder.size
            log.write(f'{verdict.folder.name}\t{verdict.folder.size}\t{verdict.reason}\n')
    return removed, removed_bytes, errors


# ── command line ───────────────────────────────────────────────────────────


def _human(count: int) -> str:
    size = float(count)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{int(size)} B' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{count} B'


def _export_path(project: Path) -> Path:
    from tumblepipe.archive.roots import project_roots
    root = project_roots(project).get('export')
    return root.path if root is not None else project / 'export'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m tumblepipe.prune', description=__doc__.split('\n\n')[0])
    parser.add_argument('project', nargs='?', help='project folder (default: $TH_PROJECT_PATH)')
    parser.add_argument('--apply', action='store_true',
                        help='delete the folders listed as prunable (default: only list them)')
    parser.add_argument('--min-age-days', type=float, default=2.0,
                        help='keep a folder no Deadline job names until it is this old (default 2)')
    args = parser.parse_args(argv)

    project = Path(args.project or os.environ.get('TH_PROJECT_PATH') or '')
    if not project.is_dir():
        print('no project: pass its folder or set TH_PROJECT_PATH', file=sys.stderr)
        return 2
    export_path = _export_path(project)
    jobs_path = export_path.joinpath(*JOBS_DIR)

    command = deadline_command()
    jobs = None
    if command is None:
        print('Deadline is not installed on this machine: nothing can be pruned safely.')
    else:
        print(f'Asking Deadline ({command}) …', flush=True)
        try:
            jobs = query_deadline(command)
        except (OSError, RuntimeError, ValueError) as error:
            print(f'Deadline could not be asked ({error}): nothing will be pruned.')
    verdicts = plan_prune(jobs_path, jobs, min_age_days=args.min_age_days)
    print(f'{len(verdicts)} job folder(s) in {jobs_path}')
    by_reason: dict[tuple[bool, str], list[Verdict]] = {}
    for verdict in verdicts:
        reason = verdict.reason
        if verdict.reason.startswith('a job that uses it is'):
            reason = Decision.KEEP_ACTIVE.format(status='not completed')
        by_reason.setdefault((verdict.prune, reason), []).append(verdict)
    for (prune, reason), group in sorted(by_reason.items(), key=lambda kv: not kv[0][0]):
        size = sum(v.folder.size for v in group)
        print(f'  {"prune" if prune else "keep ":<5}  {len(group):>5} folder(s)  {_human(size):>10}  {reason}')
    prunable = [v for v in verdicts if v.prune]
    if not prunable:
        return 0
    if not args.apply:
        print(f'\nNothing deleted. Run again with --apply to delete the '
              f'{len(prunable)} prunable folder(s) ({_human(sum(v.folder.size for v in prunable))}).')
        return 0
    log_path = export_path.joinpath(*LOGS_DIR, f'prune-{dt.date.today().isoformat()}.log')
    removed, removed_bytes, errors = apply(verdicts, jobs_path, log_path)
    print(f'\nDeleted {removed} folder(s), {_human(removed_bytes)}. Logged to {log_path}.')
    for error in errors:
        print(f'  {error}')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
