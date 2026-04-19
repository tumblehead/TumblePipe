import os

import hou

DESKTOP_NAME = "TumblePipe"


def _set_default_desktop():
    desktops = {d.name(): d for d in hou.ui.desktops()}
    desktop = desktops.get(DESKTOP_NAME)
    if desktop is None:
        return
    if hou.ui.curDesktop().name() == DESKTOP_NAME:
        return
    desktop.setAsCurrent()


def load():
    _set_default_desktop()

    # Initialize RPC server if in development mode
    if os.environ.get("TH_DEV") == "1":
        try:
            from tumblepipe.rpc.startup import initialize

            initialize()
            print("[Pipeline] RPC system initialized successfully")

        except ImportError as e:
            print(f"[Pipeline] Warning: Could not import RPC module: {e}")

        except Exception as e:
            print(f"[Pipeline] Error initializing RPC system: {e}")
            import traceback

            traceback.print_exc()


load()