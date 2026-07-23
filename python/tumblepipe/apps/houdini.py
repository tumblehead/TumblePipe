from typing import Optional
from pathlib import Path
import platform
import asyncio
import json
import os

from tumblepipe.api import path_str, to_windows_path
from tumblepipe.util import ipc
from tumblepipe.apps import app


class UsdStitchError(Exception):
    """Raised when USD stitching fails."""
    pass


def calculate_chunks(first_frame: int, last_frame: int, batch_size: int) -> list[tuple[int, int]]:
    """Split an inclusive frame range into contiguous chunks of batch_size.

    Batches are sized on integer frames (not the render step): the export
    always writes on 1s, so [first, last] is tiled into back-to-back
    [start, end] spans whose union is exactly the full range with no gaps.
    ``batch_size <= 0`` means no batching - a single whole-range chunk.

    Shared by the interactive (``pipe.houdini.lops.export_layer``) and farm
    (``farm.tasks.export.export_houdini``) exporters so both slice identically.
    """
    if batch_size <= 0:
        return [(first_frame, last_frame)]
    chunks = []
    current = first_frame
    while current <= last_frame:
        chunk_end = min(current + batch_size - 1, last_frame)
        chunks.append((current, chunk_end))
        current = chunk_end + 1
    return chunks


def flatten_sidecar_directories(export_path: Path) -> None:
    """Flatten Houdini's ``{filename}.usd.textures`` sidecar directories.

    Houdini writes COP-generated textures into a ``{filename}.usd.textures/``
    directory beside the exported USD. Move that content up next to the USD
    and drop the empty directory so the published layer's relative texture
    references resolve and (for batched export) the stitch can carry the
    textures through to the output alongside the main layer.
    """
    import shutil

    for item in export_path.iterdir():
        if item.is_dir() and item.name.endswith('.usd.textures'):
            # Move all contents from sidecar dir to parent
            for sidecar_item in item.iterdir():
                target = export_path / sidecar_item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                sidecar_item.rename(target)
            # Remove empty sidecar directory
            item.rmdir()


def stitch_usd_files(input_files: list[Path], output_file: Path) -> None:
    """Stitch multiple USD files into one using USD Python API.

    Uses pxr.UsdUtils.StitchLayers which properly merges time samples
    from multiple files without spawning external processes.

    Args:
        input_files: List of USD files to stitch together (in order).
        output_file: Path for the combined output file.

    Raises:
        UsdStitchError: If stitching fails.
    """
    from pxr import Sdf, UsdUtils

    if not input_files:
        raise UsdStitchError("No input files provided for stitching")

    # Verify all input files exist
    for f in input_files:
        if not f.exists():
            raise UsdStitchError(f"Input file does not exist: {f}")

    # Open first file as the base layer
    result_layer = Sdf.Layer.FindOrOpen(str(input_files[0]))
    if result_layer is None:
        raise UsdStitchError(f"Failed to open first input file: {input_files[0]}")

    # Stitch remaining files into the result layer
    for input_file in input_files[1:]:
        weak_layer = Sdf.Layer.FindOrOpen(str(input_file))
        if weak_layer is None:
            raise UsdStitchError(f"Failed to open input file: {input_file}")

        # StitchLayers merges weak_layer INTO result_layer
        # Time samples are merged as a union (what we want)
        UsdUtils.StitchLayers(result_layer, weak_layer)

    # Export the stitched result
    result_layer.Export(str(output_file))

    if not output_file.exists():
        raise UsdStitchError(f"Failed to create output file: {output_file}")


def stitch_usd_directories(
    chunk_dirs: list[Path],
    main_filename: str,
    output_dir: Path
) -> None:
    """Stitch multiple chunk directories into one output directory.

    Each chunk directory should contain:
    - A main USD file (main_filename)
    - Optional sidecar directories with USD files

    This function stitches the main USD files and all sidecar USD files
    found across the chunk directories, and carries any non-USD sidecar
    content (e.g. flattened .usd.textures) through to the output,
    preserving the directory structure.

    Args:
        chunk_dirs: List of chunk directories to merge (in order).
        main_filename: Name of the main USD file in each chunk (e.g., "layer.usd").
        output_dir: Output directory for merged result.

    Raises:
        UsdStitchError: If stitching fails.
    """
    import shutil

    if not chunk_dirs:
        raise UsdStitchError("No chunk directories provided")

    output_dir.mkdir(parents=True, exist_ok=True)
    main_rel = Path(main_filename)

    # 1. Stitch main USD files
    main_files = [d / main_filename for d in chunk_dirs]
    output_main = output_dir / main_filename
    stitch_usd_files(main_files, output_main)

    # 2. Group every non-main sidecar file across all chunks by its path
    # relative to its chunk. USD files (.usd) are stitched; everything else
    # (textures, volumes, ...) is carried through so it is not silently
    # dropped. Skip only the top-level main file - a like-named file deeper
    # in a sidecar directory is a distinct layer and must not be skipped.
    usd_sidecars: dict[Path, list[Path]] = {}
    other_sidecars: dict[Path, list[Path]] = {}
    for chunk_dir in chunk_dirs:
        for path in chunk_dir.rglob("*"):
            if not path.is_file():
                continue
            rel_path = path.relative_to(chunk_dir)
            if rel_path == main_rel:
                continue  # top-level main file, already stitched
            bucket = usd_sidecars if path.suffix == ".usd" else other_sidecars
            bucket.setdefault(rel_path, []).append(path)

    # 3. Stitch each USD sidecar (union its time samples across chunks)
    for rel_path, files in usd_sidecars.items():
        output_sidecar = output_dir / rel_path
        output_sidecar.parent.mkdir(parents=True, exist_ok=True)
        if len(files) == 1:
            # Only one chunk has this file, just copy it
            shutil.copy(files[0], output_sidecar)
        else:
            # Multiple chunks have this file, stitch them
            stitch_usd_files(files, output_sidecar)

    # 4. Carry non-USD sidecars through. Frame-varying assets carry a frame
    # number in their name (distinct rel paths, all preserved);
    # frame-independent ones repeat identically per chunk, so last-wins is
    # a straight copy of the same bytes.
    for rel_path, files in other_sidecars.items():
        output_sidecar = output_dir / rel_path
        output_sidecar.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(files[-1], output_sidecar)


