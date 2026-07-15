"""Diagnose and repair a department workfile's context data.

The ground truth for a department workspace is the set of real hip files on
disk: their filenames encode the true version order. Everything else — the
``_context/vNNNN.json`` lineage entries and the ``context.json`` pointer — is
derived bookkeeping that can drift ("unhinge") from that truth through
concurrent saves, a crash mid-commit, or the historical ``v0000`` re-anchor.

:func:`diagnose` reports the drift; :func:`repair` heals it. Repair is
deliberately *conservative*: it fixes structurally-broken links (a link that
points forward, dangles, or re-anchors to ``v0000`` mid-history), fills in
missing entries, resets a stale pointer, and clears orphaned reservation stubs
— but it never rewrites a lineage entry whose ``from_version`` is a valid prior
version, because provenance can legitimately be non-consecutive (an artist may
save from an older version). It backs the ``_context`` dir and ``context.json``
up before writing.

Both functions take a plain workspace directory ``Path`` (the department folder
that holds the hips, ``_context/`` and ``context.json``), so they need no
project/storage configuration and run headlessly. Only ``api.naming`` is used,
to parse version names.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from tumblepipe.api import api, get_user_name
from tumblepipe.util.io import load_json, store_json

logger = logging.getLogger(__name__)

HIP_EXTENSIONS = ("hip", "hiplc", "hipnc")


# --------------------------------------------------------------------------- #
# Reading the on-disk state
# --------------------------------------------------------------------------- #
def _hip_versions(workspace_path: Path) -> dict[str, str]:
    """Map ``version_name -> extension`` for every real hip file present."""
    versions: dict[str, str] = {}
    for ext in HIP_EXTENSIONS:
        for hip_path in workspace_path.glob(f"*.{ext}"):
            name = hip_path.stem.rsplit("_", 1)[-1]
            if api.naming.is_valid_version_name(name):
                versions[name] = ext
    return versions


def _context_entries(workspace_path: Path) -> dict[str, dict]:
    """Load every ``_context/vNNNN.json`` entry, keyed by its version name."""
    entries: dict[str, dict] = {}
    context_dir = workspace_path / "_context"
    if not context_dir.is_dir():
        return entries
    for entry_path in context_dir.glob("*.json"):
        name = entry_path.stem
        if not api.naming.is_valid_version_name(name):
            continue
        data = load_json(entry_path)
        if isinstance(data, dict):
            entries[name] = data
    return entries


def _is_reserved_stub(entry: dict) -> bool:
    """A leftover reservation claim (from a failed save), not a real entry."""
    return "reserved" in entry and "to_version" not in entry


def _sorted_versions(names) -> list[str]:
    return sorted(names, key=api.naming.get_version_code)


# --------------------------------------------------------------------------- #
# Diagnosis
# --------------------------------------------------------------------------- #
@dataclass
class Diagnosis:
    workspace_path: Path
    hip_versions: list[str]                       # ascending
    pointer: str | None                           # context.json version
    pointer_ok: bool                              # pointer == latest hip
    missing_entries: list[str]                    # hip with no finalized entry
    orphan_entries: list[str]                     # finalized entry, no hip
    reserved_stubs: list[str]                     # orphaned reservation claims
    broken_links: list[tuple[str, str]]           # (version, reason)
    null_extension: list[str]                     # entry lacks extension hint
    empty_timestamp: list[str]                    # entry has empty timestamp

    @property
    def latest_hip(self) -> str | None:
        return self.hip_versions[-1] if self.hip_versions else None

    @property
    def is_healthy(self) -> bool:
        return not (
            self.missing_entries
            or self.orphan_entries
            or self.reserved_stubs
            or self.broken_links
            or self.null_extension
            or self.empty_timestamp
            or not self.pointer_ok
        )

    def summary(self) -> str:
        if self.is_healthy:
            return f"ok    {self.workspace_path} ({len(self.hip_versions)} versions)"
        lines = [f"FAIL  {self.workspace_path}"]
        if not self.pointer_ok:
            lines.append(
                f"        pointer {self.pointer!r} != latest hip {self.latest_hip!r}"
            )
        for version, reason in self.broken_links:
            lines.append(f"        broken link at {version}: {reason}")
        if self.missing_entries:
            lines.append(f"        hips with no _context entry: {', '.join(self.missing_entries)}")
        if self.orphan_entries:
            lines.append(f"        _context entries with no hip: {', '.join(self.orphan_entries)}")
        if self.reserved_stubs:
            lines.append(f"        orphaned reservation stubs: {', '.join(self.reserved_stubs)}")
        if self.null_extension:
            lines.append(f"        entries missing extension: {', '.join(self.null_extension)}")
        if self.empty_timestamp:
            lines.append(f"        entries with empty timestamp: {', '.join(self.empty_timestamp)}")
        return "\n".join(lines)


def diagnose(workspace_path: Path) -> Diagnosis:
    """Inspect a department workspace and report every context-data drift."""
    workspace_path = Path(workspace_path)
    hip_map = _hip_versions(workspace_path)
    hip_versions = _sorted_versions(hip_map)
    hip_set = set(hip_versions)

    entries = _context_entries(workspace_path)
    finalized = {v: e for v, e in entries.items() if not _is_reserved_stub(e)}
    reserved_stubs = _sorted_versions(
        v for v, e in entries.items() if _is_reserved_stub(e)
    )
    # A reservation stub whose hip actually landed is not orphaned — it just
    # never got finalized; treat it as a missing entry instead.
    reserved_stubs = [v for v in reserved_stubs if v not in hip_set]

    missing_entries = [v for v in hip_versions if v not in finalized]
    orphan_entries = _sorted_versions(v for v in finalized if v not in hip_set)

    # Broken links: a finalized entry whose from_version is structurally wrong.
    # Non-consecutive provenance is NOT broken — only forward/self/dangling
    # references and a v0000 re-anchor sitting above a real predecessor are.
    broken_links: list[tuple[str, str]] = []
    null_extension: list[str] = []
    empty_timestamp: list[str] = []
    valid_targets = hip_set | {v for v in finalized}
    lowest_code = api.naming.get_version_code(hip_versions[0]) if hip_versions else None
    for version in _sorted_versions(finalized):
        entry = finalized[version]
        to_code = api.naming.get_version_code(version)
        from_version = entry.get("from_version")

        if from_version is None:
            broken_links.append((version, "missing from_version"))
        elif from_version == "v0000":
            # A genuine first version legitimately anchors to v0000. Above the
            # lowest real version, a v0000 anchor is the classic re-anchor bug.
            if lowest_code is not None and to_code > lowest_code:
                broken_links.append(
                    (version, "re-anchors to v0000 above a real predecessor")
                )
        elif not api.naming.is_valid_version_name(from_version):
            broken_links.append((version, f"invalid from_version {from_version!r}"))
        else:
            from_code = api.naming.get_version_code(from_version)
            if from_code >= to_code:
                broken_links.append(
                    (version, f"from_version {from_version} does not precede it")
                )
            elif from_version not in valid_targets:
                broken_links.append(
                    (version, f"from_version {from_version} does not exist")
                )

        # Quality issues only matter for an entry whose hip exists.
        if version in hip_set:
            if not entry.get("extension"):
                null_extension.append(version)
            if not entry.get("timestamp"):
                empty_timestamp.append(version)

    pointer_data = load_json(workspace_path / "context.json")
    pointer = pointer_data.get("version") if isinstance(pointer_data, dict) else None
    pointer = pointer or None
    latest_hip = hip_versions[-1] if hip_versions else None
    pointer_ok = pointer == latest_hip

    return Diagnosis(
        workspace_path=workspace_path,
        hip_versions=hip_versions,
        pointer=pointer,
        pointer_ok=pointer_ok,
        missing_entries=missing_entries,
        orphan_entries=orphan_entries,
        reserved_stubs=reserved_stubs,
        broken_links=broken_links,
        null_extension=null_extension,
        empty_timestamp=empty_timestamp,
    )


# --------------------------------------------------------------------------- #
# Repair
# --------------------------------------------------------------------------- #
@dataclass
class RepairReport:
    workspace_path: Path
    backup_path: Path | None
    actions: list[str] = field(default_factory=list)
    dry_run: bool = False


def _consecutive_predecessor(hip_versions: list[str], version: str) -> str:
    """The hip version immediately below *version*, or 'v0000' if it is first.

    Used only to heal a structurally-broken link — the best recoverable guess
    for a provenance we can no longer trust.
    """
    code = api.naming.get_version_code(version)
    prior = [v for v in hip_versions if api.naming.get_version_code(v) < code]
    return prior[-1] if prior else "v0000"


def _backup(workspace_path: Path) -> Path:
    """Snapshot _context/ and context.json before rewriting them."""
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = workspace_path / f"_context_backup_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    context_dir = workspace_path / "_context"
    if context_dir.is_dir():
        shutil.copytree(context_dir, backup_dir / "_context", dirs_exist_ok=True)
    pointer_path = workspace_path / "context.json"
    if pointer_path.is_file():
        shutil.copy2(pointer_path, backup_dir / "context.json")
    return backup_dir


def repair(
    workspace_path: Path,
    *,
    prune_orphans: bool = False,
    backup: bool = True,
    dry_run: bool = False,
) -> RepairReport:
    """Heal the context data of a department workspace.

    Conservative by design — it only touches what :func:`diagnose` flags:

    - rewrites a structurally-broken ``from_version`` to the consecutive
      on-disk predecessor (leaving valid non-consecutive provenance alone);
    - synthesizes a ``_context`` entry for a hip that has none;
    - fills a missing ``extension``/``timestamp`` from the hip file;
    - resets ``context.json`` ``version`` to the latest hip;
    - deletes orphaned reservation stubs;
    - with ``prune_orphans``, removes finalized entries that have no hip.

    Returns a :class:`RepairReport`; with ``dry_run`` nothing is written but the
    report lists what *would* change.
    """
    workspace_path = Path(workspace_path)
    diag = diagnose(workspace_path)
    report = RepairReport(workspace_path=workspace_path, backup_path=None, dry_run=dry_run)

    if diag.is_healthy:
        report.actions.append("already healthy — nothing to do")
        return report

    context_dir = workspace_path / "_context"
    hip_map = _hip_versions(workspace_path)
    hip_versions = diag.hip_versions

    if backup and not dry_run:
        report.backup_path = _backup(workspace_path)

    def _write_entry(version: str, data: dict) -> None:
        if not dry_run:
            store_json(context_dir / f"{version}.json", data)

    entries = _context_entries(workspace_path)

    # 1. Broken links -> reset from_version to the consecutive predecessor.
    broken_versions = {v for v, _ in diag.broken_links}
    for version in broken_versions:
        entry = dict(entries.get(version, {}))
        predecessor = _consecutive_predecessor(hip_versions, version)
        entry["from_version"] = predecessor
        entry.setdefault("to_version", version)
        entry.setdefault("user", entry.get("user", ""))
        _write_entry(version, entry)
        report.actions.append(f"link {version}: from_version -> {predecessor}")

    # 2. Missing entries (hip with no finalized entry) -> synthesize one.
    for version in diag.missing_entries:
        if version in broken_versions:
            continue  # already rewritten above
        predecessor = _consecutive_predecessor(hip_versions, version)
        ext = hip_map.get(version)
        mtime = None
        if ext is not None:
            hip_path = next(iter(workspace_path.glob(f"*_{version}.{ext}")), None)
            if hip_path is not None and hip_path.exists():
                mtime = dt.datetime.fromtimestamp(hip_path.stat().st_mtime).isoformat()
        _write_entry(version, dict(
            user="",
            timestamp=mtime or "",
            from_version=predecessor,
            to_version=version,
            houdini_version="unknown",
            extension=ext,
        ))
        report.actions.append(f"entry {version}: synthesized (from {predecessor})")

    # 3. Fill missing extension/timestamp on existing entries whose hip exists.
    for version in diag.null_extension + diag.empty_timestamp:
        if version in broken_versions or version in diag.missing_entries:
            continue
        entry = dict(entries.get(version, {}))
        ext = hip_map.get(version)
        changed = False
        if not entry.get("extension") and ext is not None:
            entry["extension"] = ext
            changed = True
        if not entry.get("timestamp") and ext is not None:
            hip_path = next(iter(workspace_path.glob(f"*_{version}.{ext}")), None)
            if hip_path is not None and hip_path.exists():
                entry["timestamp"] = dt.datetime.fromtimestamp(
                    hip_path.stat().st_mtime
                ).isoformat()
                changed = True
        if changed:
            _write_entry(version, entry)
            report.actions.append(f"entry {version}: filled extension/timestamp")

    # 4. Orphaned reservation stubs -> delete (a burned, never-saved number).
    for version in diag.reserved_stubs:
        if not dry_run:
            try:
                (context_dir / f"{version}.json").unlink()
            except OSError:
                pass
        report.actions.append(f"stub {version}: removed orphaned reservation")

    # 5. Orphaned finalized entries (no hip) -> keep by default; optionally prune.
    for version in diag.orphan_entries:
        if prune_orphans:
            if not dry_run:
                try:
                    (context_dir / f"{version}.json").unlink()
                except OSError:
                    pass
            report.actions.append(f"entry {version}: pruned (no hip on disk)")
        else:
            report.actions.append(
                f"entry {version}: KEPT (no hip on disk; pass prune_orphans to remove)"
            )

    # 6. Pointer -> latest hip.
    if not diag.pointer_ok and hip_versions:
        pointer_path = workspace_path / "context.json"
        pointer_data = load_json(pointer_path)
        if not isinstance(pointer_data, dict):
            pointer_data = {}
        pointer_data["version"] = hip_versions[-1]
        pointer_data.setdefault("user", get_user_name())
        pointer_data["timestamp"] = dt.datetime.now().isoformat()
        if not dry_run:
            store_json(pointer_path, pointer_data)
        report.actions.append(
            f"pointer: {diag.pointer!r} -> {hip_versions[-1]!r}"
        )

    return report
