"""``python -m tumblepipe.cleanup``: list what a project no longer needs,
and delete it with ``--apply``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tumblepipe.archive.roots import project_roots
from tumblepipe.cleanup import core


def _parser() -> argparse.ArgumentParser:
    defaults = core.Settings()
    about = '\n'.join(f'  {c.key:<8} {c.about}' for c in core.categories())
    parser = argparse.ArgumentParser(
        prog='python -m tumblepipe.cleanup',
        description='List what a project no longer needs, by category, and delete it with --apply.',
        epilog=f'Categories:\n{about}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('project', nargs='?', help='project folder (default: $TH_PROJECT_PATH)')
    parser.add_argument('--only', default=None, metavar='CATEGORIES',
                        help=f'comma-separated categories (default: all of {",".join(core.CATEGORY_KEYS)})')
    parser.add_argument('--context', default=None,
                        help='exports, renders, caches and backups: only shots, only assets, or a path under them '
                             '(shots/sq010); job folders and deleted entities are of the whole project')
    parser.add_argument('--keep-exports', type=int, default=defaults.keep_exports,
                        help=f'finished versions to keep of every export (default {defaults.keep_exports})')
    parser.add_argument('--keep-renders', type=int, default=defaults.keep_renders,
                        help=f'complete versions to keep of every render layer (default {defaults.keep_renders})')
    parser.add_argument('--check-render-days', type=float, default=defaults.check_render_days,
                        help=f'delete a check render once it is this old (default {defaults.check_render_days:g})')
    parser.add_argument('--keep-caches', type=int, default=defaults.keep_caches,
                        help=f'finished versions to keep of every th::cache (default {defaults.keep_caches})')
    parser.add_argument('--keep-backups', type=int, default=defaults.keep_backups,
                        help="Houdini backups to keep of each department's latest workfile version "
                             f'(default {defaults.keep_backups})')
    parser.add_argument('--backup-days', type=float, default=defaults.backup_days,
                        help=f'never delete a backup newer than this (default {defaults.backup_days:g})')
    parser.add_argument('--deleted-days', type=float, default=defaults.deleted_days,
                        help=f'empty a _deleted folder once its delete is this old (default {defaults.deleted_days:g})')
    parser.add_argument('--job-days', type=float, default=defaults.job_days,
                        help=f'keep a farm job folder no Deadline job names until it is this old (default {defaults.job_days:g})')
    parser.add_argument('--local-job-days', type=float, default=defaults.local_job_days,
                        help="keep a local farm job folder no batch on this machine uses until it is this old "
                             f'(default {defaults.local_job_days:g})')
    parser.add_argument('--apply', action='store_true',
                        help='delete what is listed to delete (default: only list it)')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = core.Settings(
        context=args.context, keep_exports=args.keep_exports, keep_renders=args.keep_renders,
        check_render_days=args.check_render_days, keep_caches=args.keep_caches, keep_backups=args.keep_backups,
        backup_days=args.backup_days, deleted_days=args.deleted_days,
        job_days=args.job_days, local_job_days=args.local_job_days,
    )
    problem = settings.problem()
    if problem:
        print(problem, file=sys.stderr)
        return 2
    only = None
    if args.only:
        only = tuple(key.strip() for key in args.only.split(',') if key.strip())
        unknown = sorted(set(only) - set(core.CATEGORY_KEYS))
        if unknown:
            print(f'no such category: {", ".join(unknown)} (there are {", ".join(core.CATEGORY_KEYS)})',
                  file=sys.stderr)
            return 2
    project = Path(args.project or os.environ.get('TH_PROJECT_PATH') or '')
    if not project.is_dir():
        print('no project: pass its folder or set TH_PROJECT_PATH', file=sys.stderr)
        return 2

    roots = project_roots(project)
    plan = core.make_plan(roots, settings, only, progress=lambda text: print(text, flush=True))
    print()
    print('\n'.join(core.summary(plan)))
    if not plan.prunable:
        return 0
    if not args.apply:
        print(f'\nNothing deleted. Run again with --apply to delete the {len(plan.prunable)} '
              f'listed ({core.human(plan.prunable_size)}).')
        return 0
    log_path = core.log_path_for(roots)
    results = core.apply(plan, log_path)
    failed = False
    print()
    for category in plan.categories:
        removed, removed_bytes, errors = results[category.key]
        if removed or errors:
            print(f'{category.title}: deleted {removed}, {core.human(removed_bytes)}.')
        for error in errors:
            failed = True
            print(f'  {error}')
    print(f'Logged to {log_path}.')
    return 1 if failed else 0
