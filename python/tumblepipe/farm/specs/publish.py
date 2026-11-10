"""Config schema of the publish job (``jobs/publish.py``), its worker
(``workers/publish.py`` and its hython half ``workers/publish_houdini.py``)
and the publish pipeline (``pipelines/publish.py``): the job stores its
config as the worker's context, and the worker hands it to hython as is.

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
            'downstream_departments': ['string']    # optional
        },
        'build': {                      # optional: the run owns the build
            'channels': ['string'],     # builds chained after the publish
            'first_frame': 'int',       # (empty: a later publish of the
            'last_frame': 'int'         # run chains them); range both or
        }                               # neither
    },
    'workfile_path': 'string'   # the bundled workfile, relative to the job's data dir
}
"""

from tumblepipe.farm.checks import (
    check_int,
    check_str,
    check_str_list,
    valid_entity,
    valid_frame_range,
)


def _valid_settings(settings) -> bool:
    if not valid_frame_range(settings, step=False): return False
    if not check_int(settings, 'priority'): return False
    if not check_str(settings, 'pool_name'): return False
    return True


def _valid_publish(publish) -> bool:
    if not isinstance(publish, dict): return False
    if 'downstream_departments' in publish:
        if not check_str_list(publish, 'downstream_departments'): return False
    return True


def _valid_build(build) -> bool:
    if not isinstance(build, dict): return False
    if not check_str_list(build, 'channels'): return False
    has_range = ('first_frame' in build, 'last_frame' in build)
    if has_range == (True, True):
        return valid_frame_range(build, step=False)
    return has_range == (False, False)


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not _valid_settings(config.get('settings')): return False
    if not isinstance(config.get('tasks'), dict): return False
    if not _valid_publish(config['tasks'].get('publish')): return False
    if 'build' in config['tasks'] and not _valid_build(config['tasks']['build']):
        return False
    if not check_str(config, 'workfile_path'): return False
    return True


is_valid_context = is_valid_config


def downstream_departments(config) -> list[str]:
    """The departments published after the config's own, in order."""
    # Optional: a plain publish names none.
    return config['tasks']['publish'].get('downstream_departments', [])


def build_task(config) -> dict | None:
    """The staged builds the batch chains after the publish, or None.

    The publish dialog hands its Build USD task to the last publish it
    submits for an entity, so the build waits for the publish and runs once;
    its other publishes of that entity carry an empty one. Either way the
    worker queues no build of its own (it used to, with the dialog's build
    queued beside it: two builds writing the same staged version).
    """
    return config['tasks'].get('build')
