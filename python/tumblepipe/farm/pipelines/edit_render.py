"""The Render Edit pipeline: one job that renders a published edit version
into ``render:/edits/<name>/<name>_edit_vNNNN.mp4``.

Only a finished published version renders: a working version can change
under the job, and an unfinished publish has no ``context.json`` yet. One
task, no frame split: the player renders the edit start to end and the
encode needs every frame.

config = {
    'edit': {'uri': 'entity:/edits/Main', 'version': 'v0004'},
    'settings': {
        'pool_name': 'string',
        'priority': 'int'
    }
}

A Farm Submit row of the Edits tab (``batch_submit.submit_entity_batch``)
becomes this config by :func:`config_from_row`.
"""

from pathlib import Path

from tumblepipe import naming
from tumblepipe.api import path_str
from tumblepipe.apps.deadline import Job
from tumblepipe.farm.checks import check_int, check_str
from tumblepipe.farm.jobs import edit_render as edit_render_job
from tumblepipe.farm.pipelines._batch import batch_title, submit_batch
from tumblepipe.farm.specs.edit_render import is_edit_uri
from tumblepipe.pipe import edits
from tumblepipe.util.uri import Uri


class EditRenderError(ValueError):
    """The edit version cannot be rendered."""


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    edit = config.get('edit')
    if not isinstance(edit, dict): return False
    if not is_edit_uri(edit.get('uri')): return False
    if not check_str(edit, 'version'): return False
    if not naming.is_valid_version_name(edit['version']): return False
    settings = config.get('settings')
    if not isinstance(settings, dict): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_int(settings, 'priority'): return False
    return True


def config_from_row(row: dict) -> dict:
    """The pipeline config of a Farm Submit row of the Edits tab:
    ``{'entity': {'uri', ...}, 'settings': {'version', 'pool_name', 'priority'}}``."""
    settings = row['settings']
    return dict(
        edit = dict(uri = row['entity']['uri'], version = settings['version']),
        settings = dict(
            pool_name = settings['pool_name'],
            priority = settings['priority'],
        ),
    )


def job_config(name: str, version: str, pool_name: str, priority: int, output_path: Path) -> dict:
    """The ``jobs/edit_render.py`` config that renders ``name`` at ``version``."""
    return dict(
        title = f'render edit {name} {version}',
        priority = priority,
        pool_name = pool_name,
        edit_uri = str(edits.edit_uri(name)),
        version = version,
        output_path = path_str(output_path),
    )


def build(
    config: dict,
    paths: dict[Path, Path],
    temp_path: Path,
    jobs: dict[str, Job],
    deps: dict[str, list[str]],
    depends_on: list[str] | None = None
    ) -> list[str]:
    """Add the Render Edit job after ``depends_on``; returns it."""
    if not is_valid_config(config):
        raise EditRenderError(f'Invalid Render Edit config: {config!r}')
    name = edits.edit_name(Uri.parse(config['edit']['uri']))
    version = config['edit']['version']
    if version not in edits.published_versions(name):
        raise EditRenderError(
            f'{name} has no finished published version {version}: publish '
            'it from the editor first.'
        )
    settings = config['settings']
    jobs['edit_render'] = edit_render_job.build(job_config(
        name, version, settings['pool_name'], settings['priority'],
        edits.movie_path(name, version),
    ), paths, temp_path)
    deps['edit_render'] = list(depends_on or [])
    return ['edit_render']


def submit(config: dict, paths: dict[Path, Path] | None = None) -> list[str]:
    """Submit the Render Edit of ``config`` as one batch."""
    edit = config['edit']
    return submit_batch(
        batch_title('render edit', edit['uri'], edit['version']),
        build, config, paths, entity=edit['uri'],
    )
