"""Config schema of the composite pipeline (``pipelines/composite.py``,
whose docstring shows the config): a comp workfile's export_comp nodes
cooked on the farm, then the review chain."""

from tumblepipe.farm.checks import (
    check_bool,
    check_int,
    check_str,
    valid_entity,
    valid_frame_range,
)
from tumblepipe.farm.specs.composite import valid_imports, valid_renders
from tumblepipe.farm.specs.render_pipeline import valid_preview_frame_list

COMPOSITE_TASK_KEYS = ('partial_composite', 'full_composite')


def _valid_settings(settings) -> bool:
    if not valid_frame_range(settings): return False
    if not check_str(settings, 'user_name'): return False
    if not check_str(settings, 'purpose'): return False
    if not check_int(settings, 'priority'): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_str(settings, 'input_path'): return False
    if not check_int(settings, 'batch_size'): return False
    if not check_bool(settings, 'copy_to_edit'): return False
    if not valid_preview_frame_list(settings, settings['step_size']): return False
    if 'reads_check_render' in settings and not check_bool(settings, 'reads_check_render'):
        return False
    return True


def valid_exports(exports) -> bool:
    """A non-empty list of ``{node_path, layer}``, one node per layer. A
    node path may be empty (a re-comp names the layer only)."""
    if not isinstance(exports, list) or len(exports) == 0: return False
    for export in exports:
        if not isinstance(export, dict): return False
        if not check_str(export, 'node_path'): return False
        if not check_str(export, 'layer'): return False
    layers = [export['layer'] for export in exports]
    return len(set(layers)) == len(layers)


def _valid_tasks(tasks) -> bool:
    if not isinstance(tasks, dict): return False
    if 'full_composite' not in tasks: return False
    for key in COMPOSITE_TASK_KEYS:
        if key not in tasks: continue
        if not isinstance(tasks[key], dict): return False
        if not check_str(tasks[key], 'channel_name'): return False
    return True


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    if 'entity' not in config: return False
    if not valid_entity(config['entity']): return False
    if not _valid_settings(config.get('settings')): return False
    if not valid_exports(config.get('exports')): return False
    if not valid_imports(config.get('imports')): return False
    if 'renders' in config:
        if not valid_renders(config['renders']): return False
        if config['imports']: return False
    if 'follows' in config:
        # A re-comp beside a stepped render: the render it reads.
        if not valid_renders(config['follows']): return False
        if 'renders' in config or config['imports']: return False
    if not _valid_tasks(config.get('tasks')): return False
    return True
