"""Config schema of the Render Edit pipeline (``pipelines/edit_render.py``,
whose docstring shows the config): one published edit version to render."""

from tumblepipe import naming
from tumblepipe.farm.checks import check_int, check_str
from tumblepipe.farm.specs.edit_render import is_edit_uri


def is_valid_config(config) -> bool:
    if not isinstance(config, dict): return False
    edit = config.get('edit')
    if not isinstance(edit, dict): return False
    if not is_edit_uri(edit.get('uri')): return False
    if not check_str(edit, 'version'): return False
    if not naming.is_valid_version_name(edit['version']): return False
    settings = config.get('settings')
    if not isinstance(settings, dict): return False
    if not check_str(settings, 'pool_name'): return False
    if not check_int(settings, 'priority'): return False
    return True
