"""The Clean Up Project window, for TumbleTrove Desktop's Scripts panel.

    python -m tumblepipe.cleanup.app

Run by ``scripts/farm_launcher.py --app cleanup`` in Houdini's bundled
Python, which carries PySide6 and (through the project's packages)
TumbleTrove's theme. It wears the Farm Submit window's look: on the left a
section per category (``tumblepipe.cleanup``), titled, saying what it
deletes, ticked to look at it and holding what it keeps; on the right the
overview of the listing (``cleanup.overview``): a total at the top, every
warning as a banner under it, and a row per category that opens to why
(reasons with counts and sizes) and what goes, biggest first.

It lists first and deletes only when asked: **List** reads the project and
shows the overview; **Delete…** asks once more, on a card over the window,
then deletes exactly that list and logs it. Changing anything after a
listing needs a new listing before anything can be deleted. Reading and
deleting run on a worker thread.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tumblepipe.archive.roots import project_roots
from tumblepipe.cleanup import core, overview

_CONTEXTS = (
    ('Shots and assets', None),
    ('Shots', 'shots'),
    ('Assets', 'assets'),
)

#: Per category: the settings its section shows, as (Settings field, label,
#: unit, minimum, maximum).
_FIELDS = {
    'exports': (('keep_exports', 'Keep the newest', 'finished versions', 1, 50),),
    'renders': (
        ('keep_renders', 'Keep the newest', 'complete versions', 1, 50),
        ('check_render_days', 'Check renders older than', 'days', 0, 3650),
    ),
    'caches': (('keep_caches', 'Keep the newest', 'finished versions', 1, 50),),
    'backups': (
        ('keep_backups', 'Keep the newest', "of the latest version's", 0, 100),
        ('backup_days', 'None newer than', 'days', 0, 3650),
    ),
    'jobs': (
        ('job_days', 'Not named by Deadline for', 'days', 0, 3650),
        ('local_job_days', 'Local farm jobs after', 'days', 0, 3650),
    ),
    'deleted': (('deleted_days', 'Deleted more than', 'days ago', 0, 3650),),
}


def section_summary(key: str, values: dict[str, int], on: bool) -> str:
    """The one line a category's section headline shows: its settings, or
    that it is not looked at."""
    if not on:
        return 'not looked at'
    return ', '.join(
        f'{label[0].lower()}{label[1:]} {values[name]} {unit}' for name, label, unit, _low, _high in _FIELDS[key]
    )


def sentence(about: str) -> str:
    """A category's ``about`` as a sentence; a node name (``th::cache``)
    keeps its case."""
    head = about if about.startswith('th::') else about[:1].upper() + about[1:]
    return head + '.'


def _add_houdini_dll_directory() -> None:
    """Let PySide6 load: Python 3.8+ on Windows ignores PATH."""
    hfs = os.environ.get('HFS')
    if hfs and hasattr(os, 'add_dll_directory'):
        bin_dir = Path(hfs) / 'bin'
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))


def build_window():
    from PySide6.QtCore import QObject, Qt, QThread, Signal
    from PySide6.QtGui import QColor, QFont
    from PySide6.QtWidgets import (
        QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
        QPushButton, QScrollArea, QSpinBox, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    )
    from tumbletrove.common.theme import (
        ACCENT, BG_DARK, BG_DARKEST, BG_MID, BORDER, BUTTON_PRIMARY_STYLE, COMBO_STYLE, FONT_BODY,
        FONT_FAMILY, SEARCH_STYLE, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
    )
    # The Farm Submit window's pieces: its sections, banners, titles, list
    # style, its over-the-window card and its state colours.
    from tumblepipe.asset_browser.farm_submit_ui import (
        CURRENT_COLOUR, ERROR_COLOUR, STALE_COLOUR, Overlay, Section,
        _banner, _list_style, _subtitle, _title,
    )
    from tumblepipe.util import value_ladder_qt

    chevron = (Path(__file__).resolve().parents[1] / 'asset_browser' / 'icons' / 'chevron-down.svg').as_posix()
    window_style = f"""
