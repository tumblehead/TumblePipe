from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys

from tumblepipe.pipe.paths import get_latest_staged_file_path
from tumblepipe.util.uri import Uri


def get_entity_type(entity_uri: Uri) -> str | None:
    """Get entity type from URI ('asset', 'shot', or 'group').

    Unlike ``config.variants.get_entity_type``, this also classifies
    ``groups:`` URIs and tolerates ``None`` input.
    """
    if entity_uri is None: return None
    if entity_uri.purpose == 'groups':
        return 'group'
    if entity_uri.purpose != 'entity': return None
    if len(entity_uri.segments) < 1: return None
    context = entity_uri.segments[0]
    if context == 'assets': return 'asset'
    if context == 'shots': return 'shot'
    return None


def has_staged_export(entity_uri: Uri) -> bool:
    """Check if a staged export exists for the given entity.

    This is a fast check - just constructs a path and checks if file exists.
    """
    if entity_uri is None:
        return False
    try:
        export_file = get_latest_staged_file_path(entity_uri, variant_name='default')
        return export_file is not None and export_file.exists()
    except Exception:
        return False


def load_module(module_path: Path, module_name: str):
    """Dynamically load a Python module from file path."""
    spec = spec_from_file_location(module_name, module_path)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
