"""Python facade over the tumbleResolver USD plugin.

The resolution rules live in Rust (houdini/TumblePipe/resolver-src/); this
module provides the small surface the rest of the pipeline uses:

- latest-mode toggle (via the TH_RESOLVER_LATEST_MODE env var that the
  Rust core reads on every resolve)
- URI resolution outside a USD stage context (via Ar.GetResolver())
- a helper that locates the compiled plugin's resources dir for a given
  Houdini major, used when building PXR_PLUGINPATH_NAME for farm tasks
  and other out-of-process launches
"""

from __future__ import annotations

import os
from pathlib import Path


LATEST_MODE_ENV_VAR = "TH_RESOLVER_LATEST_MODE"


def set_latest_mode(enabled: bool) -> None:
    """Toggle "latest" cascade semantics for entity:// resolution."""
    os.environ[LATEST_MODE_ENV_VAR] = "1" if enabled else "0"


def get_latest_mode() -> bool:
    return os.environ.get(LATEST_MODE_ENV_VAR, "0") == "1"


def resolve_entity_uri(uri: str) -> str:
    """Resolve an entity:// URI via USD's Ar.

    Returns the resolved filesystem path, or "" if the URI could not be
    resolved.
    """
    from pxr import Ar
    resolved = Ar.GetResolver().Resolve(uri)
    return str(resolved) if resolved else ""


def plugin_resources_path(pipeline_path: os.PathLike, houdini_major: int = 21) -> Path:
    """Location of the compiled tumbleResolver plugin's resources dir.

    Callers join this into PXR_PLUGINPATH_NAME before spawning a process
    that will load USD. `houdini_major` must match the USD ABI the
    target process will use — pass 22 when submitting to a Houdini 22
    farm slot, etc.
    """
    return (
        Path(pipeline_path)
        / "resolver"
        / f"houdini{houdini_major}"
        / "tumbleResolver"
        / "resources"
    )
