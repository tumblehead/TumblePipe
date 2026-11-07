"""Houdini's command-line tools -- hython, husk and the bundled image tools --
from the install of the Houdini major a farm job was made in."""

from typing import Optional
from pathlib import Path
import os
import re
import sys

from tumblepipe.api import path_str
from tumblepipe.apps import app


# Env var carrying the FULL version (e.g. "22.0.368") of the Houdini instance
# that created a farm job. Captured at submit and forwarded into the job env so
# the worker's plain-python task selects a matching-major Houdini/husk. See
# tumblepipe.farm.env.get_base_env for where it is set.
HOUDINI_VERSION_ENV = 'TH_HOUDINI_VERSION'

# Houdini (and hython) export their own full version into the process env. An
# artist's session never has TH_HOUDINI_VERSION — only farm jobs do.
RUNNING_HOUDINI_VERSION_ENV = 'HOUDINI_VERSION'

def _parse_version_name(version: str | None) -> str | None:
    if not version: return None
    parts = version.split('.')
    if len(parts) != 3: return None
    if not all(part.isdigit() for part in parts): return None
    return version

class HoudiniVersionError(RuntimeError):
    """No usable Houdini version: none given, malformed, or not installed."""


def resolve_houdini_version(explicit: Optional[str] = None) -> str:
    """The full Houdini version (e.g. ``"22.0.368"``) to target.

    A farm job must run against the Houdini *major* it was created in (the
    USD/resolver ABI is per-major), so this is the one place that decides it,
    in priority order:

    1. ``explicit`` — a caller forcing a version.
    2. ``TH_HOUDINI_VERSION`` — a farm job: ``get_base_env`` stamps the
       creating instance's version into the job env at submit.
    3. ``HOUDINI_VERSION`` — the running Houdini or hython, which exports its
       own version (the submit side, and in-session tools like the
       playblast's mp4 encode).

    The first one set wins, and must be a full ``major.minor.build`` version.
    None set is an error: there is no default to guess.
    """
    candidates = (
        ("explicit version", explicit),
        (HOUDINI_VERSION_ENV, os.environ.get(HOUDINI_VERSION_ENV)),
        (RUNNING_HOUDINI_VERSION_ENV, os.environ.get(RUNNING_HOUDINI_VERSION_ENV)),
    )
    for source, value in candidates:
        if not value:
            continue
        version = _parse_version_name(value)
        if version is None:
            raise HoudiniVersionError(
                f"{source} is {value!r}, not a full Houdini version like 22.0.368")
        return version
    raise HoudiniVersionError(
        f"no Houdini version: pass one, or run inside Houdini/hython "
        f"({RUNNING_HOUDINI_VERSION_ENV}) or a farm job ({HOUDINI_VERSION_ENV})")

_VERSION_RE = re.compile(r'(\d+\.\d+\.\d+)$')

def _version_of(name: str) -> str | None:
    """The ``major.minor.build`` an install folder is named for, or None.

    ``Houdini 22.0.457`` (Windows), ``Houdini22.0.457`` (macOS),
    ``hfs22.0.457`` (Linux). A folder named for ``major.minor`` only (Linux's
    ``/opt/hfs22.0`` link) is skipped: the full-version folder beside it is
    the same install.
    """
    match = _VERSION_RE.search(name.replace(' ', ''))
    return match.group(1) if match else None

def _tools_in(bin_path: Path, exe: str, script: str) -> dict[str, Path] | None:
    """The tools of the install whose ``bin`` is ``bin_path``, or None if it
    lacks the ones every job needs."""
    hython_path = bin_path / f'hython{exe}'
    husk_path = bin_path / f'husk{exe}'
    iconvert_path = bin_path / f'iconvert{exe}'
    if not hython_path.exists(): return None
    if not husk_path.exists(): return None
    if not iconvert_path.exists(): return None
    return dict(
        hython = hython_path,
        husk = husk_path,
        iconvert = iconvert_path,
        itilestitch = bin_path / f'itilestitch{exe}',
        usdstitch = bin_path / f'usdstitch{script}',
        # Houdini bundles OpenImageIO's oiiotool and ffmpeg as hoiiotool/hffmpeg,
        # so the farm needs no separate image/video tool installs.
        hoiiotool = bin_path / f'hoiiotool{exe}',
        hffmpeg = bin_path / f'hffmpeg{exe}',
        hffprobe = bin_path / f'hffprobe{exe}',
        # Houdini's CLI front-end onto the OIDN/OptiX denoisers it bundles.
        # Unlike hython it checks out no license token, which is the whole
        # reason the denoise task drives it instead.
        idenoise = bin_path / f'idenoise{exe}',
    )

