"""Houdini Python startup hook for TumblePipe.

Adds `$TH_PIPELINE_PATH/python/1x` to sys.path so `tumblepipe` imports.

The tumbleResolver USD plugin is registered by Houdini itself via the
`PXR_PLUGINPATH_NAME` (and platform DLL search path) entries in
`hpm.toml [env]` — those are applied during Houdini's package-loading
phase, before USD's Plug.Registry first-touches. No Python-side plugin
registration is needed (or possible — Plug.Registry is a one-shot
scanner read once at USD init, well before this hook runs).
"""

import os
import sys
from pathlib import Path


def load():
    pipeline_path = os.environ.get('TH_PIPELINE_PATH')
    if pipeline_path:
        pipeline_path = Path(pipeline_path)
    else:
        # Running from source without TH_PIPELINE_PATH (e.g. dev session via
        # session manager). Houdini adds python3.11libs/ to sys.path, so find
        # the entry that contains this file and use its parent as the package root.
        this_file = Path(__file__).resolve() if '__file__' in dir() else None
        pipeline_path = None
        for p in sys.path:
            sp = Path(p).resolve()
            if this_file and sp == this_file.parent:
                pipeline_path = sp.parent
                break
            if (sp / 'pythonrc.py').exists() and (sp.parent / 'python' / '1x').exists():
                pipeline_path = sp.parent
                break
        if pipeline_path is None:
            return

    packages_path = pipeline_path / 'python' / '1x'
    if packages_path.exists():
        sys.path.insert(0, str(packages_path))


load()
