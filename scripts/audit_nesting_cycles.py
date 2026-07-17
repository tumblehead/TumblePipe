"""CLI: map asset nesting across projects and flag cycles.

    python scripts/audit_nesting_cycles.py <projects_root>
    python scripts/audit_nesting_cycles.py --projects P:/paleindia P:/Snail
    python scripts/audit_nesting_cycles.py <projects_root> --quiet

Nothing in the pipeline rejects a nesting cycle. ``pipe/build.py``'s guard
catches only a *direct* self-import, and ``_drop_transitive_refs``
(``farm/tasks/build/build.py``) deliberately keeps both refs when it detects
mutual reachability — "duplicated refs are safer than losing the
composition". Every recursive walk over the nesting (``_staged_closure``,
``_expand_staged_layers``, ``_collect_leaf_layers_and_instances``) guards
only *termination*, via a visited set. So ``Set -> Arena -> Set`` is
buildable today: it terminates, composes some order-dependent result, and
nothing complains.

This is the fleet-wide answer to "is anything relying on that?", which
``designs/nested-asset-workflow.md`` §5.4 needs before a cycle can be
rejected rather than merely reported.

Also reports **diamonds** — one asset tracked by two different nesters.
Not a bug, but the prim space is flat (a nested asset's prim path comes
from its own URI, so it is a stage-root sibling of whatever nests it), so
two nesters of one sub-asset collapse onto one prim and cannot place it
differently. Worth knowing where they are.

Pure filesystem read of the staged ``context.json`` files — no Houdini, no
resolver, no config. Read-only. Exits 1 if any cycle or self-import is
found (the ``verify_*`` convention).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _latest_staged_context(asset_dir: Path) -> Path | None:
    """The newest staged context.json for an asset, across all variants."""
    staged = asset_dir / '_staged'
    if not staged.is_dir():
        return None
    candidates = []
    for variant_dir in staged.iterdir():
        if not variant_dir.is_dir():
            continue
        for version_dir in variant_dir.iterdir():
            context_path = version_dir / 'context.json'
            if version_dir.is_dir() and context_path.is_file():
                candidates.append((version_dir.name, context_path))
    if not candidates:
        return None
    return max(candidates)[1]


def _tracked_uris(context_path: Path) -> list[str]:
    """The asset URIs a staged build tracks.

    The staged context.json's ``parameters.assets`` is the flattened
    transitive closure, not direct children — the import-side re-tagging
    cascade rewrites every nested asset into the nester's own context. So an
    edge here means "reachable from", which is exactly what a cycle check
    wants.
    """
    try:
        data = json.loads(context_path.read_text())
    except (OSError, ValueError) as exc:
        print(f'  ! unreadable, skipped: {context_path} ({exc})')
        return []
    entries = data.get('parameters', {}).get('assets', [])
    if not isinstance(entries, list):
        return []
    return [
        entry['asset'] for entry in entries
        if isinstance(entry, dict) and entry.get('asset')
    ]


def _build_graph(project: Path) -> dict[str, list[str]]:
    assets_root = project / 'export' / 'assets'
    if not assets_root.is_dir():
        return {}
    graph: dict[str, list[str]] = {}
    for category in sorted(assets_root.iterdir()):
        if not category.is_dir():
            continue
        for asset in sorted(category.iterdir()):
            if not asset.is_dir():
                continue
            context_path = _latest_staged_context(asset)
            if context_path is None:
                continue
            uri = f'entity:/assets/{category.name}/{asset.name}'
            graph[uri] = _tracked_uris(context_path)
    return graph


def _find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Every distinct cycle in the tracking graph (colour-marked DFS)."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = defaultdict(int)
    cycles: list[list[str]] = []
    seen: set[frozenset] = set()

    def walk(node: str, path: list[str]) -> None:
        colour[node] = GREY
        path.append(node)
        for nxt in graph.get(node, []):
            if colour[nxt] == GREY:
                cycle = path[path.index(nxt):] + [nxt]
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cycle)
            elif colour[nxt] == WHITE:
                walk(nxt, path)
        path.pop()
        colour[node] = BLACK

    for node in list(graph):
        if colour[node] == WHITE:
            walk(node, [])
    return cycles


def _short(uri: str) -> str:
    return uri.replace('entity:/assets/', '')


def _report(project: Path, quiet: bool) -> tuple[int, int]:
    """Print one project's nesting. Returns (cycles, self_imports)."""
    graph = _build_graph(project)
    if not graph:
        return 0, 0

    nesters = {uri: subs for uri, subs in graph.items() if subs}
    cycles = _find_cycles(graph)
    self_imports = [uri for uri, subs in graph.items() if uri in subs]

    trackers: dict[str, list[str]] = defaultdict(list)
    for nester, subs in nesters.items():
        for sub in subs:
            trackers[sub].append(nester)
    diamonds = {sub: by for sub, by in trackers.items() if len(by) > 1}

    interesting = bool(cycles or self_imports or diamonds)
    if quiet and not interesting:
        return 0, 0

    flag = ' <-- CYCLE' if cycles else (' <-- SELF-IMPORT' if self_imports else '')
    print(f'\n{project.name}: {len(graph)} staged assets, '
          f'{len(nesters)} nest others{flag}')
    for nester, subs in sorted(nesters.items()):
        print(f'    {_short(nester)} -> ' + ', '.join(_short(s) for s in subs))
    for sub, by in sorted(diamonds.items()):
        print(f'    ~ diamond: {_short(sub)} tracked by '
              + ', '.join(_short(b) for b in by))
    for cycle in cycles:
        print('    !! CYCLE: ' + ' -> '.join(_short(c) for c in cycle))
    for uri in self_imports:
        print(f'    !! SELF-IMPORT: {_short(uri)}')
    return len(cycles), len(self_imports)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        'projects_root', nargs='?',
        help='directory holding one subdirectory per project',
    )
    parser.add_argument(
        '--projects', nargs='+', metavar='PATH',
        help='explicit project directories instead of a root',
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help='print only projects with a cycle, self-import, or diamond',
    )
    args = parser.parse_args()

    if args.projects:
        projects = [Path(p) for p in args.projects]
    elif args.projects_root:
        root = Path(args.projects_root)
        if not root.is_dir():
            print(f'no such directory: {root}')
            return 2
        projects = sorted(p for p in root.iterdir() if p.is_dir())
    else:
        parser.error('give a projects_root or --projects')

    scanned = cycles = self_imports = 0
    for project in projects:
        if not (project / 'export' / 'assets').is_dir():
            continue
        scanned += 1
        found_cycles, found_self = _report(project, args.quiet)
        cycles += found_cycles
        self_imports += found_self

    print(f'\n{"=" * 60}')
    print(f'{scanned} project(s) scanned; '
          f'{cycles} cycle(s), {self_imports} self-import(s)')
    return 0 if cycles == 0 and self_imports == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
