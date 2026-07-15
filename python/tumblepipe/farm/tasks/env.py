"""Common environment variable setup for farm tasks."""
import os
from pathlib import Path
from typing import Optional

from tumblepipe.api import get_user_name, path_str, to_windows_path, default_client
from tumblepipe.apps.houdini import DEFAULT_HOUDINI_VERSION, HOUDINI_VERSION_ENV
from tumblepipe.resolver import plugin_resources_path
from tumblepipe.util.uri import Uri


def resolve_houdini_version(explicit: Optional[str] = None) -> str:
    """Full Houdini version (e.g. ``"22.0.368"``) the farm env should target.

    A farm job must run against the same Houdini *major* it was created in (the
    USD/resolver ABI is per-major). This is the single point that decides which
    version everything downstream keys on, resolved in priority order:

    1. ``explicit`` — a caller forcing a specific version.
    2. ``TH_HOUDINI_VERSION`` already in the environment — the worker side,
       where ``get_base_env`` stamped the creating instance's version into the
       job env at submit.
    3. The running Houdini/hython's own version — the submit side, where this
       code executes inside the artist's Houdini and ``hou`` is importable.
    4. ``DEFAULT_HOUDINI_VERSION`` — non-farm callers and legacy jobs.
    """
    if explicit:
        return explicit
    from_env = os.environ.get(HOUDINI_VERSION_ENV)
    if from_env:
        return from_env
    try:
        import hou
        major, minor, build = hou.applicationVersion()
        return f'{major}.{minor}.{build}'
    except Exception:
        return DEFAULT_HOUDINI_VERSION


def job_data_dir() -> Path:
    """Absolute dir holding the job's bundled data files (context, archives, ...).

    Under the HPM farm plugin the task runs as `hpm run task`, whose script
    executes in the (worker-local) hpm *manifest* directory, NOT the shared job
    data dir. So files bundled into the job (and addressed relative to the data
    dir) must be resolved against this explicit path, which the plugin exports as
    TH_FARM_DATA. Falls back to the current directory for the legacy in-WSL flow,
    where the task already ran with its CWD set to the data dir — making this a
    no-op change there.
    """
    base = os.environ.get('TH_FARM_DATA')
    return Path(base) if base else Path.cwd()


# Default environment variables to print for debugging
DEFAULT_ENV_VARS = [
    'OCIO',
    'TH_USER',
    'TH_CONFIG_PATH',
    'TH_PROJECT_PATH',
    'TH_PIPELINE_PATH',
    'TH_EXPORT_PATH',
    'PXR_PLUGINPATH_NAME',
    'PYTHONPATH',
]


def print_env(env_vars: Optional[list[str]] = None):
    """Print environment variables for debugging.

    Args:
        env_vars: List of env var names to print. If None, uses DEFAULT_ENV_VARS.
    """
    print(' Environment variables '.center(80, '='))
    if env_vars is None:
        env_vars = DEFAULT_ENV_VARS
    for key in env_vars:
        value = os.environ.get(key, '<not set>')
        print(f'  {key}={value}')


def ocio_value() -> str:
    """Windows-normalized OCIO config path for a spawned tool's environment.

    The native image tools a task drives (iconvert, itilestitch, ...) want the
    OCIO path in this process's OS form, unlike the raw ``os.environ['OCIO']``
    that ``get_base_env`` forwards to the outer Deadline task.
    """
    return path_str(to_windows_path(Path(os.environ['OCIO'])))


def get_hython_env(api=None) -> dict:
    """Environment for a hython child process a farm task spawns to run a script.

    Core pipeline paths + ``HOUDINI_PACKAGE_DIR`` (so the resolver and HDAs load)
    + a Windows-normalized ``OCIO``. Distinct from ``get_base_env``, which builds
    the *outer* Deadline task env (carrying the USD-resolver plugin vars and the
    raw OCIO); this is what such a task hands to the hython it launches.
    """
    if api is None:
        api = default_client()
    return {
        'TH_USER': get_user_name(),
        'TH_CONFIG_PATH': path_str(to_windows_path(api.CONFIG_PATH)),
        'TH_PROJECT_PATH': path_str(to_windows_path(api.PROJECT_PATH)),
        'TH_PIPELINE_PATH': path_str(to_windows_path(api.PIPELINE_PATH)),
        # Only the current package's houdini dir. The legacy per-project
        # `project:/_pipeline/houdini` bundle was dropped: it ships its own
        # OCIO-setting package that Houdini pathsep-concatenates with the
        # package's OCIO into an unreadable multi-path value (the flipbook /
        # viewport-Karma "could not read OCIO profile" failure). NOTE: needs a
        # live farm job to confirm nothing still resolves HDAs/resolver content
        # out of that legacy dir.
        'HOUDINI_PACKAGE_DIR': path_str(to_windows_path(
            api.storage.resolve(Uri.parse_unsafe('pipeline:/houdini'))
        )),
        'OCIO': ocio_value(),
        # Forward the creating instance's version so a nested plain-python
        # resolve (apps.houdini) keeps selecting the same-major Houdini.
        HOUDINI_VERSION_ENV: resolve_houdini_version(),
    }


def get_base_env(api=None, houdini_version: Optional[str] = None):
    """
    Get base environment variables for farm tasks.

    Includes:
    - Core pipeline paths (TH_USER, TH_CONFIG_PATH, TH_PROJECT_PATH, etc.)
    - Color management (OCIO)
    - USD resolver configuration (PXR_PLUGINPATH_NAME, TH_EXPORT_PATH)
    - The creating instance's Houdini version (TH_HOUDINI_VERSION)

    The Houdini version is resolved via ``resolve_houdini_version`` and drives
    two things that must agree on the major: the tumbleResolver build the env
    points at (per-major USD ABI) and, on the worker, which husk/hython the
    plain-python task selects. Building this at submit (inside the artist's
    Houdini) captures their version and stamps it into the returned env, which
    the farm plugin forwards to the worker — so a job made in Houdini 22 runs
    against Houdini 22, never the old hardcoded 21.

    Args:
        api: Pipeline API client. If None, uses default_client().
        houdini_version: force a specific full version (e.g. "22.0.368");
            defaults to the creating instance's version.

    Returns:
        Dict of environment variables for farm task.
    """
    if api is None:
        api = default_client()

    version = resolve_houdini_version(houdini_version)
    houdini_major = int(version.split('.')[0])

    resources_path = path_str(to_windows_path(
        plugin_resources_path(api.PIPELINE_PATH, houdini_major=houdini_major)
    ))

    return {
        # Core pipeline variables
        'TH_USER': get_user_name(),
        'TH_CONFIG_PATH': path_str(to_windows_path(api.CONFIG_PATH)),
        'TH_PROJECT_PATH': path_str(to_windows_path(api.PROJECT_PATH)),
        'TH_PIPELINE_PATH': path_str(to_windows_path(api.PIPELINE_PATH)),
        'OCIO': os.environ['OCIO'],
        # USD Resolver variables
        'TH_EXPORT_PATH': path_str(to_windows_path(api.PROJECT_PATH / 'export')),
        'PXR_PLUGINPATH_NAME': resources_path,
        # Version of the Houdini instance that created the job; the worker's
        # husk/hython selection keys on this (see apps.houdini).
        HOUDINI_VERSION_ENV: version,
    }
