"""Regenerate CHANGELOG.md from the tag history.

    uv run --no-project python scripts/generate_changelog.py

Writes a section per release tag, newest first, classifying each commit by
its conventional-commit prefix. The section layout is shared with the
release pipeline (``.ci/_changelog.py``), so the file and the notes posted
to the github release / TumbleTrove version page can't drift apart.

The file is *derived* — never hand-edit it. Run this as part of cutting a
release (after the version-bump commit is tagged) and commit the result;
``--check`` fails without writing, for a CI guard or a pre-release
sanity check.

Tags are ordered by creation date, not by parsing the version, so v1.9.0
sorts before v1.10.0 correctly. The oldest tag's section covers every
commit that led up to it.

Only tags that share history with HEAD count. A clone can hold stray local
tags from an unrelated older history (v0.1.0-v1.0.2 in one, never on
origin); listing every tag rendered their history into the file, so the
output depended on which clone ran it. Reachability from HEAD would be too
strict: hotfix tags (v1.74.3, v1.4.x) sit on side branches.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".ci"))
from _changelog import commits_in_range, render_sections  # noqa: E402

CHANGELOG = REPO / "CHANGELOG.md"

HEADER = """# Changelog

All notable changes to TumblePipe, one section per release.

**This file is generated — do not edit it by hand.** It is derived from the
conventional-commit subjects between release tags. To change an entry, the
commit subject is the source of truth. Regenerate with:

```bash
uv run --no-project python scripts/generate_changelog.py
```

CI and build commits are omitted deliberately.
"""


def _tags() -> List[Tuple[str, str]]:
    """``(tag, iso_date)`` for every release tag sharing history with
    HEAD, oldest first."""
    result = subprocess.run(
        [
            "git", "for-each-ref", "--sort=creatordate",
            "--format=%(refname:short)%09%(creatordate:short)",
            "refs/tags",
        ],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    tags: List[Tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        name, date = line.split("\t", 1)
        if _shares_history(name.strip()):
            tags.append((name.strip(), date.strip()))
    return tags


def _shares_history(tag: str) -> bool:
    """Whether ``tag`` has a common ancestor with HEAD."""
    result = subprocess.run(
        ["git", "merge-base", tag, "HEAD"],
        cwd=REPO, capture_output=True, text=True,
    )
    return result.returncode == 0


def _render() -> str:
    tags = _tags()
    if not tags:
        return HEADER + "\nNo release tags yet.\n"

    parts: List[str] = [HEADER]
    # Newest first, each range being (previous tag, this tag].
    for index in range(len(tags) - 1, -1, -1):
        tag, date = tags[index]
        if index == 0:
            rev_range = [tag]  # first release: everything that led up to it
        else:
            rev_range = [f"{tags[index - 1][0]}..{tag}"]

        parts.append(f"\n## {tag} — {date}\n\n")
        lines = render_sections(commits_in_range(REPO, rev_range))
        if lines:
            parts.append("\n".join(lines))
        else:
            parts.append("_No user-facing changes._\n")

    return "".join(parts).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="exit non-zero if CHANGELOG.md is stale; write nothing",
    )
    args = parser.parse_args()

    rendered = _render()
    current = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else None

    if args.check:
        if current == rendered:
            print("CHANGELOG.md is up to date")
            return 0
        print(
            "CHANGELOG.md is stale — regenerate with:\n"
            "  uv run --no-project python scripts/generate_changelog.py",
            file=sys.stderr,
        )
        return 1

    if current == rendered:
        print("CHANGELOG.md already up to date")
        return 0

    # newline="\n" explicitly: the default would write CRLF on Windows, and
    # git stores the blob as LF, so a regen on Windows would look like a
    # whole-file diff to the next reader.
    CHANGELOG.write_text(rendered, encoding="utf-8", newline="\n")
    releases = len(_tags())
    print(f"Wrote {CHANGELOG.relative_to(REPO)} ({releases} releases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
