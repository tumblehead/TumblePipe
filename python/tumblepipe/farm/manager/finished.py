"""The artist's farm work that finished this session: what the Jobs popover lists.

Remy asked for "a short non-intrusive notification for when farm jobs
finish, with a button to view or inspect the outputs". The UI convention's
answer is not a toast and never a window opening on its own: when a batch
of the artist's own ends, the Farm Manager quick action takes one short
pulse and keeps a dot at its foot (accent when something finished, danger
when something failed) until the artist looks; one line goes to Houdini's
status bar, and to the OS when Houdini is not in front. A click on the
button opens a popover, "Finished since you looked", listing them.

This module is the Qt-free half: which batches count, when one has ended,
what its row says and which buttons it has, the order, the seen state and
the dot's colour. :class:`SessionWatch` keeps it from one read of the jobs
to the next (``activity.FarmActivity`` feeds it on its daemon thread); the
catalog turns it into the browser's popover.

- **Whose, and which**: the jobs of the pipeline user in the current
  project (``activity.mine``), submitted since this Houdini session began
  (:func:`session_start`), local and farm alike.
- **A batch, not a job**: a chained batch (Publish → Render → ...) is one
  report, made when its last job ends (:func:`batch_over`): every job has
  ended, or the batch has a failed job and what is left waits on it.
  A batch the artist cancelled, with nothing failed, is not reported.
  Retried and ended again, it is reported again, unseen.
- **Rows**: failures first, then the newest, at most :data:`MAX_ROWS`.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from tumblepipe.farm.local import queue
from tumblepipe.farm.manager import activity, rows

#: At most this many rows in the popover.
MAX_ROWS = 10

FINISHED = 'finished'
FAILED = 'failed'

#: The popover's buttons, by id.
PREVIEW = 'preview'
SHOW = 'show'
PREVIEW_LABEL = 'Preview in Cinedex'
SHOW_LABEL = 'Show in Jobs'

#: The farm workers whose jobs write frames: their rows count frames.
FRAME_KINDS = ('render', 'denoise', 'composite', 'slapcomp', 'playblast')
#: Which job names a batch, best first: its row says "<entity> <kind>".
HEADLINE_KINDS = (
    'render', 'playblast', 'composite', 'slapcomp', 'denoise', 'edit_render',
    'publish', 'export', 'stage', 'collapse', 'build', 'mp4',
)
# A job waiting on others: what a failed job leaves its dependents in.
_BLOCKED = (queue.PENDING, queue.SUSPENDED)

_SESSION_START: dt.datetime | None = None


def mark_session_start(now: dt.datetime | None = None) -> dt.datetime:
    """Note when this Houdini session began, once (startup calls it);
    later calls keep the first time."""
    global _SESSION_START
    if _SESSION_START is None:
        _SESSION_START = now or dt.datetime.now()
    return _SESSION_START


def session_start() -> dt.datetime:
    """When this Houdini session began: the jobs submitted since are its."""
    return _SESSION_START or mark_session_start()


# ── one batch ─────────────────────────────────────────────

def batch_over(batch: list[rows.JobRow]) -> bool:
    """Whether nothing more of ``batch`` will run: every job has ended
    (:func:`rows.ended`), or some job failed and every other one has ended
    or waits (pending or suspended) on what failed."""
    if not batch:
        return False
    if all(rows.ended(r) for r in batch):
        return True
    failed = any(r.status == queue.FAILED and rows.ended(r) for r in batch)
    return failed and all(rows.ended(r) or r.status in _BLOCKED for r in batch)


def outcome(batch: list[rows.JobRow]) -> str:
    """``failed`` when a job of the over ``batch`` failed, ``finished``
    when every job completed, '' otherwise (cancelled: not reported)."""
    if any(r.status == queue.FAILED for r in batch):
        return FAILED
    if all(r.status == queue.COMPLETED for r in batch):
        return FINISHED
    return ''


def _order(batch: list[rows.JobRow]) -> list[rows.JobRow]:
    return sorted(batch, key=lambda r: (r.submitted, r.job_index if r.job_index is not None else 0, r.id))


def headline(batch: list[rows.JobRow]) -> rows.JobRow:
    """The job a batch's row is named after: its first job of the best kind
    in :data:`HEADLINE_KINDS`, else its first job."""
    ordered = _order(batch)
    for kind in HEADLINE_KINDS:
        for row in ordered:
            if row.kind == kind:
                return row
    return ordered[0]


def failed_job(batch: list[rows.JobRow]) -> rows.JobRow | None:
    """The batch's first failed job (in submit order), or None."""
    return next((r for r in _order(batch) if r.status == queue.FAILED), None)


