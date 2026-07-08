"""Progress breadcrumbs for long-running pipeline operations.

A minimal callback registry that lets deep pipeline code (exports, builds)
report coarse progress phases ("cooking frames", "copying to server") to
whatever UI is driving it, without depending on Qt or Houdini. Whoever runs
the operation installs a reporter around it; code that has nothing installed
pays nothing.
"""

from contextlib import contextmanager
from typing import Callable

_reporter: Callable[[str], None] | None = None


@contextmanager
def progress_reporter(reporter: Callable[[str], None]):
    """Install a reporter for the duration of the block (not reentrant)."""
    global _reporter
    previous = _reporter
    _reporter = reporter
    try:
        yield
    finally:
        _reporter = previous


def report_progress(message: str):
    """Report a progress phase to the installed reporter, if any.

    Reporter failures are swallowed - progress display must never be able
    to break the operation it narrates.
    """
    if _reporter is None:
        return
    try:
        _reporter(message)
    except Exception:
        pass
