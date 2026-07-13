"""Pipeline catalog preferences — global toggles persisted between sessions.

Stored alongside ``projects.json`` (see ``_pipeline_types.projects_json_path``)
at ``~/.config/asset_browser/pipeline_prefs.json`` (or under
``$HOUDINI_USER_PREF_DIR/asset_browser/`` when set). Kept as a separate file
so the project registry can stay focused on per-project paths.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path

log = logging.getLogger(__name__)


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
    prefs_version: int = _PREFS_VERSION


def load_prefs() -> PipelinePrefs:
    path = _prefs_path()
    if not path.exists():
        return PipelinePrefs()
    try:
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
        )
    except Exception:
        log.exception("Failed to load pipeline_prefs.json — using defaults")
        return PipelinePrefs()


def save_prefs(prefs: PipelinePrefs) -> None:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(prefs)
    # Always stamp the module's current version — the instance may have
    # been built from (or mutated alongside) an older on-disk file.
    payload["prefs_version"] = _PREFS_VERSION
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
