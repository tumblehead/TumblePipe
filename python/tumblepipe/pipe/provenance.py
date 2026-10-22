"""What produced a pipeline file: the ``producer`` block sidecars carry.

Exports, caches and workfile versions each write a JSON sidecar saying who
made them and when. The ``producer`` block adds what an archive or a rebuild
needs on top: which workfile version and node wrote the file, and which
TumblePipe and Houdini versions were running. Without it a cache on disk
cannot say whether any workfile can write it again, and a rebuild cannot
pick the tools the original ran with.

Everything here is pure and tolerant: a field that cannot be determined is
``None`` (or ``'unknown'`` for a tool version), never an exception — a
sidecar missing one detail is still worth writing, while a raise here would
fail the export or cache it describes.
"""

from functools import lru_cache
from pathlib import Path
import os
import re

from tumblepipe.util.houdini import hou
from tumblepipe.util.uri import Uri

UNKNOWN = 'unknown'

_VERSION_RE = re.compile(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']')


#: This package's own folder (python/tumblepipe/pipe/ -> the package root).
_OWN_ROOT = Path(__file__).resolve().parents[3]


def package_root() -> Path | None:
    """The TumblePipe package folder: ``TH_PIPELINE_PATH``, else this module's own.

    A shell or the Desktop Scripts panel may not set ``TH_PIPELINE_PATH``;
    the HideAndReek archive, written from a shell, recorded its archiver as
    'unknown'. The package this module was loaded from is the answer then.
    """
    raw = os.environ.get('TH_PIPELINE_PATH')
    if raw:
        return Path(raw)
    return _OWN_ROOT if (_OWN_ROOT / 'hpm.toml').is_file() else None


@lru_cache(maxsize=8)
def _read_package_version(root: str) -> str:
    manifest = Path(root) / 'hpm.toml'
    try:
        text = manifest.read_text(encoding='utf-8')
    except OSError:
        return UNKNOWN
    # The first `version = "..."` is [package].version: hpm.toml opens with
    # the [package] table, and later tables use their own keys.
    match = _VERSION_RE.search(text)
    return match.group(1) if match else UNKNOWN


def tumblepipe_version(root: Path | None = None) -> str:
    """The running TumblePipe's version, from its ``hpm.toml``."""
    root = root if root is not None else package_root()
    if root is None:
        return UNKNOWN
    return _read_package_version(str(root))


def houdini_version() -> str:
    """The running Houdini's version; ``'unknown'`` outside Houdini."""
    if hou is None:
        return UNKNOWN
    try:
        return hou.applicationVersionString()
    except Exception:
        return UNKNOWN


def tool_versions(root: Path | None = None) -> dict:
    return dict(
        tumblepipe=tumblepipe_version(root),
        houdini=houdini_version(),
    )


def relative_to_project(path: Path, project_path: Path | None) -> str:
    """``path`` relative to the project, POSIX-style, when it is inside it.

    A path outside the project stays absolute: it is still the truth about
    where the file was, and the archive tool flags it rather than guessing.
    """
    path = Path(path)
    if project_path is not None:
        try:
            return path.resolve().relative_to(Path(project_path).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return path.as_posix()


def workfile_record(
    entity_uri: Uri | None,
    department_name: str | None,
    version_name: str | None,
    hip_path: Path | None,
    project_path: Path | None,
) -> dict | None:
    """The workfile a file came from; None when there is no workfile at all."""
    if hip_path is None and entity_uri is None:
        return None
    return dict(
        uri=str(entity_uri) if entity_uri is not None else None,
        department=department_name,
        version=version_name,
        path=(
            relative_to_project(hip_path, project_path)
            if hip_path is not None else None
        ),
    )


def producer(
    workfile: dict | None,
    node_path: str | None,
    tools: dict | None = None,
) -> dict:
    """The ``producer`` block: workfile, node and tool versions."""
    return dict(
        workfile=workfile,
        node=node_path,
        tools=tools if tools is not None else tool_versions(),
    )


def scene_producer(node_path: str | None) -> dict:
    """The ``producer`` block for a file written from the open scene.

    Only meaningful inside Houdini; reads the open hip and its workfile
    context. A scene that is not a pipeline workfile still records its hip
    path, with no entity.
    """
    workfile = None
    if hou is not None:
        from tumblepipe.pipe.paths import get_workfile_context
        try:
            hip_path = Path(hou.hipFile.path())
        except Exception:
            return producer(None, node_path)
        try:
            context = get_workfile_context(hip_path)
        except Exception:
            context = None
        project_raw = os.environ.get('TH_PROJECT_PATH')
        workfile = workfile_record(
            context.entity_uri if context is not None else None,
            context.department_name if context is not None else None,
            context.version_name if context is not None else None,
            hip_path,
            Path(project_raw) if project_raw else None,
        )
    return producer(workfile, node_path)