DEFAULT_HOUDINI_VERSION = '21.0.559'
RUNNER_SCRIPT_PATH = Path(__file__).parent / 'houdini_runner.py'

# Env var carrying the FULL version (e.g. "22.0.368") of the Houdini instance
# that created a farm job. Captured at submit and forwarded into the job env so
# the worker's plain-python task selects a matching-major Houdini/husk. See
# tumblepipe.farm.tasks.env.get_base_env for where it is set.
HOUDINI_VERSION_ENV = 'TH_HOUDINI_VERSION'

def _resolve_default_version() -> str:
    """The Houdini version an app wrapper should target when none is given.

    Prefers the creating instance's version forwarded through the job env
    (``TH_HOUDINI_VERSION``); falls back to ``DEFAULT_HOUDINI_VERSION`` for
    non-farm callers and legacy jobs that predate the env var.
    """
    return os.environ.get(HOUDINI_VERSION_ENV, DEFAULT_HOUDINI_VERSION)

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
        # Houdini bundles OpenImageIO's oiiotool and ffmpeg as hoiiotool/hffmpeg.
        # Using these (instead of a WSL-bridged oiiotool/ffmpeg) lets the farm
        # run image/video processing natively in Windows python — no WSL.
        hoiiotool_path = bin_path / 'hoiiotool.exe'
        hffmpeg_path = bin_path / 'hffmpeg.exe'
        # Houdini's CLI front-end onto the OIDN/OptiX denoisers it bundles.
        # Unlike hython it checks out no license token, which is the whole
        # reason the denoise task drives it instead (designs/denoise-without-hython.md).
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
    global HOUDINI_VERSIONS
    if HOUDINI_VERSIONS is not None: return HOUDINI_VERSIONS
    _versions = dict()

    if platform.system() == 'Windows':
        # Windows: scan drive letters directly
        for drive in 'CDEFGH':
            drive_path = Path(f'{drive}:/')
            if not drive_path.exists(): continue
            _versions |= _scan_path_for_versions(drive_path)
    else:
        # WSL/Linux: scan mounted drives
        for drive in 'abcdefgh':
            drive_path = Path(f'/mnt/{drive}')
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
    ) -> Optional[dict[str, Path]]:
    if version_name in versions: return versions[version_name]
    return _find_appropriate_version(version_name, versions)

