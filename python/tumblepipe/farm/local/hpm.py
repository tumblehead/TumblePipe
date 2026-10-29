"""The hpm executable the local runner drives, bootstrapped as the farm plugin does.

An artist's machine has no hpm on PATH: TumbleTrove Desktop runs hpm as a
library. So, like ``deadline-hpm-plugin``'s ``_ensure_hpm``, the runner keeps
its own managed binary, downloaded from the hpm GitHub releases, at the
version the farm runs: ``$HPM_VERSION`` (the studio knob the render nodes
read), else the plugin's pinned default. ``$TH_LOCAL_FARM_HPM`` names an
executable outright and skips all of it.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
import urllib.request
from pathlib import Path

EXECUTABLE_ENV = 'TH_LOCAL_FARM_HPM'
VERSION_ENV = 'HPM_VERSION'
RELEASES_REPO = '3db-dk/hpm'
# Keep in lockstep with deadline-hpm-plugin's DEFAULT_HPM_VERSION, so a local
# task runs under the hpm a render node would.
DEFAULT_VERSION = 'v0.22.2'


class HpmUnavailable(RuntimeError):
    """No hpm executable could be found or downloaded."""


def asset_suffix(system: str | None = None) -> str:
    """The release asset suffix for ``system`` (default: this one)."""
    system = (system or platform.system()).lower()
    if system == 'windows':
        return 'windows-x86_64.exe'
    if system == 'darwin':
        return 'darwin-universal'
    return 'linux-x86_64'


def binary_name(system: str | None = None) -> str:
    system = (system or platform.system()).lower()
    return 'hpm.exe' if system == 'windows' else 'hpm'


def version_target(environ=None) -> str:
    """The hpm release to run, as a ``v``-prefixed tag."""
    environ = os.environ if environ is None else environ
    target = (environ.get(VERSION_ENV) or '').strip() or DEFAULT_VERSION
    if target == 'latest':
        # The plugin can follow "latest"; the local runner does not chase an
        # unvetted release onto artists' machines.
        target = DEFAULT_VERSION
    return target if target.startswith('v') else f'v{target}'


def download_url(tag: str, suffix: str) -> str:
    return f'https://github.com/{RELEASES_REPO}/releases/download/{tag}/hpm-{tag}-{suffix}'


def _download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` atomically (temp file on the same volume)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'tumblepipe-local-farm'})
    fd, temp = tempfile.mkstemp(dir=str(dest.parent), prefix='.hpm-dl-')
    try:
        with urllib.request.urlopen(request, timeout=120) as response, os.fdopen(fd, 'wb') as out:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                out.write(chunk)
        if platform.system().lower() != 'windows':
            os.chmod(temp, 0o755)
        os.replace(temp, dest)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def executable(root_path: Path, environ=None, log=print) -> Path:
    """A usable hpm: ``$TH_LOCAL_FARM_HPM``, else the managed one under ``root_path/hpm``."""
    environ = os.environ if environ is None else environ
    override = (environ.get(EXECUTABLE_ENV) or '').strip()
    if override:
        path = Path(override)
        if not path.is_file():
            raise HpmUnavailable(f'{EXECUTABLE_ENV} points at {override}, which does not exist')
        return path
    managed = Path(root_path) / 'hpm'
    binary = managed / binary_name()
    state_path = managed / 'state.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    tag = version_target(environ)
    if binary.is_file() and state.get('version') == tag:
        return binary
    url = download_url(tag, asset_suffix())
    log(f'Bootstrapping hpm {tag} -> {url}')
    try:
        _download(url, binary)
    except Exception as error:
        if binary.is_file():
            log(f'hpm download failed ({error}); using the existing {state.get("version")}')
            return binary
        raise HpmUnavailable(f'Could not download hpm {tag} from {url}: {error}') from error
    managed.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'version': tag, 'suffix': asset_suffix()}), encoding='utf-8')
    return binary
