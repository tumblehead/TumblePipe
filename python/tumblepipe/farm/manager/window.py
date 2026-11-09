"""The Farm Manager window: local and farm jobs, their tasks, progress, logs and errors.

Lists this machine's local farm (``farm.local``) and the Deadline farm side
by side, a batch per row with its jobs under it. Selecting a job shows its
details (what it renders with, its frames and scheduling, its times, its
outputs) and its tasks, each with its render time; selecting a task shows
its log — the local task's log file, followed while it renders, or the
task's Deadline reports. While this machine renders, the footer and the
details show its CPU, memory and GPU (``machine``).

Threads: the local queue is small files on this machine and is read on the
GUI thread once a second. Everything Deadline (``deadline_jobs``: a
``deadlinecommand`` per call, a second or more each), a job's context.json
(on the project share) and the machine's load (``nvidia-smi``, a process
walk) run on worker threads; their results are picked up by a timer on the
GUI thread, so no Qt object is ever touched off it (*Qt and threads* in
docs/development.md).
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BG_LIGHT, BORDER, DANGER, FONT_BODY, FONT_FAMILY,
    TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY, WARNING,
)

from tumblepipe.farm.local import queue, runner
from tumblepipe.farm.manager import cleanup, machine, rows
from tumblepipe.util import desktop

log = logging.getLogger(__name__)

LOCAL_INTERVAL_MS = 1000
FARM_INTERVAL_MS = 8000
FUTURES_INTERVAL_MS = 150
# The machine's load is sampled only while the local runner works.
MACHINE_INTERVAL_MS = 2000

# Finished work recedes so failed and live work stand out: a list is mostly
# completed jobs, and a wall of bright green bars hid the one red row.
# Failed is red, as Houdini's errors are; the Errors count stays amber,
# since a job that completed after a retried task did not fail.
STATUS_COLOURS = {
    queue.COMPLETED: '#4f8f7a',
    queue.FAILED: DANGER,
    queue.RENDERING: '#7fb2f0',
    queue.QUEUED: TEXT_SECONDARY,
    queue.PENDING: '#b8a860',
    queue.SUSPENDED: '#b48ad8',
    queue.CANCELLED: TEXT_DIM,
    rows.UNKNOWN: TEXT_DIM,
}
WHERE = {rows.LOCAL: 'This machine', rows.FARM: 'Farm'}
# The headers above the task list and the log share a height, so the two
# panes start on the same line whether or not a header holds buttons.
PANE_HEADER_HEIGHT = 30

# Job tree columns.
NAME, WHERE_COL, STATUS, PROGRESS, TASKS, ERRORS, USER, SUBMITTED = range(8)
JOB_HEADERS = ['Job', 'Where', 'Status', 'Progress', 'Tasks', 'Errors', 'User', 'Submitted']
# Task tree columns.
(T_TASK, T_FRAMES, T_STATUS, T_PROGRESS, T_WORKER, T_STARTED, T_FINISHED, T_TIME,
 T_ERROR) = range(9)
TASK_HEADERS = ['Task', 'Frames', 'Status', 'Progress', 'Worker', 'Started', 'Finished',
                'Render time', 'Error']
# Details columns.
D_KEY, D_VALUE = range(2)

KEY_ROLE = Qt.UserRole
PROGRESS_ROLE = Qt.UserRole + 1
FINISHED_ROLE = Qt.UserRole + 2
PATH_ROLE = Qt.UserRole + 3

# Labels of the actions, by command; which are offered depends on the source.
ACTION_LABELS = {
    'suspend': 'Suspend', 'resume': 'Resume', 'retry': 'Retry failed',
    'requeue': 'Requeue', 'cancel': 'Cancel', 'remove': 'Remove', 'delete': 'Delete',
}

# The Clean up menu (``cleanup``): what each item is called, what it says
# under the pointer, and the word its confirmation uses.
CLEANUP_ITEMS = (
    (cleanup.COMPLETED, 'Remove completed',
     'The listed batches whose every job completed'),
    (cleanup.FAILED, 'Remove failed',
     'The listed batches that ended with a failed job (on this machine, a cancelled one too)'),
    (cleanup.FINISHED, 'Remove completed and failed',
     'The listed batches whose every job has ended'),
    (cleanup.SUPERSEDED, 'Remove superseded',
     'Older finished runs of the same work on the same entity: the newest of each stays'),
)
CLEANUP_WORDS = {
    cleanup.COMPLETED: 'completed', cleanup.FAILED: 'failed',
    cleanup.FINISHED: 'finished', cleanup.SUPERSEDED: 'superseded',
}
# Batch names a confirmation lists per side before "… and N more".
_CONFIRM_NAMES = 12

# A splitter handle's line: the grey of the header rules and a hovered
# scrollbar, lighter than the panels' borders so it stands out from them.
_HANDLE = '#4a4a4a'


def _handle_line(axis: str, colour: str) -> str:
    """A splitter handle's fill: a 1px `colour` line across the middle of its
    7px, the background either side, so the handle shows where to grab
    (one the colour of the background could not be found). `axis` is the
    one the handle is thin along: 'y' for a vertical splitter's."""
    end = 'x2:0, y2:1' if axis == 'y' else 'x2:1, y2:0'
    return (
        f"qlineargradient(x1:0, y1:0, {end}, stop:0 {BG_DARKEST}, stop:0.42 {BG_DARKEST},"
        f" stop:0.43 {colour}, stop:0.57 {colour}, stop:0.58 {BG_DARKEST}, stop:1 {BG_DARKEST})"
    )


_STYLE = f"""
QWidget {{
    background-color: {BG_DARKEST};
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
}}
QTreeWidget, QPlainTextEdit {{
    background-color: {BG_DARKEST};
    border: 1px solid {BORDER};
    border-radius: 6px;
    alternate-background-color: #1f1f1f;
    outline: none;
}}
QTreeWidget::item:selected {{ background-color: #2f3f55; }}
QHeaderView::section {{
    background-color: #262626;
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid #4a4a4a;
    border-right: 1px solid #3a3a3a;
    padding: 4px 8px;
}}
QPushButton {{
    background-color: {BG_DARK};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5a5a5a; border-color: #2e2e2e; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QLineEdit, QComboBox {{
    background-color: {BG_DARK};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{ background-color: {BG_DARK}; border: 1px solid {BORDER}; }}
QSplitter::handle:vertical {{ height: 7px; background: {_handle_line('y', _HANDLE)}; }}
QSplitter::handle:horizontal {{ width: 7px; background: {_handle_line('x', _HANDLE)}; }}
QSplitter::handle:vertical:hover, QSplitter::handle:vertical:pressed {{
    background: {_handle_line('y', ACCENT)};
}}
QSplitter::handle:horizontal:hover, QSplitter::handle:horizontal:pressed {{
    background: {_handle_line('x', ACCENT)};
}}
QPlainTextEdit {{ padding: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background-color: {BG_LIGHT}; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:hover {{ background-color: #4a4a4a; }}
QScrollBar::handle:vertical {{ min-height: 30px; }}
QScrollBar::handle:horizontal {{ min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QMenu {{
    background-color: {BG_DARK};
    border: 1px solid {BORDER};
    padding: 4px 0;
}}
QMenu::item {{ padding: 5px 22px 5px 14px; }}
QMenu::item:selected {{ background-color: #2f3f55; }}
QMenu::item:disabled {{ color: #5a5a5a; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
"""

