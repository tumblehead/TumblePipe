"""A small non-modal window that follows a Farm submission running elsewhere.

The submission itself runs in a separate process (``tumblepipe.farm.submit_plan``);
this window only reads the progress file it appends to, so nothing here can
block Houdini. Closing the window (or Houdini) does not stop the submission;
**Cancel** does, between entities (``submit_plan.request_cancel``).
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
CANCELLED_COLOUR = "#8a8a8a"

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
    border: 1px solid {BORDER};
    border-radius: 4px;
    min-height: 28px;
    max-height: 28px;
    color: {TEXT_PRIMARY};
    font-weight: 600;
    text-align: center;
}}
QPushButton {{
    background-color: {BG_DARK};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
""" + VISIBLE_SCROLLBARS


# The Close button once a submission finished cleanly. Border and padding are
# set with the colour: Houdini's style draws a button that only gets a
# background colour as bare text.
_DONE_BUTTON_STYLE = f"""
QPushButton {{
    background-color: {DONE_COLOUR};
    color: #ffffff;
    font-weight: 600;
    border: 1px solid {DONE_COLOUR};
    border-radius: 4px;
    padding: 5px 14px;
}}
QPushButton:hover {{ background-color: #35c095; border-color: #35c095; }}
"""


class FarmSubmissionWindow(QWidget):
    """Progress of one submission: a row per entity, errors inline.

    Args:
        plan_path: The submission's ``plan.json``; progress is read beside it.
        process: The runner, to notice it dying without finishing.
        configs: The plan's rows, for names and for resubmitting the failed
            or cancelled ones.
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
        self._cancelling = False

        self.setWindowTitle("Farm submission")
        self.setMinimumSize(560, 420)
        self.setStyleSheet(_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

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

        # Below the rows it counts, beside the note and buttons it leads to.
        # It carries the summary ("Submitting… 3 of 20 · 1 failed") itself,
        # so there is one place to look.
        self._bar = QProgressBar()
        self._bar.setTextVisible(True)
        self._bar.setAlignment(Qt.AlignCenter)
        self._bar.setRange(0, max(1, len(self._configs)))
        self._bar_colour = None
        root.addWidget(self._bar)

        self._note = QLabel(
            "Runs in its own process: keep working, or close this window. "
            "Closing it does not stop the submission; Cancel does."
        )
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {TEXT_SECONDARY};")
        root.addWidget(self._note)

        buttons = QHBoxLayout()
        log_btn = QPushButton("Open log folder")
        log_btn.clicked.connect(self._open_folder)
        buttons.addWidget(log_btn)
        buttons.addStretch(1)
        self._retry = QPushButton("Retry failed")
        # Shown only once a finished run has failures: a greyed-out retry
        # beside a clean run read as if something had gone wrong.
        self._retry.setVisible(False)
        self._retry.clicked.connect(self._retry_unfinished)
        buttons.addWidget(self._retry)
        # Stops between entities: the one being submitted finishes, so no
        # entity is left with half its jobs on the farm.
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._cancel_submission)
        buttons.addWidget(self._cancel)
        # "Hide" while the submission runs (closing does not stop it);
        # "Close" once it is over — green when nothing failed.
        self._close = QPushButton("Hide")
        self._close.clicked.connect(self.close)
        buttons.addWidget(self._close)
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
            for uri in self._items:
                if uri not in self._state['rows'] or self._state['rows'][uri].get('status') == 'running':
                    event = {
                        'uri': uri, 'status': 'failed',
                        'error': "the submission process stopped early — see the log",
                    }
                    self._state['rows'][uri] = event
                    self._show_row(event)
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
        elif status == 'cancelled':
            item.setText(1, "cancelled")
            item.setForeground(1, QColor(CANCELLED_COLOUR))

    def _counts(self) -> tuple[int, int, int]:
        rows = self._state['rows'].values()
        done = sum(1 for r in rows if r.get('status') == 'done')
        failed = sum(1 for r in rows if r.get('status') == 'failed')
        cancelled = sum(1 for r in rows if r.get('status') == 'cancelled')
        return done, failed, cancelled

    def _update_summary(self) -> None:
        done, failed, cancelled = self._counts()
        total = len(self._configs)
        self._bar.setValue(done + failed + cancelled)
        if self._state['finished']:
            text = f"Submitted {done} of {total}"
        elif self._cancelling:
            text = f"Cancelling… {done + failed} of {total}"
        else:
            text = f"Submitting… {done + failed} of {total}"
        if failed:
            text += f" · {failed} failed"
        if cancelled:
            text += f" · {cancelled} cancelled"
        # QProgressBar's format expands %p/%v/%m; a literal % must be doubled.
        self._bar.setFormat(text.replace('%', '%%'))
        # The fill says how it is going: accent while running, green for a
        # clean finish, amber once anything failed.
        if failed:
            colour = FAILED_COLOUR
        elif cancelled and self._state['finished']:
            colour = CANCELLED_COLOUR
        elif self._state['finished']:
            colour = DONE_COLOUR
        else:
            colour = ACCENT
        if colour != self._bar_colour:
            self._bar_colour = colour
            self._bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {colour}; border-radius: 3px; }}"
            )
        # Cancelled entities were never tried: retrying picks them up too.
        self._retry.setVisible(bool(failed or cancelled) and self._state['finished'])
        self._retry.setText("Retry failed" if not cancelled else "Submit the rest")
        if self._state['finished']:
            # "Closing it does not stop the submission" is moot once it has
            # stopped by itself.
            self._note.hide()
            self._cancel.hide()
            self._close.setText("Close")
            # Green only for a clean run: otherwise Retry failed / Submit the
            # rest is the next step, and a filled Close would compete with it.
            self._close.setStyleSheet(
                _DONE_BUTTON_STYLE if not (failed or cancelled) else ""
            )

    # ── actions ───────────────────────────────────────────

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._plan_path.parent)))

    def _cancel_submission(self) -> None:
        try:
            submit_plan.request_cancel(self._plan_path)
        except OSError as error:
            log.exception("Could not cancel the submission")
            QMessageBox.critical(self, "Farm Submit", f"Could not cancel:\n\n{error}")
            return
        self._cancelling = True
        self._cancel.setEnabled(False)
        self._cancel.setText("Cancelling…")
        self._note.setText(
            "Stopping once the entity being submitted finishes; "
            "the rest are not sent to the farm."
        )
        self._update_summary()

    def _retry_unfinished(self) -> None:
        unfinished = {
            uri for uri, row in self._state['rows'].items()
            if row.get('status') in ('failed', 'cancelled')
        }
        configs = [c for c in self._configs if c['entity']['uri'] in unfinished]
        if not configs:
            return
        from .submit_jobs_dialog import start_submission
        try:
            window = start_submission(configs, parent=self.parentWidget())
        except Exception as error:
            log.exception("Could not resubmit the unfinished entities")
            QMessageBox.critical(self, "Farm Submit", f"Could not retry:\n\n{error}")
            return
        window.show()
        self.close()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
