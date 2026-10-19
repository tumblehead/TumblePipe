"""Handing a path to the desktop: showing a folder in the OS file browser."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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
