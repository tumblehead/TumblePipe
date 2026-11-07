"""The editor, for TumbleTrove Desktop's Scripts panel.

    python -m tumblepipe.editor_app [--edit entity:/edits/<name>[@vNNNN]]

Run by ``scripts/farm_launcher.py --app edits``, which replays the
project's package files first, so ``$TH_PROJECT_PATH`` and ``$OCIO`` are a
Houdini session's. It starts ``th_player`` (``pipe.review_playlist.find_player``)
on the project: an untitled edit of every shot, or the edit named, and waits for it,
so the Desktop shows it running until the window closes.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from tumblepipe.pipe.review_playlist import find_player, project_ocio


def editor_command(player: str, project: str, ocio: str, edit: str | None = None) -> list[str]:
    """The command line that opens the editor on ``project``: ``edit``
    (``entity:/edits/<name>[@vNNNN]``) when given, else an untitled edit
    of every shot of the project."""
    command = [player, '--project', project, '--ocio', ocio]
    if edit:
        command += ['--edit', edit]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='tumblepipe.editor_app')
    parser.add_argument('--edit', help='the edit to open: entity:/edits/<name>, or @vNNNN for a published version')
    args = parser.parse_args(argv)
    project = os.environ.get('TH_PROJECT_PATH', '').strip()
    if not project:
        print('$TH_PROJECT_PATH is not set: run this from the TumbleTrove Desktop Scripts panel', file=sys.stderr)
        return 2
    try:
        command = editor_command(str(find_player()), project, project_ocio(), args.edit)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    return subprocess.call(command, stdin=subprocess.DEVNULL)


if __name__ == '__main__':
    sys.exit(main())
