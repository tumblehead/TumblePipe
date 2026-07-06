"""CLI: verify staged tracked-asset counts against department contexts.

    python scripts/verify_tracked_asset_counts.py <project_path> [--shots]

Read-only; exit 1 when anything is flagged.

For every asset with a staged build (export/assets/**/_staged/<variant>/),
compare the CURRENT staged context.json's tracked-asset entries against
what the newest-export-wins policy (TumblePipe >= 1.21.1) derives from
the departments' current context.json files: per tracked asset, the most
recently exported department layer that records it provides instances
and variant. Staged contexts produced by older releases used max()
across layers, which pinned stale counts forever (the paleindia
"six towers" bug) — this sweep finds any staged build still carrying
such a count, so it can be re-staged (or its department re-exported)
before an import surfaces phantom instances.

Flags per staged build:
- COUNT    staged instances differ from the newest department context
- VARIANT  staged variant differs from the newest department context
- STALE    staged tracks an asset no current department context records
- MISSING  a current department context tracks an asset the staged
           context lacks (staged predates the department export)

--shots additionally sweeps export/shots/**. Shot staged contexts
written before TumblePipe 1.22.0 collapsed every multi-instance
shot-flow asset to ~1, so expect broad COUNT findings there until shots
are re-staged; they are informational, not incidents.

Companion to scripts/verify_entity_casing.py.
"""

import argparse
import json
import re
import sys
from pathlib import Path

VERSION_PATTERN = re.compile(r'^v\d+$')


def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _latest_version_dir(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    versions = sorted(
        (child for child in path.iterdir()
         if child.is_dir() and VERSION_PATTERN.match(child.name)),
        key=lambda child: int(child.name[1:])
    )
    return versions[-1] if versions else None


def _staged_context(asset_root: Path, variant_name: str) -> dict | None:
    version_dir = _latest_version_dir(asset_root / '_staged' / variant_name)
    if version_dir is None:
        return None
    return _load_json(version_dir / 'context.json')


def _dept_outputs(asset_root: Path, variant_name: str, departments):
    """Yield (department, timestamp, assets) from each department's
    current context.json output entry."""
    for department in departments:
        version_dir = _latest_version_dir(asset_root / variant_name / department)
        if version_dir is None:
            continue
        context_data = _load_json(version_dir / 'context.json')
        if context_data is None:
            continue
        for output in context_data.get('outputs', []):
            if output.get('department') != department:
                continue
            yield (
                department,
                output.get('timestamp', ''),
                output.get('parameters', {}).get('assets', [])
            )


def _expected_tracked(asset_root: Path, variant_name: str, departments, own_uri):
    """Newest-export-wins snapshot per tracked asset across departments."""
    expected = {}  # uri -> (instances, variant)
    stamps = {}
    for _department, stamp, assets in _dept_outputs(
        asset_root, variant_name, departments
    ):
        for entry in assets:
            uri = entry.get('asset')
            if not uri or uri == own_uri:
                continue
            if uri in stamps and stamp <= stamps[uri]:
                continue
            stamps[uri] = stamp
            expected[uri] = (
                entry.get('instances', 1),
                entry.get('variant', 'default')
            )
    return expected


def _iter_staged_asset_roots(export_root: Path):
    """Yield (asset_root, variant_name) for every staged build found."""
    if not export_root.exists():
        return
    for staged_dir in export_root.rglob('_staged'):
        if not staged_dir.is_dir():
            continue
        for variant_dir in staged_dir.iterdir():
            if variant_dir.is_dir():
                yield staged_dir.parent, variant_dir.name


def _check_staged_build(asset_root: Path, variant_name: str) -> list[str]:
    staged = _staged_context(asset_root, variant_name)
    if staged is None:
        return []
    own_uri = staged.get('uri', '')
    parameters = staged.get('parameters', {})
    departments = parameters.get('departments', [])
    staged_assets = {
        entry['asset']: (
            entry.get('instances', 1),
            entry.get('variant', 'default')
        )
        for entry in parameters.get('assets', [])
        if entry.get('asset')
    }
    if not departments and not staged_assets:
        return []

    expected = _expected_tracked(asset_root, variant_name, departments, own_uri)

    findings = []
    label = f'{own_uri or asset_root} [{variant_name}] {staged.get("version", "?")}'
    for uri, (instances, variant) in sorted(staged_assets.items()):
        if uri not in expected:
            findings.append(f'STALE    {label}: tracks {uri} '
                            '(no current department context records it)')
            continue
        expected_instances, expected_variant = expected[uri]
        if instances != expected_instances:
            findings.append(f'COUNT    {label}: {uri} staged={instances} '
                            f'departments={expected_instances}')
        if variant != expected_variant:
            findings.append(f'VARIANT  {label}: {uri} staged={variant} '
                            f'departments={expected_variant}')
    for uri in sorted(set(expected) - set(staged_assets)):
        findings.append(f'MISSING  {label}: departments track {uri} '
                        'but the staged context lacks it')
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project_path', type=Path,
                        help='Project root (contains export/) or the export dir')
    parser.add_argument('--shots', action='store_true',
                        help='Also sweep shot staged builds (expect broad '
                             'COUNT findings until shots re-stage on >= 1.22.0)')
    args = parser.parse_args()

    project = args.project_path.resolve()
    export_root = project if project.name == 'export' else project / 'export'
    if not export_root.exists():
        raise SystemExit(f'{export_root} not found - is this a project root?')

    scan_roots = [export_root / 'assets']
    if args.shots:
        scan_roots.append(export_root / 'shots')

    findings = []
    checked = 0
    for scan_root in scan_roots:
        for asset_root, variant_name in _iter_staged_asset_roots(scan_root):
            checked += 1
            findings.extend(_check_staged_build(asset_root, variant_name))

    for finding in findings:
        print(finding)
    print(f'-- checked {checked} staged build(s), '
          f'{len(findings)} finding(s)')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
