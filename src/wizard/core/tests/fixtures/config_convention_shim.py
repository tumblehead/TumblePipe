"""Project config convention.

The config database engine now lives in the package
(``tumblepipe.config.store.JsonConfigStore``) so that fixes and features
reach every project through a normal package update instead of being frozen
into this per-project file at creation time. This module only wires it up.

If a project needs project-specific config behaviour, subclass
``JsonConfigStore`` here and return that instead.
"""

from tumblepipe.config.store import JsonConfigStore


def create() -> JsonConfigStore:
    return JsonConfigStore()
