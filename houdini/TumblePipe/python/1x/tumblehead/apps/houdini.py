from typing import Optional
from pathlib import Path
import platform
import asyncio
import json

from tumblehead.api import path_str, to_windows_path
from tumblehead.util import ipc
from tumblehead.apps import app


class UsdStitchError(Exception):
    """Raised when USD stitching fails."""
    pass


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
    found across the chunk directories, preserving the directory structure.

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

    # 1. Stitch main USD files
    main_files = [d / main_filename for d in chunk_dirs]
    output_main = output_dir / main_filename
    stitch_usd_files(main_files, output_main)

    # 2. Find all sidecar USD files across all chunks
    sidecar_files: dict[Path, list[Path]] = {}
    for chunk_dir in chunk_dirs:
        for usd_file in chunk_dir.rglob("*.usd"):
            if usd_file.name == main_filename:
                continue  # Skip main file
            rel_path = usd_file.relative_to(chunk_dir)
            if rel_path not in sidecar_files:
                sidecar_files[rel_path] = []
            sidecar_files[rel_path].append(usd_file)

    # 3. Stitch each sidecar file
    for rel_path, files in sidecar_files.items():
        output_sidecar = output_dir / rel_path
        output_sidecar.parent.mkdir(parents=True, exist_ok=True)

        if len(files) == 1:
            # Only one chunk has this file, just copy it
            shutil.copy(files[0], output_sidecar)
        else:
            # Multiple chunks have this file, stitch them
            stitch_usd_files(files, output_sidecar)


DEFAULT_HOUDINI_VERSION = '21.0.559'
RUNNER_SCRIPT_PATH = Path(__file__).parent / 'houdini_runner.py'

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
    except:
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
        result[version] = dict(
            hython = hython_path,
            husk = husk_path,
            iconvert = iconvert_path,
            itilestitch = itilestitch_path,
            usdstitch = usdstitch_path
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
    major, minor, build = version_name.split('.')
    def _valid_version(version: str) -> tuple[int, int, int]:
        other_major, other_minor, other_build = version.split('.')
        if other_major != major: return False
        if other_minor != minor: return False
        if int(other_build) < int(build): return False
        return True
    def _parse_version(version: str) -> tuple[int, int, int]:
        return tuple(map(int, version.split('.')))
    version_names = list(filter(_valid_version, versions.keys()))
    version_names.sort(key = _parse_version)
    if len(version_names) == 0: return None
    return versions[version_names[-1]]

def _get_version(
    version_name: str,
    versions: dict[str, dict[str, Path]]
    ) -> Optional[dict[str, Path]]:
    if version_name in versions: return versions[version_name]
    return _find_appropriate_version(version_name, versions)

class Hython:
    def __init__(self, version_name: str = DEFAULT_HOUDINI_VERSION):
    
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
    def __init__(self, version_name: str = DEFAULT_HOUDINI_VERSION):

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
    def __init__(self, version_name: str = DEFAULT_HOUDINI_VERSION):

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
    def __init__(self, version_name: str = DEFAULT_HOUDINI_VERSION):

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


class UsdStitch:
    """Wrapper for Houdini's bundled usdstitch tool."""

    def __init__(self, version_name: str = DEFAULT_HOUDINI_VERSION):

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