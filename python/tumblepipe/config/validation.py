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

from tumblepipe.api import api
from tumblepipe.util.uri import Uri

VALIDATORS_URI = Uri.parse('config:/validators')


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
    if not path.exists():
        return None
    return path


def load_validators_module(path: Path):
    """Dynamically load a validators.py module from path.

    Args:
        path: Path to validators.py file

    Returns:
        Loaded module. Raises if the file exists but is broken (syntax
        error, bad import, raising at module scope) — a broken validators.py
        must fail loudly rather than silently disabling validation. Absence
        is handled upstream by get_validators_path returning None.
    """
    spec = importlib.util.spec_from_file_location('validators', path)
    if spec is None or spec.loader is None:
        raise ImportError(f'Cannot load validators module: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    if not hasattr(module, 'register'):
        return []

    # Use a collector to gather validator names. A register() that raises
    # is a broken validator and propagates — don't mask it as "no validators".
    names = []

    class NameCollector:
        def register(self, name: str, validator: Callable):
            names.append(name)

    module.register(NameCollector())
    return names

