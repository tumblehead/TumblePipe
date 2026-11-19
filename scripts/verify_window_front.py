"""Headless harness for bringing a kept window to the front
(``tumblepipe.util.window_front``, and the Farm Manager's ``show_manager``).

Remy, 2026-11-18: "If the farm manager is minimized it collapses to the
corner of the screen. Clicking the farm manager button doesn't bring it
back to the front. It just highlights the collapsed window."

Real windows, offscreen. Checks:

  1. Qt's own ``show()`` leaves a minimised window minimised (the bug: if
     this ever stops holding, the helper's restore step is not what fixes
     it)
  2. ``bring_to_front`` on a minimised window: no longer minimised, shown,
     raised and activated, once each
  3. a window minimised from maximised comes back maximised, not normal
  4. a hidden (closed) window is shown; a visible one is only raised and
     activated, its state untouched
  5. the state Qt ends in is the one ``restored_state`` names, for every
     combination of minimised and maximised
  6. the Farm Manager: ``show_manager`` twice is one window; minimised in
     between, the second call restores it; closed and deleted, a third
     call builds a new one

Run (no Houdini licence):

    QT_QPA_PLATFORM=offscreen python scripts/verify_window_front.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

TEMP = Path(tempfile.mkdtemp(prefix='window-front-verify-'))
os.environ['TH_LOCAL_FARM_DIR'] = str(TEMP / 'local_farm')
os.environ.setdefault('TH_USER', 'tester')

if os.environ.get('HFS') and hasattr(os, 'add_dll_directory'):
    os.add_dll_directory(str(Path(os.environ['HFS']) / 'bin'))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from tumblepipe.util import window_front  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = '') -> None:
    _checks.append((name, bool(condition), detail))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f' — {detail}' if detail and not condition else ''))


def pump(app: QApplication, seconds: float = 0.15) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


class Window(QMainWindow):
    """Counts what reaches it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def raise_(self) -> None:
        self.calls.append('raise')
        super().raise_()

    def activateWindow(self) -> None:
        self.calls.append('activate')
        super().activateWindow()


def bits(window) -> int:
    return window_front._state_bits(window) & (window_front.MINIMIZED | window_front.MAXIMIZED)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    print('1. what Qt does on its own')
    plain = Window()
    plain.show()
    pump(app)
    plain.showMinimized()
    pump(app)
    check('the window is minimised', plain.isMinimized())
    plain.show()
    plain.raise_()
    plain.activateWindow()
    pump(app)
    check('show(), raise_() and activateWindow() leave it minimised', plain.isMinimized())

    print('2. a minimised window')
    plain.calls.clear()
    window_front.bring_to_front(plain)
    pump(app)
    check('is no longer minimised', not plain.isMinimized(), str(bits(plain)))
    check('is visible', plain.isVisible())
    check('was raised and activated once each', plain.calls == ['raise', 'activate'], str(plain.calls))

    print('3. minimised from maximised')
    plain.showMaximized()
    pump(app)
    plain.setWindowState(plain.windowState() | Qt.WindowMinimized)
    pump(app)
    check('it is minimised, with maximised remembered',
          plain.isMinimized() and bits(plain) == window_front.MINIMIZED | window_front.MAXIMIZED, str(bits(plain)))
    window_front.bring_to_front(plain)
    pump(app)
    check('it comes back maximised', plain.isMaximized() and not plain.isMinimized(), str(bits(plain)))

    print('4. hidden, and already in front')
    plain.showNormal()
    pump(app)
    plain.hide()
    pump(app)
    plain.calls.clear()
    window_front.bring_to_front(plain)
    pump(app)
    check('a hidden window is shown, raised and activated',
          plain.isVisible() and plain.calls == ['raise', 'activate'], str(plain.calls))
    plain.showMaximized()
    pump(app)
    plain.calls.clear()
    window_front.bring_to_front(plain)
    pump(app)
    check('a visible window keeps its state and is raised and activated',
          plain.isMaximized() and plain.isVisible() and plain.calls == ['raise', 'activate'], str(plain.calls))

    print('5. every state')
    for minimised in (False, True):
        for maximised in (False, True):
            window = Window()
            window.show()
            state = Qt.WindowNoState
            if maximised:
                state |= Qt.WindowMaximized
            if minimised:
                state |= Qt.WindowMinimized
            window.setWindowState(state)
            pump(app, 0.08)
            before = window_front._state_bits(window)
            window_front.bring_to_front(window)
            pump(app, 0.08)
            want = window_front.restored_state(before) & (window_front.MINIMIZED | window_front.MAXIMIZED)
            check(f'minimised={minimised} maximised={maximised}: ends as restored_state says',
                  bits(window) == want and window.isVisible(), f'{bits(window)} != {want}')
            window.close()
    plain.close()

    print('6. the Farm Manager')
    from tumblepipe.farm.manager import window as manager
    first = manager.show_manager()
    pump(app, 0.3)
    check('opens visible', first.isVisible() and not first.isMinimized())
    first.showMinimized()
    pump(app)
    check('is minimised by the artist', first.isMinimized())
    second = manager.show_manager()
    pump(app, 0.3)
    check('asking again gives the same window', second is first)
    check('restored from minimised', not second.isMinimized() and second.isVisible())
    second.showMaximized()
    pump(app)
    second.setWindowState(second.windowState() | Qt.WindowMinimized)
    pump(app)
    third = manager.show_manager()
    pump(app, 0.3)
    check('minimised from maximised, it comes back maximised',
          third is first and third.isMaximized() and not third.isMinimized())
    first.setAttribute(Qt.WA_DeleteOnClose, True)
    first.close()
    pump(app, 0.3)
    again = manager.show_manager()
    pump(app, 0.3)
    check('closed and deleted, asking again builds a new one', again is not first and again.isVisible())
    again.close()
    pump(app, 0.2)

    failures = [name for name, ok, _ in _checks if not ok]
    print(f"\n{len(_checks) - len(failures)}/{len(_checks)} checks passed")
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        code = main()
    finally:
        shutil.rmtree(TEMP, ignore_errors=True)
    sys.exit(code)
