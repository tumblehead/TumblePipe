"""Houdini's libraries for the standalone tools that run outside Houdini."""

import os
from pathlib import Path


def add_houdini_dll_directory() -> None:
    """Let PySide6 and ``pxr`` load from ``$HFS/bin``: Python 3.8+ on Windows
    ignores PATH for DLLs. Nothing happens without HFS, without its ``bin``,
    or off Windows."""
    hfs = os.environ.get('HFS')
    if hfs and hasattr(os, 'add_dll_directory'):
        bin_dir = Path(hfs) / 'bin'
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))
