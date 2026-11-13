"""Record which HDAs and parameters a release shipped, so none is ever removed.

    python scripts/snapshot_hda_parms.py              # merge the newest v* tag in
    python scripts/snapshot_hda_parms.py --ref v1.104.0
    python scripts/snapshot_hda_parms.py --fresh --ref v1.103.0 --ref v1.104.0

A scene saves every parameter of every node in it. When the scene loads with
an HDA that no longer has one of them, Houdini prints "Skipping unrecognized
parameter" for it (checked in hython 22.0.429, button parms included) and the
saved value is lost. 1.104.0 renamed th::export_texture's and
th::import_texture's ``select`` to ``entity_select`` (#77), and every scene
saved before it warned on load. So a parameter is never deleted or renamed:
hide it (``invisible``) and, if its value moves, move it in the definition's
SyncNodeVersion handler (pipe/houdini/node_version.py).

This writes ``tests/data/hda_parms_released.json``: for each expanded HDA under
``otls/`` (keyed by its directory, e.g. ``lop_th.import_shot.1.0``) the
operator type and every parameter name in its DialogScript at the given
release: parms, multiparm templates (``entity_select#``) and folders, inside
``import`` blocks too. It is read straight from git (``git show <ref>:...``),
so the working tree does not matter. By default the release is MERGED into
the existing snapshot: a parm shipped by any recorded release stays covered.

tests/test_hda_parms_kept.py fails when the current DialogScripts lack a parm
or an HDA the snapshot names. Run this after tagging a release (see
docs/development.md, *Preflight* and *Editing an HDA definition*).

The parser below is the one the test uses; it reads the text HDA layout that
``hotl -t`` writes, where every block opener ends its line with ``{`` and
every closer is a line of its own, and one-line ``key { ... }`` blocks
(``default``, ``parmtag``) open and close on the same line.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "tests" / "data" / "hda_parms_released.json"

# Blocks whose ``name`` is a parameter a saved scene can hold a value for.
PARM_BLOCKS = frozenset({
    "parm", "multiparm", "multiswitcher", "multiscroll",
    "group", "groupsimple", "groupcollapsible", "groupradio",
})

# "parm {", a bare "{", and openers with words after the kind: "menu usetokenvalue {".
_OPENER = re.compile(r"^(?:(\w+)(?:\s+\w+)*)?\s*\{$")
_NAME = re.compile(r'^name\s+(?:"([^"]*)"|(\S+))$')


def parse_dialog_script(text: str) -> tuple[str, list[str]]:
    """(operator type, parameter names in order) of one DialogScript."""
    op_type = ""
    names: list[str] = []
    # Each open block: [kind, named yet]. The outermost bare ``{`` is the
    # operator itself, whose ``name`` is its type.
    stack: list[list] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        opener = _OPENER.match(line)
        if opener:
            stack.append([opener.group(1) or "<root>", False])
            continue
        if line == "}":
            if stack:
                stack.pop()
            continue
        match = _NAME.match(line)
        if not match or not stack or stack[-1][1]:
            continue
        name = match.group(1) if match.group(1) is not None else match.group(2)
        kind = stack[-1][0]
        if kind == "<root>" and len(stack) == 1:
            op_type = name
            stack[-1][1] = True
        elif kind in PARM_BLOCKS:
            names.append(name)
            stack[-1][1] = True
    return op_type, names


def hda_key(dialog_path: str) -> str | None:
    """``otls/<hda>/<definition>/DialogScript`` -> ``<hda>``, else None."""
    parts = dialog_path.replace("\\", "/").split("/")
    if len(parts) == 4 and parts[0] == "otls" and parts[3] == "DialogScript":
        return parts[1]
    return None


def current_hdas(repo: Path = REPO) -> dict[str, dict]:
    """The snapshot shape, read from this checkout's otls/."""
    hdas = {}
    for dialog in sorted((repo / "otls").glob("*/*/DialogScript")):
        op_type, names = parse_dialog_script(dialog.read_text(encoding="utf-8", errors="replace"))
        hdas[dialog.parent.parent.name] = {"type": op_type, "parms": sorted(set(names))}
    return hdas


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True,
        encoding="utf-8", errors="replace",
    ).stdout


def hdas_at(ref: str) -> dict[str, dict]:
    """The snapshot shape, read from ``ref`` in git (one ``cat-file`` call)."""
    paths = [p for p in _git("ls-tree", "-r", "--name-only", ref, "--", "otls").splitlines()
             if hda_key(p) is not None]
    out = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=REPO, check=True, capture_output=True,
        input="".join(f"{ref}:{p}\n" for p in paths).encode("utf-8"),
    ).stdout
    hdas, pos = {}, 0
    for path in paths:
        end = out.index(b"\n", pos)
        header = out[pos:end].split()   # <sha> blob <size>
        size = int(header[2])
        body = out[end + 1:end + 1 + size].decode("utf-8", errors="replace")
        pos = end + 1 + size + 1          # the blob, then a newline
        op_type, names = parse_dialog_script(body)
        hdas[hda_key(path)] = {"type": op_type, "parms": sorted(set(names))}
    return hdas


def tags() -> list[str]:
    """This clone's v* tags, newest first (empty in a tagless clone)."""
    try:
        return _git("tag", "--list", "v*", "--sort=-v:refname").split()
    except (OSError, subprocess.CalledProcessError):
        return []


def newest_tag() -> str:
    found = tags()
    if not found:
        raise SystemExit("snapshot_hda_parms: no v* tag in this clone")
    return found[0]


def merge(snapshot: dict, ref: str, hdas: dict[str, dict]) -> dict:
    """``snapshot`` with ``ref``'s HDAs and parms unioned in."""
    merged = {key: {"type": v["type"], "parms": list(v["parms"])}
              for key, v in snapshot.get("hdas", {}).items()}
    for key, value in hdas.items():
        entry = merged.setdefault(key, {"type": value["type"], "parms": []})
        entry["type"] = value["type"] or entry["type"]
        entry["parms"] = sorted(set(entry["parms"]) | set(value["parms"]))
    releases = [r for r in snapshot.get("releases", []) if r != ref] + [ref]
    return {
        "about": "HDAs and parms shipped in these releases; see scripts/snapshot_hda_parms.py",
        "releases": releases,
        "hdas": dict(sorted(merged.items())),
    }


def load(path: Path = SNAPSHOT) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_bytes().decode("utf-8"))


def dump(snapshot: dict, path: Path = SNAPSHOT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not write_text: the repo is LF and Windows text mode writes CRLF.
    path.write_bytes((json.dumps(snapshot, indent=1) + "\n").encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", action="append",
                        help="release tag to record (repeatable; default: the newest v* tag)")
    parser.add_argument("--fresh", action="store_true",
                        help="start from nothing instead of merging into the existing snapshot")
    parser.add_argument("--output", type=Path, default=SNAPSHOT)
    args = parser.parse_args(argv)

    snapshot = {} if args.fresh else load(args.output)
    for ref in args.ref or [newest_tag()]:
        hdas = hdas_at(ref)
        snapshot = merge(snapshot, ref, hdas)
        print(f"{ref}: {len(hdas)} HDAs, {sum(len(v['parms']) for v in hdas.values())} parms")
    dump(snapshot, args.output)
    total = sum(len(v["parms"]) for v in snapshot["hdas"].values())
    print(f"wrote {args.output.relative_to(REPO).as_posix() if args.output.is_relative_to(REPO) else args.output}: "
          f"{len(snapshot['hdas'])} HDAs, {total} parms, releases {', '.join(snapshot['releases'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
