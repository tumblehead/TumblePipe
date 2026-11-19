"""Bring a window the package already has open to the front.

The Farm Manager, the Entity Editor, the Database Editor and the VRAM
Inspector are each one window, kept: asking for it again shows the one
there is. Each did ``show(); raise_(); activateWindow()``, which does
nothing for a window the artist had minimised: Qt's ``show()`` leaves a
minimised window minimised, so on Windows the click only lit the collapsed
title bar in the corner of the screen (Remy, 2026-11-18: "Clicking the
farm manager button doesn't bring it back to the front. It just highlights
the collapsed window. Hard to spot").

:func:`bring_to_front` is the one way to do it, for all of them: a
minimised window goes back to the state it was minimised from (maximised
stays maximised, full screen stays full screen: :func:`restored_state`,
not ``showNormal``, which would also un-maximise it), is shown, raised and
given the keyboard.

The decisions are plain functions over Qt's window state bits, so they are
tested without a display (``tests/test_window_front.py``);
``scripts/verify_window_front.py`` drives real windows offscreen.
"""

from __future__ import annotations

#: Qt's ``Qt.WindowState`` bits (``Qt.WindowNoState`` is 0).
MINIMIZED = 0x1
MAXIMIZED = 0x2
FULLSCREEN = 0x4
ACTIVE = 0x8

#: The steps of :func:`front_steps`.
RESTORE = 'restore'
SHOW = 'show'
RAISE = 'raise'
ACTIVATE = 'activate'


def restored_state(state: int) -> int:
    """The window state to set on a window in ``state`` that should come to
    the front: no longer minimised, active, and otherwise as it was
    (maximised or full screen kept)."""
    return (int(state) & ~MINIMIZED) | ACTIVE


def front_steps(visible: bool, state: int) -> tuple[str, ...]:
    """What bringing a window to the front takes, in order.

    ``restore`` (set :func:`restored_state`) only for a minimised window;
    ``show`` for one that is hidden or was minimised (setting the state of
    a hidden window does not show it); then always ``raise`` and
    ``activate``.
    """
    minimised = bool(int(state) & MINIMIZED)
    steps = []
    if minimised:
        steps.append(RESTORE)
    if minimised or not visible:
        steps.append(SHOW)
    steps += [RAISE, ACTIVATE]
    return tuple(steps)


def _state_bits(window) -> int:
    state = window.windowState()
    value = getattr(state, 'value', state)   # a PySide6 flag, or already an int
    return int(value)


def _qt_state(bits: int):
    from PySide6.QtCore import Qt
    return Qt.WindowState(bits)


def bring_to_front(window, *, to_state=_qt_state) -> None:
    """Show ``window`` in front of the others and give it the keyboard,
    restoring it first when it is minimised. On the GUI thread. Raises what
    Qt raises (a ``RuntimeError`` for a window whose C++ side is gone: the
    callers' cue to build a new one). ``to_state`` turns state bits into
    what ``setWindowState`` takes (Qt's flag; tests pass ``int``)."""
    state = _state_bits(window)
    for step in front_steps(window.isVisible(), state):
        if step == RESTORE:
            window.setWindowState(to_state(restored_state(state)))
        elif step == SHOW:
            window.show()
        elif step == RAISE:
            window.raise_()
        elif step == ACTIVATE:
            window.activateWindow()
