"""The Deadline farm, read and steered for the Farm Manager, through ``deadlinecommand``.

Reads use ``deadlinecommand -Json <verb>``, whose stdout is one envelope,
``{"ok": true, "result": ...}`` (``{"ok": false, "result": "<why>"}`` on a
refusal) — the commands deadline-mcp is built on. ``GetJobs`` returns every
job on the farm (about a second for a few hundred), so the window calls it
off the GUI thread, every few seconds.

Reports are bzip2 files on the repository share, listed per job by
``GetJobErrorReportFilenames``/``GetJobLogReportFilenames``. A report names
its task only as the frames it ran (``Frames: 1001-1010``) and the worker
(``Worker Name: ...``), so a task's reports are found by reading them.
They never change once written, so read ones are kept.
"""

from __future__ import annotations

import bz2
import datetime as dt
import json
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from tumblepipe.farm.manager import rows

# The window's farm actions, as deadlinecommand verbs.
COMMANDS = {
    'suspend': 'SuspendJob',
    'resume': 'ResumeJob',
    'retry': 'ResumeFailedJob',
    'requeue': 'RequeueJob',
    'delete': 'DeleteJob',
}

ERROR = 'error'
LOG = 'log'


class DeadlineError(RuntimeError):
    """deadlinecommand refused, or printed something that is not an envelope."""


def parse_envelope(stdout: str):
    """The ``result`` of a ``-Json`` envelope; raises on a refusal or non-JSON."""
    text = stdout.strip().lstrip('﻿')
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise DeadlineError(text or 'deadlinecommand printed nothing') from None
    if not isinstance(data, dict) or 'ok' not in data:
        raise DeadlineError(text)
    if data['ok'] is not True:
        raise DeadlineError(str(data.get('result') or text))
    return data.get('result')


def _command(*args: str, envelope: bool = True) -> str:
    """deadlinecommand's stdout alone: a warning on stderr must not break the JSON."""
    from tumblepipe.apps.deadline import deadline_command
    command = [str(deadline_command()), *args]
    result = subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        timeout=120,
    )
    stdout = result.stdout.decode('utf-8', errors='replace')
    # A refused -Json read still prints its envelope, which says why.
    if result.returncode != 0 and not (envelope and stdout.strip()):
        stderr = result.stderr.decode('utf-8', errors='replace').strip()
        why = stdout.strip() or stderr
        raise DeadlineError(f'{" ".join(args)} exited with code {result.returncode}: {why}')
    return stdout


def read(verb: str, *args: str):
    return parse_envelope(_command('-Json', verb, *args))


# ── jobs and tasks ────────────────────────────────────────

def fetch_jobs() -> list[rows.JobRow]:
    return [rows.farm_job_row(doc) for doc in read('GetJobs') or []]


def fetch_tasks(job_id: str) -> list[rows.TaskRow]:
    result = read('GetJobTasks', job_id) or {}
    return [rows.farm_task_row(doc) for doc in result.get('Tasks') or []]


def control(command: str, job_ids: list[str]) -> str:
    """Run a job command on ``job_ids``; returns what Deadline printed."""
    if command not in COMMANDS:
        raise ValueError(f'Unknown farm command: {command!r}')
    if not job_ids:
        return ''
    return _command(COMMANDS[command], ','.join(job_ids), envelope=False)


# ── reports ───────────────────────────────────────────────

@dataclass(frozen=True)
class Report:
    kind: str           # ERROR or LOG
    path: str
    created: str        # ISO local time, from the report id
    frames: str = ''
    worker: str = ''
    summary: str = ''   # an error report's message, first line


_FRAMES_RE = re.compile(r'(?m)^Frames:\s*(.+?)\s*$')
_WORKER_RE = re.compile(r'(?m)^Worker Name:\s*(.+?)\s*$')
_ERROR_RE = re.compile(r'=+\s*\nError\s*\n=+\s*\n(.*?)\n', re.S)


def report_created(path: str) -> str:
    """A report's time: its file name is a Mongo ObjectId, whose first 8 hex digits are seconds."""
    name = Path(path.replace('\\', '/')).name.split('.', 1)[0]
    try:
        seconds = int(name[:8], 16)
    except ValueError:
        return ''
    return dt.datetime.fromtimestamp(seconds).isoformat(timespec='seconds')


def parse_report(kind: str, path: str, text: str) -> Report:
    frames = _FRAMES_RE.search(text)
    worker = _WORKER_RE.search(text)
    error = _ERROR_RE.search(text) if kind == ERROR else None
    return Report(
        kind=kind, path=path, created=report_created(path),
        frames=frames.group(1) if frames else '',
        worker=worker.group(1) if worker else '',
        summary=error.group(1).strip() if error else '',
    )


def report_paths(job_id: str) -> list[tuple[str, str]]:
    """``(kind, path)`` of every report of the job, newest first."""
    found = [(ERROR, p) for p in read('GetJobErrorReportFilenames', job_id) or []]
    found += [(LOG, p) for p in read('GetJobLogReportFilenames', job_id) or []]
    return sorted(found, key=lambda kp: Path(kp[1].replace('\\', '/')).name, reverse=True)


_TEXTS: dict[str, str] = {}
_TEXTS_LOCK = threading.Lock()


def report_text(path: str) -> str:
    """The report's text (bzip2-decoded), read once per session."""
    with _TEXTS_LOCK:
        if path in _TEXTS:
            return _TEXTS[path]
    data = Path(path).read_bytes()
    if path.lower().endswith('.bz2'):
        data = bz2.decompress(data)
    text = data.decode('utf-8', errors='replace').lstrip('﻿')
    with _TEXTS_LOCK:
        _TEXTS[path] = text
    return text


def task_reports(job_id: str, frames: str | None, limit: int = 40) -> list[Report]:
    """The job's reports, newest first, those of ``frames`` only when given.

    Reads at most ``limit`` reports, so a job with hundreds of retried tasks
    does not read them all to show one.
    """
    result = []
    for kind, path in report_paths(job_id)[:limit]:
        report = parse_report(kind, path, report_text(path))
        if frames is None or report.frames == frames:
            result.append(report)
    return result
