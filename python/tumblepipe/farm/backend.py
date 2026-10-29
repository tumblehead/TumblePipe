"""Where a farm batch runs: the Deadline farm, or this machine.

``$TH_FARM_BACKEND`` picks it: ``local`` queues batches on this machine
(``farm.local``), anything else submits to Deadline. The Farm Submit dialog
sets it for its submission process; a local job's env file sets it for the
task, so the work a task submits stays local too. A submit from inside
Houdini (a node's button, the publish dialog) sets it for the call with
:func:`using`.
"""

from __future__ import annotations

import contextlib
import os

BACKEND_ENV = 'TH_FARM_BACKEND'
FARM = 'farm'
LOCAL = 'local'
BACKENDS = (FARM, LOCAL)


def normalize(backend) -> str:
    """``backend`` as one of :data:`BACKENDS`; anything unknown is the farm."""
    return LOCAL if (backend or '').strip().lower() == LOCAL else FARM


def selected(environ=None) -> str:
    environ = os.environ if environ is None else environ
    return normalize(environ.get(BACKEND_ENV))


def describe(backend) -> str:
    """Where ``backend`` runs work, for a sentence: "the render farm", "this machine"."""
    return 'this machine' if normalize(backend) == LOCAL else 'the render farm'


def setup_problem(backend, environ=None) -> str | None:
    """Why this machine cannot submit to ``backend``, or None when it can.

    Only the farm needs something installed (Deadline's client); this
    machine needs nothing but the pipeline.
    """
    if normalize(backend) == LOCAL:
        return None
    from tumblepipe.apps.deadline import deadline_setup_problem
    return deadline_setup_problem(environ)


@contextlib.contextmanager
def using(backend, environ=None):
    """Submit to ``backend`` for the duration: ``$TH_FARM_BACKEND`` set, then restored."""
    environ = os.environ if environ is None else environ
    previous = environ.get(BACKEND_ENV)
    environ[BACKEND_ENV] = normalize(backend)
    try:
        yield
    finally:
        if previous is None:
            environ.pop(BACKEND_ENV, None)
        else:
            environ[BACKEND_ENV] = previous


def get_farm(environ=None):
    """The farm to submit batches to: ``LocalFarm`` or ``Deadline``."""
    if selected(environ) == LOCAL:
        from tumblepipe.farm.local.backend import LocalFarm
        return LocalFarm()
    from tumblepipe.apps.deadline import Deadline
    return Deadline()