def entity_text(batch: list[rows.JobRow]) -> str:
    """What the batch is for, as the browser shows it: ``SQ010/SH020``."""
    for row in _order(batch):
        label = activity.entity_label(rows.row_entity(row))
        if label:
            return label
    return rows.batch_title(batch[0].batch, batch[0].user) if batch else ''


def title(batch: list[rows.JobRow], state: str) -> str:
    """``SQ010/SH020 render``: the entity, then the kind of the failed job
    (failed) or of the headline job (finished)."""
    job = failed_job(batch) if state == FAILED else None
    job = job or headline(batch)
    kind = (job.kind or 'job').replace('_', ' ')
    entity = entity_text(batch)
    return f'{entity} {kind}' if entity else kind


def _when(iso: str) -> dt.datetime | None:
    return rows.parse_time(iso)


def ended_at(batch: list[rows.JobRow], seen_at: dt.datetime) -> dt.datetime:
    """When the batch ended: its last job's finish time, else when it was
    first seen over (a failed job keeps no finish time)."""
    ends = [t for t in (_when(r.finished) for r in batch) if t is not None]
    return max(ends) if ends else seen_at


def span_text(seconds: float | None) -> str:
    """``40 s``, ``12 min``, ``1 h 5 min``; '' when unknown."""
    if seconds is None or seconds < 0:
        return ''
    whole = int(round(seconds))
    if whole < 60:
        return f'{whole} s'
    hours, rest = divmod(whole, 3600)
    minutes = rest // 60
    if hours:
        return f'{hours} h {minutes} min' if minutes else f'{hours} h'
    return f'{minutes} min'


def finished_line(batch: list[rows.JobRow], ended: dt.datetime) -> str:
    """A finished batch's line: frames and passes, then the time it took,
    ``48 frames, 3 passes · 12 min on the farm``.

    Frames are the headline job's when it writes frames; passes are its
    kind's full (not partial) jobs, said when more than one. The time runs
    from the batch's first start to its end.
    """
    from tumblepipe.farm.manager import frames_preview
    lead = headline(batch)
    parts = []
    if lead.kind in FRAME_KINDS:
        count = rows.frame_count(lead.frames)
        if count:
            parts.append(f"{count} frame{'' if count == 1 else 's'}")
        same = [r for r in batch if r.kind == lead.kind]
        full = [r for r in same if not frames_preview.is_partial(r)] or same
        if len(full) > 1:
            parts.append(f'{len(full)} passes')
    starts = [t for t in (_when(r.started) for r in batch) if t is not None]
    took = span_text((ended - min(starts)).total_seconds()) if starts else ''
    where = 'on this machine' if all(r.source == rows.LOCAL for r in batch) else 'on the farm'
    head = ', '.join(parts)
    if took:
        tail = f'{took} {where}'
        return f'{head} · {tail}' if head else tail
    return head or f'Done {where}'


# ── a failure, from its tasks ─────────────────────────────

_SPAN = re.compile(r'^\s*(-?\d+)(?:\s*-\s*(-?\d+))?\s*$')


def _spans(frames: Iterable[str]) -> list[tuple[int, int]]:
    """Task frame texts (``1009-1012``, ``1013``) as merged ``(first, last)``
    spans, in order; a text that is not a span is left out."""
    spans = []
    for text in frames:
        match = _SPAN.match(str(text or ''))
        if not match:
            continue
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        spans.append((min(first, last), max(first, last)))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for first, last in spans:
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def frames_phrase(frames: Iterable[str], shown: int = 2) -> str:
    """``Frames 1009–1012``, ``Frame 1009``, ``Frames 1001–1004, 1010 and 2
    more``; '' when no frames are known."""
    spans = _spans(frames)
    if not spans:
        return ''
    texts = [str(a) if a == b else f'{a}–{b}' for a, b in spans]
    many = len(spans) > 1 or spans[0][0] != spans[0][1]
    head = ', '.join(texts[:shown])
    more = len(texts) - shown
    if more > 0:
        head += f' and {more} more'
    return f"{'Frames' if many else 'Frame'} {head}"


def where_phrase(workers: Iterable[str]) -> str:
    """``on rn04``, ``on rn04 and rn07``, ``on 3 machines``; '' for none."""
    names = sorted({w for w in workers if w})
    if not names:
        return ''
    if len(names) == 1:
        return f'on {names[0]}'
    if len(names) == 2:
        return f'on {names[0]} and {names[1]}'
    return f'on {len(names)} machines'


