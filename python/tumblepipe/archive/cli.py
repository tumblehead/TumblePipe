"""``python -m tumblepipe.archive``: plan, create, verify and restore archives.

    python -m tumblepipe.archive plan    [project] [--profile proof|latest|full]
                                         [--with-caches] [--with-renders] [--list FILE]
    python -m tumblepipe.archive create  [project] --out ARCHIVE.zip [same options]
                                         [--part-size 50G]
    python -m tumblepipe.archive verify  ARCHIVE.zip
    python -m tumblepipe.archive restore ARCHIVE.zip --to FOLDER [--name NEW] [--overwrite]
    python -m tumblepipe.archive rebuild [restored project] [--dry-run] [--hython PATH]

``project`` defaults to ``$TH_PROJECT_PATH``, its config to
``$TH_CONFIG_PATH`` (else ``<project>/_config``). ``plan`` reads the project
and writes nothing; ``--list`` also writes every file's decision to FILE.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from tumblepipe.archive import inventory as inventory_mod
from tumblepipe.archive import pack
from tumblepipe.archive.plan import Options, Plan, Profile, make_plan
from tumblepipe.archive.roots import project_roots

_UNITS = {'': 1, 'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30, 'T': 1 << 40}


def parse_size(text: str) -> int:
    """``50G``, ``700M``, ``1048576`` -> bytes."""
    raw = text.strip().upper().removesuffix('B')
    unit = raw[-1] if raw and raw[-1] in _UNITS else ''
    number = raw[:-1] if unit else raw
    try:
        value = float(number)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not a size: {text!r}') from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f'size must be positive: {text!r}')
    return int(value * _UNITS[unit])


def human(count: int) -> str:
    size = float(count)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{int(size)} B' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{count} B'


def build_plan(args) -> Plan:
    project = args.project or os.environ.get('TH_PROJECT_PATH')
    if not project:
        raise SystemExit('no project: pass its folder or set TH_PROJECT_PATH')
    config = os.environ.get('TH_CONFIG_PATH') if not args.project else None
    roots = project_roots(Path(project), Path(config) if config else None)
    print(f'Reading {roots.project_path} …', flush=True)
    started = time.monotonic()
    inventory = inventory_mod.scan(roots)
    options = Options(args.profile, args.with_caches, args.with_renders)
    plan = make_plan(inventory, options)
    print(f'  {len(inventory.items)} files in {time.monotonic() - started:.0f} s', flush=True)
    return plan


def print_plan(plan: Plan) -> None:
    print(f'\nProfile: {plan.options.profile}'
          + (' + caches' if plan.options.with_caches else '')
          + (' + renders' if plan.options.with_renders else ''))
    print('\nRoots:')
    for root in plan.inventory.roots.roots:
        state = '' if root.path.is_dir() else '  (missing)'
        print(f'  {root.purpose:<10} {root.path}  ->  {root.prefix}/{state}')
    summary = plan.summary()
    print(f'\n  {"kind":<16} {"archived":>22} {"left out":>22}')
    total_in = total_out = 0
    for kind, row in sorted(summary.items(), key=lambda kv: -(kv[1]['bytes_in'] + kv[1]['bytes_out'])):
        total_in += row['bytes_in']
        total_out += row['bytes_out']
        archived = f'{row["files_in"]} / {human(row["bytes_in"])}' if row['files_in'] else '-'
        left = f'{row["files_out"]} / {human(row["bytes_out"])}' if row['files_out'] else '-'
        print(f'  {kind:<16} {archived:>22} {left:>22}')
    print(f'  {"total":<16} {human(total_in):>22} {human(total_out):>22}')
    print('\nLeft out, by reason:')
    for reason, row in sorted(plan.excluded_by_reason().items(), key=lambda kv: -kv[1]['bytes']):
        print(f'  {human(row["bytes"]):>10}  {row["files"]:>7} files  {reason}')
    if plan.warnings:
        print('\nCheck before sending:')
        for warning in plan.warnings:
            print(f'  - {warning}')


def write_list(plan: Plan, path: Path) -> None:
    with Path(path).open('w', encoding='utf-8') as handle:
        handle.write('decision\tkind\tbytes\tpath\treason\n')
        for decision in plan.decisions:
            item = decision.item
            root = plan.inventory.roots.get(item.root)
            handle.write(
                f'{"in" if decision.include else "out"}\t{item.kind}\t{item.size}\t'
                f'{root.prefix}/{item.rel}\t{decision.reason}\n'
            )


class _Printer:
    """Progress on one line, at most every half second."""

    def __init__(self, verb: str):
        self.verb = verb
        self.last = 0.0

    def __call__(self, state: pack.Progress) -> None:
        now = time.monotonic()
        if now - self.last < 0.5 and state.files_done != state.files_total:
            return
        self.last = now
        percent = 100 * state.bytes_done / state.bytes_total if state.bytes_total else 100
        print(
            f'\r  {self.verb} {state.files_done}/{state.files_total} files, '
            f'{human(state.bytes_done)} / {human(state.bytes_total)} ({percent:.0f}%)   ',
            end='', flush=True,
        )


def _add_plan_options(parser) -> None:
    parser.add_argument('project', nargs='?', help='project folder (default: $TH_PROJECT_PATH)')
    parser.add_argument('--profile', choices=Profile.ALL, default=Profile.PROOF)
    parser.add_argument('--with-caches', action='store_true',
                        help='keep the latest complete version of every cache')
    parser.add_argument('--with-renders', action='store_true',
                        help='keep the latest version of every render, playblast and daily')
    parser.add_argument('--list', metavar='FILE',
                        help='write every file and its decision to FILE (tab-separated)')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m tumblepipe.archive')
    commands = parser.add_subparsers(dest='command', required=True)

    plan_parser = commands.add_parser('plan', help='show what an archive would hold')
    _add_plan_options(plan_parser)

    create_parser = commands.add_parser('create', help='write an archive')
    _add_plan_options(create_parser)
    create_parser.add_argument('--out', required=True, help='the archive file (.zip)')
    create_parser.add_argument('--part-size', type=parse_size,
                               help='split into parts of at most this size (e.g. 50G)')

    verify_parser = commands.add_parser('verify', help='check an archive against its manifest')
    verify_parser.add_argument('archive')

    restore_parser = commands.add_parser('restore', help='unpack an archive')
    restore_parser.add_argument('archive')
    restore_parser.add_argument('--to', required=True, help='folder to unpack into')
    restore_parser.add_argument('--name', help='restore the project under a new name')
    restore_parser.add_argument('--overwrite', action='store_true',
                                help='replace files that already exist')

    rebuild_parser = commands.add_parser(
        'rebuild', help="re-make the caches an archive left out, in a restored project")
    rebuild_parser.add_argument('project', nargs='?',
                                help='the restored project (default: $TH_PROJECT_PATH)')
    rebuild_parser.add_argument('--manifest',
                                help='the archive manifest (default: the one restore left in _archive/)')
    rebuild_parser.add_argument('--hython', help='the hython to run (default: $HFS/bin/hython)')
    rebuild_parser.add_argument('--dry-run', action='store_true',
                                help='list what would be re-made, without running Houdini')

    args = parser.parse_args(argv)
    try:
        if args.command in ('plan', 'create'):
            plan = build_plan(args)
            print_plan(plan)
            if args.list:
                write_list(plan, Path(args.list))
                print(f'\nWrote the file list to {args.list}')
            if args.command == 'plan':
                return 0
            print(f'\nWriting {args.out} …')
            manifest = pack.create(
                plan, Path(args.out), part_size=args.part_size, progress=_Printer('packed'),
            )
            print(f'\nDone: {len(manifest["files"])} files in {", ".join(manifest["parts"])}')
            if manifest['skipped']:
                print(f'{len(manifest["skipped"])} file(s) could not be read and were skipped:')
                for line in manifest['skipped'][:20]:
                    print(f'  {line}')
                return 1
            return 0
        if args.command == 'verify':
            result = pack.verify(Path(args.archive), progress=_Printer('checked'))
            print()
            if result.ok:
                print(f'OK: {result.checked} files match the manifest')
                return 0
            print(f'{len(result.problems)} problem(s):')
            for problem in result.problems:
                print(f'  {problem}')
            return 1
        if args.command == 'rebuild':
            from tumblepipe.archive.rebuild import main_rebuild
            return main_rebuild(args)
        if args.command == 'restore':
            manifest = pack.restore(
                Path(args.archive), Path(args.to), new_name=args.name,
                overwrite=args.overwrite, progress=_Printer('restored'),
            )
            print(f'\nRestored {len(manifest["files"])} files into {args.to}')
            return 0
    except pack.ArchiveError as error:
        print(f'\nerror: {error}', file=sys.stderr)
        return 1
    return 2