QWidget#cleanupWindow {{ background-color: {BG_DARKEST}; }}
QLabel, QCheckBox {{
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    background: transparent;
}}
QCheckBox {{ spacing: 6px; }}
QLabel:disabled, QCheckBox:disabled {{ color: {TEXT_DIM}; }}
QSpinBox {{
    background-color: #1c1c1c;
    color: {TEXT_PRIMARY};
    border: 1px solid #3a3a3a;
    border-radius: 3px;
    padding: 3px 5px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    min-height: 20px;
}}
QSpinBox:focus {{ border-color: {ACCENT}; }}
/* No arrows, as in Farm Submit: styled, they read as two dots. Typing, the
   arrow keys, the wheel and the value ladder step it. Zero-width buttons,
   not NoButtons: under Fusion a styled spin box without buttons draws its
   text one pixel wide. */
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QSpinBox:disabled {{ color: {TEXT_DIM}; }}
QScrollArea, QScrollArea > QWidget > QWidget#cleanupSections {{ background: transparent; border: none; }}
QSplitter::handle {{ background-color: transparent; }}
QScrollBar:vertical {{ background: #1f1f1f; width: 10px; margin: 0; border: none; }}
QScrollBar:horizontal {{ background: #1f1f1f; height: 10px; margin: 0; border: none; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: #4a4a4a; border-radius: 4px; margin: 2px;
}}
QScrollBar::handle:vertical {{ min-height: 30px; }}
QScrollBar::handle:horizontal {{ min-width: 30px; }}
QScrollBar::handle:hover {{ background: #6a6a6a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QPushButton {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 14px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QToolTip {{ background-color: {BG_DARK}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; }}
"""
    combo_style = COMBO_STYLE + f"""
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; border: none; margin-right: 6px; }}
"""
    # The theme's primary button, as tall as the Delete button beside it.
    primary_style = BUTTON_PRIMARY_STYLE + 'QPushButton { padding: 6px 18px; }'
    danger_style = f"""
QPushButton {{
    background-color: {ERROR_COLOUR};
    color: #ffffff;
    border: 1px solid {ERROR_COLOUR};
    border-radius: 4px;
    padding: 6px 18px;
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
    font-weight: bold;
}}
QPushButton:hover {{ background-color: #ee6c62; border-color: #ee6c62; }}
QPushButton:disabled {{ background-color: {BG_MID}; color: {TEXT_DIM}; border-color: {BORDER}; }}
"""
    tones = {
        overview.TONE_NORMAL: TEXT_PRIMARY,
        overview.TONE_HEADING: TEXT_PRIMARY,
        overview.TONE_DIM: TEXT_SECONDARY,
        overview.TONE_WARNING: STALE_COLOUR,
        overview.TONE_ERROR: ERROR_COLOUR,
    }

    def font(*, bold: bool = False, size: int = FONT_BODY) -> QFont:
        out = QFont(FONT_FAMILY)
        out.setPixelSize(size)
        if bold:
            out.setWeight(QFont.DemiBold)
        return out

    def fill_tree(tree: QTreeWidget, rows) -> None:
        """``rows`` (``overview.Row``) into ``tree``, drawn by their tone."""
        tree.clear()

        def add(parent, row):
            item = QTreeWidgetItem(parent, list(row.cells))
            colour = QColor(tones[row.tone])
            heading = parent is tree
            for column in range(len(row.cells)):
                item.setForeground(column, colour)
                if heading:
                    item.setFont(column, font(bold=True))
                if column:
                    item.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
            if row.tip:
                item.setToolTip(0, row.tip)
            for child in row.children:
                add(item, child)
            item.setExpanded(row.expanded)

        for row in rows:
            add(tree, row)

    class Worker(QObject):
        finished = Signal(object)
        failed = Signal(str)
        progress = Signal(str)

        def __init__(self, job):
            super().__init__()
            self._job = job

        def run(self):
            try:
                self.finished.emit(self._job(self.progress.emit))
            except Exception as error:  # surfaced to the user, not swallowed
                self.failed.emit(f'{type(error).__name__}: {error}')

    class ConfirmCard(QWidget):
        """The last question before deleting, on the window's overlay."""

        confirmed = Signal()
        back = Signal()

        def __init__(self, project: str, view: overview.Overview, log_path: Path):
            super().__init__()
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(12)
            root.addWidget(_title(f'Delete from {project}?'))
            root.addWidget(_banner(
                ERROR_COLOUR,
                f'{overview.plural(view.to_delete, "item")}, {core.human(view.size)} in all. '
                'This cannot be undone.',
            ))
            self.lines = QTreeWidget()
            self.lines.setStyleSheet(_list_style())
            self.lines.setColumnCount(3)
            self.lines.setHeaderLabels(['', 'To delete', 'Frees'])
            self.lines.setRootIsDecorated(False)
            self.lines.setSelectionMode(QAbstractItemView.NoSelection)
            self.lines.setFocusPolicy(Qt.NoFocus)
            for category in view.categories:
                if category.to_delete:
                    item = QTreeWidgetItem(self.lines, [
                        category.title, overview.plural(category.to_delete, category.noun),
                        core.human(category.size),
                    ])
                    for column in (1, 2):
                        item.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
            header = self.lines.header()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            for column in (1, 2):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
            self.lines.setMaximumHeight(40 + 30 * max(1, self.lines.topLevelItemCount()))
            root.addWidget(self.lines)
            root.addWidget(_subtitle(f'Everything removed is logged to {log_path}.'))
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            self.back_button = QPushButton('Back')
            self.back_button.setToolTip('Back to the listing; nothing is deleted (Esc)')
            self.back_button.clicked.connect(self.back)
            buttons.addWidget(self.back_button)
            self.delete_button = QPushButton(overview.delete_label(view).rstrip('…'))
            self.delete_button.setStyleSheet(danger_style)
            self.delete_button.clicked.connect(self.confirmed)
            buttons.addWidget(self.delete_button)
            root.addLayout(buttons)

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.setObjectName('cleanupWindow')
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.setStyleSheet(window_style)
            self.setWindowTitle('TumblePipe — Clean Up Project')
            self.resize(1180, 780)
            self._thread = None
            self._worker = None
            self.plan = None
            self.view = None
            defaults = core.Settings()
            layout = QVBoxLayout(self)
            layout.setContentsMargins(18, 16, 18, 14)
            layout.setSpacing(12)
            layout.addWidget(_title('Clean Up Project'))
            layout.addWidget(_subtitle(
                'Deletes what the project no longer needs. Tick what to look at and set what each keeps, '
                'then List: nothing is deleted until you have seen the list and confirmed.'
            ))

            where = QHBoxLayout()
            where.setSpacing(8)
            where.addWidget(QLabel('Project folder'))
            self.project_edit = QLineEdit(os.environ.get('TH_PROJECT_PATH', ''))
            self.project_edit.setStyleSheet(SEARCH_STYLE)
            self.project_edit.setPlaceholderText('The project to clean up')
            self.project_edit.textChanged.connect(self._stale)
            where.addWidget(self.project_edit, 1)
            where.addSpacing(10)
            look = QLabel('Exports, renders, caches and backups of')
            look.setStyleSheet(f'color: {TEXT_SECONDARY};')
            where.addWidget(look)
            self.context_combo = QComboBox()
            self.context_combo.setStyleSheet(combo_style)
            for label, _value in _CONTEXTS:
                self.context_combo.addItem(label)
            self.context_combo.currentIndexChanged.connect(self._stale)
            where.addWidget(self.context_combo)
            layout.addLayout(where)

            splitter = QSplitter(Qt.Horizontal)
            splitter.setChildrenCollapsible(False)
            splitter.addWidget(self._build_sections(defaults))
            splitter.addWidget(self._build_overview())
            splitter.setStretchFactor(0, 0)
            splitter.setStretchFactor(1, 1)
            splitter.setSizes([430, 730])
            layout.addWidget(splitter, 1)

            bottom = QHBoxLayout()
            bottom.setSpacing(8)
            self.status = QLabel('')
            self.status.setStyleSheet(f'color: {TEXT_SECONDARY};')
            self.status.setWordWrap(True)
            bottom.addWidget(self.status, 1)
            self.list_button = QPushButton('List')
            self.list_button.setToolTip('Read the project and show what would be deleted; deletes nothing')
            self.list_button.setStyleSheet(primary_style)
            self.list_button.clicked.connect(self._list)
            bottom.addWidget(self.list_button)
            self.delete_button = QPushButton('Delete…')
            self.delete_button.setToolTip('Asks once more before deleting what the listing shows')
            self.delete_button.setStyleSheet(danger_style)
            self.delete_button.setEnabled(False)
            self.delete_button.clicked.connect(self._delete)
            bottom.addWidget(self.delete_button)
            layout.addLayout(bottom)

            self.overlay = Overlay(self)
            self.overlay.escape.connect(self._close_card)
            self.card = None

        # ── building ──────────────────────────────────────────

        def _build_sections(self, defaults: core.Settings) -> QWidget:
            holder = QWidget()
            holder.setObjectName('cleanupSections')
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 8, 0)
            column.setSpacing(10)
            self.sections: dict[str, Section] = {}
            self.checks: dict[str, QCheckBox] = {}
            self.spins: dict[str, QSpinBox] = {}
            self._fields: dict[str, list[QWidget]] = {}
            for category in core.categories():
                section = Section(category.title, ACCENT)
                form = section.form()
                about = QLabel(sentence(category.about))
                about.setWordWrap(True)
                about.setStyleSheet(f'color: {TEXT_SECONDARY};')
                form.addRow(about)
                check = QCheckBox('Look at these')
                check.setChecked(True)
                check.toggled.connect(self._stale)
                check.toggled.connect(lambda _on, key=category.key: self._section_changed(key))
                self.checks[category.key] = check
                form.addRow(check)
                widgets = []
                for name, label, unit, low, high in _FIELDS[category.key]:
                    spin = QSpinBox()
                    spin.setMinimumWidth(64)
                    spin.setRange(low, high)
                    default = int(getattr(defaults, name))
                    spin.setValue(default)
                    spin.valueChanged.connect(self._stale)
                    spin.valueChanged.connect(lambda _v, key=category.key: self._section_changed(key))
                    value_ladder_qt.install(spin, default=default)
                    self.spins[name] = spin
                    field = QWidget()
                    row = QHBoxLayout(field)
                    row.setContentsMargins(0, 0, 0, 0)
                    row.setSpacing(6)
                    row.addWidget(spin)
                    unit_label = QLabel(unit)
                    unit_label.setStyleSheet(f'color: {TEXT_SECONDARY};')
                    row.addWidget(unit_label)
                    row.addStretch(1)
                    caption = QLabel(label)
                    form.addRow(caption, field)
                    widgets += [caption, field]
                self._fields[category.key] = widgets
                self.sections[category.key] = section
                column.addWidget(section)
                self._section_changed(category.key)
            column.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(holder)
            scroll.setMinimumWidth(360)
            return scroll

        def _build_overview(self) -> QWidget:
            panel = QWidget()
            column = QVBoxLayout(panel)
            column.setContentsMargins(8, 0, 0, 0)
            column.setSpacing(8)
            self.headline = QLabel('Nothing listed yet')
            self.headline.setStyleSheet(
                f'font-size: {FONT_BODY + 9}px; font-weight: 600; color: {TEXT_PRIMARY};'
            )
            column.addWidget(self.headline)
            self.subline = QLabel(
                'List the project to see what would be deleted. Nothing is deleted until you confirm.'
            )
            self.subline.setWordWrap(True)
            self.subline.setStyleSheet(f'color: {TEXT_SECONDARY};')
            column.addWidget(self.subline)
            self.banners = QVBoxLayout()
            self.banners.setSpacing(6)
            column.addLayout(self.banners)
            self.tree = QTreeWidget()
            self.tree.setStyleSheet(_list_style())
            self.tree.setColumnCount(len(overview.COLUMNS))
            self.tree.setHeaderLabels(list(overview.COLUMNS))
            self.tree.setAlternatingRowColors(True)
            self.tree.setUniformRowHeights(True)
            self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
            header = self.tree.header()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            for index in range(1, len(overview.COLUMNS)):
                header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
            column.addWidget(self.tree, 1)
            return panel

        # ── state ─────────────────────────────────────────────

        def settings(self) -> core.Settings:
            values = {name: spin.value() for name, spin in self.spins.items()}
            return core.Settings(context=_CONTEXTS[self.context_combo.currentIndex()][1], **values)

        def picked(self) -> tuple[str, ...]:
            return tuple(key for key, check in self.checks.items() if check.isChecked())

        def _section_changed(self, key: str):
            on = self.checks[key].isChecked()
            for widget in self._fields.get(key, ()):
                widget.setEnabled(on)
            values = {name: spin.value() for name, spin in self.spins.items()}
            self.sections[key].set_summary(section_summary(key, values, on))

        def _stale(self, *_args):
            if self.plan is not None:
                self.plan = None
                self.delete_button.setEnabled(False)
                self.delete_button.setText(overview.delete_label(None))
                self.tree.setEnabled(False)
                self.subline.setText('Settings changed: List again before deleting.')
                self.status.setText('Settings changed: List again before deleting.')

        def _busy(self, busy: bool, message: str = ''):
            self.list_button.setEnabled(not busy)
            self.delete_button.setEnabled(not busy and bool(self.plan and self.plan.prunable))
            self.status.setText(message)

        def _set_banners(self, notices):
            """``(colour, text)`` banners above the tree, replacing the last."""
            while self.banners.count():
                widget = self.banners.takeAt(0).widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            for colour, text in notices:
                self.banners.addWidget(_banner(colour, text))

        def banner_texts(self) -> list[str]:
            texts = []
            for index in range(self.banners.count()):
                widget = self.banners.itemAt(index).widget()
                texts += [label.text() for label in widget.findChildren(QLabel)]
            return texts

        def _run(self, job, done):
            self._thread = QThread(self)
            self._worker = Worker(job)
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.progress.connect(self.status.setText)
            self._worker.finished.connect(done)
            self._worker.failed.connect(self._failed)
            self._worker.finished.connect(self._thread.quit)
            self._worker.failed.connect(self._thread.quit)
            self._thread.start()

        def _failed(self, message: str):
            self.plan = None
            self._busy(False, message)

        # ── listing ───────────────────────────────────────────

        def _list(self):
            project = Path(self.project_edit.text().strip())
            if not self.project_edit.text().strip() or not project.is_dir():
                self.status.setText('No such project folder.')
                return
            picked = self.picked()
            if not picked:
                self.status.setText('Tick at least one thing to look at.')
                return
            settings = self.settings()
            self.plan = None
            self.view = None
            self.tree.clear()
            self._set_banners([])
            self._busy(True, 'Reading the project …')
            self._run(
                lambda progress: core.make_plan(project_roots(project), settings, picked, progress=progress),
                self._listed,
            )

        def show_plan(self, plan):
            """Show a listing: the total, its warnings, the tree."""
            self.plan = plan
            self.view = view = overview.overview(plan)
            self.headline.setText(overview.headline(view))
            self.headline.setStyleSheet(
                f'font-size: {FONT_BODY + 9}px; font-weight: 600; '
                f'color: {ACCENT if view.to_delete else TEXT_PRIMARY};'
            )
            self.subline.setText(overview.subline(view))
            self._set_banners([(STALE_COLOUR, f'{title}: {text}') for title, text in overview.warnings(view)])
            self.tree.setEnabled(True)
            fill_tree(self.tree, overview.rows(view))
            self.delete_button.setText(overview.delete_label(view))
            self.delete_button.setEnabled(bool(plan.prunable) and self.list_button.isEnabled())

        def _listed(self, plan):
            self.show_plan(plan)
            self._busy(False, 'Nothing to delete.' if not plan.prunable
                       else 'Listed. Nothing is deleted until you confirm.')

        # ── deleting ──────────────────────────────────────────

        def _delete(self):
            plan = self.plan
            if plan is None or not plan.prunable:
                return
            log_path = core.log_path_for(plan.roots)
            self.card = ConfirmCard(plan.roots.project_path.name, self.view, log_path)
            self.card.back.connect(self._close_card)
            self.card.confirmed.connect(lambda: self._confirmed(plan, log_path))
            self.overlay.show_content(self.card, max_width=620)

        def _close_card(self):
            self.overlay.close_content()
            self.card = None

        def _confirmed(self, plan, log_path):
            self._close_card()
            if plan is not self.plan:
                return
            self._busy(True, 'Deleting …')
            self._run(lambda _progress: (core.apply(plan, log_path), plan, log_path), self._deleted)

        def _deleted(self, result):
            results, plan, log_path = result
            self.plan = None
            self.view = None
            failed = sum(len(errors) for _removed, _bytes, errors in results.values())
            removed = sum(count for count, _bytes, _errors in results.values())
            freed = sum(size for _count, size, _errors in results.values())
            self.headline.setText(f'{core.human(freed)} freed')
            self.headline.setStyleSheet(
                f'font-size: {FONT_BODY + 9}px; font-weight: 600; color: {CURRENT_COLOUR};'
            )
            self.subline.setText(f'{overview.plural(removed, "item")} deleted. Logged to {log_path}.')
            self._set_banners(
                [(ERROR_COLOUR, f'{overview.plural(failed, "item")} could not be deleted, and were left.')]
                if failed else []
            )
            fill_tree(self.tree, overview.deleted_rows(plan, results))
            self.delete_button.setText(overview.delete_label(None))
            self._busy(False, 'Done.' if not failed else f'Done, {failed} item(s) could not be deleted.')

    return Window()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog='python -m tumblepipe.cleanup.app').parse_args(argv)
    _add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = build_window()
    window.show()
    return app.exec()


if __name__ == '__main__':
    from tumblepipe.util.logging import setup_logging
    setup_logging()
    sys.exit(main())