# Cancel, Remove and Delete stop or throw away work: red under the pointer,
# as the Entity Editor's Retire is.
_DANGER_BUTTON_STYLE = f'QPushButton:hover {{ color: {DANGER}; border-color: {DANGER}; }}'
_MESSAGE_STYLE = f'color: {TEXT_PRIMARY};'
_ERROR_MESSAGE_STYLE = f'color: {DANGER};'
_PANE_LABEL_STYLE = f'color: {TEXT_SECONDARY};'
# What an empty pane asks for.
_NO_JOB = 'Select a job to see its tasks'
_NO_TASK = 'Select a task to see its log'
_NO_DETAILS = 'Select a job to see its details'
# Where the details pane puts the machine's load: after what renders the job.
_MACHINE_SECTION = 'This machine'


def _show_handles(splitter: QSplitter) -> None:
    """Let `splitter`'s handles light up under the mouse: _STYLE draws each
    as a 1px line with 3px to grab either side, highlighted on hover, which
    Qt reports to a handle only with WA_Hover set."""
    for index in range(1, splitter.count()):
        splitter.handle(index).setAttribute(Qt.WA_Hover, True)


def _when(iso: str) -> str:
    """``Today 14:03``, ``Oct 29 14:03``: see :func:`rows.when_text`."""
    return rows.when_text(iso)


def _is_finished(status: str) -> bool:
    return status in rows.FINISHED


def _jobs(count: int) -> str:
    return f"{count} job{'' if count == 1 else 's'}"


def _batches(count: int) -> str:
    return f"{count} batch{'' if count == 1 else 'es'}"


def _removal_lines(removals: list[cleanup.Removal]) -> list[str]:
    """A confirmation's list of batches: name and submit time, the first few."""
    lines = []
    for removal in removals[:_CONFIRM_NAMES]:
        when = _when(min(job.submitted for job in removal.jobs))
        lines.append(f'  {removal.title} · {when}' if when else f'  {removal.title}')
    if len(removals) > _CONFIRM_NAMES:
        lines.append(f'  … and {len(removals) - _CONFIRM_NAMES} more')
    return lines


class ProgressDelegate(QStyledItemDelegate):
    """A bar behind the progress text, coloured by the row's state.

    The cell's own background (alternate rows, the selection) is drawn
    first, so a selected row stays highlighted across this column too.
    Finished rows get a faint bar: only live and failed work is loud.
    """

    def paint(self, painter: QPainter, option, index) -> None:
        value = index.data(PROGRESS_ROLE)
        if value is None:
            super().paint(painter, option, index)
            return
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ''
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, background, painter, widget)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(6, 5, -6, -5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 14))
        painter.drawRoundedRect(rect, 3, 3)
        colour = index.data(Qt.ForegroundRole)
        fill = QColor(colour.color() if hasattr(colour, 'color') else ACCENT)
        finished = bool(index.data(FINISHED_ROLE))
        fill.setAlpha(70 if finished else 170)
        width = int(rect.width() * max(0.0, min(100.0, float(value))) / 100)
        if width > 0:
            painter.setBrush(fill)
            painter.drawRoundedRect(QRect(rect.x(), rect.y(), width, rect.height()), 3, 3)
        painter.setPen(QColor(TEXT_SECONDARY if finished else TEXT_PRIMARY))
        # The text gets the row's full height; the bar's would clip it.
        painter.drawText(option.rect, Qt.AlignCenter, index.data(Qt.DisplayRole) or '')
        painter.restore()


