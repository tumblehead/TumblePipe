"""Sphinx configuration for TumblePipe documentation.

Built on Read the Docs from the public mirror at github.com/tumblehead/TumblePipe.
The version is pulled from hpm.toml so it tracks the package.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
with open(_PACKAGE_ROOT / "hpm.toml", "rb") as _fp:
    _pkg = tomllib.load(_fp)["package"]

project = "TumblePipe"
author = "Tumblehead"
copyright = "Tumblehead"
release = _pkg["version"]
version = release

extensions = [
    "myst_parser",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
]

source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "smartquotes",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_title = f"TumblePipe {release}"
html_static_path = ["_static"]
html_theme_options = {
    "source_repository": "https://github.com/tumblehead/TumblePipe",
    "source_branch": "main",
    "source_directory": "docs/",
}
