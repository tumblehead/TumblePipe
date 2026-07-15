"""CLI: find (and optionally retire) legacy ``W:/_pipeline`` OCIO setters.

    python scripts/audit_legacy_ocio.py <projects_root>   [--apply]
    python scripts/audit_legacy_ocio.py --projects P:/paleindia P:/Snail [--apply]
    python scripts/audit_legacy_ocio.py --files P:/paleindia/_pipeline/_project_config.bat

Color config is now project-owned (``<project>/_config/ocio/tumblehead.ocio``,
seeded by migration v4 / ``migrate_config.py``). But the vestigial per-project
launch scripts at ``<project>/_pipeline/_project_config.bat`` still carry a
``SET OCIO=%TH_PIPELINE_PATH%/ocio/tumblehead.ocio`` that resolves under the
legacy ``W:/_pipeline`` tree. Any launch that sources one of those bats pins
OCIO at the drive-side legacy config — the source the ``ocio-project-owned``
work set out to retire (see [[ocio-project-owned-2026-07]]). Desktop launches
are unaffected (they never source these bats); this only bites the legacy
double-click-the-bat entry point.

This scans project launch bats, resolves each ``%VAR%`` reference against the
same bat's ``SET`` lines, and flags any whose effective OCIO lands under
``_pipeline``. For each hit it also reports whether the project already owns a
``_config/ocio/tumblehead.ocio`` (i.e. whether it is safe to repoint yet).

Dry-run by default (exit 1 when anything is flagged, like the ``verify_*``
scripts). ``--apply`` repoints the setter at ``%TH_CONFIG_PATH%/ocio/tumblehead.ocio``
— but ONLY when the bat defines ``TH_CONFIG_PATH`` and that project-owned config
file actually exists, so a legacy launch never ends up pointing at a missing
file. It backs the bat up to a ``backup/`` sibling first (suppress with
``--no-backup``) and leaves the original OCIO line commented above the rewrite,
so the edit is reversible by hand. Projects missing ``_config/ocio/`` are
reported and skipped — run ``migrate_config.py`` on them first.

Pure stdlib (no ``hou``); reads/writes only the ``.bat`` launch scripts.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

# A batch ``SET NAME=VALUE`` line (optionally quoted as SET "NAME=VALUE").
SET_LINE = re.compile(r'^\s*set\s+"?(?P<name>[^=\s"]+)=(?P<value>[^"\r\n]*)"?\s*$',
                      re.IGNORECASE)
# %VAR% reference, expanded against the same bat's SET map.
VAR_REF = re.compile(r'%([^%]+)%')
# The project-owned config the retire repoints at.
PROJECT_OCIO_VALUE = '%TH_CONFIG_PATH%/ocio/tumblehead.ocio'


def _parse_set_vars(lines: list[str]) -> dict[str, str]:
    """Map UPPER var name -> raw value from every SET line (last wins, as cmd does)."""
    variables: dict[str, str] = {}
    for line in lines:
        match = SET_LINE.match(line)
        if match:
            variables[match.group('name').upper()] = match.group('value').strip()
    return variables


def _resolve(value: str, variables: dict[str, str]) -> str:
    """Expand %VAR% against the SET map, iterating so %A%->%B% chains collapse."""
    for _ in range(10):  # generous cap; batch configs never chain this deep
        expanded = VAR_REF.sub(
            lambda m: variables.get(m.group(1).upper(), m.group(0)), value)
        if expanded == value:
            break
        value = expanded
    return value


def _is_legacy_path(resolved: str) -> bool:
    """True when the resolved OCIO path lives under a ``_pipeline`` tree."""
    normalized = resolved.replace('\\', '/').lower()
    return '/_pipeline/' in normalized


def _ocio_line_index(lines: list[str]) -> int | None:
    """Index of the last ``SET OCIO=...`` line, or None."""
    found = None
    for index, line in enumerate(lines):
        match = SET_LINE.match(line)
        if match and match.group('name').upper() == 'OCIO':
            found = index
    return found


def _project_owned_ocio(bat_path: Path) -> Path:
    """The project-owned config path for the project owning this launch bat."""
    # <project>/_pipeline/_project_config.bat -> <project>/_config/ocio/tumblehead.ocio
    project_root = bat_path.parents[1]
    return project_root / '_config' / 'ocio' / 'tumblehead.ocio'


def _backup(bat_path: Path) -> Path:
    backup_dir = bat_path.parent / 'backup'
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f'{bat_path.stem}_ocio_bak{bat_path.suffix}'
    shutil.copy2(bat_path, backup_path)
    return backup_path


def _process(bat_path: Path, apply: bool, backup: bool) -> str:
    """Report (and with apply=True retire) one launch bat.

    Returns one of: ``'clean'`` (no legacy setter), ``'flagged'`` (found; report
    mode), ``'retired'`` (rewrote), ``'skipped'`` (found but could not rewrite).
    """
    try:
        raw = bat_path.read_bytes().decode('utf-8', errors='replace')
    except OSError as error:
        print(f'  ! unreadable, skipped: {bat_path} ({error})')
        return 'clean'

    lines = raw.splitlines(keepends=True)
    variables = _parse_set_vars(lines)
    ocio_raw = variables.get('OCIO')
    if ocio_raw is None:
        return 'clean'

    resolved = _resolve(ocio_raw, variables)
    if not _is_legacy_path(resolved):
        return 'clean'

    owned = _project_owned_ocio(bat_path)
    owned_exists = owned.is_file()
    print(bat_path)
    print(f'  OCIO = {ocio_raw!r}  ->  {resolved}')
    print(f'  project-owned config {"present" if owned_exists else "MISSING"}: {owned}')

    if not apply:
        return 'flagged'

    if 'TH_CONFIG_PATH' not in variables:
        print('  ! bat has no TH_CONFIG_PATH - cannot repoint safely, skipped')
        return 'skipped'
    if not owned_exists:
        print('  ! project-owned config missing - run migrate_config.py first, skipped')
        return 'skipped'

    index = _ocio_line_index(lines)
    if index is None:  # resolved from an inherited var, not a literal line here
        print('  ! no literal SET OCIO= line to rewrite, skipped')
        return 'skipped'

    original = lines[index]
    newline = '\r\n' if original.endswith('\r\n') else '\n'
    lines[index] = (
        f'REM [retired-legacy-ocio] {original.rstrip()}{newline}'
        f'SET OCIO={PROJECT_OCIO_VALUE}{newline}'
    )
    if backup:
        print(f'  backed up to {_backup(bat_path)}')
    bat_path.write_bytes(''.join(lines).encode('utf-8'))
    print(f'  repointed OCIO -> {PROJECT_OCIO_VALUE}')
    return 'retired'


def _collect_bats(args: argparse.Namespace) -> list[Path]:
    if args.files:
        return list(args.files)
    if args.projects:
        return [p / '_pipeline' / '_project_config.bat' for p in args.projects]
    return sorted(args.projects_root.glob('*/_pipeline/_project_config.bat'))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('projects_root', nargs='?', type=Path,
                        help='dir of project subdirs to scan (e.g. P:/)')
    parser.add_argument('--projects', nargs='+', type=Path,
                        help='explicit project roots instead of a root scan')
    parser.add_argument('--files', nargs='+', type=Path,
                        help='explicit launch bats instead of a root scan')
    parser.add_argument('--apply', action='store_true',
                        help='repoint legacy setters at the project-owned config '
                             '(default: report only)')
    parser.add_argument('--no-backup', action='store_true',
                        help='with --apply: skip the backup/ sibling copy')
    args = parser.parse_args()

    sources = [args.projects_root is not None, bool(args.projects), bool(args.files)]
    if sum(sources) != 1:
        parser.error('give exactly one of: projects_root, --projects, --files')

    bats = _collect_bats(args)
    counts = {'flagged': 0, 'retired': 0, 'skipped': 0}
    for bat in bats:
        if not bat.is_file():
            print(f'  ! missing, skipped: {bat}')
            continue
        status = _process(bat, args.apply, not args.no_backup)
        if status in counts:
            counts[status] += 1

    if args.apply:
        print(f'\nretired {counts["retired"]} project(s); '
              f'{counts["skipped"]} skipped (need migrate_config.py or manual review)')
        return 0 if counts['skipped'] == 0 else 1
    total = counts['flagged']
    print(f'\nflagged legacy OCIO setter in {total} project(s)')
    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
