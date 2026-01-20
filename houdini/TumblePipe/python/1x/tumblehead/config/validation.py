"""Department-specific validation configuration.

This module provides APIs for discovering and loading validators
that are specific to each department (render, model, etc.).

Validators are organized in the project config directory:
    config:/validators/{context}/{department}/validators.py

Each validators.py file should define a register(registry) function
that registers the department's validators.
"""

import importlib.util
from pathlib import Path
from typing import Callable

from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

VALIDATORS_URI = Uri.parse_unsafe('config:/validators')


def get_validators_path(context: str, department: str) -> Path | None:
    """Get the path to a department's validators.py file.

    Args:
        context: 'shots' or 'assets'
        department: Department name (e.g., 'render', 'model')

    Returns:
        Path to validators.py or None if not found
    """
    validators_uri = VALIDATORS_URI / context / department / 'validators.py'
    path = api.storage.resolve(validators_uri)
    if path is None:
        return None
    if not path.exists():
        return None
    return path


def load_validators_module(path: Path):
    """Dynamically load a validators.py module from path.

    Args:
        path: Path to validators.py file

    Returns:
        Loaded module or None on error
    """
    try:
        spec = importlib.util.spec_from_file_location('validators', path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def get_validator_names_for_department(context: str, department: str) -> list[str]:
    """Get list of validator names for a department.

    Loads the department's validators.py and calls its register()
    function with a temporary registry to collect validator names.

    Args:
        context: 'shots' or 'assets'
        department: Department name (e.g., 'render', 'model')

    Returns:
        List of validator names registered for this department
    """
    path = get_validators_path(context, department)
    if path is None:
        return []

    module = load_validators_module(path)
    if module is None:
        return []

    if not hasattr(module, 'register'):
        return []

    # Use a collector to gather validator names
    names = []

    class NameCollector:
        def register(self, name: str, validator: Callable):
            names.append(name)

    try:
        module.register(NameCollector())
    except Exception:
        return []

    return names


def register_department_validators(registry, context: str, department: str) -> bool:
    """Register all validators for a department into the given registry.

    Loads the department's validators.py and calls its register()
    function with the provided registry.

    Args:
        registry: StageValidatorRegistry to register validators into
        context: 'shots' or 'assets'
        department: Department name (e.g., 'render', 'model')

    Returns:
        True if validators were registered, False otherwise
    """
    path = get_validators_path(context, department)
    if path is None:
        return False

    module = load_validators_module(path)
    if module is None:
        return False

    if not hasattr(module, 'register'):
        return False

    try:
        module.register(registry)
        return True
    except Exception:
        return False


def list_department_validators(context: str) -> dict[str, list[str]]:
    """List all validators for all departments in a context.

    Args:
        context: 'shots' or 'assets'

    Returns:
        Dict mapping department name to list of validator names
    """
    result = {}
    validators_context_uri = VALIDATORS_URI / context
    context_path = api.storage.resolve(validators_context_uri)

    if context_path is None or not context_path.exists():
        return result

    for dept_path in context_path.iterdir():
        if not dept_path.is_dir():
            continue
        department = dept_path.name
        validators = get_validator_names_for_department(context, department)
        if validators:
            result[department] = validators

    return result
