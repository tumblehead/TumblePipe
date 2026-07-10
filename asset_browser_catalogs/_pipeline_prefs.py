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


@dataclass
class PipelinePrefs:
    """Global toggles for the Pipeline catalog.

    Add new fields here with a sensible default — ``load_prefs()`` always
    returns a fully-populated dataclass even when the on-disk file is
    older and missing the new keys.
    """

    autosave_on_scene_change: bool = False
    auto_refresh_on_open: bool = True


def load_prefs() -> PipelinePrefs:
    path = _prefs_path()
    if not path.exists():
        return PipelinePrefs()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PipelinePrefs(
            autosave_on_scene_change=bool(
                data.get("autosave_on_scene_change", False)
            ),
            auto_refresh_on_open=bool(
                data.get("auto_refresh_on_open", True)
            ),
        )
    except Exception:
        log.exception("Failed to load pipeline_prefs.json — using defaults")
        return PipelinePrefs()


def save_prefs(prefs: PipelinePrefs) -> None:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(prefs), indent=2), encoding="utf-8")
