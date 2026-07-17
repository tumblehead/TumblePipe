"""Python facade over the tumbleResolver USD plugin.

The resolution rules live in Rust (houdini/TumblePipe/resolver-src/); this
module provides the small surface the rest of the pipeline uses:

- latest-mode toggle (via the TH_RESOLVER_LATEST_MODE env var that the
  Rust core reads on every resolve)
- URI resolution outside a USD stage context (via Ar.GetResolver())
- refresh_context() / deferred_refresh(): float already-composed stages
  to newly published versions by re-resolving loaded entity:// layers
  and reloading the stale ones (see refresh_context's docstring for why
  Ar's own RefreshContext machinery cannot do this)
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


@contextmanager
def latest_mode(enabled: bool):
    """Resolve as a given mode for the block, then restore the previous one.

    For callers that need to answer "what would *this node* load?" without
    leaving the session's mode flipped behind them — an import node sets the
    mode for its own execute() and leaves it set, so the global mode reflects
    whichever node ran last, not the one being asked about.

    Does not refresh_context(): the Rust core reads the env var on every
    resolve, so a bare Ar.Resolve() honours the override immediately. Already
    -composed stages are untouched, which is the point — this is for asking
    questions, not for changing what is loaded.
    """
    previous = get_latest_mode()
    set_latest_mode(enabled)
    try:
        yield
    finally:
        set_latest_mode(previous)


_defer_depth = 0
_refresh_pending = False


def refresh_context() -> None:
    """Re-resolve every loaded entity:// layer and reload the ones whose
    resolved path changed, so already-composed stages float to newly
    published versions (and honor latest-mode flips).

    Why not ``Ar.GetResolver().RefreshContext(...)``? Two dead ends,
    verified live against Houdini 22's USD (2026-07-13):

    - USD's dispatching resolver never forwards ``RefreshContext`` to
      URI-scheme resolvers, so ``TumbleResolver::_RefreshContext`` (which
      sends the affects-all ``ArNotice::ResolverChanged``) is unreachable
      through the public API — the call was a silent no-op.
    - ``ArNotice.ResolverChanged`` is listen-only in Python; it cannot be
      constructed or sent from here.

    The staleness itself lives in the Sdf layer registry: it hands back
    the originally-opened layer for an identifier forever, so a
    version-less entity: URI never re-resolves on its own — the "restart
    Houdini to see a new publish" bug. ``UpdateAssetInfo()`` re-resolves
    the identifier (updating the layer's resolved path), ``Reload()``
    re-reads the contents from it, and Sdf→Pcp→Usd change processing
    recomposes every stage using the layer.

    This is global - it can reload layers in every composed stage in the
    session - so it is expensive in large scenes. Inside a
    :func:`deferred_refresh` block the call is recorded instead, and a
    single refresh runs when the outermost block exits.
    """
    global _refresh_pending
    if _defer_depth > 0:
        _refresh_pending = True
        return
    _reload_stale_entity_layers()


def _reload_stale_entity_layers() -> None:
    import os
    from pxr import Ar, Sdf

    def _norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))

    ar = Ar.GetResolver()
    for layer in Sdf.Layer.GetLoadedLayers():
        # GetLoadedLayers() hands back weak layer handles, and one can be
        # expired (dropped/GC'd mid-iteration). Touching any attribute on
        # an expired handle raises Boost.Python.ArgumentError because the
        # null C++ ptr can't bind the SdfLayer lvalue signature — skip it.
        if not layer:
            continue
        identifier = layer.identifier
        if not identifier.startswith("entity:"):
            continue
        resolved = str(ar.Resolve(identifier))
        if not resolved:
            # Target vanished (deleted export?) — keep the composed
            # content rather than reloading into an error.
            continue
        if _norm(resolved) == _norm(layer.realPath or ""):
            continue
        # Re-resolve the identifier so the layer points at the new file,
        # then re-read the contents from it. force=True: the layer's own
        # dirtiness bookkeeping is about the OLD file and must not veto
        # the reload.
        layer.UpdateAssetInfo()
        layer.Reload(force=True)


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
            _reload_stale_entity_layers()


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


def plugin_resources_path(pipeline_path: os.PathLike, houdini_major: int) -> Path:
    """Location of the compiled tumbleResolver plugin's resources dir.

    Callers join this into PXR_PLUGINPATH_NAME before spawning a process
    that will load USD. `houdini_major` must match the USD ABI the
    target process will use — pass 22 for a Houdini 22 process, etc. It is
    required (no default): silently defaulting to one major is exactly how a
    job/viewer ends up loading the wrong resolver build against a mismatched
    USD ABI. Derive it from the target Houdini (hou.applicationVersion()[0] or
    the job's TH_HOUDINI_VERSION).
    """
    return (
        Path(pipeline_path)
        / "resolver"
        / f"houdini{houdini_major}"
        / "tumbleResolver"
        / "resources"
    )
