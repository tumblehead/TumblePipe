"""Open a USD file in usdview, the viewer Houdini bundles under ``$HFS/bin``.

The publish dialog's "View latest export" is the only caller. There is no
viewer setting: usdview ships with every Houdini, so the only thing that can
be missing is the executable itself, and the error says so.
"""

from pathlib import Path
from typing import Optional
import logging
import os
import platform
import subprocess

from tumblepipe.util.houdini import hou


logger = logging.getLogger(__name__)

USD_EXTENSIONS = frozenset({'.usd', '.usda', '.usdc', '.usdz'})


def is_usd_file(path: Path) -> bool:
    """True if ``path`` has a USD extension (any case)."""
    return path.suffix.lower() in USD_EXTENSIONS


def usdview_path(hfs: Path, system: Optional[str] = None) -> Path:
    """Where usdview lives under ``hfs`` on ``system`` (default: this OS).

    Windows ships a ``usdview.cmd`` wrapper that calls hython; Linux and
    macOS ship a ``usdview`` script.
    """
    system = (system or platform.system()).lower()
    name = 'usdview.cmd' if system == 'windows' else 'usdview'
    return Path(hfs) / 'bin' / name


def _current_hfs() -> Optional[str]:
    if hou is not None:
        hfs = hou.getenv('HFS')
        if hfs:
            return hfs
    return os.environ.get('HFS') or None


def find_usdview(hfs: Optional[str] = None) -> Path:
    """The usdview executable of ``hfs`` (default: the running Houdini's).

    Raises:
        FileNotFoundError: ``$HFS`` is not set, or has no usdview in ``bin``.
    """
    hfs = hfs if hfs is not None else _current_hfs()
    if not hfs:
        raise FileNotFoundError("usdview not found: $HFS is not set")
    path = usdview_path(Path(hfs))
    if not path.exists():
        raise FileNotFoundError(f"usdview not found under {Path(hfs) / 'bin'}")
    return path


def launch_usdview(file_path: Path, hfs: Optional[str] = None) -> None:
    """Open ``file_path`` in usdview as a detached process.

    Raises:
        ValueError: ``file_path`` is not a USD file.
        FileNotFoundError: the file, or usdview under ``$HFS/bin``, is missing.
        OSError: the viewer process could not be started.
    """
    if not is_usd_file(file_path):
        raise ValueError(f"{file_path} is not a USD file")
    if not file_path.exists():
        raise FileNotFoundError(f"{file_path} does not exist")
    viewer = find_usdview(hfs)
    subprocess.Popen(
        [str(viewer), str(file_path)],
        cwd=str(viewer.parent),
        env=_add_resolver_env(os.environ.copy()),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    logger.info(f"Launched usdview for {file_path}")


def _add_resolver_env(env: dict) -> dict:
    """Let the out-of-process usdview resolve ``entity:/`` sublayers.

    Prepends the tumbleResolver plugin to ``PXR_PLUGINPATH_NAME`` and the
    pipeline's python to ``PYTHONPATH``.
    """
    from tumblepipe.resolver import plugin_resources_path

    pipeline_path = os.environ.get('TH_PIPELINE_PATH')
    if not pipeline_path or hou is None:
        return env

    # usdview links this session's USD, so the resolver build must match the
    # running Houdini's major (the USD ABI is per-major).
    houdini_major = hou.applicationVersion()[0]
    resolver_resources = plugin_resources_path(pipeline_path, houdini_major=houdini_major)
    if not resolver_resources.exists():
        logger.warning(f"USD resolver resources not found: {resolver_resources}")
        return env

    current_plugin_path = env.get('PXR_PLUGINPATH_NAME', '')
    env['PXR_PLUGINPATH_NAME'] = (
        f"{resolver_resources}{os.pathsep}{current_plugin_path}"
        if current_plugin_path
        else str(resolver_resources)
    )

    pipeline_python = Path(pipeline_path) / 'python'
    if pipeline_python.exists():
        current_python_path = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = (
            f"{pipeline_python}{os.pathsep}{current_python_path}"
            if current_python_path
            else str(pipeline_python)
        )

    return env
