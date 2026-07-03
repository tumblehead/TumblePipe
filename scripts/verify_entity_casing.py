"""CLI: verify a project's entity casing is consistent end to end.

    python scripts/verify_entity_casing.py <project_path> [--usd]

Checks (read-only; exit 1 when anything is flagged):

1. config    - no two keys anywhere in entity.json differ only by case.
2. sidecars  - every ``entity:/assets/...`` / ``entity:/shots/...`` URI in
               ``.json``/``.usda`` files under export/, assets/ and shots/
               matches a config entity path with EXACT case.
3. filenames - no file's name starts with a case-variant of a config
               category prefix (e.g. ``clash_...`` when config says Clash).
4. crates    - with ``--crates``: binary ``.usd`` layers embedding a
               case-variant URI (these need a re-export; text patching
               cannot fix them). Off by default - proving absence means
               reading every crate byte in the project, which can take
               a long time on a network share.
5. usd roots - with ``--usd`` (needs the pxr module, e.g.
               ``uvx --with usd-core python scripts/verify_entity_casing.py ... --usd``):
               open every asset's latest export per department and flag root
               prims whose name is a case-variant of the config category or
               asset name. This is the check that catches geometry authored
               under a wrong-case root (config merge alone cannot fix that).

Companion to scripts/fix_case_duplicate_category.py.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

TEXT_SUFFIXES = {'.json', '.usda'}
CRATE_SUFFIXES = {'.usd', '.usdc'}
SCAN_ROOTS = ('export', 'assets', 'shots')
URI_PATTERN = re.compile(rb'entity:/((?:assets|shots)/[^?@"\'\s\\]+)')


def _resolve_project(arg: Path) -> Path:
    path = arg.resolve()
    if path.name == '_config':
        return path.parent
    return path


def _load_entity_tree(project: Path) -> dict:
    db_path = project / '_config' / 'db' / 'entity.json'
    if not db_path.exists():
        raise SystemExit(f'{db_path} not found - is this a project root?')
    return json.loads(db_path.read_text())


def _iter_files(project: Path, suffixes: set[str]):
    for root_name in SCAN_ROOTS:
        root = project / root_name
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if path.is_file() and path.suffix.lower() in suffixes:
                yield path


def check_config_duplicates(data: dict) -> list[str]:
    findings = []

    def walk(node: dict, trail: list[str]):
        children = node.get('children') or {}
        lowered = Counter(key.lower() for key in children)
        for key, count in lowered.items():
            if count > 1:
                variants = sorted(k for k in children if k.lower() == key)
                findings.append(f"config: /{'/'.join(trail)} has case-duplicate keys {variants}")
        for key, child in children.items():
            walk(child, trail + [key])

    walk(data, [])
    return findings


def _canonical_paths(data: dict) -> dict[str, str]:
    """Map lowercased entity path -> canonical-cased path from config."""
    canonical = {}

    def walk(node: dict, trail: list[str]):
        children = node.get('children') or {}
        for key, child in children.items():
            path = '/'.join(trail + [key])
            canonical[path.lower()] = path
            walk(child, trail + [key])

    walk(data, [])
    return canonical


def _case_mismatched_uris(content: bytes, canonical: dict[str, str]) -> set[tuple[str, str]]:
    mismatches = set()
    for match in URI_PATTERN.finditer(content):
        path = match.group(1).decode(errors='replace')
        expected = canonical.get(path.lower())
        if expected is not None and expected != path:
            mismatches.add((path, expected))
    return mismatches


def check_sidecar_uris(project: Path, canonical: dict[str, str]) -> list[str]:
    findings = []
    for path in _iter_files(project, TEXT_SUFFIXES):
        for found, expected in sorted(_case_mismatched_uris(path.read_bytes(), canonical)):
            findings.append(f'sidecar: {path}: entity:/{found} (config says {expected})')
    return findings


def check_filenames(project: Path, data: dict) -> list[str]:
    categories = (data.get('children', {}).get('assets', {}).get('children') or {})
    # Only a full <category>_<asset> filename prefix counts - a lone
    # category-shaped first segment hits pipeline-internal files like
    # set_metadata.usd (vs a 'SET' category). While a case-duplicate still
    # exists in config there is no single canonical casing to compare
    # against, so those pairs collapse to whichever key wins; the config
    # check already reports the duplicate itself.
    pairs = {
        (category.lower(), asset.lower()): f'{category}_{asset}'
        for category, node in categories.items()
        for asset in (node.get('children') or {})
    }
    findings = []
    for path in _iter_files(project, TEXT_SUFFIXES | CRATE_SUFFIXES | {'.hip'}):
        # Export 'stage/' intermediates carry artist- and node-derived names
        # (e.g. CLASH_SET_POSITION.usd), not pipeline naming - and hips
        # reference them by that exact name, so they must not be flagged
        # for a case rename.
        if 'stage' in path.parts:
            continue
        segments = path.name.split('_')
        if len(segments) < 2:
            continue
        expected = pairs.get((segments[0].lower(), segments[1].lower()))
        if expected is not None and f'{segments[0]}_{segments[1]}' != expected:
            findings.append(f'filename: {path} (prefix {segments[0]}_{segments[1]}, config says {expected})')
    return findings


def _iter_chunks(path: Path, overlap: int = 4096, chunk_size: int = 8 * 1024 * 1024):
    """Overlapping chunks so multi-GB crates never load whole into memory.

    The overlap means a URI spanning a chunk boundary is still seen (in the
    next chunk's head); duplicated matches collapse in the caller's set.
    """
    tail = b''
    with path.open('rb') as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                return
            yield tail + chunk
            tail = chunk[-overlap:]


def check_crates(project: Path, canonical: dict[str, str]) -> list[str]:
    findings = []
    for path in _iter_files(project, CRATE_SUFFIXES):
        for chunk in _iter_chunks(path):
            mismatches = _case_mismatched_uris(chunk, canonical)
            if mismatches:
                found, expected = sorted(mismatches)[0]
                findings.append(
                    f'crate: {path}: entity:/{found} (config says {expected}) - needs re-export'
                )
                break  # one finding per file is enough; skip the rest of it
    return findings


def check_usd_roots(project: Path, data: dict) -> list[str]:
    try:
        from pxr import Usd
    except ImportError:
        print('usd roots: SKIPPED - pxr not importable; run via '
              '"uvx --with usd-core python scripts/verify_entity_casing.py ... --usd"')
        return []

    findings = []
    categories = (data.get('children', {}).get('assets', {}).get('children') or {})
    for category, cat_node in categories.items():
        for asset, _ in (cat_node.get('children') or {}).items():
            asset_dir = project / 'export' / 'assets' / category / asset
            if not asset_dir.exists():
                continue
            for variant_dir in sorted(asset_dir.iterdir()):
                if not variant_dir.is_dir() or variant_dir.name == '_staged':
                    continue
                for dept_dir in sorted(variant_dir.iterdir()):
                    versions = sorted(v for v in dept_dir.glob('v*') if v.is_dir())
                    if not versions:
                        continue
                    layers = [f for f in versions[-1].iterdir()
                              if f.suffix.lower() in CRATE_SUFFIXES | {'.usda'}]
                    if not layers:
                        continue
                    label = f'{category}/{asset} {dept_dir.name}@{versions[-1].name}'
                    try:
                        stage = Usd.Stage.Open(str(layers[0]))
                    except Exception as error:  # pragma: no cover - pxr raises Tf errors
                        findings.append(f'usd: {label}: failed to open ({error})')
                        continue
                    roots = {str(p.GetPath()).split('/')[1]
                             for p in stage.TraverseAll()
                             if len(str(p.GetPath()).split('/')) > 1}
                    for root_name in sorted(roots):
                        for expected in (category, asset):
                            if root_name.lower() == expected.lower() and root_name != expected:
                                findings.append(
                                    f'usd: {label}: root prim /{root_name} is a case-variant '
                                    f'of {expected!r} - fix the workfile and re-export'
                                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project', type=Path, help='project root (or its _config dir)')
    parser.add_argument('--usd', action='store_true',
                        help='also open latest exports and check root prim casing (needs pxr)')
    parser.add_argument('--crates', action='store_true',
                        help='also byte-scan binary .usd layers for wrong-case URIs (slow)')
    args = parser.parse_args()

    project = _resolve_project(args.project)
    data = _load_entity_tree(project)
    canonical = _canonical_paths(data)

    findings = []
    findings += check_config_duplicates(data)
    findings += check_sidecar_uris(project, canonical)
    findings += check_filenames(project, data)
    if args.crates:
        findings += check_crates(project, canonical)
    if args.usd:
        findings += check_usd_roots(project, data)

    if findings:
        print(f'== {len(findings)} finding(s) in {project} ==')
        for finding in findings:
            print(finding)
        return 1
    print(f'== clean: no casing drift found in {project} ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())