class ManagerWindow(QWidget):
    """The Farm Manager.

    Args:
        source: ``'local'`` or ``'farm'`` to open showing only that source;
            None shows both.
    """

    def __init__(self, parent: QWidget | None = None, source: str | None = None) -> None:
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('Farm Manager')
        self.setMinimumSize(980, 620)
        self.resize(1400, 860)
        self.setStyleSheet(_STYLE)

        self._root = queue.root()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix='farm-manager')
        self._futures: dict[Future, tuple] = {}
        self._local_rows: list[rows.JobRow] = []
        self._local_problems: list[str] = []
        self._farm_rows: list[rows.JobRow] = []
        self._farm_error: str | None = None
        self._farm_updated: str | None = None
        self._farm_loading = False
        self._farm_available = True
        self._job_items: dict[str, QTreeWidgetItem] = {}
        self._batch_items: dict[str, QTreeWidgetItem] = {}
        self._rows_by_id: dict[str, rows.JobRow] = {}
        self._current_job: rows.JobRow | None = None
        self._tasks: list[rows.TaskRow] = []
        self._task_items: dict[int, QTreeWidgetItem] = {}
        self._log_key: tuple | None = None
        self._log_offset = 0
        self._reports: list = []
        # Job context.json files by job id ({} when unreadable), and those being read.
        self._contexts: dict[str, dict] = {}
        self._contexts_loading: set[str] = set()
        self._detail_layout: list | None = None
        self._detail_items: list[tuple[QTreeWidgetItem, rows.Detail]] = []
        self._sampler = machine.Sampler()
        self._machine_stats: machine.MachineStats | None = None
        self._machine_loading = False
        self._runner_working = False

        self._user, self._project = _identity()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)
        root.addLayout(self._build_filters(source))

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        # Jobs on the left, the selected job's details beside them, as the
        # Deadline Monitor's job panel sits beside its job list.
        top = QSplitter(Qt.Horizontal)
        top.setChildrenCollapsible(False)
        jobs_panel = QWidget()
        top_layout = QVBoxLayout(jobs_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)
        self._jobs = self._build_jobs_tree()
        top_layout.addWidget(self._jobs, 1)
        top_layout.addLayout(self._build_actions())
        top.addWidget(jobs_panel)
        top.addWidget(self._build_details())
        top.setStretchFactor(0, 1)
        top.setSizes([1000, 380])
        splitter.addWidget(top)

        bottom = QSplitter(Qt.Horizontal)
        bottom.setChildrenCollapsible(False)
        bottom.addWidget(self._build_tasks_tree())
        bottom.addWidget(self._build_log_panel())
        bottom.setSizes([700, 700])
        splitter.addWidget(bottom)
        splitter.setSizes([460, 380])
        # Each handle is drawn as a line that lights up under the mouse
        # (_STYLE): a handle the colour of the background could not be found.
        for each in (splitter, top, bottom):
            _show_handles(each)
        root.addWidget(splitter, 1)

        # The footer: where both farms stand on the left, refreshed every
        # second; on the right the last action's answer, which stays until
        # the next one (it used to share the left label and was overwritten
        # within a second).
        footer = QHBoxLayout()
        footer.setSpacing(16)
        self._status = QLabel()
        self._status.setStyleSheet(f'color: {TEXT_SECONDARY};')
        footer.addWidget(self._status, 1)
        self._message = QLabel()
        self._message.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        footer.addWidget(self._message)
        root.addLayout(footer)

        self._local_timer = QTimer(self, interval=LOCAL_INTERVAL_MS, timeout=self._refresh_local)
        self._farm_timer = QTimer(self, interval=FARM_INTERVAL_MS, timeout=self._refresh_farm)
        self._futures_timer = QTimer(self, interval=FUTURES_INTERVAL_MS, timeout=self._poll_futures)
        self._machine_timer = QTimer(self, interval=MACHINE_INTERVAL_MS, timeout=self._refresh_machine)
        self._local_timer.start()
        self._farm_timer.start()
        self._futures_timer.start()
        self._machine_timer.start()
        self._refresh_local()
        self._refresh_farm()
        self._update_actions()

    # ── building ──────────────────────────────────────────

    def _build_filters(self, source: str | None) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(QLabel('Show'))
        self._source = QComboBox()
        self._source.addItem('All jobs', None)
        self._source.addItem('This machine', rows.LOCAL)
        self._source.addItem('Render farm', rows.FARM)
        self._source.setCurrentIndex(max(0, self._source.findData(source)))
        self._source.currentIndexChanged.connect(self._on_filters_changed)
        bar.addWidget(self._source)

        self._mine = QCheckBox('Only mine')
        self._mine.setChecked(bool(self._user))
        self._mine.setEnabled(bool(self._user))
        self._mine.setToolTip(
            f'Only the jobs {self._user} submitted' if self._user
            else 'No pipeline user is set (TH_USER), so every user is shown'
        )
        self._mine.toggled.connect(self._on_filters_changed)
        bar.addWidget(self._mine)

        self._this_project = QCheckBox(f'Only {self._project}' if self._project else 'Only this project')
        self._this_project.setChecked(bool(self._project))
        self._this_project.setEnabled(bool(self._project))
        self._this_project.toggled.connect(self._on_filters_changed)
        bar.addWidget(self._this_project)

        self._hide_finished = QCheckBox('Hide finished')
        self._hide_finished.toggled.connect(self._on_filters_changed)
        bar.addWidget(self._hide_finished)

        self._search = QLineEdit()
        self._search.setPlaceholderText('Filter by name…')
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filters_changed)
        bar.addWidget(self._search, 1)

        refresh = QPushButton('Refresh')
        refresh.clicked.connect(self._refresh_all)
        bar.addWidget(refresh)
        bar.addWidget(self._build_cleanup())
        return bar

    def _build_cleanup(self) -> QPushButton:
        """The Clean up menu, beside the filters it acts through: each item
        removes finished batches of those the list shows (``cleanup``)."""
        button = QPushButton('Clean up ▾')
        button.setToolTip(
            'Remove finished batches of those listed: the filters above choose which. '
            'A batch goes only when every job of it has ended.'
        )
        menu = QMenu(button)
        menu.setToolTipsVisible(True)
        self._cleanup_actions = {}
        for kind, label, tip in CLEANUP_ITEMS:
            if kind == cleanup.SUPERSEDED:
                menu.addSeparator()
            action = menu.addAction(label, lambda k=kind: self._clean_up(k))
            action.setToolTip(tip)
            self._cleanup_actions[kind] = action
        menu.aboutToShow.connect(self._update_cleanup_menu)
        button.setMenu(menu)
        self._cleanup_button = button
        return button

    def _build_jobs_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setColumnCount(len(JOB_HEADERS))
        tree.setHeaderLabels(JOB_HEADERS)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tree.setItemDelegateForColumn(PROGRESS, ProgressDelegate(tree))
        header = tree.header()
        header.setSectionResizeMode(NAME, QHeaderView.Stretch)
        for column, width in ((WHERE_COL, 100), (STATUS, 90), (PROGRESS, 140), (TASKS, 70),
                              (ERRORS, 60), (USER, 110), (SUBMITTED, 100)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            tree.setColumnWidth(column, width)
        header.setStretchLastSection(False)
        tree.itemSelectionChanged.connect(self._on_job_selection)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._on_job_menu)
        return tree

    def _build_details(self) -> QTreeWidget:
        """The selected job's properties, in sections, as the Monitor's job panel lists them."""
        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels(['Job details', ''])
        tree.setRootIsDecorated(False)
        tree.setItemsExpandable(False)
        tree.setUniformRowHeights(True)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.setColumnWidth(D_KEY, 128)
        tree.header().setStretchLastSection(True)
        tree.setMinimumWidth(260)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._on_details_menu)
        tree.itemDoubleClicked.connect(self._on_detail_double_clicked)
        self._details = tree
        self._show_details_placeholder()
        return tree

    def _build_actions(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._action_buttons: dict[str, QPushButton] = {}
        # Pause and continue, run again, stop for good: a gap between the
        # groups, and the last group red under the pointer.
        groups = (('suspend', 'resume'), ('retry', 'requeue'), ('cancel', 'remove', 'delete'))
        for position, group in enumerate(groups):
            if position:
                bar.addSpacing(18)
            for command in group:
                button = QPushButton(ACTION_LABELS[command])
                button.clicked.connect(lambda _checked=False, c=command: self._run_action(c))
                if group is groups[-1]:
                    button.setStyleSheet(_DANGER_BUTTON_STYLE)
                bar.addWidget(button)
                self._action_buttons[command] = button
        self._action_buttons['requeue'].setToolTip('Farm: render every task of the job again')
        self._action_buttons['remove'].setToolTip(
            'This machine: take the batch off the list (its outputs stay)'
        )
        self._action_buttons['delete'].setToolTip('Farm: delete the job from Deadline')
        bar.addStretch(1)
        self._open_job_dir = QPushButton('Open job folder')
        self._open_job_dir.clicked.connect(self._on_open_job_dir)
        bar.addWidget(self._open_job_dir)
        self._open_output = QPushButton('Open output folder')
        self._open_output.clicked.connect(self._on_open_output)
        bar.addWidget(self._open_output)
        return bar

    def _build_tasks_tree(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        head = QWidget()
        head.setFixedHeight(PANE_HEADER_HEIGHT)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        self._tasks_label = QLabel(_NO_JOB)
        self._tasks_label.setStyleSheet(_PANE_LABEL_STYLE)
        head_layout.addWidget(self._tasks_label)
        layout.addWidget(head)
        tree = QTreeWidget()
        tree.setColumnCount(len(TASK_HEADERS))
        tree.setHeaderLabels(TASK_HEADERS)
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setItemDelegateForColumn(T_PROGRESS, ProgressDelegate(tree))
        header = tree.header()
        # Fixed columns narrow enough that the Error column, the one worth
        # reading, keeps the spare width instead of scrolling off the side.
        for column, width in ((T_TASK, 44), (T_FRAMES, 84), (T_STATUS, 80), (T_PROGRESS, 90),
                              (T_WORKER, 90), (T_STARTED, 96), (T_FINISHED, 96), (T_TIME, 90)):
            tree.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        tree.itemSelectionChanged.connect(self._on_task_selection)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._on_task_menu)
        layout.addWidget(tree, 1)
        self._task_tree = tree
        return panel

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        head_widget = QWidget()
        head_widget.setFixedHeight(PANE_HEADER_HEIGHT)
        head = QHBoxLayout(head_widget)
        head.setContentsMargins(0, 0, 0, 0)
        self._log_label = QLabel('Log')
        self._log_label.setStyleSheet(_PANE_LABEL_STYLE)
        head.addWidget(self._log_label, 1)
        self._report_combo = QComboBox()
        self._report_combo.setVisible(False)
        self._report_combo.currentIndexChanged.connect(self._on_report_chosen)
        head.addWidget(self._report_combo, 2)
        self._follow = QCheckBox('Follow')
        self._follow.setChecked(True)
        self._follow.setToolTip('Keep the end of a rendering task\'s log in view')
        head.addWidget(self._follow)
        self._open_log = QPushButton('Open log file')
        self._open_log.clicked.connect(self._on_open_log)
        head.addWidget(self._open_log)
        layout.addWidget(head_widget)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText(_NO_TASK)
        self._log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._log.setMaximumBlockCount(50000)
        mono = QFont('Consolas')
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self._log.setFont(mono)
        layout.addWidget(self._log, 1)
        return panel

    # ── refreshing ────────────────────────────────────────

    def _refresh_all(self) -> None:
        self._refresh_local()
        self._refresh_farm()

    def _refresh_local(self) -> None:
        try:
            self._local_rows, self._local_problems = rows.local_rows(self._root)
        except Exception as error:
            self._local_rows, self._local_problems = [], [str(error)]
        self._sync_jobs()
        job = self._current_job
        if job is not None and job.source == rows.LOCAL:
            self._load_local_tasks(job)
        elif job is not None and self._tasks:
            # A farm job's tasks are read every few seconds; the render
            # time of the rendering ones still ticks every second.
            self._show_tasks(self._tasks)
        self._follow_local_log()
        self._update_status()
        self._update_details()

    def _refresh_machine(self) -> None:
        """Sample the machine's load on a worker thread, while the local runner works."""
        if self._machine_loading:
            return
        if not self._runner_working:
            if self._machine_stats is not None:
                self._machine_stats = None
                self._update_status()
                self._update_details()
            return
        job, task = self._current_job, None
        if job is not None and job.source == rows.LOCAL:
            task = next((t for t in self._tasks if t.status == queue.RENDERING and t.pid), None)
        self._machine_loading = True
        self._submit(('machine',), self._sampler.sample, task.pid if task else 0)

    def _refresh_farm(self) -> None:
        if self._farm_loading or not self._farm_available:
            return
        if self._source.currentData() == rows.LOCAL:
            return
        self._farm_loading = True
        self._submit(('farm_jobs',), _fetch_farm_jobs)
        job = self._current_job
        if job is not None and job.source == rows.FARM:
            self._submit(('farm_tasks', job.id), _fetch_farm_tasks, job.id)

    def _submit(self, key: tuple, func, *args) -> None:
        self._futures[self._executor.submit(func, *args)] = key

    def _poll_futures(self) -> None:
        for future in [f for f in self._futures if f.done()]:
            key = self._futures.pop(future)
            try:
                result = future.result()
            except Exception as error:
                self._on_future_failed(key, error)
            else:
                self._on_future_done(key, result)

    def _on_future_done(self, key: tuple, result) -> None:
        kind = key[0]
        if kind == 'farm_jobs':
            self._farm_loading = False
            self._farm_rows, self._farm_error = result, None
            from datetime import datetime
            self._farm_updated = datetime.now().strftime('%H:%M:%S')
            self._sync_jobs()
            self._update_status()
        elif kind == 'farm_tasks':
            job = self._current_job
            if job is not None and job.id == key[1]:
                self._show_tasks(result)
        elif kind == 'farm_reports':
            if self._log_key == ('farm', key[1], key[2]):
                self._show_reports(result)
        elif kind == 'farm_report_text':
            if self._report_combo.currentData() == key[1]:
                self._set_log_text(result, follow=False)
        elif kind == 'farm_control':
            self._say(f'{ACTION_LABELS[key[1]]} sent to the farm for {_jobs(key[2])}')
            self._refresh_farm()
        elif kind == 'farm_cleanup':
            cleaned, skipped = result
            text = f'Clean-up: {_batches(cleaned.batches)} ({_jobs(cleaned.jobs)}) deleted from the farm'
            if skipped:
                text += f'; {skipped} left, no longer finished'
            if cleaned.error:
                self._say(f'{text}; the rest failed', error=True)
                QMessageBox.warning(self, 'Farm Manager', f'Clean-up stopped short:\n\n{cleaned.error}')
            else:
                self._say(text)
            self._refresh_farm()
        elif kind == 'job_context':
            self._contexts_loading.discard(key[1])
            self._contexts[key[1]] = result
            job = self._current_job
            if job is not None and job.id == key[1]:
                self._update_details()
        elif kind == 'machine':
            self._machine_loading = False
            self._machine_stats = result if self._runner_working else None
            self._update_status()
            self._update_details()

    def _on_future_failed(self, key: tuple, error: Exception) -> None:
        kind = key[0]
        if kind == 'job_context':
            self._contexts_loading.discard(key[1])
            self._contexts[key[1]] = {}
            return
        if kind == 'machine':
            self._machine_loading = False
            self._machine_stats = None
            log.debug('Farm Manager: sampling this machine failed', exc_info=error)
            return
        from tumblepipe.apps.deadline import DeadlineNotInstalled
        if kind == 'farm_jobs':
            self._farm_loading = False
            self._farm_error = str(error)
            if isinstance(error, DeadlineNotInstalled):
                # No Deadline Client here: stop asking, the local farm still works.
                self._farm_available = False
                self._farm_rows = []
                self._sync_jobs()
            self._update_status()
        elif kind == 'farm_control':
            self._say(f'{ACTION_LABELS[key[1]]} failed on the farm', error=True)
            QMessageBox.warning(self, 'Farm Manager', f'{ACTION_LABELS[key[1]]} failed:\n\n{error}')
        elif kind == 'farm_cleanup':
            self._say('Clean-up failed on the farm', error=True)
            QMessageBox.warning(self, 'Farm Manager', f'Clean-up failed:\n\n{error}')
        elif kind in ('farm_tasks', 'farm_reports', 'farm_report_text'):
            self._set_log_text(f'Could not read from Deadline: {error}', follow=False)
        log.warning('Farm Manager: %s failed', kind, exc_info=error)

    def _update_status(self) -> None:
        parts = []
        working = runner.is_running(self._root)
        if working and not self._runner_working:
            self._runner_working = True
            self._refresh_machine()
        self._runner_working = working
        local_count = len(self._local_rows)
        here = f"This machine: {'rendering' if working else 'idle'} · {local_count} job{'' if local_count == 1 else 's'}"
        load = machine.summary_text(self._machine_stats) if working else ''
        parts.append(f'{here} · {load}' if load else here)
        if self._local_problems:
            parts.append(f'{len(self._local_problems)} unreadable batch(es): {self._local_problems[0]}')
        if not self._farm_available:
            parts.append('Farm: no Deadline Client on this machine')
        elif self._farm_error:
            parts.append(f'Farm: {self._farm_error}')
        elif self._farm_updated:
            parts.append(f'Farm: updated {self._farm_updated} · {len(self._farm_rows)} jobs on the farm')
        else:
            parts.append('Farm: loading…')
        self._status.setText('   |   '.join(parts))

    # ── the job tree ──────────────────────────────────────

    def _visible_rows(self, hide_finished: bool | None = None) -> list[rows.JobRow]:
        """The rows the filters let through; ``hide_finished`` overrides its checkbox."""
        source = self._source.currentData()
        if hide_finished is None:
            hide_finished = self._hide_finished.isChecked()
        filters = dict(
            source=source,
            user=self._user if self._mine.isChecked() else None,
            project=self._project if self._this_project.isChecked() else None,
            hide_finished=hide_finished,
            text=self._search.text().strip(),
        )
        candidates = self._local_rows + self._farm_rows
        return [row for row in candidates if rows.keep(row, **filters)]

    def _on_filters_changed(self, *_args) -> None:
        self._sync_jobs()
        if self._source.currentData() != rows.LOCAL and not self._farm_rows:
            self._refresh_farm()

    def _sync_jobs(self) -> None:
        """Update the tree in place: selection, expansion and scroll survive a refresh."""
        visible = self._visible_rows()
        self._rows_by_id = {row.id: row for row in self._local_rows + self._farm_rows}
        batches: dict[str, list[rows.JobRow]] = {}
        for row in visible:
            batches.setdefault(row.batch_key, []).append(row)
        # Newest batch first.
        order = sorted(batches, key=lambda k: max(r.submitted for r in batches[k]), reverse=True)

        tree = self._jobs
        tree.setUpdatesEnabled(False)
        try:
            for key in [k for k in self._batch_items if k not in batches]:
                item = self._batch_items.pop(key)
                for i in range(item.childCount()):
                    self._job_items.pop(item.child(i).data(NAME, KEY_ROLE), None)
                tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
            for position, key in enumerate(order):
                jobs = batches[key]
                item = self._batch_items.get(key)
                if item is None:
                    item = QTreeWidgetItem()
                    item.setData(NAME, KEY_ROLE, key)
                    self._batch_items[key] = item
                    tree.insertTopLevelItem(position, item)
                    item.setExpanded(len(jobs) <= 12)
                elif tree.indexOfTopLevelItem(item) != position:
                    expanded = item.isExpanded()
                    selected = item.isSelected()
                    tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
                    tree.insertTopLevelItem(position, item)
                    item.setExpanded(expanded)
                    item.setSelected(selected)
                batch_submitted = self._fill_batch_item(item, jobs)
                wanted = {row.id for row in jobs}
                for i in reversed(range(item.childCount())):
                    child = item.child(i)
                    if child.data(NAME, KEY_ROLE) not in wanted:
                        self._job_items.pop(child.data(NAME, KEY_ROLE), None)
                        item.removeChild(child)
                for row in jobs:
                    child = self._job_items.get(row.id)
                    if child is None:
                        child = QTreeWidgetItem(item)
                        child.setData(NAME, KEY_ROLE, row.id)
                        self._job_items[row.id] = child
                    self._fill_job_item(child, row, batch_submitted)
        finally:
            tree.setUpdatesEnabled(True)
        job = self._current_job
        if job is not None and job.id in self._rows_by_id:
            self._current_job = self._rows_by_id[job.id]
        self._update_actions()

    def _fill_job_item(self, item: QTreeWidgetItem, row: rows.JobRow, batch_submitted: str) -> None:
        """A job under its batch row: where and who are the batch's, so its
        cells stay empty; Submitted shows only when it differs."""
        self._fill_state(item, row.status, row.progress, f'{row.done}/{row.total}', row.errors)
        item.setText(NAME, row.name)
        item.setToolTip(NAME, f'{row.name}\n{row.id}')
        submitted = _when(row.submitted)
        item.setText(SUBMITTED, '' if submitted == batch_submitted else submitted)

    def _fill_batch_item(self, item: QTreeWidgetItem, jobs: list[rows.JobRow]) -> str:
        """The batch row; returns its Submitted text for its jobs to compare with."""
        status = rows.batch_status([row.status for row in jobs])
        done = sum(row.done for row in jobs)
        total = sum(row.total for row in jobs)
        progress = sum(row.progress * row.total for row in jobs) / total if total else 0.0
        errors = sum(row.errors for row in jobs)
        self._fill_state(item, status, progress, f'{done}/{total}', errors)
        bold = item.font(NAME)
        bold.setBold(True)
        item.setFont(NAME, bold)
        item.setText(NAME, rows.batch_title(jobs[0].batch, jobs[0].user))
        item.setToolTip(NAME, jobs[0].batch)
        item.setText(WHERE_COL, WHERE[jobs[0].source])
        item.setText(USER, jobs[0].user)
        submitted = _when(min(row.submitted for row in jobs))
        item.setText(SUBMITTED, submitted)
        return submitted

    def _fill_state(self, item: QTreeWidgetItem, status: str, progress: float,
                    tasks: str, errors: int) -> None:
        """The cells a job and a batch row share. A finished row's text is
        dimmed, so the eye lands on the failed and live ones."""
        finished = _is_finished(status)
        text = QColor(TEXT_SECONDARY if finished else TEXT_PRIMARY)
        for column in (NAME, WHERE_COL, TASKS, USER, SUBMITTED):
            item.setForeground(column, text)
        colour = QColor(STATUS_COLOURS.get(status, TEXT_SECONDARY))
        item.setText(STATUS, status)
        item.setForeground(STATUS, colour)
        item.setText(PROGRESS, f'{progress:.0f}%')
        item.setData(PROGRESS, PROGRESS_ROLE, progress)
        item.setData(PROGRESS, FINISHED_ROLE, finished)
        item.setForeground(PROGRESS, colour)
        item.setText(TASKS, tasks)
        item.setText(ERRORS, str(errors) if errors else '')
        item.setForeground(ERRORS, QColor(WARNING))

    def _selected_rows(self) -> list[rows.JobRow]:
        """The selected jobs; a selected batch stands for all of its shown jobs."""
        found: dict[str, rows.JobRow] = {}
        for item in self._jobs.selectedItems():
            key = item.data(NAME, KEY_ROLE)
            if key in self._batch_items and item.parent() is None:
                for i in range(item.childCount()):
                    row = self._rows_by_id.get(item.child(i).data(NAME, KEY_ROLE))
                    if row is not None:
                        found[row.id] = row
            elif key in self._rows_by_id:
                found[key] = self._rows_by_id[key]
        return list(found.values())

    def _on_job_selection(self) -> None:
        items = self._jobs.selectedItems()
        job = None
        if items:
            item = items[0]
            key = item.data(NAME, KEY_ROLE)
            if item.parent() is None and item.childCount():
                key = item.child(0).data(NAME, KEY_ROLE)
            job = self._rows_by_id.get(key)
        changed = (job is None) != (self._current_job is None) or (
            job is not None and self._current_job is not None and job.id != self._current_job.id
        )
        self._current_job = job
        if changed:
            self._task_tree.clear()
            self._task_items.clear()
            self._tasks = []
            self._clear_log()
            if job is None:
                self._tasks_label.setText(_NO_JOB)
            else:
                self._tasks_label.setText(f'Tasks of {job.name}')
                if job.source == rows.LOCAL:
                    self._load_local_tasks(job)
                else:
                    self._submit(('farm_tasks', job.id), _fetch_farm_tasks, job.id)
                self._load_context(job)
            self._refresh_machine()
        self._update_details()
        self._update_actions()

    def _load_context(self, job: rows.JobRow) -> None:
        """Read the job's context.json (what it renders, and how) off the GUI thread, once."""
        if job.id in self._contexts or job.id in self._contexts_loading:
            return
        if not job.arguments:
            self._contexts[job.id] = {}
            return
        self._contexts_loading.add(job.id)
        self._submit(('job_context', job.id), rows.read_job_context, job.data_dir, job.arguments)

    # ── tasks ─────────────────────────────────────────────

    def _load_local_tasks(self, job: rows.JobRow) -> None:
        try:
            tasks = rows.local_task_rows(Path(job.folder), job.job_index)
        except Exception as error:
            self._tasks_label.setText(f'Tasks of {job.name}: {error}')
            return
        self._show_tasks(tasks)

    def _show_tasks(self, tasks: list[rows.TaskRow]) -> None:
        from datetime import datetime
        now = datetime.now()
        self._tasks = tasks
        tree = self._task_tree
        wanted = {task.index for task in tasks}
        for index in [i for i in self._task_items if i not in wanted]:
            item = self._task_items.pop(index)
            tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
        for task in tasks:
            item = self._task_items.get(task.index)
            if item is None:
                item = QTreeWidgetItem(tree)
                item.setData(T_TASK, KEY_ROLE, task.index)
                self._task_items[task.index] = item
            colour = QColor(STATUS_COLOURS.get(task.status, TEXT_SECONDARY))
            item.setText(T_TASK, str(task.index))
            item.setText(T_FRAMES, task.frames)
            item.setText(T_STATUS, task.status)
            item.setForeground(T_STATUS, colour)
            item.setText(T_PROGRESS, f'{task.progress}%')
            item.setData(T_PROGRESS, PROGRESS_ROLE, task.progress)
            item.setData(T_PROGRESS, FINISHED_ROLE, _is_finished(task.status))
            item.setForeground(T_PROGRESS, colour)
            text = QColor(TEXT_SECONDARY if _is_finished(task.status) else TEXT_PRIMARY)
            for column in (T_TASK, T_FRAMES, T_WORKER, T_STARTED, T_FINISHED):
                item.setForeground(column, text)
            item.setText(T_WORKER, task.worker)
            item.setText(T_STARTED, _when(task.started))
            item.setText(T_FINISHED, _when(task.finished))
            # The render time: how long it took, or, while it renders, how
            # long it has been at it (in the rendering blue).
            seconds = rows.task_seconds(task, now)
            took = rows.duration_text(seconds)
            rendering = task.status == queue.RENDERING
            item.setText(T_TIME, took)
            item.setForeground(T_TIME, QColor(STATUS_COLOURS[queue.RENDERING]) if rendering else text)
            item.setToolTip(T_TIME, (f'Rendering for {took}' if rendering else f'Rendered in {took}') if took else '')
            error = task.error or (f'{task.errors} error report(s)' if task.errors else '')
            item.setText(T_ERROR, error.splitlines()[0] if error else '')
            item.setToolTip(T_ERROR, error)
            # Red for the task that failed, amber for errors a task got past.
            item.setForeground(T_ERROR, QColor(DANGER if task.status == queue.FAILED else WARNING))
        if not tree.selectedItems() and tasks:
            # Open on the task worth reading: the one rendering, else the
            # last that failed, else the last that ran.
            pick = next((t for t in tasks if t.status == queue.RENDERING), None)
            pick = pick or next((t for t in reversed(tasks) if t.status == queue.FAILED), None)
            pick = pick or next((t for t in reversed(tasks) if t.started), None)
            if pick is not None:
                self._task_items[pick.index].setSelected(True)
        self._update_details()

    def _selected_task(self) -> rows.TaskRow | None:
        items = self._task_tree.selectedItems()
        if not items:
            return None
        index = items[0].data(T_TASK, KEY_ROLE)
        return next((t for t in self._tasks if t.index == index), None)

    def _on_task_selection(self) -> None:
        self._show_task_log()
        self._update_actions()

    def _show_task_log(self) -> None:
        job, task = self._current_job, self._selected_task()
        if job is None or task is None:
            self._clear_log()
            return
        if job.source == rows.LOCAL:
            key = ('local', task.log_path)
            if key != self._log_key:
                self._clear_log()
                self._log_key = key
                self._log_label.setText(f'Log of task {task.index} ({task.frames})')
                self._follow_local_log()
        else:
            key = ('farm', job.id, task.frames)
            if key != self._log_key:
                self._clear_log()
                self._log_key = key
                self._log_label.setText(f'Reports of task {task.index} ({task.frames})')
                self._set_log_text('Reading the task\'s reports from Deadline…', follow=False)
                self._submit(('farm_reports', job.id, task.frames), _fetch_reports, job.id, task.frames)

    # ── the log ───────────────────────────────────────────

    def _clear_log(self) -> None:
        self._log_key = None
        self._log_offset = 0
        self._log.clear()
        self._reports = []
        self._report_combo.blockSignals(True)
        self._report_combo.clear()
        self._report_combo.blockSignals(False)
        self._report_combo.setVisible(False)
        self._log_label.setText('Log')

    def _set_log_text(self, text: str, follow: bool) -> None:
        self._log.setPlainText(text)
        if follow:
            self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

    def _follow_local_log(self) -> None:
        """Append what the local task's log gained since the last read."""
        if self._log_key is None or self._log_key[0] != 'local':
            return
        path = Path(self._log_key[1])
        try:
            with open(path, 'rb') as handle:
                handle.seek(self._log_offset)
                data = handle.read()
        except FileNotFoundError:
            if self._log_offset == 0 and not self._log.toPlainText():
                self._log.setPlainText('(this task has not run yet)')
            return
        if not data:
            return
        if self._log_offset == 0:
            self._log.clear()
        # Only whole lines: the rest is read with the next tick.
        cut = data.rfind(b'\n') + 1
        if cut == 0:
            return
        self._log_offset += cut
        scrollbar = self._log.verticalScrollBar()
        at_end = scrollbar.value() >= scrollbar.maximum() - 4
        self._log.appendPlainText(data[:cut].decode('utf-8', errors='replace').rstrip('\n'))
        if self._follow.isChecked() and at_end:
            scrollbar.setValue(scrollbar.maximum())

    def _show_reports(self, reports: list) -> None:
        self._reports = reports
        combo = self._report_combo
        combo.blockSignals(True)
        combo.clear()
        for report in reports:
            label = f"{'Error' if report.kind == 'error' else 'Log'} · {_when(report.created)}"
            if report.worker:
                label += f' · {report.worker}'
            if report.summary:
                label += f' · {report.summary[:80]}'
            combo.addItem(label, report.path)
        combo.blockSignals(False)
        combo.setVisible(bool(reports))
        if not reports:
            self._set_log_text(
                'Deadline has no reports for this task yet (a report is written '
                'when a task finishes or fails).', follow=False,
            )
            return
        combo.setCurrentIndex(0)
        self._on_report_chosen(0)

    def _on_report_chosen(self, index: int) -> None:
        path = self._report_combo.itemData(index)
        if not path:
            return
        self._set_log_text('Reading the report…', follow=False)
        self._submit(('farm_report_text', path), _read_report, path)

    # ── details ───────────────────────────────────────────

    def _show_details_placeholder(self) -> None:
        self._details.clear()
        self._detail_items = []
        self._detail_layout = None
        item = QTreeWidgetItem(self._details, [_NO_DETAILS])
        item.setFirstColumnSpanned(True)
        item.setForeground(D_KEY, QColor(TEXT_DIM))
        item.setFlags(Qt.NoItemFlags)

    def _detail_sections(self) -> list[tuple[str, list[rows.Detail]]]:
        job = self._current_job
        names = {row.id: row.name for row in self._rows_by_id.values()}
        sections = rows.job_details(job, self._tasks, self._contexts.get(job.id), names)
        stats = self._machine_stats
        if job.source == rows.LOCAL and job.status == queue.RENDERING and stats is not None:
            load = [rows.Detail(key, value, tip) for key, value, tip in machine.stat_rows(stats)]
            if load:
                titles = [title for title, _details in sections]
                after = next((titles.index(t) + 1 for t in ('Renderer', 'Frames', 'Job') if t in titles), 0)
                sections.insert(after, (_MACHINE_SECTION, load))
        return sections

    def _update_details(self) -> None:
        """Fill the details pane; in place while its rows stay the same, so it does not flicker."""
        if self._current_job is None:
            if self._detail_layout is not None:
                self._show_details_placeholder()
            return
        sections = self._detail_sections()
        layout = [(title, [d.key for d in details]) for title, details in sections]
        tree = self._details
        if layout == self._detail_layout:
            flat = [d for _title, details in sections for d in details]
            for (item, _old), detail in zip(self._detail_items, flat):
                self._fill_detail(item, detail)
            self._detail_items = [(item, d) for (item, _old), d in zip(self._detail_items, flat)]
            return
        scroll = tree.verticalScrollBar().value()
        tree.setUpdatesEnabled(False)
        try:
            tree.clear()
            self._detail_items = []
            for title, details in sections:
                head = QTreeWidgetItem(tree, [title])
                head.setFirstColumnSpanned(True)
                font = head.font(D_KEY)
                font.setBold(True)
                head.setFont(D_KEY, font)
                head.setForeground(D_KEY, QColor(TEXT_SECONDARY))
                head.setFlags(Qt.ItemIsEnabled)
                for detail in details:
                    item = QTreeWidgetItem(tree)
                    item.setForeground(D_KEY, QColor(TEXT_SECONDARY))
                    self._fill_detail(item, detail)
                    self._detail_items.append((item, detail))
            self._detail_layout = layout
        finally:
            tree.setUpdatesEnabled(True)
        tree.verticalScrollBar().setValue(scroll)

    def _fill_detail(self, item: QTreeWidgetItem, detail: rows.Detail) -> None:
        item.setText(D_KEY, detail.key)
        item.setText(D_VALUE, detail.value)
        tip = detail.tip or detail.value
        item.setToolTip(D_VALUE, tip)
        item.setToolTip(D_KEY, tip)
        item.setData(D_VALUE, PATH_ROLE, detail.path)
        colour = TEXT_PRIMARY
        if detail.key == 'Status':
            colour = STATUS_COLOURS.get(detail.value, TEXT_PRIMARY)
        elif detail.path:
            colour = ACCENT
        item.setForeground(D_VALUE, QColor(colour))

    def _selected_detail(self) -> rows.Detail | None:
        items = self._details.selectedItems()
        if not items:
            return None
        return next((d for item, d in self._detail_items if item is items[0]), None)

    def _on_detail_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(D_VALUE, PATH_ROLE)
        if path:
            _open_folder_of(path)

    def _on_details_menu(self, position) -> None:
        detail = self._selected_detail()
        item = self._details.itemAt(position)
        if item is not None:
            detail = next((d for i, d in self._detail_items if i is item), detail)
        menu = QMenu(self)
        if detail is not None and detail.path:
            menu.addAction('Open folder', lambda: _open_folder_of(detail.path))
        copy = menu.addAction('Copy value', lambda: _copy(detail.tip if detail.path else detail.value))
        copy.setEnabled(detail is not None)
        menu.addAction('Copy all details', self._copy_details).setEnabled(bool(self._detail_items))
        menu.exec(self._details.viewport().mapToGlobal(position))

    def _copy_details(self) -> None:
        if self._current_job is None:
            return
        lines = []
        for title, details in self._detail_sections():
            lines.append(f'{title}:')
            lines.extend(f'  {d.key}: {d.path or d.value}' for d in details)
        _copy('\n'.join(lines))

    # ── context menus ─────────────────────────────────────

    def _add_job_actions(self, menu: QMenu, suffix: str = '') -> None:
        """The action buttons, enabled as they are."""
        groups = (('suspend', 'resume'), ('retry', 'requeue'), ('cancel', 'remove', 'delete'))
        for position, group in enumerate(groups):
            if position:
                menu.addSeparator()
            for command in group:
                button = self._action_buttons[command]
                action = menu.addAction(
                    ACTION_LABELS[command] + suffix,
                    lambda c=command: self._run_action(c),
                )
                action.setEnabled(button.isEnabled())
                action.setToolTip(button.toolTip())

    def _on_job_menu(self, position) -> None:
        selected = self._selected_rows()
        if not selected:
            return
        menu = QMenu(self)
        self._add_job_actions(menu)
        menu.addSeparator()
        menu.addAction('Open job folder', self._on_open_job_dir).setEnabled(self._open_job_dir.isEnabled())
        menu.addAction('Open output folder', self._on_open_output).setEnabled(self._open_output.isEnabled())
        menu.addSeparator()
        menu.addAction('Copy job ID' if len(selected) == 1 else 'Copy job IDs',
                       lambda: _copy('\n'.join(row.id for row in selected)))
        menu.addAction('Copy name' if len(selected) == 1 else 'Copy names',
                       lambda: _copy('\n'.join(row.name for row in selected)))
        menu.exec(self._jobs.viewport().mapToGlobal(position))

    def _on_task_menu(self, position) -> None:
        task = self._selected_task()
        if task is None or self._current_job is None:
            return
        menu = QMenu(self)
        menu.addAction('Open log folder', self._on_open_log).setEnabled(self._open_log.isEnabled())
        menu.addAction('Copy log', lambda: _copy(self._log.toPlainText())).setEnabled(
            bool(self._log.toPlainText())
        )
        menu.addAction('Copy frames', lambda: _copy(task.frames))
        if task.error:
            menu.addAction('Copy error', lambda: _copy(task.error))
        menu.addSeparator()
        # Suspend, retry and cancel work on the whole job, on Deadline's
        # job menu as here.
        self._add_job_actions(menu, ' job')
        menu.exec(self._task_tree.viewport().mapToGlobal(position))

    # ── actions ───────────────────────────────────────────

    def _update_actions(self) -> None:
        selected = self._selected_rows()
        sources = {row.source for row in selected}
        for command, button in self._action_buttons.items():
            button.setEnabled(any(command in rows.ACTIONS[s] for s in sources))
        self._open_job_dir.setEnabled(any(row.job_dir for row in selected))
        self._open_output.setEnabled(any(row.outputs for row in selected))
        task = self._selected_task()
        self._open_log.setEnabled(bool(task and task.log_path and Path(task.log_path).exists()))
        self._follow.setVisible(self._log_key is not None and self._log_key[0] == 'local')

    def _run_action(self, command: str) -> None:
        selected = self._selected_rows()
        local = [row for row in selected if row.source == rows.LOCAL and command in rows.ACTIONS[rows.LOCAL]]
        farm = [row for row in selected if row.source == rows.FARM and command in rows.ACTIONS[rows.FARM]]
        if command == 'delete' and farm:
            names = '\n'.join(f'  {row.name}' for row in farm[:12])
            more = f'\n  … and {len(farm) - 12} more' if len(farm) > 12 else ''
            if QMessageBox.question(
                self, 'Farm Manager',
                f'Delete {len(farm)} job(s) from the Deadline farm?\n\n{names}{more}',
            ) != QMessageBox.Yes:
                return
        if command == 'remove' and local:
            folders = sorted({row.folder for row in local})
            if QMessageBox.question(
                self, 'Farm Manager',
                f'Take {len(folders)} batch(es) off this machine\'s list? Anything '
                'still rendering in them is cancelled; their outputs stay.',
            ) != QMessageBox.Yes:
                return
        if local:
            try:
                self._local_action(command, local)
            except Exception as error:
                QMessageBox.warning(self, 'Farm Manager', f'{ACTION_LABELS[command]} failed:\n\n{error}')
        if farm:
            ids = [row.id for row in farm]
            self._submit(('farm_control', command, len(ids)), _farm_control, command, ids)

    def _local_action(self, command: str, selected: list[rows.JobRow]) -> None:
        if command == queue.REMOVE:
            for folder in sorted({row.folder for row in selected}):
                queue.request(Path(folder), queue.REMOVE)
        else:
            for row in selected:
                queue.request(Path(row.folder), command, row.job_index)
        # The runner applies requests; start it if it stopped (a resume or
        # retry is work for it again).
        runner.ensure_running(self._root)
        self._say(f'{ACTION_LABELS[command]} requested on this machine for {_jobs(len(selected))}')

    # ── clean-ups ─────────────────────────────────────────

    def _cleanup_candidates(self, kind: str) -> list[cleanup.Removal]:
        """The batches the clean-up ``kind`` removes now: of those the filters
        let through (source, Only mine, Only this project, the search),
        finished ones included while *Hide finished* hides them, each judged
        whole against every job read."""
        return cleanup.removable(
            self._local_rows + self._farm_rows, self._visible_rows(hide_finished=False), kind,
        )

    def _update_cleanup_menu(self) -> None:
        """Count each clean-up's batches as the menu opens; one with none is greyed."""
        for kind, label, _tip in CLEANUP_ITEMS:
            count = len(self._cleanup_candidates(kind))
            action = self._cleanup_actions[kind]
            action.setText(f'{label} ({_batches(count)})' if count else label)
            action.setEnabled(bool(count))

    def _clean_up(self, kind: str) -> None:
        """Remove the batches of a clean-up, once confirmed.

        What is removed is what was confirmed and still qualifies on a
        fresh read (``cleanup.recheck``): a batch retried while the
        question was open is left alone.
        """
        chosen = self._cleanup_candidates(kind)
        if not chosen:
            self._say(f'Nothing {CLEANUP_WORDS[kind]} to clean up')
            return
        if not self._confirm_cleanup(kind, chosen):
            return
        local = [removal for removal in chosen if removal.source == rows.LOCAL]
        farm = [removal for removal in chosen if removal.source == rows.FARM]
        if local:
            try:
                again = cleanup.recheck(rows.local_rows(self._root)[0], local, kind)
                count = cleanup.remove_local(again, self._root, start=runner.ensure_running)
            except Exception as error:
                self._say('Clean-up failed on this machine', error=True)
                QMessageBox.warning(self, 'Farm Manager', f'Clean-up failed:\n\n{error}')
            else:
                text = f"Clean-up: {_batches(count)} taken off this machine's list"
                if len(local) > count:
                    text += f'; {len(local) - count} left, no longer finished'
                self._say(text)
                self._refresh_local()
        if farm:
            self._submit(('farm_cleanup', kind), _farm_cleanup, farm, kind)

    def _confirm_cleanup(self, kind: str, chosen: list[cleanup.Removal]) -> bool:
        """Ask before a clean-up, naming its batches, the farm's and this machine's apart."""
        farm = [removal for removal in chosen if removal.source == rows.FARM]
        local = [removal for removal in chosen if removal.source == rows.LOCAL]
        count = len(chosen)
        jobs = sum(len(removal.jobs) for removal in chosen)
        lines = [
            f"Remove {count} {CLEANUP_WORDS[kind]} batch{'' if count == 1 else 'es'} "
            f'({_jobs(jobs)})?'
        ]
        if farm:
            lines += ['', 'Deleted from the Deadline farm:', *_removal_lines(farm)]
        if local:
            lines += ['', "Taken off this machine's list:", *_removal_lines(local)]
        if kind == cleanup.SUPERSEDED:
            lines += ['', 'The newest run of each stays.']
        lines += ['', 'Their outputs stay.']
        return QMessageBox.question(self, 'Farm Manager', '\n'.join(lines)) == QMessageBox.Yes

    def _say(self, text: str, error: bool = False) -> None:
        """Answer an action in the footer, with the time it was answered."""
        from datetime import datetime
        self._message.setStyleSheet(_ERROR_MESSAGE_STYLE if error else _MESSAGE_STYLE)
        self._message.setText(f"{text} at {datetime.now().strftime('%H:%M:%S')}")

    def _on_open_job_dir(self) -> None:
        for row in self._selected_rows():
            if row.job_dir:
                desktop.open_in_file_browser(Path(row.job_dir))
                return

    def _on_open_output(self) -> None:
        for row in self._selected_rows():
            if row.outputs:
                _open_folder_of(row.outputs[0])
                return

    def _on_open_log(self) -> None:
        task = self._selected_task()
        if task and task.log_path:
            desktop.open_in_file_browser(Path(task.log_path).parent)

    # ── lifetime ──────────────────────────────────────────

    def set_source(self, source: str | None) -> None:
        self._source.setCurrentIndex(max(0, self._source.findData(source)))

    def closeEvent(self, event) -> None:
        for timer in (self._local_timer, self._farm_timer, self._futures_timer, self._machine_timer):
            timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


# ── worker-thread calls (no Qt in here) ───────────────────

def _fetch_farm_jobs():
    from tumblepipe.farm.manager import deadline_jobs
    return deadline_jobs.fetch_jobs()


def _fetch_farm_tasks(job_id: str):
    from tumblepipe.farm.manager import deadline_jobs
    return deadline_jobs.fetch_tasks(job_id)


def _fetch_reports(job_id: str, frames: str):
    from tumblepipe.farm.manager import deadline_jobs
    return deadline_jobs.task_reports(job_id, frames)


def _read_report(path: str) -> str:
    from tumblepipe.farm.manager import deadline_jobs
    return deadline_jobs.report_text(path)


def _farm_control(command: str, ids: list[str]) -> str:
    from tumblepipe.farm.manager import deadline_jobs
    return deadline_jobs.control(command, ids)


def _farm_cleanup(chosen: list[cleanup.Removal], kind: str) -> tuple[cleanup.Cleaned, int]:
    """Delete the confirmed farm batches that still qualify on a fresh read:
    ``(what was deleted, how many confirmed batches were left)``."""
    from tumblepipe.farm.manager import deadline_jobs
    again = cleanup.recheck(deadline_jobs.fetch_jobs(), chosen, kind)
    return cleanup.delete_farm(again, deadline_jobs.control), len(chosen) - len(again)


def _open_folder_of(path: str) -> None:
    """Open the folder an output is written to (an image sequence's ``####`` pattern included)."""
    target = Path(path)
    desktop.open_in_file_browser(target if target.is_dir() else target.parent)


def _copy(text: str) -> None:
    QApplication.clipboard().setText(text or '')


def _identity() -> tuple[str, str]:
    """``(pipeline user, project name)``; '' for whichever this session has not got."""
    user = project = ''
    try:
        from tumblepipe.api import get_user_name
        user = get_user_name() or ''
    except Exception:
        log.debug('No pipeline user for the Farm Manager', exc_info=True)
    try:
        from tumblepipe.api import get_project_name
        project = get_project_name() or ''
    except Exception:
        log.debug('No project for the Farm Manager', exc_info=True)
    return user, project


# ── opening it ────────────────────────────────────────────

_WINDOW: ManagerWindow | None = None


def show_manager(parent: QWidget | None = None, source: str | None = None) -> ManagerWindow:
    """Show the Farm Manager, raising the open one instead of opening a second."""
    global _WINDOW
    if _WINDOW is not None:
        try:
            if source is not None:
                _WINDOW.set_source(source)
            _WINDOW.show()
            _WINDOW.raise_()
            _WINDOW.activateWindow()
            return _WINDOW
        except RuntimeError:
            _WINDOW = None  # Its C++ side was deleted on close.
    window = ManagerWindow(parent, source)
    window.destroyed.connect(_forget)
    _WINDOW = window
    window.show()
    window.raise_()
    return window


def _forget(*_args) -> None:
    global _WINDOW
    _WINDOW = None
