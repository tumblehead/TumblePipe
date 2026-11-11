"""Work an HDA's OnLoaded asks for, held until the scene has finished loading.

An OnLoaded script runs while Houdini is still loading the network: after
the node is read, before Houdini's last pass over the loaded nodes. A script
that creates or destroys nodes then leaves that pass a node that is gone,
and Houdini crashes (signal 11 in ``OP_Node::syncNodeVersionIfNeeded``).
th::light_mix rebuilt its ``mix`` network that way, so a comp with a light
mix saved cable first crashed Houdini every time it was opened (Remy,
2026-11-11; reproduced in hython 22.0.429).

:func:`run` does the work at once when no scene is loading (an asset
definition installed into an open scene), and otherwise when the load or
merge has finished, in the order it was asked for. A failing piece is
printed and never stops the rest.
"""

import traceback

import hou

_pending: list = []


def _finished_events() -> tuple:
    kinds = hou.hipFileEventType
    return (kinds.AfterLoad, kinds.AfterMerge)


def _on_event(event_type) -> None:
    if event_type not in _finished_events(): return
    hou.hipFile.removeEventCallback(_on_event)
    work = list(_pending)
    _pending.clear()
    for fn in work:
        try:
            fn()
        except Exception:
            traceback.print_exc()


def run(fn) -> None:
    """Call ``fn()`` now, or after the scene being loaded has loaded."""
    if not hou.hipFile.isLoadingHipFile():
        fn()
        return
    if not _pending:
        hou.hipFile.addEventCallback(_on_event)
    _pending.append(fn)


def pending() -> int:
    """How many pieces of work wait for the load to finish."""
    return len(_pending)
