"""The Prune Old Exports window grew into Clean Up Project.

    python -m tumblepipe.prune_app

Kept so a TumbleTrove Desktop that still runs ``farm_launcher.py --app
prune`` opens the window it became (``tumblepipe.cleanup.app``), where old
export versions are one category among the others.
"""

from __future__ import annotations

import sys

from tumblepipe.cleanup.app import build_window, main  # noqa: F401

if __name__ == '__main__':
    sys.exit(main())
