Department Validators Configuration
====================================

This directory contains example validator configuration files.
Copy these to your project config directory to enable department-specific validation.

Setup Instructions:
-------------------

1. Copy the validators directory structure to your project config:

   From: validators_examples/
   To:   P:\buzz2\_config\validators\

   The structure should be:
   P:\buzz2\_config\validators\
   ├── shots/
   │   └── render/
   │       └── validators.py
   └── assets/
       ├── model/
       │   └── validators.py
       └── lookdev/
           └── validators.py

2. Customize the validators.py files to specify which validators
   should run for each department.

Available Built-in Validators:
------------------------------

- render_var_names: Check RenderVar prim names match aov:name attribute
- ordered_vars: Check orderedVars relationship matches stage RenderVars
- render_settings: Check /Render/rendersettings prim exists and is configured
- render_products: Check RenderProduct prims exist with camera references
- cameras: Check cameras exist and render camera is valid
- rest_geometry: Check meshes have rest normals (primvars:rest)
- model_structure: Check asset prim exists with 'geo' child (Scope type)
- lookdev_structure: Check asset prim exists with 'mat' child (Scope type)
- material_bindings: Check all geometry has material bindings
- shot_root_prims: Check only allowed root prims exist (for shot departments)

Adding a New Department:
------------------------

To add validation for a new department:

1. Create the directory: P:\buzz2\_config\validators\{context}\{department}\
   - context: 'shots' or 'assets'
   - department: e.g., 'fx', 'rig', 'comp'

2. Create validators.py with a register() function:

   def register(registry):
       registry.register('validator_name', None)
       # Add more validators as needed

If no validators.py file exists for a department, all built-in
validators will run.

Note: For shot departments, 'shot_root_prims' is automatically included
to enforce stage structure conventions. You don't need to explicitly
register it in your validators.py file.
