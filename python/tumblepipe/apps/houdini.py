"""Houdini's command-line tools -- hython, husk and the bundled image tools --
from the install of the Houdini major a farm job was made in."""

from typing import Optional
from pathlib import Path
import os

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

def _is_valid_version(version: str) -> bool:
    if len(version) != 8: return False
    parts = version.split('.')
    if len(parts) != 3: return False
    if not parts[0].isdigit(): return False
    if not parts[1].isdigit(): return False
    if not parts[2].isdigit(): return False
    return True

def _scan_path_for_versions(root_path) -> dict[str, dict[str, Path]]:
    sidefx_path = root_path / 'Program Files' / 'Side Effects Software'
    try:
        if not sidefx_path.exists(): return dict()
    except OSError:
        # Unreadable drive/mount — treat as "no Houdini here"; anything
        # else raising is a real bug and must surface.
        return dict()
    result = dict()
    for houdini_version in sidefx_path.iterdir():
        if not houdini_version.is_dir(): continue
        if not houdini_version.name.startswith('Houdini'): continue
        name = houdini_version.name
        if ' ' in name: name = name.replace(' ', '')
        if len(name) != 15: continue
        version = name[7:]
        if not _is_valid_version(version): continue
        bin_path = houdini_version / 'bin'
        hython_path = bin_path / 'hython.exe'
        husk_path = bin_path / 'husk.exe'
        iconvert_path = bin_path / 'iconvert.exe'
        itilestitch_path = bin_path / 'itilestitch.exe'
        if not hython_path.exists(): continue
        if not husk_path.exists(): continue
        if not iconvert_path.exists(): continue
        usdstitch_path = bin_path / 'usdstitch.cmd'
        # Houdini bundles OpenImageIO's oiiotool and ffmpeg as hoiiotool/hffmpeg,
        # so the farm needs no separate image/video tool installs.
        hoiiotool_path = bin_path / 'hoiiotool.exe'
        hffmpeg_path = bin_path / 'hffmpeg.exe'
        # Houdini's CLI front-end onto the OIDN/OptiX denoisers it bundles.
        # Unlike hython it checks out no license token, which is the whole
        # reason the denoise task drives it instead.
        idenoise_path = bin_path / 'idenoise.exe'
        result[version] = dict(
            hython = hython_path,
            husk = husk_path,
            iconvert = iconvert_path,
            itilestitch = itilestitch_path,
            usdstitch = usdstitch_path,
            hoiiotool = hoiiotool_path,
            hffmpeg = hffmpeg_path,
            idenoise = idenoise_path
        )
    return result

HOUDINI_VERSIONS = None
def _scan_drives_for_versions() -> dict[str, dict[str, Path]]:
    """Every Houdini install under ``<drive>:/Program Files/Side Effects Software``.

    Windows only: the app wrappers drive the ``.exe`` tools of a Windows
    install, the only platform the farm runs them on.
    """
    global HOUDINI_VERSIONS
    if HOUDINI_VERSIONS is not None: return HOUDINI_VERSIONS
    _versions = dict()
    for drive in 'CDEFGH':
        drive_path = Path(f'{drive}:/')
        if not drive_path.exists(): continue
        _versions |= _scan_path_for_versions(drive_path)
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
