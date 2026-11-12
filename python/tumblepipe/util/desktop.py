"""Handing a path to the desktop: showing a folder in the OS file browser."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


def open_in_file_browser(path: Path) -> None:
    """Show ``path`` in Explorer / Finder / the desktop's file manager.

    A path that does not exist raises FileNotFoundError: the shell would
    otherwise open some default location and look as if it had worked.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such folder: {path}")
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606  (the documented way on Windows)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_command(path: Path, platform: str) -> list[str] | str:
    """The command that shows ``path`` (a file) in the OS file browser.

    Windows Explorer and the macOS Finder select the file inside its folder;
    xdg-open has no select, so Linux opens the file's folder.

    On Windows the command is one string, ``explorer /select,"<path>"``,
    with a backslash path. As a list, subprocess would quote the whole
    ``/select,<path>`` argument, which Explorer misparses when the path has
    a space: it opens Documents instead of selecting the file.
    """
    if platform == "win32":
        native = str(PureWindowsPath(str(path)))
        return f'explorer /select,"{native}"'
    path = PurePosixPath(str(path))
    if platform == "darwin":
        return ["open", "-R", str(path)]
    return ["xdg-open", str(path.parent)]


def reveal_in_file_browser(path: Path) -> None:
    """Show the file ``path`` in the OS file browser, selected where the OS
    allows. A missing file raises FileNotFoundError."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such file: {path}")
    # explorer.exe exits 1 even when it worked: never check the result
    subprocess.Popen(reveal_command(path, sys.platform))
