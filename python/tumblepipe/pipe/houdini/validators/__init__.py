"""USD Stage Validation System - extensible validation for Houdini LOPs.

Usage:
    from tumblepipe.pipe.houdini.validators import validate_stage, ValidationResult

    # Run all registered validators
    result = validate_stage(stage.GetPseudoRoot())
    if not result.passed:
        print(result.format_message())

    # Run department-specific validators
    from tumblepipe.pipe.houdini.validators import validate_stage_for_department

    result = validate_stage_for_department(stage.GetPseudoRoot(), 'shots', 'render')
"""

from .base import (
    ValidationSeverity,
    ValidationIssue,
    ValidationResult,
    StageValidatorRegistry,
)
from .render_vars import validate_render_var_names, validate_ordered_vars
from .render_settings import validate_render_settings, validate_render_products
from .camera import validate_cameras
from .geometry import validate_rest_geometry, validate_material_bindings
from .asset_structure import validate_model_structure, validate_lookdev_structure
from .shot_structure import validate_shot_root_prims

# Global registry instance with all built-in validators
_registry = StageValidatorRegistry()

# Register built-in validators
_registry.register('render_var_names', validate_render_var_names)
_registry.register('ordered_vars', validate_ordered_vars)
_registry.register('render_settings', validate_render_settings)
_registry.register('render_products', validate_render_products)
_registry.register('cameras', validate_cameras)
_registry.register('rest_geometry', validate_rest_geometry)
_registry.register('material_bindings', validate_material_bindings)
_registry.register('model_structure', validate_model_structure)
_registry.register('lookdev_structure', validate_lookdev_structure)
_registry.register('shot_root_prims', validate_shot_root_prims)


# Per-department default validator sets. Keyed by (entity_context, department).
# Project config at config:/validators/{ctx}/{dept}/validators.py overrides these.
_DEFAULT_VALIDATORS: dict[tuple[str, str], list[str]] = {
    ('assets', 'model'): ['model_structure', 'rest_geometry'],
    ('assets', 'blendshape'): ['model_structure'],
    ('assets', 'lookdev'): ['lookdev_structure', 'material_bindings'],
    ('assets', 'rig'): ['model_structure'],
    ('shots', 'layout'): ['shot_root_prims'],
    ('shots', 'environment'): ['shot_root_prims'],
    ('shots', 'animation'): ['shot_root_prims'],
    ('shots', 'crowd'): ['shot_root_prims'],
    ('shots', 'cfx'): ['shot_root_prims'],
    ('shots', 'effects'): ['shot_root_prims'],
    ('shots', 'light'): ['shot_root_prims'],
    ('shots', 'render'): [
        'shot_root_prims',
        'cameras',
        'render_settings',
        'render_products',
        'render_var_names',
        'ordered_vars',
    ],
    ('shots', 'composite'): [],
    ('shots', 'compsite'): [],  # typo-tolerant alias for Growth config
}


def get_default_validator_names(entity_context: str, department: str) -> list[str]:
    """Return the built-in default validator names for a department."""
    return list(_DEFAULT_VALIDATORS.get((entity_context, department), []))


def get_registry() -> StageValidatorRegistry:
    """Get the global validator registry."""
    return _registry


def validate_stage(
    root,
    validators: list[str] | None = None,
    context: dict | None = None
) -> ValidationResult:
    """Validate a USD stage using registered validators.

    This is the main entry point for stage validation.

    Args:
        root: USD stage pseudo root prim
        validators: Optional list of validator names to run. If None, runs all.
        context: Optional context dict passed to validators that accept it.
                 May contain 'entity_uri' for asset structure validation.

    Returns:
        ValidationResult with all issues found
    """
    return _registry.run(root, validators, context=context)


def validate_stage_for_department(
    root,
    entity_context: str,
    department: str,
    validation_context: dict | None = None
) -> ValidationResult:
    """Validate a USD stage using department-specific validators.

    Resolution order:
    1. Project config at config:/validators/{entity_context}/{department}/validators.py
    2. Built-in defaults in _DEFAULT_VALIDATORS (see get_default_validator_names)
    3. Empty list - no validators run

    Args:
        root: USD stage pseudo root prim
        entity_context: 'shots' or 'assets'
        department: Department name (e.g., 'render', 'model')
        validation_context: Optional context dict passed to validators.
                           May contain 'entity_uri' for asset structure validation.

    Returns:
        ValidationResult with all issues found
    """
    from tumblepipe.config.validation import get_validator_names_for_department

    # Project config takes precedence
    validator_names = get_validator_names_for_department(entity_context, department)

    # Fall back to built-in per-department defaults
    if not validator_names:
        validator_names = get_default_validator_names(entity_context, department)

    if not validator_names:
        # No defaults and no project config - skip validation rather than running everything
        return ValidationResult()

    return _registry.run(root, validator_names, context=validation_context)


__all__ = [
    'ValidationSeverity',
    'ValidationIssue',
    'ValidationResult',
    'StageValidatorRegistry',
    'get_registry',
    'get_default_validator_names',
    'validate_stage',
    'validate_stage_for_department',
    'validate_render_var_names',
    'validate_ordered_vars',
    'validate_render_settings',
    'validate_render_products',
    'validate_cameras',
    'validate_rest_geometry',
    'validate_material_bindings',
    'validate_model_structure',
    'validate_lookdev_structure',
    'validate_shot_root_prims',
]
