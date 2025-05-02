from typing import Optional
from pathlib import Path
import asyncio
import json

from tumblehead.api import path_str, to_windows_path
from tumblehead.util import ipc
from tumblehead.apps import app

RUNNER_SCRIPT_PATH = Path(__file__).parent / 'mayapy_runner.py'

def _find_mayapy_versions(root_path) -> dict[str, Path]:
    result = dict()
    autodesk_path = root_path / 'Program Files' / 'Autodesk'
    if not autodesk_path.exists(): return result
    for maya_version in autodesk_path.iterdir():
        if not maya_version.is_dir(): continue
        if len(maya_version.name) != 8: continue
        if not maya_version.name.startswith('Maya'): continue
        year = maya_version.name[4:]
        if not year.isdigit(): continue
        maya_version_path = maya_version / 'bin'
        for mayapy_exe in maya_version_path.iterdir():
            if mayapy_exe.name != 'mayapy.exe': continue
            result[year] = mayapy_exe
    return result

class MayaPy:
    def __init__(self, version: str):
        
        # Check if mayapy is available
        _versions = _find_mayapy_versions(Path('/mnt/c'))
        _versions.update(_find_mayapy_versions(Path('/mnt/e')))
        assert version in _versions, f'Mayapy {version} is not available'
        
        # Members
        self._version = version
        self._mayapy = _versions[version]

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
                path_str(self._mayapy),
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