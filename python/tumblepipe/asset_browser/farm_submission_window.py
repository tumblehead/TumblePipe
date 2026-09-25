"""A small non-modal window that follows a Farm submission running elsewhere.

The submission itself runs in a separate process (``tumblepipe.farm.submit_plan``);
this window only reads the progress file it appends to, so nothing here can
block Houdini. Closing the window (or Houdini) does not stop the submission.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tumbletrove.asset_browser.core.theme import (
    ACCENT, BG_DARK, BG_DARKEST, BORDER, FONT_BODY, FONT_FAMILY,
    TEXT_PRIMARY, TEXT_SECONDARY,
)

from tumblepipe.farm import submit_plan

from .submit_jobs_dialog import VISIBLE_SCROLLBARS

log = logging.getLogger(__name__)

DONE_COLOUR = "#2bae86"
FAILED_COLOUR = "#f0a030"
RUNNING_COLOUR = "#7fb2f0"

_STYLE = f"""
QWidget {{
    background-color: {BG_DARKEST};
    color: {TEXT_PRIMARY};
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_BODY}px;
}}
QTreeWidget {{
    background-color: {BG_DARKEST};
    border: 1px solid {BORDER};
    border-radius: 6px;
    alternate-background-color: #1f1f1f;
    outline: none;
}}
QHeaderView::section {{
    background-color: #262626;
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid #4a4a4a;
    border-right: 1px solid #3a3a3a;
    padding: 4px 8px;
}}
QProgressBar {{
    background-color: {BG_DARK};
    border: none;
    border-radius: 2px;
    max-height: 4px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 2px; }}
QPushButton {{
    background-color: {BG_DARK};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
""" + VISIBLE_SCROLLBARS


class FarmSubmissionWindow(QWidget):
    """Progress of one submission: a row per entity, errors inline.

    Args:
        plan_path: The submission's ``plan.json``; progress is read beside it.
        process: The runner, to notice it dying without finishing.
        configs: The plan's rows, for names and **Retry failed**.
    """

    def __init__(
        self,
        plan_path: Path,
        process: subprocess.Popen | None,
        configs: Sequence[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._plan_path = Path(plan_path)
        self._progress_path = self._plan_path.parent / submit_plan.PROGRESS_NAME
        self._process = process
        self._configs = list(configs)
        self._offset = 0
        self._state = submit_plan.fold_events([])
        self._items: dict[str, QTreeWidgetItem] = {}

        self.setWindowTitle("Farm submission")
        self.setMinimumSize(560, 420)
        self.setStyleSheet(_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        self._title = QLabel()
        self._title.setStyleSheet("font-weight: 600;")
        root.addWidget(self._title)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setRange(0, max(1, len(self._configs)))
        root.addWidget(self._bar)

        self._table = QTreeWidget()
        self._table.setRootIsDecorated(False)
        self._table.setAlternatingRowColors(True)
        self._table.setColumnCount(2)
        self._table.setHeaderLabels(["Entity", "Status"])
        self._table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.setWordWrap(True)
        for config in self._configs:
            uri = config['entity']['uri']
            item = QTreeWidgetItem(self._table, [uri.split(':/', 1)[-1], "queued"])
            item.setForeground(1, QColor(TEXT_SECONDARY))
            self._items[uri] = item
        root.addWidget(self._table, 1)

        note = QLabel(
            "Runs in its own process: keep working, or close this window. "
            "Closing it does not stop the submission."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_SECONDARY};")
        root.addWidget(note)

        buttons = QHBoxLayout()
        log_btn = QPushButton("Open log folder")
        log_btn.clicked.connect(self._open_folder)
        buttons.addWidget(log_btn)
        buttons.addStretch(1)
        self._retry = QPushButton("Retry failed")
        self._retry.setEnabled(False)
        self._retry.clicked.connect(self._retry_failed)
        buttons.addWidget(self._retry)
        hide_btn = QPushButton("Hide")
        hide_btn.clicked.connect(self.close)
        buttons.addWidget(hide_btn)
        root.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()

    # ── progress ──────────────────────────────────────────

    def _poll(self) -> None:
        events, self._offset = submit_plan.read_events(self._progress_path, self._offset)
        if events:
            submit_plan.fold_events(events, self._state)
            for event in events:
                if event.get('event') == 'row':
                    self._show_row(event)
        if not self._state['finished'] and self._runner_died():
            self._state['finished'] = True
            for uri, item in self._items.items():
                if uri not in self._state['rows'] or self._state['rows'][uri].get('status') == 'running':
                    self._show_row({
                        'uri': uri, 'status': 'failed',
                        'error': "the submission process stopped early — see the log",
                    })
        self._update_summary()
        if self._state['finished']:
            self._timer.stop()

    def _runner_died(self) -> bool:
        return self._process is not None and self._process.poll() is not None

    def _show_row(self, event: dict) -> None:
        item = self._items.get(event.get('uri'))
        if item is None:
            return
        status = event.get('status')
        if status == 'running':
            item.setText(1, "submitting…")
            item.setForeground(1, QColor(RUNNING_COLOUR))
        elif status == 'done':
            jobs = event.get('jobs', 0)
            item.setText(1, f"submitted · {jobs} job{'' if jobs == 1 else 's'}")
            item.setForeground(1, QColor(DONE_COLOUR))
        elif status == 'failed':
            item.setText(1, "failed")
            item.setForeground(1, QColor(FAILED_COLOUR))
            item.setToolTip(0, event.get('error', ''))
            item.setToolTip(1, event.get('error', ''))
            if item.childCount() == 0:
                child = QTreeWidgetItem(item, [event.get('error', ''), ""])
                child.setForeground(0, QColor(FAILED_COLOUR))
                child.setFirstColumnSpanned(True)
            item.setExpanded(True)

    def _counts(self) -> tuple[int, int]:
        rows = self._state['rows'].values()
        done = sum(1 for r in rows if r.get('status') == 'done')
        failed = sum(1 for r in rows if r.get('status') == 'failed')
        return done, failed

    def _update_summary(self) -> None:
        done, failed = self._counts()
        total = len(self._configs)
        self._bar.setValue(done + failed)
        if self._state['finished']:
            text = f"Submitted {done} of {total}"
        else:
            text = f"Submitting… {done + failed} of {total}"
        if failed:
            text += f" · {failed} failed"
        self._title.setText(text)
        self._retry.setEnabled(bool(failed) and self._state['finished'])

    # ── actions ───────────────────────────────────────────

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._plan_path.parent)))

    def _retry_failed(self) -> None:
        failed = {
            uri for uri, row in self._state['rows'].items()
            if row.get('status') == 'failed'
        }
        configs = [c for c in self._configs if c['entity']['uri'] in failed]
        if not configs:
            return
        from .submit_jobs_dialog import start_submission
        try:
            window = start_submission(configs, parent=self.parentWidget())
        except Exception as error:
            log.exception("Could not retry the failed entities")
            QMessageBox.critical(self, "Farm Submit", f"Could not retry:\n\n{error}")
            return
        window.show()
        self.close()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
