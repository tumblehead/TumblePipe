from typing import Optional
from pathlib import Path
import asyncio
import json

from tumblehead.api import path_str, to_windows_path
from tumblehead.util import ipc
from tumblehead.apps import app

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
        if not hython_path.exists(): continue
        if not husk_path.exists(): continue
        result[version] = dict(
            hython = hython_path,
            husk = husk_path
        )
    return result

HOUDINI_VERSIONS = None
def _scan_drives_for_versions() -> dict[str, dict[str, Path]]:
    global HOUDINI_VERSIONS
    if HOUDINI_VERSIONS is not None: return HOUDINI_VERSIONS
    _versions = dict()
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
    def __init__(self, version_name: str):
    
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
            return await app.run_async([
                path_str(self._hython),
                path_str(to_windows_path(RUNNER_SCRIPT_PATH)),
                str(port)
            ], env = env)

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
    def __init__(self, version_name: str):

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
        ) -> int:
        return await app.run_async([
            path_str(self._husk),
            path_str(usd_path),
            *args
        ])

    def run(self,
        usd_path: Path,
        args: list[str]
        ) -> int:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self.run_async(usd_path, args)
        )