from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from tumblehead.api import path_str
from tumblehead.apps import app

MICROMAMBA_PATH = Path('~/.local/bin/micromamba').expanduser()

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

def _valid_python_version(version: int) -> bool:
    return version >= 10 and version <= 12

def _script(commands: list[app.Command]) -> bool:
    for command in commands:
        return_code = app.run(command)
        if return_code == 0: continue
        return False
    return True

def _micromamba_available() -> bool:
    return app.run([path_str(MICROMAMBA_PATH), '--version']) == 0

class Micromamba:
    def __init__(self):
        assert _micromamba_available(), 'Micromamba is not available'

    def create_env(self,
        env_name: str,
        version: int,
        env_spec: list[PackageSpec]
        ) -> bool:
        assert _valid_python_version(version), 'Invalid Python version'
        pip_installs = [
            [path_str(MICROMAMBA_PATH), 'run', '-n', env_name, 'pip', 'install', str(package_spec)]
            for package_spec in env_spec
        ]
        success = _script([
            [path_str(MICROMAMBA_PATH), 'create', '-n', env_name, f'python=3.{version}', '-c', 'conda-forge', '-y'],
            [path_str(MICROMAMBA_PATH), 'run', '-n', env_name, 'pip', 'install', 'pip', '--upgrade']
        ] + pip_installs)
        return success
    
    def list_envs(self) -> list[str]:
        lines = app.call([path_str(MICROMAMBA_PATH), 'env', 'list']).splitlines()
        return [line.split()[0] for line in lines[3:]]

    def remove_env(self, env_name):
        return app.run([path_str(MICROMAMBA_PATH), 'env', 'remove', '-n', env_name, '-y']) == 0

    def run(self,
        env_path: Path,
        env_name: str,
        script_path: Path,
        args: list[str],
        env: Optional[dict[str, str]] = None
        ) -> int:
        return app.run(
            [path_str(MICROMAMBA_PATH), 'run', '-n', env_name, 'python', str(script_path)] + args,
            env_path,
            env
        )