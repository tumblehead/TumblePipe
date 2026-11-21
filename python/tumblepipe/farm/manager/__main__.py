"""The Farm Manager as a standalone window, for TumbleTrove Desktop's Farm Manager button.

    python -m tumblepipe.farm.manager [--source local|farm] [--job <id>]

Run by ``scripts/farm_launcher.py --app manager`` in Houdini's bundled
Python (PySide6, no licence), with the environment a Houdini session would
have for the project, like the Farm Submit window (``tumblepipe.farm_app``).

``--job`` opens it on one job, selected with its tasks and log showing
(``ManagerWindow.select_job``): a Deadline job id, or a local job's
``local:<batch>/<index>``. Cinedex's **Show Farm Job in Farm Manager**
opens it so on the job a clip's badge stands for. Opening it changes
nothing on the farm.
"""

from __future__ import annotations

import argparse
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='tumblepipe.farm.manager')
    parser.add_argument('--source', choices=('local', 'farm'), default=None)
    parser.add_argument('--job', default='', help='the id of the job to open on')
    args = parser.parse_args(argv)
    args.job = (args.job or '').strip()
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    from tumblepipe.farm_app import _add_houdini_dll_directory
    _add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('TumblePipe Farm Manager')
    app.setQuitOnLastWindowClosed(True)
    try:
        from tumblepipe.farm.manager.window import show_manager
        show_manager(source=args.source, select=args.job)
    except Exception as error:
        QMessageBox.critical(None, 'Farm Manager', f'Could not open the Farm Manager:\n\n{error}')
        return 1
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
