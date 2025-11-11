from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from tumblehead.api import path_str
from tumblehead.apps import app

UV_PATH = Path('~/.local/bin/uv').expanduser()

@dataclass
class PackageSpec:
    name: str
    version: str

    def __str__(self) -> str:
        return f'{self.name}=={self.version}'

def parse_package_spec(spec: str) -> PackageSpec:
    assert '==' in spec, f'Invalid package spec: {spec}'
    name, version = spec.split('==')
    return PackageSpec(name, version)

def _valid_python_version(version: str) -> bool:
    """Validate Python version string (e.g., '3.11', '3.12')"""
    try:
        parts = version.split('.')
        if len(parts) != 2: return False
        major, minor = int(parts[0]), int(parts[1])
        return major == 3 and 10 <= minor <= 13
    except:
        return False

def _script(commands: list[app.Command]) -> bool:
    for command in commands:
        return_code = app.run(command)
        if return_code == 0: continue
        return False
    return True

def _uv_available() -> bool:
    return app.run([path_str(UV_PATH), '--version']) == 0

class UV:
    def __init__(self, cache_dir: Optional[Path] = None):
        assert _uv_available(), 'UV is not available'
        self._cache_dir = cache_dir or Path('/tmp/uv-cache')

    def create_venv(self,
        venv_path: Path,
        version: str,
        package_specs: Optional[list[PackageSpec]] = None
        ) -> bool:
        """Create a virtual environment with UV and install packages"""
        assert _valid_python_version(version), f'Invalid Python version: {version}'

        # Create venv
        commands = [
            [path_str(UV_PATH), 'venv', str(venv_path), '--python', version, '--cache-dir', str(self._cache_dir)]
        ]

        # Install packages if provided
        if package_specs:
            pip_installs = [
                [path_str(UV_PATH), 'pip', 'install', '--python', str(venv_path / 'bin' / 'python'),
                 '--cache-dir', str(self._cache_dir), str(spec)]
                for spec in package_specs
            ]
            commands.extend(pip_installs)

        return _script(commands)

    def install_requirements(self,
        venv_path: Path,
        requirements_path: Path
        ) -> bool:
        """Install packages from requirements.txt into a venv"""
        assert venv_path.exists(), f'Venv does not exist: {venv_path}'
        assert requirements_path.exists(), f'Requirements file not found: {requirements_path}'

        return app.run([
            path_str(UV_PATH), 'pip', 'install',
            '--python', str(venv_path / 'bin' / 'python'),
            '--cache-dir', str(self._cache_dir),
            '-r', str(requirements_path)
        ]) == 0

    def install_packages(self,
        venv_path: Path,
        package_specs: list[PackageSpec]
        ) -> bool:
        """Install specific packages into a venv"""
        assert venv_path.exists(), f'Venv does not exist: {venv_path}'

        for spec in package_specs:
            success = app.run([
                path_str(UV_PATH), 'pip', 'install',
                '--python', str(venv_path / 'bin' / 'python'),
                '--cache-dir', str(self._cache_dir),
                str(spec)
            ]) == 0
            if not success: return False
        return True

    def list_python_versions(self) -> list[str]:
        """List installed Python versions managed by UV"""
        output = app.call([path_str(UV_PATH), 'python', 'list'])
        versions = []
        for line in output.splitlines():
            if line.strip():
                # Parse output like "cpython-3.11.0-linux-x86_64-gnu"
                parts = line.strip().split()
                if parts and parts[0].startswith('cpython-'):
                    version = parts[0].split('-')[1]
                    versions.append(version)
        return versions

    def install_python(self, version: str) -> bool:
        """Install a specific Python version with UV"""
        assert _valid_python_version(version), f'Invalid Python version: {version}'
        return app.run([path_str(UV_PATH), 'python', 'install', version]) == 0

    def run(self,
        venv_path: Path,
        script_path: Path,
        args: list[str],
        cwd_path: Optional[Path] = None,
        env: Optional[dict[str, str]] = None
        ) -> int:
        """Run a script using the venv's Python interpreter"""
        assert venv_path.exists(), f'Venv does not exist: {venv_path}'
        assert script_path.exists(), f'Script not found: {script_path}'

        python_bin = venv_path / 'bin' / 'python'
        command = [str(python_bin), str(script_path)] + args

        return app.run(command, cwd_path, env)

    def sync(self, venv_path: Path, requirements_path: Path) -> bool:
        """Sync venv to exactly match requirements.txt (like pip-sync)"""
        assert venv_path.exists(), f'Venv does not exist: {venv_path}'
        assert requirements_path.exists(), f'Requirements file not found: {requirements_path}'

        return app.run([
            path_str(UV_PATH), 'pip', 'sync',
            '--python', str(venv_path / 'bin' / 'python'),
            '--cache-dir', str(self._cache_dir),
            str(requirements_path)
        ]) == 0

    def compile_requirements(self,
        requirements_in: Path,
        requirements_out: Path,
        universal: bool = True
        ) -> bool:
        """Compile requirements.in to locked requirements.txt"""
        assert requirements_in.exists(), f'Requirements input not found: {requirements_in}'

        command = [
            path_str(UV_PATH), 'pip', 'compile',
            str(requirements_in),
            '-o', str(requirements_out),
            '--cache-dir', str(self._cache_dir)
        ]

        if universal:
            command.append('--universal')

        return app.run(command) == 0