def _first_line(text: str, limit: int = 140) -> str:
    line = next((part.strip() for part in str(text or '').splitlines() if part.strip()), '')
    return line if len(line) <= limit else line[:limit - 1].rstrip() + '…'


def written_frames(tasks: list[rows.TaskRow]) -> int:
    """How many frames the job's completed tasks wrote."""
    return sum(rows.frame_count(t.frames) for t in tasks if t.status == queue.COMPLETED)


def failure_line(
    kind: str, tasks: list[rows.TaskRow], errors: list[tuple[str, str]] | None = None,
    total_frames: int = 0,
    ) -> str:
    """What failed, where, and what was written, from the failed job's tasks:
    ``Frames 1009–1012 failed on rn04: out of GPU memory. 8 of 12 written.``

    ``errors`` are the job's error reports' ``(worker, first line)`` (farm
    jobs: a Deadline task keeps no message of its own); a task's own error
    wins. ``total_frames`` is the job's frame count (its tasks' when 0).
    Frames and "written" are said for jobs that write frames; another kind
    says ``Publish failed on rn04: ...``.
    """
    errors = list(errors or [])
    failed = [t for t in tasks if t.status == queue.FAILED]
    writes = kind in FRAME_KINDS
    what = frames_phrase(t.frames for t in failed) if writes else ''
    if not what:
        what = f"{(kind or 'The job').replace('_', ' ').capitalize()}" if kind else 'The job'
    workers = [t.worker for t in failed if t.worker] or [w for w, _m in errors if w]
    where = where_phrase(workers)
    why = next((_first_line(t.error) for t in failed if _first_line(t.error)), '')
    if not why and errors:
        counts: dict[str, int] = {}
        for _w, message in errors:
            message = _first_line(message)
            if message:
                counts[message] = counts.get(message, 0) + 1
        if counts:
            why = sorted(counts, key=lambda m: (-counts[m], m))[0]
    line = f'{what} failed'
    if where:
        line += f' {where}'
    if why:
        line += f': {why.rstrip(".")}'
    line += '.'
    if writes:
        total = total_frames or sum(rows.frame_count(t.frames) for t in tasks)
        if total:
            line += f' {written_frames(tasks)} of {total} written.'
    return line


# ── reports ───────────────────────────────────────────────

@dataclass(frozen=True)
class Detail:
    """What only the job's tasks and output folder tell, read once when a
    batch ends (on the watcher's thread: it may run deadlinecommand and
    look at the share)."""
    line: str = ''
    written: int = 0                 # frames written (failed: by the failed job)
    preview_folder: str = ''         # the frames Preview in Cinedex opens; '' for none


@dataclass(frozen=True)
class Report:
    """One ended batch of the artist's, as the popover shows it."""
    key: str                          # the batch key
    state: str                        # FINISHED or FAILED
    title: str                        # SQ010/SH020 render
    ended: dt.datetime
    line: str
    select_id: str                    # the job Show in Jobs selects
    written: int = 0
    preview_folder: str = ''
    job_ids: tuple[str, ...] = field(default=(), compare=False)

    @property
    def failed(self) -> bool:
        return self.state == FAILED

    @property
    def seen_key(self) -> tuple[str, str]:
        """What "seen" remembers: the batch and when it ended, so a batch
        retried and ended again is unseen again."""
        return (self.key, self.ended.isoformat(timespec='seconds'))


def caption(report: Report) -> str:
    """``done 14:32`` / ``failed 14:32``."""
    word = 'failed' if report.failed else 'done'
    return f"{word} {report.ended.strftime('%H:%M')}"


def status_line(report: Report) -> str:
    """The one line for Houdini's status bar and the OS:
    ``SQ010/SH020 render done: 48 frames · 12 min on the farm``."""
    word = 'failed' if report.failed else 'done'
    return f'{report.title} {word}: {report.line}'


def buttons(report: Report) -> list[tuple[str, str, bool]]:
    """The row's buttons, ``(id, label, primary)``, left to right.

    Finished: *Preview in Cinedex* (primary) when it wrote frames there are
    on disk, then *Show in Jobs*. Failed: *Show in Jobs* (primary, opened at
    the error), then *Preview N Frames in Cinedex* when some were written.
    """
    if report.failed:
        result = [(SHOW, SHOW_LABEL, True)]
        if report.written > 0 and report.preview_folder:
            noun = 'Frame' if report.written == 1 else 'Frames'
            result.append((PREVIEW, f'Preview {report.written} {noun} in Cinedex', False))
        return result
    if report.preview_folder:
        return [(PREVIEW, PREVIEW_LABEL, True), (SHOW, SHOW_LABEL, False)]
    return [(SHOW, SHOW_LABEL, True)]


