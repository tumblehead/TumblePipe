"""Houdini Python startup hook for TumblePipe.

Adds `$TH_PIPELINE_PATH/python` to sys.path so `tumblepipe` imports, then
delegates all startup registrations (tumbletrove radial menus/actions) to
`tumblepipe.startup` so Houdini 21 and Houdini 22 run identical code —
this stub must stay byte-identical to its sibling in the other
python3.*libs directory.

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
    pipeline_path = Path(os.environ['TH_PIPELINE_PATH'])
    packages_path = pipeline_path / 'python'
    sys.path.insert(0, str(packages_path))

    from tumblepipe.startup import register_radial
    register_radial(pipeline_path)


load()