def _list_dir(path: Path) -> list[Path]:
    """``path``'s entries; none if it is missing or unreadable (an unreadable
    drive or mount is "no Houdini here")."""
    try:
        if not path.is_dir(): return []
        return list(path.iterdir())
    except OSError:
        return []

def _scan_path_for_versions(root_path) -> dict[str, dict[str, Path]]:
    """The Windows installs under ``<root>/Program Files/Side Effects Software``."""
    result = dict()
    for install in _list_dir(root_path / 'Program Files' / 'Side Effects Software'):
        if not install.name.startswith('Houdini'): continue
        version = _version_of(install.name)
        if version is None: continue
        tools = _tools_in(install / 'bin', '.exe', '.cmd')
        if tools is not None: result[version] = tools
    return result

def _scan_macos_versions(root_path: Path) -> dict[str, dict[str, Path]]:
    """The macOS installs under ``root_path`` (``/Applications/Houdini``).

    ``$HFS`` of each is
    ``Houdini<v>/Frameworks/Houdini.framework/Versions/<major.minor>/Resources``,
    whose ``bin`` holds the tools.
    """
    result = dict()
    for install in _list_dir(root_path):
        if not install.name.startswith('Houdini'): continue
        version = _version_of(install.name)
        if version is None: continue
        versions_dir = install / 'Frameworks' / 'Houdini.framework' / 'Versions'
        for framework_version in sorted(_list_dir(versions_dir)):
            tools = _tools_in(framework_version / 'Resources' / 'bin', '', '')
            if tools is not None:
                result[version] = tools
                break
    return result

def _scan_linux_versions(root_path: Path) -> dict[str, dict[str, Path]]:
    """The Linux installs under ``root_path`` (``/opt``): ``hfs<v>/bin``."""
    result = dict()
    for install in _list_dir(root_path):
        if not install.name.startswith('hfs'): continue
        version = _version_of(install.name)
        if version is None: continue
        tools = _tools_in(install / 'bin', '', '')
        if tools is not None: result[version] = tools
    return result

HOUDINI_VERSIONS = None
def _scan_drives_for_versions() -> dict[str, dict[str, Path]]:
    """Every Houdini install in SideFX's default place for this platform.

    Windows: ``<drive>:/Program Files/Side Effects Software/Houdini <v>``.
    macOS: ``/Applications/Houdini/Houdini<v>``. Linux: ``/opt/hfs<v>``.
    The render farm is Windows; the local farm runs on all three.
    """
    global HOUDINI_VERSIONS
    if HOUDINI_VERSIONS is not None: return HOUDINI_VERSIONS
    _versions = dict()
    if sys.platform == 'win32':
        for drive in 'CDEFGH':
            drive_path = Path(f'{drive}:/')
            if not drive_path.exists(): continue
            _versions |= _scan_path_for_versions(drive_path)
    elif sys.platform == 'darwin':
        _versions = _scan_macos_versions(Path('/Applications/Houdini'))
    else:
        _versions = _scan_linux_versions(Path('/opt'))
    HOUDINI_VERSIONS = _versions
    return _versions

def _find_appropriate_version(
    version_name: str,
    versions: dict[str, dict[str, Path]]
    ) -> dict[str, Path]:
    """Pick the best installed Houdini for a requested version.

    A farm job carries the version of the Houdini instance that created it, and
    the resolver/USD ABI is stable within a major. So the guarantee we honour is
    the MAJOR: a job made in Houdini 22 must run against a Houdini 22 install,
    never 21. Within that:

    1. Prefer the same ``major.minor`` at an equal-or-newer build (closest to
       what the artist used).
    2. Otherwise fall back to the newest install sharing the major, so trivial
       build/minor drift across the farm (e.g. worker on 22.0.345, job from
       22.0.368) still runs instead of hard-failing.
    """
    major, minor, build = version_name.split('.')
    def _parse_version(version: str) -> tuple[int, int, int]:
        return tuple(map(int, version.split('.')))
    def _same_major(version: str) -> bool:
        return version.split('.')[0] == major
    def _preferred(version: str) -> bool:
        other_major, other_minor, other_build = version.split('.')
        if other_major != major: return False
        if other_minor != minor: return False
        if int(other_build) < int(build): return False
        return True

    preferred = sorted(filter(_preferred, versions.keys()), key = _parse_version)
    if len(preferred) > 0:
        return versions[preferred[-1]]

    same_major = sorted(filter(_same_major, versions.keys()), key = _parse_version)
    if len(same_major) > 0:
        return versions[same_major[-1]]

    return None