class Hython:
    def __init__(self, version_name: Optional[str] = None):
        version_name = version_name or _resolve_default_version()
    
        # Check if hython is available
        _versions = _scan_drives_for_versions()
        version = _get_version(version_name, _versions)
        if version is None:
            assert False, 'No valid Houdini version was found'

        # Members
        self._version = version_name
        self._hython = version['hython']
    
    async def run_async(self,
        script_path: Path,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:

        async def _from_runner(_message):
            return json.dumps({
                'cwd': None if cwd is None else path_str(cwd),
                'env': env,
                'path': path_str(script_path),
                'args': args
            })
        
        port = ipc.free_port()
        async with ipc.Server('localhost', port, _from_runner):
            return await app.run_async(
                [
                    path_str(self._hython),
                    path_str(to_windows_path(RUNNER_SCRIPT_PATH)),
                    str(port)
                ],
                env = env
            )

    def run(self,
        script_path: Path,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self.run_async(script_path, args, cwd, env)
        )

class Husk:
    def __init__(self, version_name: Optional[str] = None):
        version_name = version_name or _resolve_default_version()

        # Check if husk is available
        _versions = _scan_drives_for_versions()
        version = _get_version(version_name, _versions)
        if version is None:
            assert False, 'No valid Houdini version was found'

        # Members
        self._version = version_name
        self._husk = version['husk']
    
    async def run_async(self,
        usd_path: Path,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        return await app.run_async(
            [
                path_str(self._husk),
                path_str(usd_path),
                *args
            ],
            cwd = cwd,
            env = env
        )

    def run(self,
        usd_path: Path,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self.run_async(usd_path, args, cwd, env)
        )

class IConvert:
    def __init__(self, version_name: Optional[str] = None):
        version_name = version_name or _resolve_default_version()

        # Check if iconvert is available
        _versions = _scan_drives_for_versions()
        version = _get_version(version_name, _versions)
        if version is None:
            assert False, 'No valid Houdini version was found'
        
        # Members
        self._version = version_name
        self._iconvert = version['iconvert']
    
    async def run_async(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        return await app.run_async(
            [
                path_str(self._iconvert),
                *args
            ],
            cwd = cwd,
            env = env
        )

    def run(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self.run_async(args, cwd, env)
        )

class ITileStitch:
    def __init__(self, version_name: Optional[str] = None):
        version_name = version_name or _resolve_default_version()

        # Check if iconvert is available
        _versions = _scan_drives_for_versions()
        version = _get_version(version_name, _versions)
        if version is None:
            assert False, 'No valid Houdini version was found'

        # Members
        self._version = version_name
        self._itilestitch = version['itilestitch']

    async def run_async(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        return await app.run_async(
            [
                path_str(self._itilestitch),
                *args
            ],
            cwd = cwd,
            env = env
        )

    def run(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self.run_async(args, cwd, env)
        )


class OIIOTool:
    """Wrapper for Houdini's bundled OpenImageIO `hoiiotool`.

    Runs natively (no WSL bridge): the worker is Windows python and the tool is
    a Windows binary, so paths passed in should be native (`local_path`/Windows),
    not `/mnt` forms.
    """
    def __init__(self, version_name: Optional[str] = None):
        version_name = version_name or _resolve_default_version()
        version = _get_version(version_name, _scan_drives_for_versions())
        if version is None:
            assert False, 'No valid Houdini version was found'
        self._version = version_name
        self._oiiotool = version['hoiiotool']

    def run(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        return app.run([path_str(self._oiiotool), *args], cwd, env)

    def call(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> str:
        return app.call([path_str(self._oiiotool), *args], cwd, env)


class IDenoise:
    """Wrapper for Houdini's bundled `idenoise`.

    The same Intel OIDN the `denoiseai` COP drives, minus Houdini: idenoise
    consumes NO license token (verified by running it with SESI_LMHOST pointed
    at a dead host, where hython fails to license). Runs natively, so paths
    passed in should be native Windows forms.

    Reads its guide planes (`--normal`/`--albedo`) from *inside* the input
    file, so callers must merge the per-AOV EXRs first (`exr.combine_aovs`).
    """
    def __init__(self, version_name: Optional[str] = None):
        version_name = version_name or _resolve_default_version()
        version = _get_version(version_name, _scan_drives_for_versions())
        if version is None:
            assert False, 'No valid Houdini version was found'
        self._version = version_name
        self._idenoise = version['idenoise']

    def run(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        return app.run([path_str(self._idenoise), *args], cwd, env)

    def call(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> tuple[int, str]:
        """Run and return `(exit_code, output)`.

        Callers MUST use this rather than `run` when guide planes are in play:
        a `--normal`/`--albedo` name that doesn't resolve only prints
        "Warning: can't find specified AOV '<name>'" and still exits 0, having
        silently denoised unguided. The exit code cannot see that; the text can.
        """
        return app.run_capture([path_str(self._idenoise), *args], cwd, env)


class FFmpeg:
    """Wrapper for Houdini's bundled `hffmpeg`.

    SideFX's ffmpeg ships `libopenh264` (software H.264) rather than `libx264`;
    callers select the encoder explicitly. Runs natively (no WSL bridge).
    """
    def __init__(self, version_name: Optional[str] = None):
        version_name = version_name or _resolve_default_version()
        version = _get_version(version_name, _scan_drives_for_versions())
        if version is None:
            assert False, 'No valid Houdini version was found'
        self._version = version_name
        self._ffmpeg = version['hffmpeg']

    def run(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        return app.run([path_str(self._ffmpeg), *args], cwd, env)


class UsdStitch:
    """Wrapper for Houdini's bundled usdstitch tool."""

    def __init__(self, version_name: Optional[str] = None):
        version_name = version_name or _resolve_default_version()

        # Check if usdstitch is available
        _versions = _scan_drives_for_versions()
        version = _get_version(version_name, _versions)
        if version is None:
            assert False, 'No valid Houdini version was found'

        # Members
        self._version = version_name
        self._usdstitch = version['usdstitch']

    async def run_async(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        return await app.run_async(
            [
                path_str(self._usdstitch),
                *args
            ],
            cwd = cwd,
            env = env
        )

    def run(self,
        args: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self.run_async(args, cwd, env)
        )

    def stitch(self,
        input_files: list[Path],
        output_file: Path,
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        """Stitch multiple USD files into one.

        Args:
            input_files: List of USD files to stitch together.
            output_file: Path for the combined output file.
            cwd: Optional working directory.
            env: Optional environment variables.

        Returns:
            Exit code (0 for success).
        """
        args = [path_str(f) for f in input_files] + ['-o', path_str(output_file)]
        return self.run(args, cwd=cwd, env=env)