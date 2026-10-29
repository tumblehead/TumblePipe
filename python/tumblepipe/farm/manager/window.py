"""The Farm Manager window: local and farm jobs, their tasks, progress, logs and errors.

Lists this machine's local farm (``farm.local``) and the Deadline farm side
by side, a batch per row with its jobs under it. Selecting a job shows its
tasks; selecting a task shows its log — the local task's log file, followed
while it renders, or the task's Deadline reports.

Threads: the local queue is small files on this machine and is read on the
GUI thread once a second. Everything Deadline (``deadline_jobs``: a
``deadlinecommand`` per call, a second or more each) runs on a worker
thread; its results are picked up by a timer on the GUI thread, so no Qt
object is ever touched off it (*Qt and threads* in docs/development.md).
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BORDER, FONT_BODY, FONT_FAMILY,
    TEXT_PRIMARY, TEXT_SECONDARY,
)

from tumblepipe.farm.local import queue, runner
from tumblepipe.farm.manager import rows
from tumblepipe.util import desktop

log = logging.getLogger(__name__)

LOCAL_INTERVAL_MS = 1000
FARM_INTERVAL_MS = 8000
FUTURES_INTERVAL_MS = 150

STATUS_COLOURS = {
    queue.COMPLETED: '#2bae86',
    queue.FAILED: '#f0a030',
    queue.RENDERING: '#7fb2f0',
    queue.QUEUED: TEXT_SECONDARY,
    queue.PENDING: '#b8a860',
    queue.SUSPENDED: '#b48ad8',
    queue.CANCELLED: '#8a8a8a',
    rows.UNKNOWN: '#8a8a8a',
}
WHERE = {rows.LOCAL: 'This machine', rows.FARM: 'Farm'}

# Job tree columns.
NAME, WHERE_COL, STATUS, PROGRESS, TASKS, ERRORS, USER, SUBMITTED = range(8)
JOB_HEADERS = ['Job', 'Where', 'Status', 'Progress', 'Tasks', 'Errors', 'User', 'Submitted']
# Task tree columns.
T_TASK, T_FRAMES, T_STATUS, T_PROGRESS, T_WORKER, T_STARTED, T_FINISHED, T_ERROR = range(8)
TASK_HEADERS = ['Task', 'Frames', 'Status', 'Progress', 'Worker', 'Started', 'Finished', 'Error']

KEY_ROLE = Qt.UserRole
PROGRESS_ROLE = Qt.UserRole + 1

# Labels of the actions, by command; which are offered depends on the source.
ACTION_LABELS = {
    'suspend': 'Suspend', 'resume': 'Resume', 'retry': 'Retry failed',
    'requeue': 'Requeue', 'cancel': 'Cancel', 'remove': 'Remove', 'delete': 'Delete',
}

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
QLineEdit, QComboBox {{
    background-color: {BG_DARK};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{ background-color: {BG_DARK}; border: 1px solid {BORDER}; }}
QSplitter::handle {{ background-color: {BG_DARKEST}; }}
"""


def _when(iso: str) -> str:
    """``2026-10-29T14:03:11`` as ``10-29 14:03``."""
    if not iso or 'T' not in iso:
        return iso or ''
    date, time = iso.split('T', 1)
    return f'{date[5:]} {time[:5]}'


