"""CLI: sweep workfiles for LOPs with H22's Layer Save Path left enabled.

    hython scripts/fix_enabled_savepaths.py <project_path> [--apply]
    hython scripts/fix_enabled_savepaths.py --files a.hip b.hip [--apply]

H22 creates sopcreate / sopimport LOPs with 'Layer Save Path' enabled at
$HIP/usd/$OS.usd (H21 shipped it off), so every workfile touched under H22
may carry nodes that write their layer next to the workfile on export and
publish a hollow layer (see pipe.usd.find_escaping_layer_paths). The
shipped scripts/lop/*_OnCreated.py guards stop NEW nodes; this script
retrofits the workfiles that already saved the enabled state.

Given a project path it scans ``assets/``, ``shots/`` and ``groups/`` for
hip files whose raw bytes carry an enabled toggle (cheap pre-filter — the
saved form is ``enable_savepath ... ( "on" )``), then opens each candidate
and reports every sopcreate / sopimport / sopmodify LOP outside locked
HDAs with the toggle on. Dry-run by default (exit 1 when anything is
flagged, like the verify_* scripts); ``--apply`` disables the toggle,
clears the path and resaves, writing a ``<name>_savepath_bak.hip`` sibling
under ``backup/`` first (suppress with ``--no-backup``).

Locked-HDA internals are skipped on purpose: the 2026-07-07 audit found
every shipped HDA stores the toggle off, and instance-level edits inside
locked assets don't belong to the workfile anyway. Non-``.hip`` variants
(``.hiplc``/``.hipnc``) are skipped with a warning — loading one downgrades
the hython session's license and would poison later commercial saves; run
those separately if any turn up.

Runs under hython (needs ``hou``); no pipeline imports, so any build works.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

import hou

WATCH_TYPES = ('sopcreate', 'sopimport', 'sopmodify')
SCAN_ROOTS = ('assets', 'shots', 'groups')
# The exact form a hip file stores an enabled toggle in.
ENABLED_SIGNATURE = re.compile(rb'enable_savepath\s*\[[^\]]*\]\s*\(\s*"?on"?\s*\)')


def _candidate_hips(project_path: Path) -> list[Path]:
    """Hip files under the workfile trees whose bytes carry an enabled toggle."""
    candidates = []
    for root_name in SCAN_ROOTS:
        root = project_path / root_name
        if not root.is_dir():
            continue
        for hip in sorted(root.rglob('*.hip*')):
            if not hip.is_file():
                continue
            try:
                if ENABLED_SIGNATURE.search(hip.read_bytes()):
                    candidates.append(hip)
            except OSError as error:
                print(f'  ! unreadable, skipped: {hip} ({error})')
    return candidates


def _find_enabled_nodes() -> list:
    """Watch-type LOPs in the loaded scene (outside locked HDAs) with the toggle on."""
    found = []
    for node in hou.node('/').allSubChildren(True, False):
        if node.type().name() not in WATCH_TYPES:
            continue
        if node.type().category().name() != 'Lop':
            continue
        enable_parm = node.parm('enable_savepath')
        if enable_parm is not None and enable_parm.eval():
            found.append(node)
    return found


def _backup(hip_path: Path) -> Path:
    backup_dir = hip_path.parent / 'backup'
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f'{hip_path.stem}_savepath_bak{hip_path.suffix}'
    shutil.copy2(hip_path, backup_path)
    return backup_path


def _process(hip_path: Path, apply: bool, backup: bool) -> int:
    """Report (and with apply=True fix) one hip. Returns flagged-node count."""
    try:
        hou.hipFile.load(str(hip_path), suppress_save_prompt=True, ignore_load_warnings=True)
    except hou.LoadWarning:
        pass  # missing HDAs etc. — parms still load
    except hou.OperationFailed as error:
        print(f'  ! load failed, skipped: {hip_path} ({error})')
        return 0

    nodes = _find_enabled_nodes()
    if not nodes:
        return 0

    print(hip_path)
    for node in nodes:
        savepath_parm = node.parm('savepath')
        raw = savepath_parm.rawValue() if savepath_parm is not None else ''
        print(f'  - {node.path()} ({node.type().name()}) savepath={raw!r}')
        if apply:
            node.parm('enable_savepath').set(False)
            if savepath_parm is not None:
                savepath_parm.set('')

    if apply:
        if backup:
            print(f'  backed up to {_backup(hip_path)}')
        hou.hipFile.save(str(hip_path))
        print(f'  fixed {len(nodes)} node(s) and saved')
    return len(nodes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project_path', nargs='?', type=Path,
                        help='project root to scan (assets/, shots/, groups/)')
    parser.add_argument('--files', nargs='+', type=Path,
                        help='explicit hip files instead of a project scan')
    parser.add_argument('--apply', action='store_true',
                        help='disable the save paths and resave (default: report only)')
    parser.add_argument('--no-backup', action='store_true',
                        help='with --apply: skip the backup/ sibling copy')
    args = parser.parse_args()

    if (args.project_path is None) == (args.files is None):
        parser.error('give a project path or --files, not both')

    if args.files is not None:
        hips = args.files
    else:
        if not args.project_path.is_dir():
            parser.error(f'not a directory: {args.project_path}')
        print(f'scanning {args.project_path} for candidate hips...')
        hips = _candidate_hips(args.project_path)
        print(f'{len(hips)} candidate file(s)\n')

    flagged_files = 0
    flagged_nodes = 0
    for hip in hips:
        if hip.suffix != '.hip':
            print(f'  ! non-.hip skipped (license downgrade risk, run separately): {hip}')
            continue
        if not hip.is_file():
            print(f'  ! missing, skipped: {hip}')
            continue
        count = _process(hip, args.apply, not args.no_backup)
        if count:
            flagged_files += 1
            flagged_nodes += count

    verb = 'fixed' if args.apply else 'flagged'
    print(f'\n{verb} {flagged_nodes} node(s) across {flagged_files} file(s)')
    return 0 if (args.apply or flagged_nodes == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
