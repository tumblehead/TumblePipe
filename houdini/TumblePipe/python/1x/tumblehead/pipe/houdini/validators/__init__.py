"""USD Stage Validation System - extensible validation for Houdini LOPs.

Usage:
    from tumblehead.pipe.houdini.validators import validate_stage, ValidationResult

    # Run all registered validators
    result = validate_stage(stage.GetPseudoRoot())
    if not result.passed:
        print(result.format_message())

    # Run department-specific validators
    from tumblehead.pipe.houdini.validators import validate_stage_for_department

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

    Loads validators from the project config directory at:
        config:/validators/{entity_context}/{department}/validators.py

    If no department-specific validators are found, falls back to
    running all built-in validators.

    For shot departments, 'shot_root_prims' is automatically included
    to enforce stage structure conventions.

    Args:
        root: USD stage pseudo root prim
        entity_context: 'shots' or 'assets'
        department: Department name (e.g., 'render', 'model')
        validation_context: Optional context dict passed to validators.
                           May contain 'entity_uri' for asset structure validation.

    Returns:
        ValidationResult with all issues found
    """
    from tumblehead.config.validation import get_validator_names_for_department

    # Get department-specific validator names
    validator_names = get_validator_names_for_department(entity_context, department)

    if not validator_names:
        # No department-specific config, run all validators
        return _registry.run(root, context=validation_context)

    # For shot departments, always include shot_root_prims validator
    if entity_context == 'shots' and 'shot_root_prims' not in validator_names:
        validator_names = ['shot_root_prims'] + validator_names

    # Run only the validators specified for this department
    return _registry.run(root, validator_names, context=validation_context)


__all__ = [
    'ValidationSeverity',
    'ValidationIssue',
    'ValidationResult',
    'StageValidatorRegistry',
    'get_registry',
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
