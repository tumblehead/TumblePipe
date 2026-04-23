"""Python facade over the tumbleResolver USD plugin.

The resolution rules live in Rust (houdini/TumblePipe/resolver-src/); this
module provides the small surface the rest of the pipeline uses:

- latest-mode toggle (via the TH_RESOLVER_LATEST_MODE env var that the
  Rust core reads on every resolve)
- URI resolution outside a USD stage context (via Ar.GetResolver())
- a helper that locates the compiled plugin's resources dir for a given
  platform + Houdini major, used when building PXR_PLUGINPATH_NAME for
  farm tasks and other out-of-process launches
"""

from __future__ import annotations

import os
import platform
from pathlib import Path


LATEST_MODE_ENV_VAR = "TH_RESOLVER_LATEST_MODE"

# Maps (system, machine) to the HPM platform slug used on disk under
# resolver/<slug>/houdini<major>/. Keep in sync with hpm.toml.
_HPM_PLATFORM_SLUGS = {
    ("windows", "amd64"): "windows-x86_64",
    ("windows", "x86_64"): "windows-x86_64",
    ("linux", "x86_64"): "linux-x86_64",
    ("darwin", "arm64"): "macos-arm64",
}


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
    # Lazy import: importing pxr.Ar at module level initializes Plug.Registry
    # (scanning PXR_PLUGINPATH_NAME) the moment this module is loaded. pythonrc.py
    # imports plugin_resources_path from here to compute the tumbleResolver path
    # *before* setting PXR_PLUGINPATH_NAME — a module-level pxr import would make
    # that update too late and the resolver plugin would never be discovered.
    from pxr import Ar
    resolved = Ar.GetResolver().Resolve(uri)
    return str(resolved) if resolved else ""


def current_platform_slug() -> str:
    """Return the HPM platform slug matching the host OS + arch.

    Unknown platforms fall back to "<system>-<machine>" so the error is
    visible in the PXR_PLUGINPATH_NAME string rather than silently
    loading the wrong binary.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("amd64",):
        machine = "x86_64"
    return _HPM_PLATFORM_SLUGS.get((system, machine), f"{system}-{machine}")


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
        / current_platform_slug()
        / f"houdini{houdini_major}"
        / "tumbleResolver"
        / "resources"
    )
