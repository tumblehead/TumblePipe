"""Putting a failure in front of the artist, and running on the main thread.

The one path for an *artist-initiated* action that failed — a button, a
menu entry, a node's Execute — to reach the artist: :func:`report_failure`
logs it, names the log file, and shows the error, from whichever thread it
was raised on. :func:`reporting` makes a callback (a menu entry, a button)
its own boundary that does so. :func:`run_in_background` runs an action's
slow part off the main thread and its outcome back on it, under one such
boundary.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


def run_on_main_thread(func: Callable, *args, **kwargs) -> None:
    """Schedule ``func(*args, **kwargs)`` on Houdini's main thread.

    Thin wrapper around
    ``tumbletrove.common.gui.gui_dispatch`` that accepts args
    and kwargs explicitly instead of forcing callers to construct a
    closure with default-arg capture (``def _do(p=path): ...``).
    """
    from tumbletrove.common.gui import gui_dispatch
    if args or kwargs:
        gui_dispatch(lambda: func(*args, **kwargs))
    else:
        gui_dispatch(func)


def run_in_background(
    action: str, work: Callable[[], T], then: Callable[[T], object],
) -> None:
    """Run ``work()`` on a worker thread, then ``then(result)`` on the main one.

    For an artist's action whose slow part (a hython job, a share that takes
    its time) must not freeze Houdini, and whose outcome is shown with
    ``hou.ui`` afterwards. Both halves are one failure boundary reported as
    ``action``: whatever ``work`` or ``then`` raises reaches the artist.
    ``work`` must not touch ``hou`` or Qt — it is off the main thread.
    """
    import threading

    def _run() -> None:
        try:
            result = work()
        except Exception as exc:
            report_failure(action, exc)
            return
        run_on_main_thread(reporting(action, lambda: then(result)))

    threading.Thread(target=_run, name=f"th-bg: {action}", daemon=True).start()


def report_failure(action: str, exc: BaseException) -> None:
    """Put a failed artist-initiated action in front of the artist.

    An action handler runs behind a toolbar button or a menu entry with no
    console in sight, so an exception that is logged and nothing more reads
    as "the button does nothing" - which is exactly how a dead export window
    got reported. Every failure still lands in the log (this logs it); the
    dialog only guarantees it is *visible*, and names the log file so the
    full trace can be found afterwards.

    Only for actions the artist explicitly asked for. Background upkeep -
    detail/card repaints, cache invalidation, refresh hooks - is not
    reported from here: an error dialog the artist cannot act on is noise,
    and one raised from a repaint can fire repeatedly. It raises, into the
    action that triggered it or the host's own boundary - never swallowed.

    Safe to call from any thread. Quick actions reach here from the browser's
    GUI thread as readily as from Houdini's main thread, and ``hou.ui`` off
    the main thread is the same unsupported call the scene ops marshal to
    avoid - so the dialog goes through :func:`run_on_main_thread` rather than
    trusting each caller to know which thread it is on. The message is built
    eagerly, before the hop, so it does not depend on ``exc`` outliving it.

    Never raises: a reporter that throws would replace the failure it was
    called to explain with a different one.
    """
    log.exception("%s failed", action)
    try:
        from tumblepipe.util.logging import get_log_paths
        locations = "\n".join(f"  {path}" for path in get_log_paths())
    except Exception:
        locations = ""
    detail = f"\n\nFull traceback:\n{locations}" if locations else ""
    message = f"{action} failed: {type(exc).__name__}: {exc}{detail}"

    def _show() -> None:
        try:
            import hou
            hou.ui.displayMessage(message, severity=hou.severityType.Error)
        except Exception:
            log.exception("Failed to show the %s failure dialog", action)

    try:
        run_on_main_thread(_show)
    except Exception:
        log.exception("Failed to report the %s failure to the user", action)


def reporting(action: str, fn: Callable[[], object]) -> Callable[[], None]:
    """``fn`` made its own failure boundary, reported as ``action``.

    For a callback Qt calls - a menu entry, a button, a checkbox, work
    hopped onto the main thread - where a raise reaches the console and
    nothing else, so the artist sees a control that did nothing.
    """

    def run(*_signal_args) -> None:
        try:
            fn()
        except Exception as exc:
            report_failure(action, exc)

    return run
