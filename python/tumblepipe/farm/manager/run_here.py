"""Run a farm job on this machine (#50).

A farm job that cannot run on the farm (no licence free on the workers that
take it, QA 2026-11-10: every encode of a batch failed on the one encode
worker) can run on the artist's machine, which holds a licence. The job's
files are already written (manifest, env file, data folder, context), so
the local farm queues the *same* files: nothing is exported or rendered
again. The jobs on the farm that wait on it, and have not completed, come
along in the same local batch, so a notify waiting on an encode does not
stay pending on Deadline.

Everything here is pure: it reads Deadline's job documents (``GetJobs``)
and writes a ``batch.json`` document (``farm.local.queue``). The Farm
Manager asks first, queues it and, by default, suspends the farm copies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tumblepipe.farm import backend as farm_backend
from tumblepipe.farm.local import queue
from tumblepipe.farm.manager import rows

#: The farm states a job can be taken off the farm from: not running, not done.
RUNNABLE = (queue.FAILED, queue.QUEUED, queue.PENDING, queue.SUSPENDED)


class RunHereError(ValueError):
    """Why a farm job cannot run on this machine."""


@dataclass(frozen=True)
class Plan:
    """The farm jobs to run here, in dependency order, and their local deps.

    ``deps[i]`` are indices into ``docs`` of the jobs ``docs[i]`` waits on
    inside the plan; a dependency outside it has completed on the farm.
    """
    docs: tuple
    deps: tuple

    @property
    def ids(self) -> list[str]:
        return [doc.get('_id', '') for doc in self.docs]

    @property
    def names(self) -> list[str]:
        return [_props(doc).get('Name', '') for doc in self.docs]


_FRAME_TOKEN = re.compile(r'^\s*(-?\d+)(?:\s*-\s*(-?\d+)(?:\s*[xX:]\s*(\d+))?)?\s*$')


def parse_frames(text: str) -> list[int]:
    """A Deadline frame list (``1001-1100``, ``1-99x2``, ``1001,1120,1240``,
    or a mix) as sorted frames without repeats, as Deadline reads it."""
    frames: set[int] = set()
    for token in str(text or '').split(','):
        if not token.strip():
            continue
        match = _FRAME_TOKEN.match(token)
        if match is None:
            raise RunHereError(f'cannot read the frame list {text!r}')
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        step = int(match.group(3) or 1)
        if step < 1 or last < first:
            raise RunHereError(f'cannot read the frame list {text!r}')
        frames.update(range(first, last + 1, step))
    return sorted(frames)


def _props(doc: dict) -> dict:
    props = doc.get('Props')
    return props if isinstance(props, dict) else {}


def _status(doc: dict) -> str:
    return rows.deadline_job_status(doc.get('Stat'), doc.get('RenderingChunks'))


def _dep_ids(doc: dict) -> list[str]:
    return [
        str(dep.get('JobID') if isinstance(dep, dict) else dep)
        for dep in _props(doc).get('Dep') or () if dep
    ]


def plan(docs: list[dict], job_id: str) -> Plan:
    """The jobs that run here for ``job_id``: it, and every job of the
    farm that waits on it (transitively) and has not completed.

    Raises :class:`RunHereError` when the job is not on the farm, is not
    failed, queued, pending or suspended, is not an HPM job, or waits on a
    farm job outside the plan that has not completed (its input is not
    there yet).
    """
    by_id = {doc.get('_id'): doc for doc in docs if doc.get('_id')}
    root = by_id.get(job_id)
    if root is None:
        raise RunHereError(f'job {job_id} is not on the farm any more')
    status = _status(root)
    if status not in RUNNABLE:
        raise RunHereError(
            f'"{_props(root).get("Name", job_id)}" is {status}: only a failed, '
            'queued, pending or suspended job can run on this machine'
        )
    chosen = [job_id]
    seen = {job_id}
    changed = True
    while changed:
        changed = False
        for doc in docs:
            other = doc.get('_id')
            if not other or other in seen or _status(doc) == queue.COMPLETED:
                continue
            if any(dep in seen for dep in _dep_ids(doc)):
                chosen.append(other)
                seen.add(other)
                changed = True
    # Dependency order: a job after everything it waits on inside the plan.
    ordered: list[str] = []
    placed: set[str] = set()
    while len(ordered) < len(chosen):
        progress = False
        for other in chosen:
            if other in placed:
                continue
            inside = [dep for dep in _dep_ids(by_id[other]) if dep in seen]
            if all(dep in placed for dep in inside):
                ordered.append(other)
                placed.add(other)
                progress = True
        if not progress:
            raise RunHereError('the jobs that wait on it depend on each other in a loop')
    for other in ordered:
        doc = by_id[other]
        if not (_props(doc).get('PlugInfo') or {}).get('Manifest'):
            raise RunHereError(
                f'"{_props(doc).get("Name", other)}" is not an HPM job: this '
                'machine runs HPM jobs only'
            )
        for dep in _dep_ids(doc):
            if dep in seen:
                continue
            dep_doc = by_id.get(dep)
            if dep_doc is not None and _status(dep_doc) != queue.COMPLETED:
                raise RunHereError(
                    f'"{_props(doc).get("Name", other)}" waits on '
                    f'"{_props(dep_doc).get("Name", dep)}", which has not completed '
                    'on the farm: run that one here too, or wait for it'
                )
    index = {other: i for i, other in enumerate(ordered)}
    return Plan(
        docs=tuple(by_id[other] for other in ordered),
        deps=tuple(
            tuple(sorted(index[dep] for dep in _dep_ids(by_id[other]) if dep in index))
            for other in ordered
        ),
    )


def job_entry(doc: dict, job_index: int, deps) -> dict:
    """The ``batch.json`` entry of one farm job, from its Deadline document."""
    props = _props(doc)
    plugin = props.get('PlugInfo') or {}
    frames = parse_frames(props.get('Frames') or '')
    if not frames:
        raise RunHereError(f'"{props.get("Name", "")}" has no frames')
    chunk = int(props.get('Chunk') or 1)
    max_seconds = props.get('MaxTime')
    outputs = [
        str(Path(d) / f).replace('\\', '/')
        for d, f in zip(doc.get('OutDir') or [], doc.get('OutFile') or [])
    ]
    from tumblepipe.farm.local.backend import frames_text
    return {
        'index': job_index,
        'name': props.get('Name', ''),
        'deps': sorted(deps),
        'tasks': queue.task_spans(frames, chunk),
        'package': plugin['Package'],
        'script': plugin['ScriptFile'],
        'arguments': plugin.get('Arguments', ''),
        'data_dir': plugin.get('StartupDirectory', ''),
        'env_file': plugin.get('EnvironmentFile'),
        'manifest': plugin['Manifest'],
        # MaxTime is the task timeout in seconds; with FrameTimeout (every
        # job TumblePipe submits) Deadline gives it to each frame.
        'timeout': queue.task_timeout(
            max_seconds if isinstance(max_seconds, int) else 0, frames, chunk,
            per_frame=bool(props.get('FrameTimeout', True)),
        ),
        'outputs': outputs,
        'priority': int(props.get('Pri') or 50),
        'pool': str(props.get('Pool') or ''),
        'group': str(props.get('Grp') or ''),
        'chunk_size': chunk,
        'frames': frames_text(frames),
        'extra_info': {k: v for k, v in (props.get('ExDic') or {}).items()},
        # The job's env file says the farm; what it submits from here goes
        # to this machine's queue, as a job queued here in the first place.
        'env': {farm_backend.BACKEND_ENV: farm_backend.LOCAL},
    }


def batch_document(the_plan: Plan, user: str, machine: str) -> dict:
    """The ``batch.json`` of ``the_plan``: one local batch, its jobs in order."""
    docs = the_plan.docs
    first = _props(docs[0])
    job_dirs = {
        str(Path((_props(d).get('PlugInfo') or {}).get('StartupDirectory', '')).parent).replace('\\', '/')
        for d in docs
    }
    name = first.get('Batch') or first.get('Name', '')
    return {
        'version': queue.BATCH_VERSION,
        'id': queue.new_batch_id(),
        'name': f'{name} (on this machine)',
        'user': user,
        'machine': machine,
        'submitted': queue.now(),
        'job_dir': sorted(job_dirs)[0],
        'jobs': [job_entry(doc, i, the_plan.deps[i]) for i, doc in enumerate(docs)],
    }
