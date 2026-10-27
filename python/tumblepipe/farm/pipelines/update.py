"""The update pipeline: republish every shot whose workfiles are newer than
their exports, department by department up to a given one.

A command-line entry point (for a scheduled farm update)::

    python -m tumblepipe.farm.pipelines.update <department> <pool> <priority>

Every shot (``entity:/shots/*/*``) is visited; within a shot, a department
is published when its workfile is newer than its export, or when an
earlier department of the shot was, each publish waiting for the one
before it. Each publish takes its frame range from the shot.
"""

from pathlib import Path
import argparse
import logging
import sys

from tumblepipe.api import api, get_user_name
from tumblepipe.apps.deadline import Deadline, Job
from tumblepipe.config.channels import DEFAULT_CHANNEL
from tumblepipe.config.department import list_departments
from tumblepipe.farm.pipelines import publish as publish_pipeline
from tumblepipe.farm.pipelines._batch import batch_title, submit_batch
from tumblepipe.util.uri import Uri

# Exports are checked against the default channel's.
UPDATE_CHANNEL = DEFAULT_CHANNEL


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the out-of-date shots' publish chains after ``depends_on``;
    returns the last job of each chain.

    ``config`` is ``{'department': str, 'pool_name': str, 'priority': int}``.
    """
    depends_on = list(depends_on or [])
    department_names = [d.name for d in list_departments('shots')]
    department_names = department_names[:department_names.index(config['department']) + 1]

    terminal_job_names = []
    # The store has no wildcards: 'entity:/shots/*/*' listed nothing, so
    # this pipeline never published a shot. Shots are three segments deep.
    shot_uris = [
        uri for uri in api.config.list_entity_uris(Uri.parse('entity:/shots'), closure=True)
        if len(uri.segments) == 3
    ]
    for shot_uri in shot_uris:
        prev_job_name = None
        for dept_name in department_names:
            if not publish_pipeline.is_submissable(shot_uri, dept_name):
                continue
            # Once one department republishes, every later one follows it
            if prev_job_name is None and not publish_pipeline.is_out_of_date(
                shot_uri, UPDATE_CHANNEL, dept_name
                ):
                continue
            # The URI, not a '_'-joined name: sq_1/sh10 and sq/1_sh10 would
            # share one key and the second job would replace the first.
            job_name = f'publish {shot_uri} {dept_name}'
            jobs[job_name] = publish_pipeline.create_publish_job(
                shot_uri, dept_name,
                config['pool_name'], config['priority'],
                paths, temp_path
            )
            deps[job_name] = list(depends_on) if prev_job_name is None else [prev_job_name]
            prev_job_name = job_name
        if prev_job_name is not None:
            terminal_job_names.append(prev_job_name)
    return terminal_job_names


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the update of ``config`` as one batch."""
    return submit_batch(batch_title('update', get_user_name()), build, config, paths)


def cli() -> int:
    logging.basicConfig(level = logging.DEBUG, format = '%(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('department_name', type = str)
    parser.add_argument('pool_name', type = str)
    parser.add_argument('priority', type = int)
    args = parser.parse_args()

    department_names = [d.name for d in list_departments('shots')]
    if args.department_name not in department_names:
        logging.error(f'Invalid department name: {args.department_name}')
        return 1
    if args.pool_name not in Deadline().list_pools():
        logging.error(f'Invalid pool name: {args.pool_name}')
        return 1
    if not 0 <= args.priority <= 100:
        logging.error(f'Invalid priority: {args.priority}')
        return 1

    submit(dict(
        department = args.department_name,
        pool_name = args.pool_name,
        priority = args.priority
    ))
    return 0


if __name__ == '__main__':
    sys.exit(cli())
