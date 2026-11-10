"""The hpm executable the local runner drives, bootstrapped as the farm plugin does.

An artist's machine has no hpm on PATH: TumbleTrove Desktop runs hpm as a
library. So, like ``deadline-hpm-plugin``'s ``_ensure_hpm``, the runner keeps
its own managed binary, downloaded from the hpm GitHub releases, at the
version the farm runs: ``$HPM_VERSION`` (the studio knob the render nodes
read), else the plugin's pinned default. ``$TH_LOCAL_FARM_HPM`` names an
executable outright and skips all of it.

The download verifies HTTPS against ``ca_file()`` when Python's own OpenSSL
has no certificates: Houdini's bundled Python on macOS looks for them on
SideFX's build machine.
"""

from __future__ import annotations

import json
import os
import platform
import ssl
import tempfile
import urllib.request
from pathlib import Path

EXECUTABLE_ENV = 'TH_LOCAL_FARM_HPM'
VERSION_ENV = 'HPM_VERSION'
RELEASES_REPO = '3db-dk/hpm'
# Keep in lockstep with deadline-hpm-plugin's DEFAULT_HPM_VERSION, so a local
# task runs under the hpm a render node would.
DEFAULT_VERSION = 'v0.36.1'
# The operating system's own CA bundles: macOS, Debian/Ubuntu, RHEL/Rocky.
SYSTEM_CA_FILES = (
    '/etc/ssl/cert.pem',
    '/etc/ssl/certs/ca-certificates.crt',
    '/etc/pki/tls/certs/ca-bundle.crt',
)


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


def _certifi_file() -> str | None:
    try:
        import certifi
    except ImportError:
        return None
    return certifi.where()


def ca_file(
    system: str | None = None,
    environ=None,
    verify_paths=None,
    is_file=os.path.isfile,
    certifi_file=_certifi_file,
) -> str | None:
    """The CA bundle to verify HTTPS with, or None for Python's default.

    Houdini 22's bundled Python on macOS has OpenSSL look for certificates
    under ``/Users/prisms/builder-new/...``, SideFX's build machine, so every
    HTTPS download from it failed with CERTIFICATE_VERIFY_FAILED. When
    Python's default finds no bundle and ``SSL_CERT_FILE``/``SSL_CERT_DIR``
    are not set, this is the system's bundle, else certifi's. Windows loads
    its own certificate store whatever OpenSSL's paths say.
    """
    system = (system or platform.system()).lower()
    environ = os.environ if environ is None else environ
    if system == 'windows':
        return None
    if environ.get('SSL_CERT_FILE') or environ.get('SSL_CERT_DIR'):
        return None
    paths = ssl.get_default_verify_paths() if verify_paths is None else verify_paths
    # get_default_verify_paths gives None for a file or folder that is missing.
    if paths.cafile or paths.capath:
        return None
    for candidate in (*SYSTEM_CA_FILES, certifi_file()):
        if candidate and is_file(candidate):
            return candidate
    return None


def ssl_context() -> ssl.SSLContext | None:
    """The context to download with: None (Python's default) unless ``ca_file`` names one."""
    cafile = ca_file()
    return ssl.create_default_context(cafile=cafile) if cafile else None


def _download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` atomically (temp file on the same volume)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'tumblepipe-local-farm'})
    context = ssl_context()
    fd, temp = tempfile.mkstemp(dir=str(dest.parent), prefix='.hpm-dl-')
    try:
        with (
            urllib.request.urlopen(request, timeout=120, context=context) as response,
            os.fdopen(fd, 'wb') as out,
        ):
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
