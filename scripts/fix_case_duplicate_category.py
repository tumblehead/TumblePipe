"""CLI: merge an asset category that duplicates another by case only.

    python scripts/fix_case_duplicate_category.py <project_path> --from clash --to Clash [--apply]

Windows' case-insensitive filesystem lets two config categories that differ
only in casing (e.g. ``clash`` and ``Clash``) share one on-disk folder, while
URIs and USD prim paths stay case-sensitive. The result is assets whose
metadata scripts target a prim path that doesn't exist, so they silently
drop from exports (and, since v1.17.0, hard-block publishes).

This script performs the config + text-sidecar half of the cleanup:

1. ``_config/db/entity.json``: move the duplicate category's children under
   the canonical key and delete the duplicate (backing the file up first).
2. Rewrite ``entity:/assets/<from>/`` -> ``entity:/assets/<to>/`` in every
   ``.json`` / ``.usda`` under ``export/``, ``assets/`` and ``shots/``.
3. Case-rename files whose names start with ``<from>_`` (staged layers,
   workfile hips) to the canonical prefix, via a two-step rename because
   Windows refuses same-name case renames.
4. Report (never rewrite) binary ``.usd`` crates that embed the wrong-case
   URI - those need a re-export from a fixed workfile instead.

It does NOT touch prim paths inside USD layers or parms inside hip files:
geometry authored under a wrong-case root needs its workfile fixed and the
asset re-exported/restaged, and import parms are re-picked in Houdini.

Dry-run by default; pass ``--apply`` to write. Run
``scripts/verify_entity_casing.py`` afterwards.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TEXT_SUFFIXES = {'.json', '.usda'}
CRATE_SUFFIXES = {'.usd', '.usdc'}
SCAN_ROOTS = ('export', 'assets', 'shots')


def _resolve_project(arg: Path) -> Path:
    """Accept a project root or its _config directory; return the root."""
    path = arg.resolve()
    if path.name == '_config':
        return path.parent
    return path


def _entity_db(project: Path) -> Path:
    return project / '_config' / 'db' / 'entity.json'


def _store_json(path: Path, data: dict):
    """Atomic JSON write matching tumblepipe.util.io.store_json (indent=4)."""
    fd, tmp = tempfile.mkstemp(suffix='.json', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(data, file, indent=4)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def merge_config(project: Path, from_key: str, to_key: str, apply: bool) -> bool:
    db_path = _entity_db(project)
    data = json.loads(db_path.read_text())
    categories = data['children']['assets']['children']

    if from_key not in categories:
        print(f"config: nothing to do - category '{from_key}' not present")
        return False
    if to_key not in categories:
        raise SystemExit(f"config: canonical category '{to_key}' not found")
    if from_key.lower() != to_key.lower():
        raise SystemExit('config: --from and --to must differ only by case')

    from_node = categories[from_key]
    to_node = categories[to_key]
    moved = sorted(from_node.get('children', {}))
    collisions = [name for name in moved if name in to_node.get('children', {})]
    if collisions:
        raise SystemExit(
            f"config: {collisions} exist under BOTH '{from_key}' and "
            f"'{to_key}' - resolve manually before merging"
        )

    print(f"config: move {moved or '(no children)'} from '{from_key}' to '{to_key}', delete '{from_key}'")
    if to_node.get('schema') is None and from_node.get('schema'):
        print(f"config: adopt schema {from_node['schema']!r} onto '{to_key}' (was None)")

    if not apply:
        return True

    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = db_path.with_name(f'entity.json.bak-{stamp}')
    shutil.copy2(db_path, backup)
    print(f'config: backed up to {backup}')

    to_node.setdefault('children', {}).update(from_node.get('children', {}))
    if to_node.get('schema') is None and from_node.get('schema'):
        to_node['schema'] = from_node['schema']
    del categories[from_key]
    _store_json(db_path, data)
    print(f'config: wrote {db_path}')
    return True


def _iter_files(project: Path, suffixes: set[str]):
    for root_name in SCAN_ROOTS:
        root = project / root_name
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if path.is_file() and path.suffix.lower() in suffixes:
                yield path


def rewrite_uris(project: Path, from_key: str, to_key: str, apply: bool) -> int:
    old = f'entity:/assets/{from_key}/'.encode()
    new = f'entity:/assets/{to_key}/'.encode()
    changed = 0
    for path in _iter_files(project, TEXT_SUFFIXES):
        content = path.read_bytes()
        if old not in content:
            continue
        count = content.count(old)
        changed += 1
        print(f'rewrite: {path} ({count} occurrence{"s" if count != 1 else ""})')
        if apply:
            path.write_bytes(content.replace(old, new))
    print(f'rewrite: {changed} file(s) reference entity:/assets/{from_key}/')
    return changed


def rename_files(project: Path, from_key: str, to_key: str, apply: bool) -> int:
    old_prefix = f'{from_key}_'
    new_prefix = f'{to_key}_'
    renamed = 0
    # Materialize before renaming so the walk isn't disturbed.
    for path in list(_iter_files(project, TEXT_SUFFIXES | CRATE_SUFFIXES | {'.hip'})):
        name = path.name
        if not name.startswith(old_prefix):
            continue
        target = path.with_name(new_prefix + name[len(old_prefix):])
        renamed += 1
        print(f'rename:  {path} -> {target.name}')
        if apply:
            # Windows refuses a same-name case rename in one step.
            tmp = path.with_name(name + '.caserename')
            path.rename(tmp)
            tmp.rename(target)
    print(f'rename:  {renamed} file(s) carry the {old_prefix!r} prefix')
    return renamed


def _stream_contains(path: Path, needle: bytes, chunk_size: int = 8 * 1024 * 1024) -> bool:
    """Chunked search so multi-GB crate files never load whole into memory."""
    overlap = len(needle) - 1
    tail = b''
    with path.open('rb') as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                return False
            if needle in tail + chunk[:overlap] or needle in chunk:
                return True
            tail = chunk[-overlap:] if overlap else b''


def report_crates(project: Path, from_key: str) -> int:
    """Binary crates can't be text-patched - list the ones needing re-export."""
    needle = f'entity:/assets/{from_key}/'.encode()
    hits = 0
    for path in _iter_files(project, CRATE_SUFFIXES):
        if _stream_contains(path, needle):
            hits += 1
            print(f'crate:   {path} embeds {needle.decode()!r} - needs re-export')
    if hits == 0:
        print('crate:   no binary layers embed the wrong-case URI')
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project', type=Path, help='project root (or its _config dir)')
    parser.add_argument('--from', dest='from_key', required=True,
                        help='duplicate category key to remove (e.g. clash)')
    parser.add_argument('--to', dest='to_key', required=True,
                        help='canonical category key to keep (e.g. Clash)')
    parser.add_argument('--apply', action='store_true',
                        help='write changes (default: dry-run report)')
    args = parser.parse_args()

    project = _resolve_project(args.project)
    if not _entity_db(project).exists():
        raise SystemExit(f'{_entity_db(project)} not found - is this a project root?')

    mode = 'APPLY' if args.apply else 'DRY-RUN'
    print(f'== {mode}: merge {args.from_key!r} -> {args.to_key!r} in {project} ==')
    merge_config(project, args.from_key, args.to_key, args.apply)
    rewrite_uris(project, args.from_key, args.to_key, args.apply)
    rename_files(project, args.from_key, args.to_key, args.apply)
    crates = report_crates(project, args.from_key)

    if not args.apply:
        print('\ndry-run only - re-run with --apply to write')
    if crates:
        print(f'\nNOTE: {crates} binary layer(s) still embed the old URI - '
              'fix the source workfile and re-export those.')
    print('next: run scripts/verify_entity_casing.py, then fix wrong-case '
          'geometry roots in Houdini (re-export + restage) and re-pick the '
          'entity on any import nodes that stored the old URI.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
