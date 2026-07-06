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

from contextlib import contextmanager
import os
from pathlib import Path


LATEST_MODE_ENV_VAR = "TH_RESOLVER_LATEST_MODE"


def set_latest_mode(enabled: bool) -> None:
    """Toggle "latest" cascade semantics for entity:// resolution."""
    os.environ[LATEST_MODE_ENV_VAR] = "1" if enabled else "0"


def get_latest_mode() -> bool:
    return os.environ.get(LATEST_MODE_ENV_VAR, "0") == "1"


_defer_depth = 0
_refresh_pending = False


def refresh_context() -> None:
    """Invalidate USD's resolver caches so the next composition re-resolves
    every entity:// URI (picking up latest-mode flips and newly published
    versions).

    This is global - it dirties every composed stage in the session - so it
    is expensive in large scenes. Inside a :func:`deferred_refresh` block the
    call is recorded instead, and a single refresh runs when the outermost
    block exits.
    """
    global _refresh_pending
    if _defer_depth > 0:
        _refresh_pending = True
        return
    from pxr import Ar
    Ar.GetResolver().RefreshContext(Ar.ResolverContext())


@contextmanager
def deferred_refresh():
    """Collapse refresh_context() calls in the block into one refresh.

    Used by batch operations (e.g. re-executing every import node on
    workfile open) where each node requests a refresh but one invalidation
    at the end is sufficient. No refresh runs if nothing requested one.
    Re-entrant: nested blocks defer to the outermost exit.
    """
    global _defer_depth, _refresh_pending
    _defer_depth += 1
    try:
        yield
    finally:
        _defer_depth -= 1
        if _defer_depth == 0 and _refresh_pending:
            _refresh_pending = False
            from pxr import Ar
            Ar.GetResolver().RefreshContext(Ar.ResolverContext())


class ResolveError(RuntimeError):
    """An entity:// URI that was required to resolve did not."""


def resolve_entity_uri(uri: str) -> str:
    """Resolve an entity:// URI via USD's Ar.

    Returns the resolved filesystem path. Raises :class:`ResolveError` if
    the URI does not resolve — an unresolvable required URI is a real
    failure, and an empty string silently poisons downstream path joins.
    Use :func:`try_resolve_entity_uri` to probe for optional layers.
    """
    resolved = try_resolve_entity_uri(uri)
    if resolved is None:
        raise ResolveError(f'entity URI did not resolve: {uri}')
    return resolved


def try_resolve_entity_uri(uri: str) -> str | None:
    """Probe form of :func:`resolve_entity_uri`.

    Returns the resolved filesystem path, or None if the URI does not
    resolve. For callers where absence is a legitimate outcome (optional
    shared layers, staged-file existence checks).
    """
    from pxr import Ar
    resolved = Ar.GetResolver().Resolve(uri)
    return str(resolved) if resolved else None


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
