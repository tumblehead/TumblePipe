from pathlib import Path
import platform
import sys
import os

def load():

    # Paths
    pipeline_path = Path(os.environ['TH_PIPELINE_PATH'])
    package_path = pipeline_path / 'houdini' / 'TumblePipe'
    libs_path = package_path / 'python3.11libs'

    # Add the shared python packages location to sys.path
    packages_path = package_path / 'python' / '1x'
    sys.path.insert(0, str(packages_path))

    # Add the additional python packages location to sys.path
    external_path = (
        libs_path /
        'external' /
        platform.system().lower() /
        'Lib' /
        'site-packages'
    )
    sys.path.insert(0, str(external_path))

    # Set project environment variables
    os.environ['OCIO'] = str(pipeline_path / 'ocio' / 'config.ocio')

load()