def order(reports: Iterable[Report], limit: int = MAX_ROWS) -> list[Report]:
    """Failures first, then the newest, at most ``limit``."""
    return sorted(reports, key=lambda r: (not r.failed, -r.ended.timestamp(), r.key))[:max(0, limit)]


def dot(reports: Iterable[Report], seen: set[tuple[str, str]]) -> str:
    """The button's dot: ``danger`` when an unseen report failed, ``accent``
    when one finished, '' when all are seen."""
    unseen = [r for r in reports if r.seen_key not in seen]
    if any(r.failed for r in unseen):
        return 'danger'
    return 'accent' if unseen else ''


def no_detail(batch: list[rows.JobRow], state: str) -> Detail:
    """A :class:`Detail` from the job rows alone (no tasks, no disk)."""
    return Detail()


def read_detail(batch: list[rows.JobRow], state: str) -> Detail:
    """Read what the tasks and the output folder say about an ended batch.

    Failed: the failed job's tasks (local: from disk; farm: deadlinecommand,
    plus its error reports when no task says why), for the failure line and
    the frames written; Finished: the frames on disk for Preview in Cinedex.
    Never raises: what cannot be read is left out.
    """
    from tumblepipe.farm.manager import frames_preview
    if state == FAILED:
        job = failed_job(batch)
        if job is None:
            return Detail()
        tasks: list[rows.TaskRow] = []
        errors: list[tuple[str, str]] = []
        try:
            if job.source == rows.LOCAL:
                from pathlib import Path
                tasks = rows.local_task_rows(Path(job.folder), job.job_index)
            else:
                from tumblepipe.farm.manager import deadline_jobs
                tasks = deadline_jobs.fetch_tasks(job.id)
                if not any(t.error for t in tasks):
                    errors = deadline_jobs.job_errors(job.id, limit=10)
        except Exception:
            pass
        written = written_frames(tasks) if job.kind in FRAME_KINDS else 0
        folder = ''
        if written:
            try:
                target = frames_preview.job_target(job)
                folder = str(target.folder) if target.enabled else ''
            except Exception:
                folder = ''
        line = failure_line(job.kind, tasks, errors, rows.frame_count(job.frames))
        return Detail(line=line, written=written, preview_folder=folder)
    try:
        target = frames_preview.best_target([r for r in batch if r.kind in frames_preview.FRAME_KINDS])
        folder = str(target.folder) if target.enabled else ''
    except Exception:
        folder = ''
    return Detail(preview_folder=folder)


def make_report(
    key: str, batch: list[rows.JobRow], state: str, seen_at: dt.datetime, detail: Detail,
    ) -> Report:
    ended = ended_at(batch, seen_at)
    lead = failed_job(batch) if state == FAILED else None
    lead = lead or headline(batch)
    line = detail.line or (
        finished_line(batch, ended) if state == FINISHED else f'The {lead.kind or "job"} failed.'
    )
    return Report(
        key=key, state=state, title=title(batch, state), ended=ended, line=line,
        select_id=lead.id, written=detail.written, preview_folder=detail.preview_folder,
        job_ids=tuple(r.id for r in _order(batch)),
    )


