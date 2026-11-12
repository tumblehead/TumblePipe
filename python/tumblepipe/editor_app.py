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

from tumblepipe.pipe.review_playlist import find_player, project_ocio


def editor_command(player: str, project: str, ocio: str, edit: str | None = None) -> list[str]:
    """The command line that opens the editor on ``project``: ``edit``
    (``entity:/edits/<name>[@vNNNN]``) when given, else an untitled edit
    of every shot of the project."""
    command = [player, '--project', project, '--ocio', ocio]
    if edit:
        command += ['--edit', edit]
    return command


def default_edit(master: str | None) -> str | None:
    """The edit opened when none is named: the master edit ``master`` (its
    name; None when the project has none, which opens every shot)."""
    return None if master is None else f'entity:/edits/{master}'


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
        print('$TH_PROJECT_PATH is not set: run this from the TumbleTrove Desktop Scripts panel', file=sys.stderr)
        return 2
    edit = args.edit or default_edit(_master_edit())
    try:
        command = editor_command(str(find_player()), project, project_ocio(), edit)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    return subprocess.call(command, stdin=subprocess.DEVNULL)


if __name__ == '__main__':
    sys.exit(main())
