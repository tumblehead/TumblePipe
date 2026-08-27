#!/usr/bin/env python3
"""CLI: migrate a project's ``_config`` forward to the current layout.

    python scripts/migrate_config.py [project_path] [--dry-run]

``project_path`` may be a project root or its ``_config`` directory. It
defaults to ``$TH_PROJECT_PATH`` so the TumbleTrove desktop launcher's
per-project Scripts panel can run it against the active project with no
arguments — a user adopting a new pipeline version clicks Run to bring their
own project forward. Use ``--dry-run`` to report without writing anything.

This is a shim. The migrations themselves live in the ``th_project_core``
crate and run from the prebuilt ``tt_prepare`` binary, which is also what the
``tt_prepare`` launch hook uses — one implementation, so the button and the
hook can never disagree about what a migration does. This wrapper survives
because it is the cross-platform invocation the docs, the Scripts panel and
any bulk sweep already spell, and because working out which
``bin/<platform>/`` binary to run is exactly the sort of thing a caller
should not have to do.
"""

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Mirrors PLATFORMS in .ci/build_wizard.py — the bin/<platform>/ directory the
# build places these in, keyed by what this host reports.
_PLATFORMS = {
    ("windows", "amd64"): ("windows-x86_64", "tt_prepare.exe"),
    ("windows", "x86_64"): ("windows-x86_64", "tt_prepare.exe"),
    ("linux", "x86_64"): ("linux-x86_64", "tt_prepare"),
    ("linux", "amd64"): ("linux-x86_64", "tt_prepare"),
    ("darwin", "arm64"): ("macos-aarch64", "tt_prepare"),
    ("darwin", "aarch64"): ("macos-aarch64", "tt_prepare"),
}


def _binary() -> Path:
    """The prebuilt migrator for this host."""
    key = (platform.system().lower(), platform.machine().lower())
    entry = _PLATFORMS.get(key)
    if entry is None:
        raise SystemExit(
            f"no tt_prepare build for {key[0]}/{key[1]} — supported hosts are "
            + ", ".join(sorted({tag for tag, _ in _PLATFORMS.values()}))
        )
    tag, filename = entry
    path = REPO_ROOT / "bin" / tag / filename
    if not path.is_file():
        raise SystemExit(
            f"{path} is missing. It ships in the package archive; in a source "
            f"tree build it with:\n"
            f"    python .ci/build_wizard.py --platform {tag}"
        )
    return path


def _resolve_project(arg: "str | None") -> str:
    """The project to migrate: the explicit arg, else ``$TH_PROJECT_PATH``."""
    if arg:
        return arg
    env = os.environ.get("TH_PROJECT_PATH", "").strip()
    if not env:
        raise SystemExit(
            "no project given and TH_PROJECT_PATH is not set — pass a project "
            "path, or run this from a prepared project in the desktop launcher"
        )
    return env


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bring a project's _config layout up to the current version"
    )
    parser.add_argument("project_path", nargs="?", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing anything",
    )
    args = parser.parse_args()

    cmd = [
        str(_binary()),
        "--migrate",
        "--template-dir",
        str(REPO_ROOT / "scripts" / "project_template"),
        _resolve_project(args.project_path),
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
