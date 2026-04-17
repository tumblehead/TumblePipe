"""Houdini Python startup hook for TumblePipe.

Runs once per Houdini session, before any pxr/USD modules are imported
by user code. Responsibilities:
  1. Add `$TH_PIPELINE_PATH/python/1x` to sys.path so `tumblepipe` imports.
  2. Prepend the tumbleResolver plugin resources dir for this Houdini
     major to PXR_PLUGINPATH_NAME so USD discovers our entity:// handler.
"""

import os
import sys
from pathlib import Path


def _add_pipeline_packages(pipeline_path: Path) -> None:
    packages_path = pipeline_path / 'python' / '1x'
    if packages_path.exists():
        sys.path.insert(0, str(packages_path))


def _register_resolver_plugin(pipeline_path: Path) -> None:
    """Prepend the platform/major-specific tumbleResolver to PXR_PLUGINPATH_NAME."""
    try:
        import hou  # type: ignore[import-not-found]
    except ImportError:
        return  # non-UI launch (hython without the module), caller sets env
    try:
        from tumblepipe.resolver import plugin_resources_path
    except ImportError:
        # tumblepipe not importable yet — sys.path insertion failed or
        # the package is mid-install. Nothing to register; USD will log
        # an "unknown URI scheme" warning if an entity:// URI hits it.
        return

    houdini_major = hou.applicationVersion()[0]
    resources = plugin_resources_path(pipeline_path, houdini_major=houdini_major)
    if not resources.exists():
        return  # no binary for this (platform, major); USD falls back

    current = os.environ.get('PXR_PLUGINPATH_NAME', '')
    os.environ['PXR_PLUGINPATH_NAME'] = (
        f"{resources}{os.pathsep}{current}" if current else str(resources)
    )


def load():
    pipeline_path = os.environ.get('TH_PIPELINE_PATH')
    if not pipeline_path:
        return
    pipeline_path = Path(pipeline_path)
    _add_pipeline_packages(pipeline_path)
    _register_resolver_plugin(pipeline_path)


load()