class SessionWatch:
    """The session's ended batches, from one read of the jobs to the next.

    :meth:`update` takes every job row read (local and farm), keeps the
    user's batches in the project submitted since ``since``, and returns
    the reports of the batches that ended since the last update: what the
    pulse, the status line and the OS notification announce, once per
    batch. :meth:`reports`, :meth:`dot` and :meth:`mark_seen` serve the
    popover. ``detail`` reads a batch's tasks and frames (:func:`read_detail`);
    tests pass :func:`no_detail` or their own.

    Not locked: one thread updates, others only read attributes that are
    replaced whole (a reader sees the old value or the new one).
    """

    def __init__(
        self, *, since: Callable[[], dt.datetime] = session_start,
        detail: Callable[[list[rows.JobRow], str], Detail] = read_detail,
        ) -> None:
        self._since = since
        self._detail = detail
        self._reports: dict[str, Report] = {}
        self._open: set[str] = set()       # batches seen still running
        self._seen: frozenset[tuple[str, str]] = frozenset()
        self._watching = False

    def update(
        self, job_rows: list[rows.JobRow], now: dt.datetime, *, user: str = '', project: str = '',
        ) -> list[Report]:
        since = self._since()
        batches: dict[str, list[rows.JobRow]] = {}
        for row in job_rows:
            if activity.mine(row, user=user, project=project):
                batches.setdefault(row.batch_key, []).append(row)
        reports = dict(self._reports)
        fresh: list[Report] = []
        watching = False
        for key, batch in batches.items():
            submitted = [t for t in (_when(r.submitted) for r in batch) if t is not None]
            if not submitted or min(submitted) < since:
                continue
            if not batch_over(batch):
                self._open.add(key)
                watching = True
                continue
            state = outcome(batch)
            if key in reports and key not in self._open:
                continue                   # reported, and not run since
            self._open.discard(key)
            if not state:
                reports.pop(key, None)     # cancelled: nothing to say
                continue
            report = make_report(key, batch, state, now, self._detail(batch, state))
            reports[key] = report
            fresh.append(report)
        self._reports = reports
        self._watching = watching
        return fresh

    def reports(self) -> list[Report]:
        """The popover's rows: failures first, then the newest, at most ten."""
        return order(self._reports.values())

    def report(self, key: str) -> Report | None:
        return self._reports.get(key)

    def dot(self) -> str:
        return dot(self._reports.values(), set(self._seen))

    def mark_seen(self) -> None:
        """The popover opened: every report so far is seen."""
        self._seen = self._seen | {r.seen_key for r in self._reports.values()}

    def unseen(self) -> list[Report]:
        return [r for r in order(self._reports.values(), len(self._reports))
                if r.seen_key not in self._seen]

    @property
    def watching(self) -> bool:
        """Whether a batch of this session is still running: worth reading
        the jobs for even while nobody looks at the browser."""
        return self._watching

    def forget(self) -> None:
        self._reports = {}
        self._open = set()
        self._seen = frozenset()


# ── what the popover and the announcement say ─────────────

#: The popover's words (the UI convention's "Side panels collapsed; Jobs
#: popover" board).
POPOVER_TITLE = 'Finished since you looked'
POPOVER_LINK = 'Open Jobs'
POPOVER_FOOTER = 'Your jobs from this session; failures first. Opening this marks them seen.'
POPOVER_EMPTY = 'None of your farm jobs from this session has finished yet.'
ICON_FINISHED = 'circle-check'
ICON_FAILED = 'circle-x'


@dataclass(frozen=True)
class RowSpec:
    """One popover row, in plain values (the catalog builds TumbleTrove's
    ``PopoverRow`` from it)."""
    id: str
    title: str
    icon: str
    tone: str                                   # 'accent' or 'danger'
    caption: str
    line: str
    buttons: tuple[tuple[str, str, bool], ...]  # (id, label, primary)


@dataclass(frozen=True)
class PopoverSpec:
    title: str
    link_label: str
    rows: tuple[RowSpec, ...]
    empty_text: str
    footer: str


def row_spec(report: Report) -> RowSpec:
    return RowSpec(
        id=report.key, title=report.title,
        icon=ICON_FAILED if report.failed else ICON_FINISHED,
        tone='danger' if report.failed else 'accent',
        caption=caption(report), line=report.line, buttons=tuple(buttons(report)),
    )


def popover_spec(reports: Iterable[Report]) -> PopoverSpec:
    """The Jobs popover for ``reports`` (already :func:`order`-ed)."""
    return PopoverSpec(
        title=POPOVER_TITLE, link_label=POPOVER_LINK,
        rows=tuple(row_spec(r) for r in reports),
        empty_text=POPOVER_EMPTY, footer=POPOVER_FOOTER,
    )


@dataclass(frozen=True)
class Announcement:
    """What one read's ended batches say: the status-bar line, and the OS
    notification's title and body (the same line)."""
    line: str
    title: str
    failed: bool


def announcement(reports: Iterable[Report]) -> Announcement | None:
    """One line for the batches that just ended: the most urgent one's
    (:func:`order`), ``and N more`` when several ended at once; None for none."""
    ordered = order(reports, limit=1 << 30)
    if not ordered:
        return None
    first = ordered[0]
    line = status_line(first)
    if len(ordered) > 1:
        line += f' (and {len(ordered) - 1} more)'
    failed = any(r.failed for r in ordered)
    plural = len(ordered) > 1
    title = ('Farm jobs' if plural else 'Farm job') + (' failed' if failed else ' finished')
    return Announcement(line=line, title=title, failed=failed)
