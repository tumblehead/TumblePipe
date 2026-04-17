"""Common environment variable setup for farm tasks."""
import os
from typing import Optional

from tumblepipe.api import get_user_name, path_str, to_windows_path, default_client
from tumblepipe.resolver import plugin_resources_path


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


def get_base_env(api=None, houdini_major: int = 21):
    """
    Get base environment variables for farm tasks.

    Includes:
    - Core pipeline paths (TH_USER, TH_CONFIG_PATH, TH_PROJECT_PATH, etc.)
    - Color management (OCIO)
    - USD resolver configuration (PXR_PLUGINPATH_NAME, TH_EXPORT_PATH)

    Args:
        api: Pipeline API client. If None, uses default_client().
        houdini_major: major version of Houdini the farm slot will run;
            determines which tumbleResolver build the env points at.

    Returns:
        Dict of environment variables for farm task.
    """
    if api is None:
        api = default_client()

    resources_path = path_str(to_windows_path(
        plugin_resources_path(api.PIPELINE_PATH, houdini_major=houdini_major)
    ))

    return dict(
        # Core pipeline variables
        TH_USER=get_user_name(),
        TH_CONFIG_PATH=path_str(to_windows_path(api.CONFIG_PATH)),
        TH_PROJECT_PATH=path_str(to_windows_path(api.PROJECT_PATH)),
        TH_PIPELINE_PATH=path_str(to_windows_path(api.PIPELINE_PATH)),
        OCIO=os.environ['OCIO'],
        # USD Resolver variables
        TH_EXPORT_PATH=path_str(to_windows_path(api.PROJECT_PATH / 'export')),
        PXR_PLUGINPATH_NAME=resources_path,
    )
