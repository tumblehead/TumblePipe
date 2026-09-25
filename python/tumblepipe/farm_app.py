"""The Farm Submit grid as a standalone window, for TumbleTrove Desktop's Farm Submit button.

    python -m tumblepipe.farm_app [--context shots|assets]

Run by ``scripts/farm_launcher.py`` in Houdini's bundled Python (which has
PySide6 and ``pxr`` and takes no licence), with the environment Houdini would
have for the project. The dialog is the same one the Farm Submit quick action opens
inside Houdini; only the host differs, so no Houdini session is needed to
publish, playblast and render on the farm.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _add_houdini_dll_directory() -> None:
    """Let PySide6 and ``pxr`` load: Python 3.8+ on Windows ignores PATH."""
    hfs = os.environ.get('HFS')
    if hfs and hasattr(os, 'add_dll_directory'):
        bin_dir = Path(hfs) / 'bin'
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='tumblepipe.farm_app')
    parser.add_argument('--context', choices=('shots', 'assets'), default='shots')
    args = parser.parse_args(argv)

    _add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('TumblePipe Farm Submit')
    app.setQuitOnLastWindowClosed(True)

    try:
        from tumblepipe.asset_browser.submit_jobs_dialog import SubmitJobsDialog
        dialog = SubmitJobsDialog([], [], args.context, tick_kinds=())
    except Exception as error:
        QMessageBox.critical(None, 'Farm Submit', f'Could not open the Farm Submit grid:\n\n{error}')
        return 1
    # Accepting (Submit) closes the dialog and shows the status window; the
    # app then lives until that window closes too.
    dialog.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
