"""Render department validators.

This file should be placed at:
    P:\buzz2\_config\validators\shots\render\validators.py

It defines which validators run for the render department.
The validators themselves are defined in the pipeline code; this file
just specifies which ones to run.
"""


def register(registry):
    """Register validators for the render department.

    Available built-in validators:
    - render_var_names: Check RenderVar prim names match aov:name
    - ordered_vars: Check orderedVars matches stage RenderVars
    - render_settings: Check RenderSettings prim exists and is configured
    - render_products: Check RenderProduct prims exist with camera references
    - cameras: Check cameras exist and are valid
    - rest_geometry: Check meshes have rest normals (for model exports)
    - model_structure: Check asset prim exists with 'geo' child (Scope type)
    - lookdev_structure: Check asset prim exists with 'mat' child (Scope type)
    - material_bindings: Check all geometry has material bindings
    - shot_root_prims: Check only allowed root prims exist (for shot departments)

    The registry.register(name, validator) call just records which validators
    to run. The second argument is ignored - pass None or the name again.

    Args:
        registry: Object with register(name, validator) method
    """
    # Render department validation suite
    # Note: shot_root_prims is auto-included for all shot departments
    registry.register('render_var_names', None)
    registry.register('ordered_vars', None)
    registry.register('render_settings', None)
    registry.register('render_products', None)
    registry.register('cameras', None)
