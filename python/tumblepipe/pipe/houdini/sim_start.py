"""Deterministic sims in what TumblePipe cooks: th::animate, exports, caches.

TumbleRig's rule (2026-11-18): a rig that simulates (Motion Effects, the
Temporal family, Collisions, Wobl...) must give the same frame N in every
process and whatever was cooked before: From Start Frame, DOP style. A sim
inside a rig can't catch up by itself, so whatever cooks the rig replays it
from the shot's start (minus the prewarm) when it isn't at the frame before.
TumbleRig's `TumbleRig.sim_start` does that; this module calls it when
TumbleRig is installed and is a plain cook otherwise.

- `settle(node, start)`: in the chain. Called first thing in the cook of a
  Python SOP below the Scene Invoke (th::animate's `points_sops/placed`): when
  the rigs above are not at the frame before, they are replayed and the caught
  up geometry is returned (None: the input as cooked is right). Every reader
  below (the imports, a SOP Import in a CFX scene, a cache, a DOP) then gets
  the same frame, a jump on the timeline included.
- `geometry(sop, start)`: the SOP's geometry now, replayed when needed: for a
  caller that reads a SOP with no settle above it.
- `forced()`: a context in which every TumbleRig sim runs From Start Frame,
  also in a GUI session: exports and caches the artist starts.
- `prime(sop, first, start)`: before a ROP writes `first`.., brings the sims
  to the frame before it.
"""

import contextlib


def _tr():
    """The replay: the shared kit's (ttkit.sim_replay) when it is installed,
    else TumbleRig's copy of it."""
    try:
        from ttkit import sim_replay
        return sim_replay
    except Exception:
        pass
    try:
        from TumbleRig import sim_start
        return sim_start
    except Exception:
        return None


def available() -> bool:
    """Whether a replay module is installed: without one every call here is
    a plain cook, and callers need not work out frames for it."""
    return _tr() is not None


def settle(node, start=None):
    """The caught-up geometry of `node`'s input, or None when the input as
    cooked is right (no rig that simulates, sims in step, no replay module
    that settles)."""
    tr = _tr()
    if tr is None or not hasattr(tr, 'caught_up'):
        return None
    try:
        return tr.caught_up(node, start=start)
    except Exception:
        import logging
        logging.getLogger(__name__).warning('sim settle failed on %s', node.path(), exc_info=True)
        return None


def geometry(sop, start=None):
    tr = _tr()
    if tr is None:
        return sop.geometry()
    return tr.cook(sop, start=start)


@contextlib.contextmanager
def forced():
    tr = _tr()
    if tr is None:
        yield
        return
    with tr.export():
        yield


def prime(sop, first, start=None) -> int:
    tr = _tr()
    if tr is None or sop is None:
        return 0
    return tr.prime(sop, first, start=start)
