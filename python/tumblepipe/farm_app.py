"""The Farm Submit grid as a standalone window, for TumbleTrove Desktop's Farm Submit button.

    python -m tumblepipe.farm_app [--context shots|assets] [--entity URI]...

``--entity`` (as often as wanted) opens the grid with those entities
ticked for a render, as the Render quick action opens it on the shots
selected in Houdini. Nothing is submitted until Submit is pressed in the
window: Cinedex's **Farm Submit...** on shots opens it this way.

Run by ``scripts/farm_launcher.py`` in Houdini's bundled Python (which has
PySide6 and ``pxr`` and takes no licence), with the environment Houdini would
have for the project. The dialog is the same one the Render quick action opens
inside Houdini; only the host differs, so no Houdini session is needed to
publish, playblast and render on the farm.
"""

from __future__ import annotations

import argparse
import sys
from tumblepipe.util.hfs import add_houdini_dll_directory


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='tumblepipe.farm_app')
    parser.add_argument('--context', choices=('shots', 'assets'), default='shots')
    parser.add_argument('--entity', action='append', default=[], metavar='URI',
                        help='an entity to open the grid ticked on (repeatable)')
    return parser.parse_args(argv)


def preselection(entities: list[str], context: str) -> list:
    """The dialog's entity URIs for ``--entity`` values: each once, in the
    order given. Raises
    ``ValueError`` for one that is no entity of ``context`` (``entity:/shots/…``
    for shots), so a wrong one is said, not dropped."""
    from tumblepipe.util.uri import Uri
    uris, seen = [], set()
    for raw in entities:
        uri = Uri.parse(raw)
        segments = uri.segments
        if uri.purpose != 'entity' or len(segments) < 2 or segments[0] != context:
            raise ValueError(f'{raw}: not an entity of {context}')
        if str(uri) in seen:
            continue
        seen.add(str(uri))
        uris.append(uri)
    return uris


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('TumblePipe Farm Submit')
    app.setQuitOnLastWindowClosed(True)

    try:
        from tumblepipe.asset_browser.submit_jobs_dialog import SubmitJobsDialog
        uris = preselection(args.entity, args.context)
        if uris:
            dialog = SubmitJobsDialog(uris, args.context)
        else:
            dialog = SubmitJobsDialog([], args.context, tick_kinds=())
    except Exception as error:
        QMessageBox.critical(None, 'Farm Submit', f'Could not open the Farm Submit grid:\n\n{error}')
        return 1
    # The submission's progress shows in the dialog itself; the app lives
    # until the dialog closes (the submission runs on in its own process).
    dialog.show()
    return app.exec()


if __name__ == '__main__':
    from tumblepipe.util.logging import setup_logging
    setup_logging()
    sys.exit(main())
