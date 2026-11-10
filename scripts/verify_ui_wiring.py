"""Audit: every push button and menu action of TumblePipe's windows is wired.

Run inside a live Houdini session of a project (it opens windows), e.g. over
TumbleTrove Desktop's bridge:

    exec(open('<repo>/scripts/verify_ui_wiring.py').read()); print(run())

It opens the Farm Manager (farm and local), Farm Submit on each tab (the
project's first shot, first asset and first edit) and the Asset Browser
panel when one is open, off-screen, and lists every enabled push button
with nothing connected to clicked/toggled/pressed/released, and every
enabled action with nothing on triggered/toggled. Checkboxes and radio
buttons are state, read when something else runs, so they are not
counted; a tool button with a menu or a default action, a dialog button
box's buttons and Qt's own (``qt_*``) are wired by Qt.

QA 2026-11-10 (P9): Farm Manager, Farm Submit and the Asset Browser came
out clean (68 browser buttons). Kept so the nightly re-runs it.
"""


def unwired(window) -> list[str]:
    """The enabled push buttons and actions of ``window`` nothing is connected to."""
    from PySide6.QtCore import QMetaMethod
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QAbstractButton, QCheckBox, QDialogButtonBox, QRadioButton, QToolButton,
    )

    found = []
    for button in window.findChildren(QAbstractButton):
        if isinstance(button, (QCheckBox, QRadioButton)) or not button.isEnabled():
            continue
        if isinstance(button, QToolButton) and (
            button.menu() is not None or button.defaultAction() is not None
        ):
            continue
        if isinstance(button.parent(), QDialogButtonBox) or button.objectName().startswith('qt_'):
            continue
        signals = (button.clicked, button.toggled, button.pressed, button.released)
        if not any(button.isSignalConnected(QMetaMethod.fromSignal(s)) for s in signals):
            found.append(f'button {type(button).__name__} {button.text() or button.toolTip()[:40]!r}')
    for action in window.findChildren(QAction):
        if not action.isEnabled() or not action.text() or action.isSeparator() or action.menu():
            continue
        if not any(action.isSignalConnected(QMetaMethod.fromSignal(s))
                   for s in (action.triggered, action.toggled)):
            found.append(f'action {action.text()!r}')
    return found


def _first(kind: str):
    from tumblepipe.api import api
    from tumblepipe.util.uri import Uri
    if kind == 'edits':
        from tumblepipe.pipe import edits
        names = edits.list_edits()
        return (edits.edit_uri(names[0]), names[0]) if names else None
    for entity in api.config.list_entities(Uri.parse(f'entity:/{kind}'), closure=True):
        uri = getattr(entity, 'uri', entity)
        return uri, str(uri).rsplit('/', 1)[-1]
    return None


def run() -> dict[str, list[str]]:
    """``{window: [what is not wired]}`` for every window audited."""
    from PySide6.QtWidgets import QApplication
    from tumblepipe.asset_browser import scene
    from tumblepipe.farm.manager.window import ManagerWindow

    report = {}
    for source in ('farm', 'local'):
        window = ManagerWindow(source=source)
        window.move(-4000, 0)
        window.show()
        report[f'Farm Manager ({source})'] = unwired(window)
        window.close()
        window.deleteLater()
    for context in ('shots', 'assets', 'edits'):
        first = _first(context)
        if first is None:
            report[f'Farm Submit ({context})'] = ['(skipped: the project has none)']
            continue
        scene.open_submit_jobs_dialog([first[0]], [first[1]], context)
        dialog = scene._submit_dialog
        dialog.move(-4000, 0)
        report[f'Farm Submit ({context})'] = unwired(dialog)
        dialog.close()
    for widget in QApplication.allWidgets():
        if type(widget).__name__ == 'AssetBrowserWidget':
            report['Asset Browser'] = unwired(widget)
            break
    return report
