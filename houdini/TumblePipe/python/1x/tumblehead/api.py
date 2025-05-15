from pathlib import Path
import importlib.util
import platform
import getpass
import os

def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def to_wsl_path(path: Path):
    raw_path = str(path).replace('\\', '/')
    if raw_path.startswith('/'): return path
    parts = raw_path.split('/')
    drive = parts[0][:-1].lower()
    return Path('/mnt', drive, *parts[1:])

def to_windows_path(path: Path):
    raw_path = str(path).replace('\\', '/')
    if not raw_path.startswith('/mnt/'): return path
    parts = raw_path.split('/')
    drive = f'{parts[2].upper()}:/'
    return Path(drive, *parts[3:])

def fix_path(path: Path):

    def _expand_windows_short_path(path: Path):
        from ctypes import create_unicode_buffer, windll
        short_path_name = str(path)
        BUFFER_SIZE = 500
        buffer = create_unicode_buffer(BUFFER_SIZE)
        get_long_path_name = windll.kernel32.GetLongPathNameW
        get_long_path_name(short_path_name, buffer, BUFFER_SIZE)
        long_path_name = buffer.value
        return Path(long_path_name)

    if platform.system() == 'Windows':
        return to_windows_path(path)
    return to_wsl_path(path)

def path_str(path: Path):
    return str(path).replace('\\', '/')

class Client:
    def __init__(self, project_path, pipeline_path, config_path):

        # Set project path
        self.PROJECT_PATH = fix_path(project_path)
        assert self.PROJECT_PATH.exists(), (
            'Invalid project path: '
            f'"{self.PROJECT_PATH}"'
        )

        # Set pipeline path
        self.PIPELINE_PATH = fix_path(pipeline_path)
        assert self.PIPELINE_PATH.exists(), (
            'Invalid pipeline path: '
            f'"{self.PIPELINE_PATH}"'
        )

        # Set config path
        self.CONFIG_PATH = fix_path(config_path)
        assert self.CONFIG_PATH.exists(), (
            'Invalid config path: '
            f'"{self.CONFIG_PATH}"'
        )

        # Load naming convention
        self.NAMING_CONVENTION_PATH = config_path / 'naming_convention.py'
        _naming_module = _load_module(self.NAMING_CONVENTION_PATH)
        self.naming = _naming_module.create()

        # Load storage convention
        self.STORAGE_CONVENTION_PATH = config_path / 'storage_convention.py'
        _storage_module = _load_module(self.STORAGE_CONVENTION_PATH)
        self.storage = _storage_module.create()

        # Load config convention
        self.CONFIG_CONVENTION_PATH = config_path / 'config_convention.py'
        _config_module = _load_module(self.CONFIG_CONVENTION_PATH)
        self.config = _config_module.create()

        # Load render convention
        self.RENDER_CONVENTION_PATH = config_path / 'render_convention.py'
        _render_module = _load_module(self.RENDER_CONVENTION_PATH)
        self.render = _render_module.create()

def _env(key):
    assert key in os.environ, f'{key} environment variable not set'
    return fix_path(Path(os.environ[key]))

def is_dev():
    return os.environ.get('TH_DEV', '0') == '1'

def get_project_name():
    project_path = _env('TH_PROJECT_PATH')
    return project_path.name

def get_user_name():
    default_user = getpass.getuser()
    return os.environ.get('TH_USER', default_user)

def get_edit_path():
    return _env('TH_EDIT_PATH')

def get_project_path():
    return _env('TH_PROJECT_PATH')

def get_pipeline_path():
    return _env('TH_PIPELINE_PATH')

def get_config_path():
    return _env('TH_CONFIG_PATH')

def default_client():
    project_path = _env('TH_PROJECT_PATH')
    pipeline_path = _env('TH_PIPELINE_PATH')
    config_path = _env('TH_CONFIG_PATH')
    return Client(project_path, pipeline_path, config_path)