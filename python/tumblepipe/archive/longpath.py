"""Paths past Windows' 260-character limit.

A project's deepest files are already long
(``…/export/assets/CHAR/Daughter/default/blendshape/v0001/assets_CHAR_Daughter_default_blendshape_v0001.usd``),
and an archive is restored wherever someone picks. A first restore of
HideAndReek into a temp folder failed at file 858 of 2642 on exactly that.
Windows accepts any length with the ``\\\\?\\`` prefix (``\\\\?\\UNC\\`` for a
share), so every filesystem call the archive makes goes through
:func:`long_path`. Elsewhere it returns the path unchanged.
"""

import os
from pathlib import Path

_PREFIX = '\\\\?\\'


def long_path(path) -> Path:
    """``path``, absolute, in the form Windows opens at any length."""
    if os.name != 'nt':
        return Path(path)
    text = str(path)
    if text.startswith(_PREFIX):
        return Path(text)
    text = os.path.abspath(text)
    if text.startswith('\\\\'):
        return Path(_PREFIX + 'UNC\\' + text[2:])
    return Path(_PREFIX + text)
