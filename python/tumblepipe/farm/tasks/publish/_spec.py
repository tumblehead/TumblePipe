"""Config schema for the publish task family.

Shared by ``publish.py`` (worker CLI), ``task.py`` (submit-side task builder)
and ``farm/jobs/houdini/publish/job.py`` — they all validate the same config
and used to carry identical copies of this validator. The task builder
additionally requires ``workfile_path`` (it bundles the workfile with the
task); by worker run time that key is no longer part of the contract.

config = {
    'entity': {
        'uri': 'entity:/assets/category/asset' | 'entity:/shots/sequence/shot',
        'department': 'string'
    },
    'settings': {
        'priority': 'int',
        'pool_name': 'string',
        'first_frame': 'int',
        'last_frame': 'int'
    },
    'tasks': {
        'publish': {
            'downstream_departments': ['list']
        }
    },
    'workfile_path': 'string'  # task builder only
}
"""

from tumblepipe.farm._common import (
    valid_entity,
    check_str,
    check_int,
    check_list,
)


def _valid_settings(settings):
    if not isinstance(settings, dict): return False
    if not check_int(settings, 'priority'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_int(settings, 'first_frame'): return False
    if not check_int(settings, 'last_frame'): return False
    return True


def _valid_publish(publish):
    if not isinstance(publish, dict): return False
    if 'downstream_departments' in publish:
        if not check_list(publish, 'downstream_departments'): return False
        # Validate each department name is a string
        for dept in publish['downstream_departments']:
            if not isinstance(dept, str): return False
    return True


def _valid_tasks(tasks):
    if not isinstance(tasks, dict): return False
    if 'publish' in tasks:
        if not _valid_publish(tasks['publish']): return False
    return True


def is_valid_config(config):
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if 'settings' not in config: return False
    if not _valid_settings(config['settings']): return False
    if 'tasks' not in config: return False
    if not _valid_tasks(config['tasks']): return False
    return True


def is_valid_build_config(config):
    """The task builder's stricter contract: config plus the workfile path."""
    if not is_valid_config(config): return False
    if not check_str(config, 'workfile_path'): return False
    return True
