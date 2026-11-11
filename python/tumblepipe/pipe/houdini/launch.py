"""Starting other Houdini processes from this session: hython workers and
new interactive Houdinis.

Both run this session's own build (``$HFS``, ``sys.executable``) with this
process's environment, so the child sees the same packages, licence and
pipeline context. The one place that knows how to detach a child on each
platform, and where hython lives.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def hython_path() -> Path:
    """This session's hython (``$HFS/bin/hython``)."""
    name = "hython.exe" if sys.platform == "win32" else "hython"
    return Path(os.environ["HFS"]) / "bin" / name


def _detached() -> dict:
    """Popen arguments that let the child outlive this process's callback.

    Windows puts it in its own process group; POSIX gives it its own
    session, so neither a console Ctrl+C nor this Houdini closing takes it
    down.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


# Variables that belong to *this* Houdini session, not to its build or
# pipeline context. TumbleTrove's pythonrc sets TUMBLETROVE_BOOTSTRAPPED
# once per session and skips its whole block (sys.path, package
# registration, the Desktop bridge) when it is already set, so a child that
# inherits it comes up without TumbleTrove. TT_SESSION_* name the Desktop
# session this process registered as; a child must not claim it.
SESSION_ONLY_ENV = (
    'TUMBLETROVE_BOOTSTRAPPED',
    'TT_SESSION_ENDPOINT',
    'TT_SESSION_ID',
    'TT_SESSION_TOKEN',
    'TT_SESSION_NONCE',
)


def child_houdini_env(
    parent: dict[str, str], env_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """The environment a new interactive Houdini gets from ``parent``.

    The parent's environment without its session-only variables
    (``SESSION_ONLY_ENV``), with ``env_overrides`` applied on top.
    """
    env = {k: v for k, v in parent.items() if k not in SESSION_ONLY_ENV}
    env.update(env_overrides or {})
    return env


def launch_houdini(
    hip_path: Path, env_overrides: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Open ``hip_path`` in a new, detached Houdini of this session's build.

    The child inherits this process's environment (see
    ``child_houdini_env``) with ``env_overrides`` applied on top (the Asset
    Browser retargets ``TH_PROJECT_PATH`` / ``TH_CONFIG_PATH`` to open
    another project's workfile). A launch that fails raises.
    """
    env = child_houdini_env(dict(os.environ), env_overrides)
    return subprocess.Popen(
        [sys.executable, hip_path.as_posix()], env=env, **_detached(),
    )


def run_hython(
    script: Path, args: list[str], log_path: Path,
) -> subprocess.CompletedProcess:
    """Run ``hython script *args`` to completion; blocking.

    stdout is captured and returned (a worker reports its results there);
    stderr — tracebacks and Houdini's own chatter — goes to ``log_path``,
    followed by the stdout, so the log reads as the whole run. A non-zero
    exit is the caller's to judge, not raised. Call it off the main thread.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.run(
            [str(hython_path()), str(script), *args],
            stdout=subprocess.PIPE, stderr=log_file,
            text=True, env=dict(os.environ), check=False,
        )
        log_file.write(process.stdout or "")
    return process
