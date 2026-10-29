"""Submitting a farm batch to this machine's local farm.

:class:`LocalFarm` stands beside ``apps.deadline.Deadline``: the same
``submit(batch, jobs_path)``, writing the same job folder
(``apps.deadline.write_job_files``), but the batch is queued here
(``farm.local.queue``) and this machine's runner started.

Every job's env file carries ``TH_FARM_BACKEND=local``, so a task that
submits further work (a collapse submitting its playblast) submits it to
this queue too, not to Deadline.
"""

from __future__ import annotations

import platform
from pathlib import Path

from tumblepipe.api import get_user_name, path_str
from tumblepipe.apps.deadline import open_job_folder, write_job_files
from tumblepipe.farm import backend as farm_backend
from tumblepipe.farm.local import queue, runner

#: The prefix of a local job id: ``local:<batch id>/<job index>``.
ID_PREFIX = 'local:'


def job_id(batch_id: str, job_index: int) -> str:
    return f'{ID_PREFIX}{batch_id}/{job_index}'


def job_entry(job_path: Path, job_index: int, job, deps, plugin_info: dict) -> dict:
    """The ``batch.json`` entry for ``job``, from what was written for the plugin."""
    if 'Manifest' not in plugin_info:
        raise ValueError(f'Job {job.name!r} has no hpm manifest; the local farm runs HPM jobs only')
    frames = queue.job_frames(job.frames, job.start_frame, job.end_frame, job.step_size)
    return {
        'index': job_index,
        'name': job.name,
        'deps': sorted(deps),
        'tasks': queue.task_spans(frames, job.chunk_size),
        'package': plugin_info['Package'],
        'script': plugin_info['ScriptFile'],
        'arguments': plugin_info['Arguments'],
        'data_dir': plugin_info['StartupDirectory'],
        'env_file': plugin_info.get('EnvironmentFile'),
        'manifest': plugin_info['Manifest'],
        'timeout': (job.max_frame_time or 0) * 60,
        'outputs': [path_str(p) for p in job.output_paths],
        'priority': job.priority,
    }


class LocalFarm:
    """This machine, as a farm."""

    def __init__(self, root_path: Path | None = None) -> None:
        self._root = Path(root_path or queue.root())

    def submit(self, batch, jobs_path) -> list[str]:
        """Queue ``batch`` here; returns its local job ids, in submit order."""
        order = batch.topological_order()
        jobs = []
        job_path = open_job_folder(jobs_path)
        for job_index in order:
            job = batch.get_job(job_index)
            job.env[farm_backend.BACKEND_ENV] = farm_backend.LOCAL
            job.job_info()  # The scheduling a Deadline job must have, checked the same way.
            plugin_info = write_job_files(job_path, job_index, job)
            jobs.append(job_entry(job_path, job_index, job, batch.get_deps(job_index), plugin_info))
        batch_id = queue.new_batch_id()
        queue.add_batch(self._root, {
            'version': queue.BATCH_VERSION,
            'id': batch_id,
            'name': batch.get_name(),
            'user': get_user_name(),
            'machine': platform.node(),
            'submitted': queue.now(),
            'job_dir': path_str(job_path),
            'jobs': jobs,
        })
        runner.ensure_running(self._root)
        return [job_id(batch_id, index) for index in order]
