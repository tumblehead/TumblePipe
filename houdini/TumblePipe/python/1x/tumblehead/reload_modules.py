"""
Module reload helper for Tumblehead pipeline

Run this in Houdini's Python shell to force reload pipeline modules
after making code changes.

Usage in Houdini Python shell:
    import tumblehead.reload_modules
    tumblehead.reload_modules.reload_all()
"""

import importlib
import sys


def reload_all():
    """Reload all tumblehead.pipe.houdini.lops modules"""

    modules_to_reload = [
        'tumblehead.pipe.houdini.lops.export_layer',
        'tumblehead.pipe.houdini.lops.export_kit_layer',
        'tumblehead.pipe.houdini.lops.import_layer',
        'tumblehead.pipe.houdini.lops.import_kit_layer',
        'tumblehead.pipe.houdini.lops.layer_split',
        'tumblehead.pipe.houdini.lops.build_shot',
    ]

    print("="*60)
    print("Reloading Tumblehead pipeline modules...")
    print("="*60)

    for module_name in modules_to_reload:
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
                print(f"[OK] Reloaded: {module_name}")
            except Exception as e:
                print(f"[FAIL] Failed to reload {module_name}: {e}")
        else:
            print(f"  Skipped: {module_name} (not loaded)")

    print("="*60)
    print("Reload complete!")
    print("="*60)


if __name__ == "__main__":
    reload_all()
