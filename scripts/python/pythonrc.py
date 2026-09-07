"""TumblePipe bootstrapper.

Houdini auto-runs ``$HOUDINI_PATH/scripts/python/pythonrc.py`` on startup.
This is the location TumbleTrove, Radial and NodePilot all bootstrap from; the
older ``python3.<x>libs/pythonrc.py`` stubs remain for the radial-menu
registrations they already carry.

What runs here is the TumbleTrove package registration: TumblePipe's identity,
its documentation link, and the pipeline catalog it contributes to the asset
browser. That catalog used to reach the browser by a different route entirely
— ``ASSET_BROWSER_CATALOG_PATH`` pointed at ``asset_browser_catalogs/`` and
TumbleTrove globbed it for a module exporting ``create_catalog()``. Declaring
it means a renamed factory is an ImportError here instead of a catalog that
silently stops appearing, and it means the catalog's settings page is filed
under TumblePipe rather than under whoever hosts the browser.

Best-effort and deferred: TumblePipe must load with or without TumbleTrove,
and nothing here may gate a Houdini launch.
"""

import os
import sys
from pathlib import Path

if os.environ.get("TUMBLEPIPE_BOOTSTRAPPED") != "1":
    os.environ["TUMBLEPIPE_BOOTSTRAPPED"] = "1"

    def _register():
        try:
            from tumblepipe.startup import register_package
            register_package()
        except Exception:
            import traceback
            print("[tumblepipe] package registration failed:")
            traceback.print_exc()

    # `tumblepipe` lives under $TH_PIPELINE_PATH/python, which hpm sets.
    _pipeline_path = os.environ.get("TH_PIPELINE_PATH", "")
    if _pipeline_path:
        _py = str(Path(_pipeline_path) / "python")
        if _py not in sys.path:
            sys.path.insert(0, _py)

    try:
        import hdefereval
        hdefereval.executeDeferred(_register)
    except Exception:
        # No graphical Houdini (hython, a farm job): register inline so the
        # catalog is still declared for headless callers.
        _register()
