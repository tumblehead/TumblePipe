from pathlib import Path
import importlib.util
import platform
import getpass
import os
import threading

def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def to_wsl_path(path: Path):
    # Legacy /mnt mapping. The farm no longer bridges any tool to WSL (image/video
    # processing runs on Houdini's native hoiiotool/hffmpeg), so this is only used
    # internally by local_path() for the non-Windows branch.
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

def local_path(path: Path):
    """Native path for the *current* process: Windows form on Windows, /mnt on WSL.

    The canonical path conversion for farm tasks. Use it for everything the
    running python touches itself: LOCAL file IO (`.exists()`, `.stat()`,
    `.open()`, `shutil.copyfile`, `store_json`, `mkdir`) AND arguments to the
    native tools the task drives (husk, hoiiotool, hffmpeg, iconvert) — all run
    in this same process's OS, so they want this process's native path.

    The farm no longer bridges any tool to WSL, so the non-Windows branch
    (`to_wsl_path`, always `/mnt`) has no task callers today.
    """
    if platform.system() == 'Windows':
        return to_windows_path(path)
    return to_wsl_path(path)

def path_str(path: Path):
    return str(path).replace('\\', '/')

class Client:
    def __init__(self, project_path, pipeline_path, config_path):

        # Set project path
        self.PROJECT_PATH = local_path(project_path)
        assert self.PROJECT_PATH.exists(), (
            'Invalid project path: '
            f'"{self.PROJECT_PATH}"'
        )

        # Set pipeline path
        self.PIPELINE_PATH = local_path(pipeline_path)
        assert self.PIPELINE_PATH.exists(), (
            'Invalid pipeline path: '
            f'"{self.PIPELINE_PATH}"'
        )

        # Set config path
        self.CONFIG_PATH = local_path(config_path)
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

def _env(key):
    assert key in os.environ, f'{key} environment variable not set'
    return local_path(Path(os.environ[key]))

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

# Module-level singleton instance with thread safety.
#
# A plain, non-reentrant lock is correct: constructing the Client loads the
# project's naming/storage/config convention modules, and none of them call
# default_client() at import time, so the first caller never reenters while
# holding the lock. The config conventions resolve the client lazily (via the
# ``api`` proxy above) — that is what removed the import-time recursion which
# previously forced an RLock here.
_default_client_instance = None
_default_client_lock = threading.Lock()

def default_client():
    """Return the global shared API client instance.

    Implements thread-safe lazy singleton pattern using double-checked locking.
    First call creates instance, subsequent calls return the same instance.
    This ensures all modules share the same cached data.

    To reset the client (e.g., when switching projects), call reset_default_client().
    """
    global _default_client_instance
    if _default_client_instance is None:
        with _default_client_lock:
            # Double-check after acquiring lock
            if _default_client_instance is None:
                project_path = _env('TH_PROJECT_PATH')
                pipeline_path = _env('TH_PIPELINE_PATH')
                config_path = _env('TH_CONFIG_PATH')
                _default_client_instance = Client(project_path, pipeline_path, config_path)
    return _default_client_instance

class _LazyClient:
    """Attribute-forwarding proxy to the global client (``default_client()``).

    Modules across the package bind this once with::

        from tumblepipe.api import api

    and reference ``api.config`` / ``api.naming`` / ``api.storage`` /
    ``api.PROJECT_PATH`` etc. in their functions exactly as before. Each access
    is forwarded to the *current* ``default_client()``.

    The point is that *importing* such a module must construct nothing. Binding
    ``api = default_client()`` at module load forced ``TH_*`` to be set just to
    import the package, drove the import-time recursion that the default_client
    lock had to be reentrant for, and pinned the module to the first client so
    ``reset_default_client()`` (project switch) left it stale. The proxy is
    inert at import and resolves the cached singleton at the moment of use, so
    it always reflects the live client.

    A bare module-global proxy (not a PEP 562 module ``__getattr__``) is
    required because in-function references compile to a ``LOAD_GLOBAL`` on
    ``api``, which never consults a module ``__getattr__`` — it needs a real
    attribute to find.
    """
    __slots__ = ()

    def __getattr__(self, name):
        return getattr(default_client(), name)


# Inert lazy handle to the global client; safe to import without TH_* set.
api = _LazyClient()

def reset_default_client():
    """Reset the global client instance.

    Call this when environment variables change (e.g., switching projects)
    to force creation of a new Client instance on the next default_client() call.
    """
    global _default_client_instance
    with _default_client_lock:
        _default_client_instance = None

def refresh_global_cache(purpose: str | None = None):
    """Drop the global client's in-memory config cache.

    Args:
        purpose: Specific cache purpose to drop (e.g. 'entity', 'schemas').
                 If None, drops every purpose.

    Reads are coherent — the store reloads any ``db/*.json`` whose stamp
    changed on the next access — so this is no longer required for
    correctness. It is kept as an explicit "discard what you have"; the
    underlying ``refresh_cache`` enumerates the actual cached purposes
    rather than a hardcoded guessed list, so there is nothing to swallow.
    """
    default_client().config.refresh_cache(purpose)