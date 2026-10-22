"""Where a project's files live, and where each root goes inside an archive.

``storage.json`` puts some purposes inside the project folder (``assets``,
``shots``, ``groups``, ``export``, ``edit``) and others beside it
(``{project_parent}/{project_name}_render``, ``_cache``, ``_proxy``,
``_turntable``). An archive that only walked the project folder would miss
every rendered frame, so the roots come from the table, never from
assumptions about the folder.

Inside an archive every root is a top-level folder named as it is on disk
when it sits next to the project (``HideAndReek/``, ``HideAndReek_render/``),
so unpacking next to each other reproduces the layout ``storage.json``
expects. A root anywhere else goes under ``_roots/<purpose>/``.
"""

from dataclasses import dataclass
from pathlib import Path

from tumblepipe.storage import Storage, load_table
from tumblepipe.util.uri import Uri

#: The purposes whose roots an archive walks, besides the project folder and
#: its config. ``temp`` is machine-local scratch and ``preset`` lives in the
#: config, so neither is walked on its own.
WALKED_PURPOSES = (
    'assets', 'shots', 'groups', 'export', 'edit',
    'render', 'cache', 'proxy', 'turntable',
)

#: Purposes holding workfile folders.
WORKFILE_PURPOSES = ('assets', 'shots', 'groups')


@dataclass(frozen=True)
class Root:
    """One directory the archive walks."""
    purpose: str            # 'project', 'config', or a storage purpose
    path: Path
    prefix: str             # its top-level folder inside an archive


@dataclass(frozen=True)
class ProjectRoots:
    project_path: Path
    config_path: Path
    roots: tuple[Root, ...]

    @property
    def name(self) -> str:
        return self.project_path.name

    def get(self, purpose: str) -> Root | None:
        for root in self.roots:
            if root.purpose == purpose:
                return root
        return None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def archive_prefix(purpose: str, path: Path, project_path: Path) -> str:
    """The top-level folder ``path`` gets inside an archive.

    The project folder keeps its name. A root inside the project is a
    sub-folder of it. A root beside the project keeps its own name. Anything
    else is filed under ``_roots/<purpose>``.
    """
    if path == project_path:
        return project_path.name
    if _is_within(path, project_path):
        return f'{project_path.name}/{path.relative_to(project_path).as_posix()}'
    if path.parent == project_path.parent:
        return path.name
    return f'_roots/{purpose}'


def project_roots(
    project_path: Path,
    config_path: Path | None = None,
    pipeline_path: Path | None = None,
) -> ProjectRoots:
    """The roots of the project at ``project_path``, from its ``storage.json``."""
    project_path = Path(project_path).resolve()
    config_path = (
        Path(config_path).resolve() if config_path is not None
        else project_path / '_config'
    )
    # Raises StorageTableError for a project without a valid table, which is
    # the right answer: the archive cannot know where renders live without it.
    load_table(config_path)
    storage = Storage(
        project_path,
        Path(pipeline_path) if pipeline_path is not None else project_path,
        config_path,
    )
    roots = [
        Root('project', project_path, project_path.name),
        Root('config', config_path, archive_prefix('config', config_path, project_path)),
    ]
    seen = {project_path, config_path}
    for purpose in WALKED_PURPOSES:
        path = storage.resolve(Uri.parse(f'{purpose}:/')).resolve()
        if path in seen:
            continue
        seen.add(path)
        roots.append(Root(purpose, path, archive_prefix(purpose, path, project_path)))
    return ProjectRoots(project_path, config_path, tuple(roots))
