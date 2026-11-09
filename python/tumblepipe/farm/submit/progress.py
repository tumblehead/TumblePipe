"""What a submission's progress means, read from its folded events.

The Farm Submit window follows a submission through the events the plan
runner appends (``plan.fold_events``). Which entities went out (Back to grid
unticks them), which a retry sends again, what a runner that died early
leaves behind and what each row says are decided here, without Qt, so they
can be tested on their own (``tests/test_farm_submit_progress.py``).
"""

from __future__ import annotations

from typing import Sequence

DONE = 'done'
FAILED = 'failed'
CANCELLED = 'cancelled'
RUNNING = 'running'

DIED_EARLY = "the submission process stopped early; see the log"


def _status(state: dict, uri: str) -> str | None:
    row = state['rows'].get(uri)
    return row.get('status') if isinstance(row, dict) else None


def sent_uris(state: dict) -> set[str]:
    """The entities whose jobs went out."""
    return {uri for uri in state['rows'] if _status(state, uri) == DONE}


def unfinished_configs(state: dict, configs: Sequence[dict]) -> list[dict]:
    """The configs of the rows that failed or were cancelled, in their order:
    what Retry sends again. A row never reported is not among them."""
    unfinished = {uri for uri in state['rows'] if _status(state, uri) in (FAILED, CANCELLED)}
    return [c for c in configs if c['entity']['uri'] in unfinished]


def counts(state: dict) -> tuple[int, int, int]:
    """``(done, failed, cancelled)`` over the rows reported so far."""
    statuses = [_status(state, uri) for uri in state['rows']]
    return statuses.count(DONE), statuses.count(FAILED), statuses.count(CANCELLED)


def close_out(state: dict, uris: Sequence[str], error: str = DIED_EARLY) -> list[dict]:
    """The runner died before its end event: mark the submission finished and
    every row of ``uris`` not yet done, failed or cancelled as failed.

    Returns the row events it wrote, so the view can show them. A row that
    reached an outcome keeps it: its jobs are on the farm, or were never sent.
    """
    state['finished'] = True
    written = []
    for uri in uris:
        if _status(state, uri) in (DONE, FAILED, CANCELLED):
            continue
        event = {'event': 'row', 'uri': uri, 'status': FAILED, 'error': error}
        state['rows'][uri] = event
        written.append(event)
    return written


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def problems(state: dict, names: dict[str, str]) -> list[tuple[str, str]]:
    """Each entity whose row went wrong, by name, with what went wrong in
    full: a failed submission's error, else a clean-up's.

    The status column cuts long errors short, and an error often says how to
    fix it, so the progress card lists these under its table. Entities come
    in the order of ``names`` (the table's); a name is its URI when missing.
    """
    out = []
    for uri, name in names.items():
        row = state.get('rows', {}).get(uri)
        cleanup = state.get('cleanup', {}).get(uri)
        status = row.get('status') if isinstance(row, dict) else None
        if status == FAILED:
            error = row.get('error') or 'failed, with no reason given'
        elif isinstance(cleanup, dict) and cleanup.get('error'):
            error = f"old jobs not cleaned up: {cleanup['error']}"
        else:
            continue
        out.append((name or uri, str(error)))
    return out


def row_text(row: dict | None, cleanup: dict | None, backend: str) -> str:
    """What an entity's row says: its status, then what its clean-up did.

    A clean-up that failed or stopped short says so, so the artist knows the
    old jobs are still there; one that removed nothing says nothing.
    """
    status = row.get('status') if isinstance(row, dict) else None
    if status == RUNNING:
        text = "submitting…"
    elif status == DONE:
        jobs = row.get('jobs', 0)
        jobs = jobs if isinstance(jobs, int) and not isinstance(jobs, bool) else 0
        where = "submitted" if backend == "farm" else "queued here"
        text = f"{where}, {_plural(jobs, 'job')}"
    elif status == FAILED:
        error = row.get('error') or ''
        text = f"failed: {error}" if error else "failed"
    elif status == CANCELLED:
        return "cancelled, not sent"
    else:
        text = "queued"
    if isinstance(cleanup, dict):
        removed = cleanup.get('removed')
        if isinstance(removed, int) and not isinstance(removed, bool) and removed > 0:
            text += f"; {_plural(removed, 'old job')} cleaned up"
        if cleanup.get('error'):
            text += f"; old jobs not cleaned up: {cleanup['error']}"
    return text
