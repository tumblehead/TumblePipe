"""Repack a Multi's shared timeline, and say when an edit made shots overlap (#31).

A shared-timeline Multi stores each member's start (``config.multi_timeline``)
so a longer shot does not move the animation of the shots after it. That
leaves gaps when shots get shorter and overlaps when one gets longer, and
nothing offered ``multi_timeline.repack`` or said so. Repack is always the
artist's choice: it moves shots, and the animation made against them.

:func:`overlap_text` is Qt-free; the two dialogs import Qt when shown.
"""

from __future__ import annotations

from typing import Callable

from tumblepipe.util.uri import Uri

#: How many overlapping pairs a message lists per Multi.
SHOWN = 6

REPACK_NOTE = (
    "Repack lays the shots end to end in member order. Shots after the "
    "change move, and so does whatever was animated against their frames."
)


def _short(uri: str) -> str:
    """``entity:/shots/sq010/SH020`` -> ``sq010/SH020``."""
    return "/".join(uri.split(":/", 1)[-1].split("/")[1:]) or uri


def overlap_text(found: dict[str, list[tuple[str, str]]]) -> str:
    """One paragraph per Multi: which shot runs into which."""
    blocks = []
    for key in sorted(found):
        pairs = found[key]
        lines = [f"  {_short(a)} runs into {_short(b)}" for a, b in pairs[:SHOWN]]
        if len(pairs) > SHOWN:
            lines.append(f"  …and {len(pairs) - SHOWN} more")
        name = key.rsplit("/", 1)[-1]
        blocks.append(f"Multi {name}:\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def offer_repack(
    parent, found: dict[str, list[tuple[str, str]]],
    repack: Callable[[Uri], object],
) -> list[str]:
    """Warn that ``found``'s shots now overlap on their shared timelines and
    offer to repack them; the Multis repacked (none unless asked)."""
    if not found:
        return []
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Shared timeline")
    box.setText(
        "Shots now overlap on a shared timeline:\n\n"
        f"{overlap_text(found)}\n\n{REPACK_NOTE}"
    )
    label = "Repack" if len(found) == 1 else f"Repack {len(found)} Multis"
    repack_button = box.addButton(label, QMessageBox.AcceptRole)
    box.addButton("Leave as is", QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is not repack_button:
        return []
    for key in sorted(found):
        repack(Uri.parse(key))
    return sorted(found)


def confirm_repack(parent, name: str, overlap: str) -> bool:
    """The Multi menu's Repack Timeline…: ask first, it moves shots."""
    from tumbletrove.asset_browser.ui.dialogs import confirm_destructive

    message = f"Repack the shared timeline of Multi {name}?\n\n{REPACK_NOTE}"
    if overlap:
        message += f"\n\nOverlapping now:\n\n{overlap}"
    return confirm_destructive(
        parent, title=f"Repack {name}", message=message, action_label="Repack",
    )