def _get_version(
    version_name: str,
    versions: dict[str, dict[str, Path]]
    ) -> dict[str, Path]:
    """The install to use for ``version_name``; raises if its major has none."""
    if version_name in versions: return versions[version_name]
    found = _find_appropriate_version(version_name, versions)
    if found is None:
        raise HoudiniVersionError(
            f"no installed Houdini {version_name.split('.')[0]}.x for "
            f"{version_name} (found: {', '.join(sorted(versions)) or 'none'})")
    return found

def _find_tool(tool: str, version_name: Optional[str]) -> Path:
    """The path of a bundled Houdini tool, from the install matching ``version_name``."""
    version_name = resolve_houdini_version(version_name)
    return _get_version(version_name, _scan_drives_for_versions())[tool]


class Hython:
    """Runs a script in Houdini's ``hython``."""

    def __init__(self, version_name: Optional[str] = None):
        self._hython = _find_tool('hython', version_name)

    def run(self,
        script_path: Path,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> None:
        app.run([path_str(self._hython), path_str(script_path), *args], cwd, env)


class Husk:
    """Renders a USD file with Houdini's ``husk``."""

    def __init__(self, version_name: Optional[str] = None):
        self._husk = _find_tool('husk', version_name)

    def run(self,
        usd_path: Path,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> None:
        app.run([path_str(self._husk), path_str(usd_path), *args], cwd, env)


class _Tool:
    """A bundled Houdini command-line tool, run with plain arguments."""

    TOOL = None

    def __init__(self, version_name: Optional[str] = None):
        self._path = _find_tool(self.TOOL, version_name)

    def run(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> None:
        app.run([path_str(self._path), *args], cwd, env)


class IConvert(_Tool):
    TOOL = 'iconvert'


class ITileStitch(_Tool):
    TOOL = 'itilestitch'


class OIIOTool(_Tool):
    """Houdini's bundled OpenImageIO ``hoiiotool``. Paths passed in are native."""

    TOOL = 'hoiiotool'

    def call(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> str:
        """Run without echoing and return the output (``--info`` queries)."""
        return app.run([path_str(self._path), *args], cwd, env, echo=False)


class IDenoise(_Tool):
    """Houdini's bundled ``idenoise``.

    The same Intel OIDN the `denoiseai` COP drives, minus Houdini: idenoise
    consumes NO license token (verified by running it with SESI_LMHOST pointed
    at a dead host, where hython fails to license). Paths passed in should be
    native Windows forms.

    Reads its guide planes (`--normal`/`--albedo`) from *inside* the input
    file, so callers must merge the per-AOV EXRs first (`exr.combine_aovs`).
    """

    TOOL = 'idenoise'

    def call(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> str:
        """Run, echoing, and return the output.

        Callers MUST use this rather than `run` when guide planes are in play:
        a `--normal`/`--albedo` name that doesn't resolve only prints
        "Warning: can't find specified AOV '<name>'" and still exits 0, having
        silently denoised unguided. The exit code cannot see that; the text can.
        """
        return app.run([path_str(self._path), *args], cwd, env)


class FFmpeg(_Tool):
    """Houdini's bundled ``hffmpeg``.

    SideFX's ffmpeg ships `libopenh264` (software H.264) rather than `libx264`;
    callers select the encoder explicitly.
    """

    TOOL = 'hffmpeg'


class FFProbe(_Tool):
    """Houdini's bundled ``hffprobe``."""

    TOOL = 'hffprobe'

    def call(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> str:
        """Run without echoing and return the output (a query's answer)."""
        return app.run([path_str(self._path), *args], cwd, env, echo=False)
