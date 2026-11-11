"""Import Shots from CSV… in the browser's Shots section (#32).

``tools.csv_shot_import`` was CLI-only. This picks the sheet, plans it
(``plan_sheet``: the header row found by its column names) and shows the
plan, the ``[ADD SEQ]`` / ``[ADD]`` / ``[ERROR]`` lines the CLI's dry run
prints, before anything is written. Import writes exactly that plan.

:func:`summary` is Qt-free; :class:`CsvImportDialog` imports Qt.
"""

from __future__ import annotations

from tumblepipe.tools.csv_shot_import import SheetPlan, plan_lines


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def summary(sheet: SheetPlan) -> str:
    """One line: what Import would add, skip and refuse."""
    plan = sheet.plan
    parts = [_count(len(plan.new), "new shot")]
    if plan.sequences_to_create:
        parts.append(_count(len(plan.sequences_to_create), "new sequence"))
    if plan.skipped:
        parts.append(f"{len(plan.skipped)} already there")
    if plan.invalid:
        parts.append(_count(len(plan.invalid), "error"))
    other = sheet.rows_read - sheet.shot_rows
    if other:
        parts.append(f"{_count(other, 'row')} without a shot")
    return ", ".join(parts) + "."


def import_label(sheet: SheetPlan) -> str:
    """The dialog's Import button: how many shots it writes."""
    return f"Import {_count(len(sheet.plan.new), 'Shot')}"


def make_dialog(project: str, path: str, sheet: SheetPlan, parent=None):
    """The plan dialog; ``exec()`` is Accepted when the artist imports."""
    from PySide6.QtWidgets import (
        QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
    )
    from tumbletrove.asset_browser.core.theme import scaled

    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Import Shots from CSV — {project}")
    dialog.resize(scaled(640), scaled(420))
    layout = QVBoxLayout(dialog)
    source = QLabel(path)
    source.setWordWrap(True)
    layout.addWidget(source)
    layout.addWidget(QLabel(summary(sheet)))
    lines = plan_lines(sheet.plan)
    listing = QPlainTextEdit("\n".join(lines) or "Nothing to add: every shot is already there.")
    listing.setReadOnly(True)
    layout.addWidget(listing, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
    import_button = buttons.addButton(import_label(sheet), QDialogButtonBox.AcceptRole)
    import_button.setEnabled(bool(sheet.plan.new))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.import_button = import_button
    dialog.listing = listing
    return dialog
