"""Single source of truth for the optional ``hou`` module.

``hou`` only exists inside a running Houdini (or hython) process. Everywhere
else — farm workers, the desktop launcher, the test harness — importing it
raises ImportError. Modules that branch on Houdini availability import from
here instead of each re-running their own ``try: import hou`` guard.

- ``hou``               — the module object, or ``None`` outside Houdini.
- ``HOUDINI_AVAILABLE`` — ``True`` iff ``hou`` imported.
- ``require_hou()``     — return ``hou`` or raise, for code that only ever runs
                          inside Houdini and wants a clear error otherwise.

Importing this module is cheap and side-effect-free: the ``import hou`` runs
once here and the result is cached, so ``from tumblepipe.util.houdini import
hou`` elsewhere just binds the already-resolved name.
"""

try:
    import hou
except ImportError:
    hou = None

HOUDINI_AVAILABLE = hou is not None


def require_hou():
    """Return the ``hou`` module, or raise if not running inside Houdini."""
    if hou is None:
        raise RuntimeError("Houdini module not available")
    return hou
