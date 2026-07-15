"""Verify (and optionally repair) department workfile context chains.

    python scripts/verify_context_chain.py <workspace-dir> [...]
    python scripts/verify_context_chain.py --scan P:/proj/shots
    python scripts/verify_context_chain.py --scan P:/proj/shots --repair

A department workspace is a folder holding versioned hip files, a ``_context/``
lineage directory, and a ``context.json`` pointer. Concurrent saves, a crash
mid-commit, or the historical ``v0000`` re-anchor can drift that bookkeeping
away from the hip files on disk ("unhinge" it). This tool reports the drift and,
with ``--repair``, heals it conservatively (rebuilding only what is broken and
backing up ``_context``/``context.json`` first).

Needs the pipeline environment configured (TH_CONFIG_PATH etc.) so ``api.naming``
can parse version names — run it from a pipeline shell, same as any other task.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make ``import tumblepipe`` work when run straight from the repo.
_PYTHON_ROOT = Path(__file__).resolve().parents[1] / "python"
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from tumblepipe.pipe.context_repair import diagnose, repair  # noqa: E402


def _find_workspaces(root: Path) -> list[Path]:
    """Every dept workspace under *root* — a dir with a ``_context`` child."""
    workspaces = []
    for context_dir in root.rglob("_context"):
        if context_dir.is_dir():
            workspaces.append(context_dir.parent)
    return sorted(set(workspaces))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="workspace directories to check")
    parser.add_argument("--scan", type=Path, metavar="ROOT",
                        help="recursively find and check every workspace under ROOT")
    parser.add_argument("--repair", action="store_true", help="apply repairs (default: report only)")
    parser.add_argument("--prune-orphans", action="store_true",
                        help="when repairing, also delete _context entries with no hip on disk")
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the _context/context.json backup taken before repair")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --repair, show what would change without writing")
    args = parser.parse_args(argv)

    workspaces: list[Path] = list(args.paths)
    if args.scan is not None:
        workspaces.extend(_find_workspaces(args.scan))
    workspaces = sorted(set(workspaces))

    if not workspaces:
        parser.error("no workspaces given (pass directories or --scan ROOT)")

    unhealthy = 0
    for workspace in workspaces:
        diag = diagnose(workspace)
        print(diag.summary())
        if diag.is_healthy:
            continue
        unhealthy += 1
        if args.repair:
            report = repair(
                workspace,
                prune_orphans=args.prune_orphans,
                backup=not args.no_backup,
                dry_run=args.dry_run,
            )
            prefix = "would " if args.dry_run else ""
            for action in report.actions:
                print(f"        {prefix}{action}")
            if report.backup_path is not None:
                print(f"        backup: {report.backup_path}")

    print()
    if args.repair and not args.dry_run:
        print(f"checked {len(workspaces)} workspace(s); repaired {unhealthy}")
        return 0
    if unhealthy:
        print(f"{unhealthy}/{len(workspaces)} workspace(s) unhealthy")
        return 1
    print(f"{len(workspaces)}/{len(workspaces)} workspace(s) healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