class ProgressDelegate(QStyledItemDelegate):
    """A thin bar behind the progress text, coloured by the row's state."""

    def paint(self, painter: QPainter, option, index) -> None:
        value = index.data(PROGRESS_ROLE)
        if value is None:
            super().paint(painter, option, index)
            return
        painter.save()
        rect = option.rect.adjusted(4, 5, -4, -5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#2a2a2a'))
        painter.drawRoundedRect(rect, 3, 3)
        colour = index.data(Qt.ForegroundRole)
        fill = QColor(colour.color() if hasattr(colour, 'color') else ACCENT)
        fill.setAlpha(150)
        width = int(rect.width() * max(0.0, min(100.0, float(value))) / 100)
        if width > 0:
            painter.setBrush(fill)
            painter.drawRoundedRect(QRect(rect.x(), rect.y(), width, rect.height()), 3, 3)
        painter.setPen(QColor(TEXT_PRIMARY))
        painter.drawText(rect, Qt.AlignCenter, index.data(Qt.DisplayRole) or '')
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

        self._user, self._project = _identity()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)
        root.addLayout(self._build_filters(source))

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)
        self._jobs = self._build_jobs_tree()
        top_layout.addWidget(self._jobs, 1)
        top_layout.addLayout(self._build_actions())
        splitter.addWidget(top)

        bottom = QSplitter(Qt.Horizontal)
        bottom.setChildrenCollapsible(False)
        bottom.addWidget(self._build_tasks_tree())
        bottom.addWidget(self._build_log_panel())
        bottom.setSizes([620, 780])
        splitter.addWidget(bottom)
        splitter.setSizes([460, 380])
        root.addWidget(splitter, 1)

        self._status = QLabel()
        self._status.setStyleSheet(f'color: {TEXT_SECONDARY};')
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._local_timer = QTimer(self, interval=LOCAL_INTERVAL_MS, timeout=self._refresh_local)
        self._farm_timer = QTimer(self, interval=FARM_INTERVAL_MS, timeout=self._refresh_farm)
        self._futures_timer = QTimer(self, interval=FUTURES_INTERVAL_MS, timeout=self._poll_futures)
        self._local_timer.start()
        self._farm_timer.start()
        self._futures_timer.start()
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
        return bar

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
        return tree

    def _build_actions(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._action_buttons: dict[str, QPushButton] = {}
        for command in ('suspend', 'resume', 'retry', 'requeue', 'cancel', 'remove', 'delete'):
            button = QPushButton(ACTION_LABELS[command])
            button.clicked.connect(lambda _checked=False, c=command: self._run_action(c))
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
        self._tasks_label = QLabel('Tasks')
        self._tasks_label.setStyleSheet(f'color: {TEXT_SECONDARY};')
        layout.addWidget(self._tasks_label)
        tree = QTreeWidget()
        tree.setColumnCount(len(TASK_HEADERS))
        tree.setHeaderLabels(TASK_HEADERS)
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setItemDelegateForColumn(T_PROGRESS, ProgressDelegate(tree))
        header = tree.header()
        for column, width in ((T_TASK, 50), (T_FRAMES, 90), (T_STATUS, 85), (T_PROGRESS, 110),
                              (T_WORKER, 100), (T_STARTED, 90), (T_FINISHED, 90)):
            tree.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        tree.itemSelectionChanged.connect(self._on_task_selection)
        layout.addWidget(tree, 1)
        self._task_tree = tree
        return panel

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        head = QHBoxLayout()
        self._log_label = QLabel('Log')
        self._log_label.setStyleSheet(f'color: {TEXT_SECONDARY};')
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
        layout.addLayout(head)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
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
        self._follow_local_log()
        self._update_status()

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
            self._status.setText(f'Farm: {ACTION_LABELS[key[1]].lower()} sent for {key[2]} job(s)')
            self._refresh_farm()

    def _on_future_failed(self, key: tuple, error: Exception) -> None:
        kind = key[0]
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
            QMessageBox.warning(self, 'Farm Manager', f'{ACTION_LABELS[key[1]]} failed:\n\n{error}')
        elif kind in ('farm_tasks', 'farm_reports', 'farm_report_text'):
            self._set_log_text(f'Could not read from Deadline: {error}', follow=False)
        log.warning('Farm Manager: %s failed', kind, exc_info=error)

    def _update_status(self) -> None:
        parts = []
        working = runner.is_running(self._root)
        local_count = len(self._local_rows)
        parts.append(
            f"This machine: {'rendering' if working else 'idle'} · {local_count} job{'' if local_count == 1 else 's'}"
        )
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

    def _visible_rows(self) -> list[rows.JobRow]:
        source = self._source.currentData()
        filters = dict(
            source=source,
            user=self._user if self._mine.isChecked() else None,
            project=self._project if self._this_project.isChecked() else None,
            hide_finished=self._hide_finished.isChecked(),
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
                self._fill_batch_item(item, jobs)
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
                    self._fill_job_item(child, row)
        finally:
            tree.setUpdatesEnabled(True)
        job = self._current_job
        if job is not None and job.id in self._rows_by_id:
            self._current_job = self._rows_by_id[job.id]
        self._update_actions()

    def _fill_job_item(self, item: QTreeWidgetItem, row: rows.JobRow) -> None:
        colour = QColor(STATUS_COLOURS.get(row.status, TEXT_SECONDARY))
        item.setText(NAME, row.name)
        item.setText(WHERE_COL, WHERE[row.source])
        item.setText(STATUS, row.status)
        item.setForeground(STATUS, colour)
        item.setText(PROGRESS, f'{row.progress:.0f}%')
        item.setData(PROGRESS, PROGRESS_ROLE, row.progress)
        item.setForeground(PROGRESS, colour)
        item.setText(TASKS, f'{row.done}/{row.total}')
        item.setText(ERRORS, str(row.errors) if row.errors else '')
        item.setForeground(ERRORS, QColor(STATUS_COLOURS[queue.FAILED]))
        item.setText(USER, row.user)
        item.setText(SUBMITTED, _when(row.submitted))
        item.setToolTip(NAME, f'{row.name}\n{row.id}')

    def _fill_batch_item(self, item: QTreeWidgetItem, jobs: list[rows.JobRow]) -> None:
        status = rows.batch_status([row.status for row in jobs])
        colour = QColor(STATUS_COLOURS.get(status, TEXT_SECONDARY))
        done = sum(row.done for row in jobs)
        total = sum(row.total for row in jobs)
        progress = sum(row.progress * row.total for row in jobs) / total if total else 0.0
        bold = item.font(NAME)
        bold.setBold(True)
        item.setFont(NAME, bold)
        item.setText(NAME, jobs[0].batch)
        item.setToolTip(NAME, jobs[0].batch)
        item.setText(WHERE_COL, WHERE[jobs[0].source])
        item.setText(STATUS, status)
        item.setForeground(STATUS, colour)
        item.setText(PROGRESS, f'{progress:.0f}%')
        item.setData(PROGRESS, PROGRESS_ROLE, progress)
        item.setForeground(PROGRESS, colour)
        item.setText(TASKS, f'{done}/{total}')
        errors = sum(row.errors for row in jobs)
        item.setText(ERRORS, str(errors) if errors else '')
        item.setForeground(ERRORS, QColor(STATUS_COLOURS[queue.FAILED]))
        item.setText(USER, jobs[0].user)
        item.setText(SUBMITTED, _when(min(row.submitted for row in jobs)))

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
                self._tasks_label.setText('Tasks')
            else:
                self._tasks_label.setText(f'Tasks of {job.name}')
                if job.source == rows.LOCAL:
                    self._load_local_tasks(job)
                else:
                    self._submit(('farm_tasks', job.id), _fetch_farm_tasks, job.id)
        self._update_actions()

    # ── tasks ─────────────────────────────────────────────

    def _load_local_tasks(self, job: rows.JobRow) -> None:
        try:
            tasks = rows.local_task_rows(Path(job.folder), job.job_index)
        except Exception as error:
            self._tasks_label.setText(f'Tasks of {job.name}: {error}')
            return
        self._show_tasks(tasks)

    def _show_tasks(self, tasks: list[rows.TaskRow]) -> None:
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
            item.setForeground(T_PROGRESS, colour)
            item.setText(T_WORKER, task.worker)
            item.setText(T_STARTED, _when(task.started))
            item.setText(T_FINISHED, _when(task.finished))
            error = task.error or (f'{task.errors} error report(s)' if task.errors else '')
            item.setText(T_ERROR, error.splitlines()[0] if error else '')
            item.setToolTip(T_ERROR, error)
            item.setForeground(T_ERROR, QColor(STATUS_COLOURS[queue.FAILED]))
        if not tree.selectedItems() and tasks:
            # Open on the task worth reading: the one rendering, else the
            # last that failed, else the last that ran.
            pick = next((t for t in tasks if t.status == queue.RENDERING), None)
            pick = pick or next((t for t in reversed(tasks) if t.status == queue.FAILED), None)
            pick = pick or next((t for t in reversed(tasks) if t.started), None)
            if pick is not None:
                self._task_items[pick.index].setSelected(True)

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
        self._status.setText(f'This machine: {ACTION_LABELS[command].lower()} requested for {len(selected)} job(s)')

    def _on_open_job_dir(self) -> None:
        for row in self._selected_rows():
            if row.job_dir:
                desktop.open_in_file_browser(Path(row.job_dir))
                return

    def _on_open_output(self) -> None:
        for row in self._selected_rows():
            if row.outputs:
                path = Path(row.outputs[0])
                desktop.open_in_file_browser(path if path.is_dir() else path.parent)
                return

    def _on_open_log(self) -> None:
        task = self._selected_task()
        if task and task.log_path:
            desktop.open_in_file_browser(Path(task.log_path).parent)

    # ── lifetime ──────────────────────────────────────────

    def set_source(self, source: str | None) -> None:
        self._source.setCurrentIndex(max(0, self._source.findData(source)))

    def closeEvent(self, event) -> None:
        for timer in (self._local_timer, self._farm_timer, self._futures_timer):
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
