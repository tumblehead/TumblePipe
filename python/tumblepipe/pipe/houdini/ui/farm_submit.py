"""Submitting farm work from inside Houdini: one choice, one check, one answer.

A node's own Submit button (th::build_comp, th::lookdev_studio) and the
publish dialog submit from where their settings live, through the same
``farm.pipelines`` jobs Farm Submit uses. This module gives them what Farm
Submit's dialog gives its grid:

- **Run on**: the render farm (Deadline) or this machine (``farm.local``),
  offered with the last choice preselected; the choice is shared with Farm
  Submit (``PipelinePrefs.farm_backend``), so both open on the same one;
- the Deadline check, only when the work goes to Deadline;
- ``$TH_FARM_BACKEND`` set for the submit (``farm.backend.using``);
- an answer that says where the work went, with the Farm Manager one click
  away.

The node's own settings (pool, priority, range, batch) are never touched:
they come from the node, wherever the work runs.
"""

from __future__ import annotations

import logging
from typing import Callable

from tumblepipe.farm import backend as farm_backend

log = logging.getLogger(__name__)

# The order the choice is offered in; Cancel follows.
_CHOICES = (farm_backend.FARM, farm_backend.LOCAL)
_LABELS = {farm_backend.FARM: 'Render farm', farm_backend.LOCAL: 'This machine'}


def saved_backend() -> str:
    """The last submission's Run on choice; the farm when unknown."""
    try:
        # The pref file is plain JSON (no Qt, no browser); Farm Submit
        # reads and writes the same field.
        from tumblepipe.asset_browser.prefs import load_prefs
        return farm_backend.normalize(load_prefs().farm_backend)
    except Exception:
        log.warning("Could not read the saved 'Run on' choice", exc_info=True)
        return farm_backend.FARM


def remember_backend(backend: str) -> None:
    """Offer ``backend`` first next time; a prefs file we cannot write only loses that."""
    try:
        from tumblepipe.asset_browser.prefs import load_prefs, save_prefs
        prefs = load_prefs()
        prefs.farm_backend = farm_backend.normalize(backend)
        save_prefs(prefs)
    except Exception:
        log.warning("Could not remember the 'Run on' choice", exc_info=True)


def check_backend(backend: str, title: str) -> bool:
    """True when this machine can submit to ``backend``; otherwise say why."""
    problem = farm_backend.setup_problem(backend)
    if problem is None:
        return True
    import hou
    hou.ui.displayMessage(problem, title=title, severity=hou.severityType.Warning)
    return False


def choose_backend(what: str, title: str) -> str | None:
    """Ask where ``what`` should run; None when the artist cancels or it cannot."""
    import hou
    saved = saved_backend()
    index = hou.ui.displayMessage(
        f'Submit {what}.\n\n'
        'Render farm: the job goes to Deadline.\n'
        'This machine: the same job runs here, in the background, one task '
        'at a time, as a render node would run it.',
        buttons=(*(_LABELS[b] for b in _CHOICES), 'Cancel'),
        default_choice=_CHOICES.index(saved),
        close_choice=len(_CHOICES),
        title=title,
    )
    if index >= len(_CHOICES):
        return None
    backend = _CHOICES[index]
    if not check_backend(backend, title):
        return None
    return backend


def submitted_text(backend: str, what: str) -> str:
    """The answer after a submit: where ``what`` went and where to follow it."""
    if farm_backend.normalize(backend) == farm_backend.LOCAL:
        return (
            f'{what} is queued on this machine. It runs in the background, '
            'one task at a time; follow it in the Farm Manager.'
        )
    return f'{what} is submitted to the render farm.'


def show_submitted(backend: str, what: str, title: str) -> None:
    """Say where ``what`` went, with a button to the Farm Manager."""
    import hou
    choice = hou.ui.displayMessage(
        submitted_text(backend, what), buttons=('OK', 'Farm Manager'), default_choice=0, close_choice=0,
        title=title,
    )
    if choice == 1:
        open_manager(backend)


def open_manager(backend: str | None = None) -> None:
    """The Farm Manager, showing ``backend``'s jobs."""
    from .report import report_failure
    try:
        import hou
        from tumblepipe.farm.manager.window import show_manager
        show_manager(parent=hou.qt.mainWindow(), source=backend)
    except Exception as error:
        report_failure('Opening the Farm Manager', error)


def submit(what: str, title: str, fn: Callable[[], object]) -> bool:
    """Ask where to run ``what``, submit it there with ``fn``, say where it went.

    ``fn`` does the node's own submit (it raises as it always has: a node
    error reaches the artist through the node). False when the artist
    cancelled.
    """
    backend = choose_backend(what, title)
    if backend is None:
        return False
    with farm_backend.using(backend):
        fn()
    remember_backend(backend)
    show_submitted(backend, what[:1].upper() + what[1:], title)
    return True
