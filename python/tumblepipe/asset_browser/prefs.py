"""Pipeline catalog preferences — global toggles persisted between sessions.

Stored alongside ``projects.json`` (see ``types.projects_json_path``)
at ``~/.config/asset_browser/pipeline_prefs.json`` (or under
``$HOUDINI_USER_PREF_DIR/asset_browser/`` when set). Kept as a separate file
so the project registry can stay focused on per-project paths.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field, fields
from pathlib import Path


def _prefs_path() -> Path:
    houdini_pref = os.environ.get("HOUDINI_USER_PREF_DIR")
    if houdini_pref:
        return Path(houdini_pref) / "asset_browser" / "pipeline_prefs.json"
    return Path.home() / ".config" / "asset_browser" / "pipeline_prefs.json"


# Bumped when a default flip must reach users whose on-disk file froze
# the old default (save_prefs persists the WHOLE dataclass, so toggling
# any one pref bakes every other pref's then-current value into the
# file). Version 2 = the 2026-07-10 auto_refresh_on_open False→True flip.
_PREFS_VERSION = 2


@dataclass
class PipelinePrefs:
    """Global toggles for the Pipeline catalog.

    Add new fields here with a sensible default — ``load_prefs()`` always
    returns a fully-populated dataclass even when the on-disk file is
    older and missing the new keys.
    """

    autosave_on_scene_change: bool = False
    auto_refresh_on_open: bool = True
    prompt_note_on_save: bool = True
    # Where Farm Submit sends work: 'farm' (Deadline) or 'local' (this
    # machine, farm.local). Remembered from the last submission.
    farm_backend: str = "farm"
    # The shot slate under a look-through camera's frame
    # (pipe.houdini.ui.shot_slate), and its size: 'full', 'mini' or 'dot'.
    shot_slate: bool = True
    shot_slate_size: str = "full"
    # Cards and hovers leave out the departments an entity has no workfile
    # in (the detail panel still lists them all, to start one from).
    hide_unused_departments: bool = False
    # A desktop notification when a render's preview frames are done on
    # this machine (farm.local.previews, #75). The local runner reads it.
    notify_preview_ready: bool = True
    # What the Farm Submit window remembers between openings: per project
    # and tab, the pinned settings, batch choices and collapsed columns;
    # window-wide, collapsed sections, column and clean-up choices, size.
    # Shaped and checked by tumblepipe.farm.submit.remembered.
    farm_submit: dict = field(default_factory=dict)
    # The entity picker's recent picks: project path -> entity URIs, most
    # recent first (pipe.entity_picker.push_recent shapes each list).
    entity_picker_recent: dict = field(default_factory=dict)
    prefs_version: int = _PREFS_VERSION


def load_prefs() -> PipelinePrefs:
    """The saved prefs, or the defaults when none were saved yet.

    A file that will not parse raises: it is ours, so a torn or hand-broken
    one is a fault to fix, not a reason to quietly reset the artist's
    choices.
    """
    path = _prefs_path()
    if not path.exists():
        return PipelinePrefs()
    data = json.loads(path.read_text(encoding="utf-8"))
    auto_refresh = bool(data.get("auto_refresh_on_open", True))
    if int(data.get("prefs_version", 1)) < 2:
        # Pre-flip file: an auto_refresh_on_open=False in it almost
        # always means "toggled some OTHER pref while the default was
        # still False", not an explicit opt-out — so re-default it
        # once. The next save_prefs stamps the current version, after
        # which an explicit off is honored forever.
        auto_refresh = True
    return PipelinePrefs(
        autosave_on_scene_change=bool(
            data.get("autosave_on_scene_change", False)
        ),
        auto_refresh_on_open=auto_refresh,
        # Defaults True and needs no _PREFS_VERSION bump: this is a
        # NEW key, not a flipped default, so a file written before it
        # existed simply misses it and gets the default. The version
        # gate is only for defaults that changed value.
        prompt_note_on_save=bool(
            data.get("prompt_note_on_save", True)
        ),
        farm_backend="local" if data.get("farm_backend") == "local" else "farm",
        # New keys with defaults, like prompt_note_on_save: no version bump.
        shot_slate=bool(data.get("shot_slate", True)),
        shot_slate_size=(
            data.get("shot_slate_size")
            if data.get("shot_slate_size") in ("full", "mini", "dot")
            else "full"
        ),
        hide_unused_departments=bool(
            data.get("hide_unused_departments", False)
        ),
        notify_preview_ready=bool(data.get("notify_preview_ready", True)),
        farm_submit=(
            data["farm_submit"] if isinstance(data.get("farm_submit"), dict) else {}
        ),
        entity_picker_recent=(
            data["entity_picker_recent"]
            if isinstance(data.get("entity_picker_recent"), dict) else {}
        ),
    )


def save_prefs(prefs: PipelinePrefs) -> None:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(prefs)
    # Always stamp the module's current version — the instance may have
    # been built from (or mutated alongside) an older on-disk file.
    payload["prefs_version"] = _PREFS_VERSION
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def update_prefs(**changes) -> PipelinePrefs:
    """Change only the named prefs, reading the rest fresh from disk.

    Every writer goes through this, never ``save_prefs`` on a copy it has
    held for a while: several places write the one file (the Settings
    page, Farm Submit's "Run on", the shot slate's size menu), and saving
    a long-held whole dataclass put back the values another of them had
    changed since. Returns the prefs as now saved.
    """
    unknown = set(changes) - {f.name for f in fields(PipelinePrefs)}
    if unknown:
        raise TypeError(f"Unknown prefs: {', '.join(sorted(unknown))}")
    prefs = load_prefs()
    for name, value in changes.items():
        setattr(prefs, name, value)
    save_prefs(prefs)
    return prefs
