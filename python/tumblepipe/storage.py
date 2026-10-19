"""Where a project's URIs live on disk.

``project:/``, ``pipeline:/`` and ``config:/`` are the directories TumblePipe
is launched with. Every other storage purpose comes from the project's
``_config/storage.json``, a table of path templates::

    {
        "assets": "{project_path}/assets",
        "cache": "{project_parent}/{project_name}_cache",
        "temp": "{local_temp}/th_temp/{project_name}",
        ...
    }

The table is project data, not code: it is the one thing about storage that
legitimately differs between projects (an edit root on another drive, scratch
somewhere else), and a project changes it by editing the file. It must list
exactly :data:`PURPOSES`, and a template may use only :data:`VARIABLES`.

``local_temp`` is machine-local scratch. Everything that publishes stages
there first and copies the finished result to its final home (an export
writes its whole version payload into a temp dir and copies it into
``export/``; a farm render writes its tiles there before the stitch), so it
must not resolve onto the shared project drive: that turns one write into two
over the network. ``TH_TEMP`` overrides the OS temp directory for a machine
whose system drive is small, such as a farm worker with a scratch disk.

Projects made before migration v11 carried a ``storage_convention.py`` module
instead; v11 reads its paths into this table.
"""

import os
import tempfile
from pathlib import Path

from tumblepipe.util.io import load_json
from tumblepipe.util.uri import Uri

#: The project's own storage table, in its config directory.
STORAGE_FILE = 'storage.json'

#: The purposes ``storage.json`` maps. Mirrored by the wizard's
#: ``conventions::PURPOSES``.
PURPOSES = (
    'assets', 'shots', 'groups', 'export', 'cache', 'render', 'turntable',
    'temp', 'proxy', 'preset', 'edit',
)

#: The placeholders a path template may use. Mirrored by the wizard's
#: ``conventions::VARIABLES``.
VARIABLES = (
    'project_path', 'project_parent', 'project_name', 'pipeline_path',
    'config_path', 'home', 'local_temp',
)


class StorageTableError(ValueError):
    """A project's ``storage.json`` is missing or malformed."""


def _variables(project_path: Path, pipeline_path: Path, config_path: Path) -> dict[str, str]:
    values = {
        'project_path': project_path,
        'project_parent': project_path.parent,
        'project_name': project_path.name,
        'pipeline_path': pipeline_path,
        'config_path': config_path,
        'home': Path.home(),
        'local_temp': os.environ.get('TH_TEMP') or tempfile.gettempdir(),
    }
    assert tuple(values) == VARIABLES
    return {name: str(value).replace('\\', '/') for name, value in values.items()}


def load_table(config_path: Path) -> dict[str, str]:
    """The project's storage table, checked against :data:`PURPOSES`."""
    path = config_path / STORAGE_FILE
    if not path.is_file():
        raise StorageTableError(
            f'{path} does not exist. The project config predates layout v11; '
            'run the migration (tt_prepare --migrate, or '
            'scripts/migrate_config.py) to create it.'
        )
    table = load_json(path)
    if not isinstance(table, dict):
        raise StorageTableError(f'{path} is not a JSON object')
    missing = [p for p in PURPOSES if p not in table]
    unknown = [p for p in table if p not in PURPOSES]
    if missing or unknown:
        raise StorageTableError(
            f'{path} must map exactly {", ".join(PURPOSES)}; '
            f'missing {missing}, unknown {unknown}'
        )
    for purpose, template in table.items():
        if not isinstance(template, str):
            raise StorageTableError(f'{path}: {purpose!r} is not a string')
    return table


class Storage:
    """Resolves storage URIs to paths for one project."""

    def __init__(self, project_path: Path, pipeline_path: Path, config_path: Path):
        variables = _variables(project_path, pipeline_path, config_path)
        roots = {
            'project': project_path,
            'pipeline': pipeline_path,
            'config': config_path,
        }
        for purpose, template in load_table(config_path).items():
            try:
                roots[purpose] = Path(template.format_map(variables))
            except (KeyError, ValueError, IndexError) as error:
                raise StorageTableError(
                    f'{config_path / STORAGE_FILE}: {purpose!r} is {template!r}, '
                    f'which is not a template over {", ".join(VARIABLES)} ({error})'
                ) from error
        self._roots = roots

    def resolve(self, uri: Uri) -> Path:
        if not isinstance(uri, Uri):
            raise TypeError(f'Expected Uri, got {type(uri).__name__}')
        if uri.is_wild():
            raise ValueError(f'Cannot resolve a wildcard URI to a path: {uri}')
        root = self._roots.get(uri.purpose)
        if root is None:
            raise ValueError(f'{uri.purpose!r} is not a storage purpose: {uri}')
        return root.joinpath(*uri.segments)
