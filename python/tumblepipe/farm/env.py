"""The environment a farm job runs in, and the one its worker hands hython."""
import os
from pathlib import Path

from tumblepipe.api import api, get_user_name, path_str
from tumblepipe.apps.houdini import HOUDINI_VERSION_ENV, resolve_houdini_version
from tumblepipe.resolver import plugin_resources_path


def job_data_dir() -> Path:
    """Absolute dir holding the job's bundled data files (context, archives, ...).

    Under the HPM farm plugin the task runs as `hpm run task`, whose script
    executes in the (worker-local) hpm *manifest* directory, NOT the shared job
    data dir. So files bundled into the job (and addressed relative to the data
    dir) must be resolved against this explicit path, which the plugin exports as
    TH_FARM_DATA. A task run without it has no data dir to read from.
    """
    base = os.environ.get('TH_FARM_DATA')
    if not base:
        raise RuntimeError(
            'TH_FARM_DATA is not set: a farm task resolves its bundled files '
            "against the job's data dir, which the Deadline HPM plugin exports "
            'as TH_FARM_DATA. Run the task through the plugin, or set it to '
            "the job's data dir."
        )
    return Path(base)


# The environment a worker logs before it starts
LOGGED_ENV_VARS = [
    'OCIO',
    'TH_USER',
    'TH_CONFIG_PATH',
    'TH_PROJECT_PATH',
    'TH_PIPELINE_PATH',
    'TH_EXPORT_PATH',
    'PXR_PLUGINPATH_NAME',
    'PYTHONPATH',
]


def print_env() -> None:
    """Log the pipeline environment a farm worker runs with."""
    print(' Environment variables '.center(80, '='))
    for key in LOGGED_ENV_VARS:
        print(f"  {key}={os.environ.get(key, '<not set>')}")


def ocio_value() -> str:
    """Windows-normalized OCIO config path for a spawned tool's environment.

    The native image tools a task drives (iconvert, itilestitch, ...) want the
    OCIO path in this process's OS form, unlike the raw ``os.environ['OCIO']``
    that ``get_base_env`` forwards to the outer Deadline task.
    """
    return path_str(Path(os.environ['OCIO']))


def houdini_package_dir(env) -> str:
    """The ``HOUDINI_PACKAGE_DIR`` a farm task's hython loads packages from.

    The job's own: `hpm install` of the job manifest writes one Houdini package
    file per dependency into ``$HPM_PACKAGE_ROOT/.hpm/packages`` — the project's
    packages, TumbleRig included — and `hpm run` exports ``HPM_PACKAGE_ROOT``
    to the task. A task run any other way has no packages to load, so no rig
    could compile: it raises instead of handing hython a directory that loads
    nothing (the pipeline package's ``houdini`` dir, which it does not ship,
    used to stand in).
    """
    root = env.get('HPM_PACKAGE_ROOT')
    if not root:
        raise RuntimeError(
            'HPM_PACKAGE_ROOT is not set: a farm task runs under `hpm run`, '
            "which exports it. Run the task through the Deadline HPM plugin."
        )
    packages = Path(root) / '.hpm' / 'packages'
    if not packages.is_dir():
        raise RuntimeError(
            f'{packages} does not exist: `hpm install` of the job manifest '
            'did not set up its packages.'
        )
    return path_str(packages)


def get_hython_env() -> dict:
    """Environment for a hython child process a farm task spawns to run a script.

    Core pipeline paths + ``HOUDINI_PACKAGE_DIR`` (the job's packages, so the
    resolver, HDAs and the project's other packages load; see
    :func:`houdini_package_dir`) + a Windows-normalized ``OCIO``. Distinct from
    ``get_base_env``, which builds the *outer* Deadline task env (carrying the
    USD-resolver plugin vars and the raw OCIO); this is what such a task hands
    to the hython it launches.
    """
    return {
        'TH_USER': get_user_name(),
        # TumblePipe's package file sets TH_USER = $TT_USER_NAME, and a worker
        # has no TT_USER_NAME: once hython loads the job's packages it would
        # blank the job's user. Passing the account name keeps them agreeing.
        'TT_USER_NAME': get_user_name(),
        'TH_CONFIG_PATH': path_str(api.CONFIG_PATH),
        'TH_PROJECT_PATH': path_str(api.PROJECT_PATH),
        'TH_PIPELINE_PATH': path_str(api.PIPELINE_PATH),
        # The job's packages, as hpm installed them. The legacy per-project
        # `project:/_pipeline/houdini` bundle was dropped: it ships its own
        # OCIO-setting package that Houdini pathsep-concatenates with the
        # package's OCIO into an unreadable multi-path value (the flipbook /
        # viewport-Karma "could not read OCIO profile" failure).
        'HOUDINI_PACKAGE_DIR': houdini_package_dir(os.environ),
        'OCIO': ocio_value(),
        # Forward the creating instance's version so a nested plain-python
        # resolve (apps.houdini) keeps selecting the same-major Houdini.
        HOUDINI_VERSION_ENV: resolve_houdini_version(),
    }


def get_base_env() -> dict:
    """
    The environment a farm job runs in, for the active project.

    Includes:
    - Core pipeline paths (TH_USER, TH_CONFIG_PATH, TH_PROJECT_PATH, etc.)
    - Color management (OCIO)
    - USD resolver configuration (PXR_PLUGINPATH_NAME, TH_EXPORT_PATH)
    - The creating instance's Houdini version (TH_HOUDINI_VERSION)

    The Houdini version is resolved via ``apps.houdini.resolve_houdini_version`` and drives
    two things that must agree on the major: the tumbleResolver build the env
    points at (per-major USD ABI) and, on the worker, which husk/hython the
    plain-python task selects. Building this at submit (inside the artist's
    Houdini) captures their version and stamps it into the returned env, which
    the farm plugin forwards to the worker — so a job made in Houdini 22 runs
    against Houdini 22.
    """
    version = resolve_houdini_version()
    houdini_major = int(version.split('.')[0])

    resources_path = path_str(plugin_resources_path(api.PIPELINE_PATH, houdini_major=houdini_major))

    return {
        # Core pipeline variables
        'TH_USER': get_user_name(),
        'TH_CONFIG_PATH': path_str(api.CONFIG_PATH),
        'TH_PROJECT_PATH': path_str(api.PROJECT_PATH),
        'TH_PIPELINE_PATH': path_str(api.PIPELINE_PATH),
        'OCIO': os.environ['OCIO'],
        # USD Resolver variables
        'TH_EXPORT_PATH': path_str(api.PROJECT_PATH / 'export'),
        'PXR_PLUGINPATH_NAME': resources_path,
        # Version of the Houdini instance that created the job; the worker's
        # husk/hython selection keys on this (see apps.houdini).
        HOUDINI_VERSION_ENV: version,
    }
