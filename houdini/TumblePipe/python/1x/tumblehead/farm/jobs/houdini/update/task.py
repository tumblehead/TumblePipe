from pathlib import Path
import datetime as dt
import logging
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import get_user_name, path_str, to_wsl_path, to_windows_path, default_client
from tumblehead.apps.deadline import Deadline, Batch, Job
from tumblehead.pipe.paths import (
    latest_shot_hip_file_path,
    latest_shot_export_path,
    next_shot_export_path
)

api = default_client()

def _is_submissable(sequence_name, shot_name, department_name):
    if sequence_name == '000': return False
    if shot_name == '000': return False
    latest_hip_file_path = latest_shot_hip_file_path(
        sequence_name,
        shot_name,
        department_name
    )
    if latest_hip_file_path is None: return False
    return latest_hip_file_path.exists()

def _is_out_of_date(sequence_name, shot_name, department_name):
    latest_hip_file_path = latest_shot_hip_file_path(
        sequence_name,
        shot_name,
        department_name
    )
    latest_export_path = latest_shot_export_path(
        sequence_name,
        shot_name,
        department_name
    )
    if latest_export_path is None: return True
    if not latest_export_path.exists(): return True
    return latest_hip_file_path.stat().st_mtime > latest_export_path.stat().st_mtime

def _error(msg):
    logging.error(msg)
    return 1

PUBLISH_SCRIPT_PATH = Path(__file__).parent / 'publish.py'
def _create_publish_job(
    api,
    sequence_name,
    shot_name,
    department_name,
    pool_name,
    priority
    ):
    logging.debug(
        f'Creating publish job for '
        f'{sequence_name} '
        f'{shot_name} '
        f'{department_name}'
    )
    
    frame_range = api.config.get_frame_range(sequence_name, shot_name)
    render_range = frame_range.full_range()

    output_path = next_shot_export_path(
        sequence_name,
        shot_name,
        department_name
    )
    version_name = output_path.name
    job = Job(
        to_wsl_path(PUBLISH_SCRIPT_PATH), None,
        sequence_name,
        shot_name,
        department_name
    )
    job.name = (
        f'publish '
        f'{sequence_name} '
        f'{shot_name} '
        f'{department_name} '
        f'{version_name}'
    )
    job.pool = pool_name
    job.group = 'houdini'
    job.priority = priority
    job.start_frame = render_range.first_frame
    job.end_frame = render_range.last_frame
    job.step_size = 1
    job.chunk_size = len(render_range)
    job.max_frame_time = 10
    job.env.update(dict(
        TH_USER = get_user_name(),
        TH_CONFIG_PATH = path_str(to_wsl_path(api.CONFIG_PATH)),
        TH_PROJECT_PATH = path_str(to_wsl_path(api.PROJECT_PATH)),
        TH_PIPELINE_PATH = path_str(to_wsl_path(api.PIPELINE_PATH))
    ))
    job.output_paths.append(to_windows_path(output_path))
    return job

def _add_jobs(batch, jobs):
    jobs = list(filter(lambda job_layer: len(job_layer) != 0, jobs))
    job_indices = [
        [
            batch.add_job(job)
            for job in job_layer
        ]
        for job_layer in jobs
    ]
    if len(jobs) <= 1: return
    prev_layer = job_indices[0]
    for curr_layer in job_indices[1:]:
        for curr_index in curr_layer:
            for prev_index in prev_layer:
                batch.add_dep(curr_index, prev_index)
        prev_layer = curr_layer

def main(
    api,
    department_name,
    pool_name,
    priority
    ) -> int:

    # Parameters
    project_name = api.PROJECT_PATH.name
    user_name = get_user_name()
    timestamp = dt.datetime.now().strftime('%Y/%m/%d %H:%M:%S')

    # Find partment names up to and including the given department name
    department_names = api.config.list_shot_department_names()
    department_names = department_names[:department_names.index(department_name) + 1]

    # Get deadline ready
    farm = Deadline()

    # Batch and jobs
    logging.info(f'Creating batch')
    batch_name = f'{project_name} publish {user_name} {timestamp}'
    job_batch = Batch(batch_name)
    job_layers = []

    # Update shots
    department_layers = {
        department_name: []
        for department_name in department_names
    }
    for sequence_name in api.config.list_sequence_names():
        for shot_name in api.config.list_shot_names(sequence_name):
            down_stream_changed = False
            for department_name in department_names:
                if not _is_submissable(sequence_name, shot_name, department_name): continue
                out_of_date = _is_out_of_date(sequence_name, shot_name, department_name)
                if not down_stream_changed and not out_of_date: continue
                job = _create_publish_job(
                    api,
                    sequence_name,
                    shot_name,
                    department_name,
                    pool_name,
                    priority
                )
                department_layers[department_name].append(job)
                down_stream_changed = True
    for department_name in department_names:
        job_layers.append(department_layers[department_name])
    
    # Export shots

    # Add job layers
    _add_jobs(job_batch, job_layers)

    # Submit
    logging.info(f'Submitting batch')
    farm.submit(job_batch, api.storage.resolve('export:/other/jobs'))

    # Done
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('department_name', type = str)
    parser.add_argument('pool_name', type = str)
    parser.add_argument('priority', type = int)
    args = parser.parse_args()

    # Prepare
    deadline = Deadline()

    # Check department name
    department_name = args.department_name
    department_names = api.config.list_shot_department_names()
    if department_name not in department_names:
        return _error(f'Invalid department name: {department_name}')

    # Check pool name
    pool_name = args.pool_name
    if pool_name not in deadline.list_pools():
        return _error(f'Invalid pool name: {pool_name}')

    # Check priority
    priority = args.priority
    if priority < 0 or priority > 100:
        return _error(f'Invalid priority: {priority}')
    
    # Run main
    return main(
        api,
        department_name,
        pool_name,
        priority
    )

if __name__ == "__main__":
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())