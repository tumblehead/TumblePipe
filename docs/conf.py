"""Sphinx configuration for TumblePipe documentation.

Built on Read the Docs from the public mirror at github.com/tumblehead/TumblePipe.
The version is pulled from hpm.toml so it tracks the package.

The same ``docs/`` tree ships inside the package and is rendered offline by
TumbleTrove (>= 0.27.0) under **TumbleTrove ▸ Documentation**. That renderer
is plain python-markdown2, so the pages carry no MyST directives: ``index.md``
is a plain Markdown page whose ``## Heading`` sections and linked list items
*are* the navigation (the rule TumbleTrove reads the sidebar by). The
``source-read`` hook below derives the Sphinx ``toctree`` from those same
lists, so there is one navigation to maintain and the two renderers cannot
disagree about it. It also appends the ``CHANGELOG.md`` include to the
changelog page — an include directive the offline renderer would show as a
code block.
"""

from __future__ import annotations

import re
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


# ── Navigation from index.md ─────────────────────────────────────────────────

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _nav_toctrees(index_text: str) -> str:
    """Hidden ``{toctree}`` blocks mirroring the index's sections and lists.

    Mirrors ``tumbletrove.docs.nav.parse_index``: a ``##`` heading opens a
    section (its caption), a list item linking a ``.md`` file is a page.
    Nested list items are flattened into their section — Sphinx nests by
    per-document toctrees, which these pages deliberately do not carry. A
    page is listed once, in the first section that names it.
    """
    sections: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    in_fence = False
    for line in index_text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING.match(line)
        if heading:
            if len(heading.group(1)) > 1:
                sections.append((re.sub(r"[`*]", "", heading.group(2)).strip(), []))
            continue
        item = _LIST_ITEM.match(line)
        if not item or not sections:
            continue
        for _label, target in _LINK.findall(item.group(1)):
            path = target.split("#", 1)[0]
            if "://" in target or target.startswith(("#", "/", "mailto:")):
                continue
            if not path.lower().endswith(".md"):
                continue
            docname = path[:-3]
            if docname not in seen:
                seen.add(docname)
                sections[-1][1].append(docname)
            break
    blocks = []
    for caption, docs in sections:
        if not docs:
            continue
        blocks.append(
            "\n```{toctree}\n"
            f":caption: {caption}\n"
            ":maxdepth: 1\n"
            ":hidden:\n\n"
            + "\n".join(docs)
            + "\n```\n"
        )
    return "".join(blocks)


def _inject(app, docname: str, source: list[str]) -> None:
    if docname == "index":
        source[0] += _nav_toctrees(source[0])
    elif docname == "changelog":
        source[0] += "\n```{include} ../CHANGELOG.md\n```\n"


def setup(app):
    app.connect("source-read", _inject)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
