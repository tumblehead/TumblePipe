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


def launch_houdini(
    hip_path: Path, env_overrides: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Open ``hip_path`` in a new, detached Houdini of this session's build.

    The child inherits this process's whole environment with
    ``env_overrides`` applied on top (the Asset Browser retargets
    ``TH_PROJECT_PATH`` / ``TH_CONFIG_PATH`` to open another project's
    workfile). A launch that fails raises.
    """
    env = dict(os.environ)
    env.update(env_overrides or {})
    return subprocess.Popen(
        [sys.executable, str(hip_path)], env=env, **_detached(),
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
