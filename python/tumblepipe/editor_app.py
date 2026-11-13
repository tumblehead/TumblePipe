"""The editor, for TumbleTrove Desktop's Scripts panel.

    python -m tumblepipe.editor_app [--edit entity:/edits/<name>[@vNNNN]]

Run by ``scripts/farm_launcher.py --app edits``, which replays the
project's package files first, so ``$TH_PROJECT_PATH`` and ``$OCIO`` are a
Houdini session's. It starts ``cinedex`` (``pipe.review_playlist.find_player``)
on the project: the edit named, else the project's master edit (the whole
film's cut, ``pipe.edits.master_edit``), else an untitled edit of every
shot; and waits for it, so the Desktop shows it running until the window
closes.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from tumblepipe.pipe.review_playlist import edit_arguments, find_player, project_ocio, timeline_arguments
from tumblepipe.util.uri import Uri


def editor_command(player: Path, project: str, ocio: str, edit: str | None = None, master: str | None = None) -> list[str]:
    """The command line that opens the editor on ``project``: ``edit``
    (``entity:/edits/<name>[@vNNNN]``) when given, else what the asset
    browser's Open Timeline opens with nothing selected
    (``review_playlist.timeline_arguments``): the master edit ``master``
    (its name), else every shot of the project."""
    if edit:
        return edit_arguments(player, project, ocio, Uri.parse(edit))
    return timeline_arguments(player, project, ocio, [], master)


def report(message: str) -> None:
    """Tell the artist what failed: a dialog (a Scripts-panel run has no
    console they watch), and stderr for the Desktop's log. A missing
    PySide leaves the log line alone."""
    print(message, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv[:1])
        QMessageBox.critical(None, 'Open Cinedex', message)
        del app
    except Exception:  # no PySide / no display: the stderr line stands
        pass


def _master_edit() -> str | None:
    """The project's master edit, None when it has none or it cannot be
    read: the editor then opens every shot, as with no master."""
    try:
        from tumblepipe.pipe.edits import master_edit
        return master_edit()
    except (OSError, ValueError, KeyError) as error:  # a broken config must not keep the editor shut
        print(f'Could not read the master edit ({error}): opening every shot', file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='tumblepipe.editor_app')
    parser.add_argument('--edit', help='the edit to open: entity:/edits/<name>, or @vNNNN for a published version')
    args = parser.parse_args(argv)
    project = os.environ.get('TH_PROJECT_PATH', '').strip()
    if not project:
        report('$TH_PROJECT_PATH is not set: run Open Cinedex from the project\'s menu in TumbleTrove Desktop')
        return 2
    try:
        command = editor_command(find_player(), project, project_ocio(), args.edit, _master_edit())
    except (OSError, ValueError) as error:
        report(str(error))
        return 2
    return subprocess.call(command, stdin=subprocess.DEVNULL)


if __name__ == '__main__':
    sys.exit(main())
