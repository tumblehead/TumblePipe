"""The project setting *Layout in Animate (Beta)*.

On, a shot's layout animates on the USD stage in an APEX Animate LOP (a
control per component, characters on their rigs, a camera rig), publishes a
baked layer and its keys, and animation copies layout's keys per character.
Off, layout and animation work as they always have. Off by default: the APEX
LOPs it builds on are Beta in Houdini 22.0.

Stored as ``layout_in_animate`` on ``config:/project``; a project that never
set it reads as off, so it needs no migration.
"""

from tumblepipe.api import api
from tumblepipe.config.store import JsonConfigStore
from tumblepipe.util.uri import Uri

PROJECT_URI = Uri.parse('config:/project')
KEY = 'layout_in_animate'


def enabled_in(store: JsonConfigStore) -> bool:
    """Whether the project of ``store`` has Layout in Animate on."""
    properties = store.get_properties(PROJECT_URI)
    if properties is None:
        return False
    return properties.get(KEY) is True


def set_enabled_in(store: JsonConfigStore, on: bool) -> None:
    """Turn Layout in Animate on or off for the project of ``store``."""
    own = dict(store.get_own_properties(PROJECT_URI) or {})
    own[KEY] = bool(on)
    store.set_own_properties(PROJECT_URI, own)


# --------------------------------------------------------------------------- #
# The active project
# --------------------------------------------------------------------------- #
# The pipeline, the HDAs and the project templates work in the active project
# and call these; the Asset Browser, which works on any project, calls the
# ``_in`` cores with that project's store.


def enabled() -> bool:
    return enabled_in(api.config)


def set_enabled(on: bool) -> None:
    set_enabled_in(api.config, on)
