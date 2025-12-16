from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import datetime as dt
import json
import sys

from tumblehead.pipe.paths import (
    list_hip_file_paths,
    get_hip_file_path,
    latest_hip_file_path,
    next_hip_file_path,
    latest_export_path,
    get_latest_staged_file_path,
    Context
)
from tumblehead.util.uri import Uri

# Re-export from pipe.context for backward compatibility
from tumblehead.pipe.context import (
    save_context,
    save_entity_context,
    file_path_from_context,
    get_timestamp_from_context,
)


def get_entity_type(entity_uri: Uri) -> str | None:
    """Get entity type from URI ('asset', 'shot', or 'group')."""
    if entity_uri is None: return None
    if entity_uri.purpose == 'groups':
        return 'group'
    if entity_uri.purpose != 'entity': return None
    if len(entity_uri.segments) < 1: return None
    context = entity_uri.segments[0]
    if context == 'assets': return 'asset'
    if context == 'shots': return 'shot'
    return None


def entity_uri_from_path(path: list[str]) -> Uri | None:
    """Convert legacy path list to entity Uri."""
    match path:
        case ["assets", category, asset, *_]:
            return Uri.parse_unsafe(f'entity:/assets/{category}/{asset}')
        case ["shots", sequence, shot, *_]:
            return Uri.parse_unsafe(f'entity:/shots/{sequence}/{shot}')
        case ["groups", context, group_name, *_]:
            return Uri.parse_unsafe(f'groups:/{context}/{group_name}')
        case _:
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


def context_from_selection(entity_uri: Uri, department_name: str, version_name: str = None) -> Context:
    """Create Context from entity Uri and department info."""
    return Context(
        entity_uri=entity_uri,
        department_name=department_name,
        version_name=version_name
    )


def latest_context(entity_uri: Uri, department_name: str) -> Context:
    """Get latest context for any entity type. Returns paths.Context directly."""
    file_path = latest_hip_file_path(entity_uri, department_name)
    version_name = None if file_path is None else file_path.stem.rsplit("_", 1)[-1]
    return Context(
        entity_uri=entity_uri,
        department_name=department_name,
        version_name=version_name
    )


def next_file_path(context: Context):
    """Get next file path for a context."""
    return next_hip_file_path(context.entity_uri, context.department_name)


def latest_file_path(context: Context):
    """Get latest file path for a context."""
    return latest_hip_file_path(context.entity_uri, context.department_name)


def list_file_paths(context: Context):
    """List all file paths for a context."""
    return list_hip_file_paths(context.entity_uri, context.department_name)


def latest_export_path_from_context(context: Context, variant_name: str = 'default'):
    """Get latest export path for a context."""
    return latest_export_path(context.entity_uri, variant_name, context.department_name)


def get_user_from_context(context: Context):
    """Get the username who saved the workfile for the given context.

    Returns:
        str: Username if found in context data
        None: If no workfile exists for this context
        "Unknown": If workfile exists but user data is missing or corrupted
    """
    if context is None or context.version_name is None:
        return None

    file_path = file_path_from_context(context)
    if file_path is None:
        return None

    context_dir = file_path.parent / "_context"
    context_file = context_dir / f"{context.version_name}.json"

    if not context_file.exists():
        return None

    try:
        with open(context_file, 'r') as f:
            context_data = json.load(f)
        return context_data.get('user', 'Unknown')
    except (json.JSONDecodeError, OSError, KeyError):
        return "Unknown"


def format_relative_time(timestamp):
    """Format a timestamp as relative time (e.g., '3 min ago', '2 hours ago').

    Args:
        timestamp: datetime object or None

    Returns:
        str: Formatted relative time string, or empty string if timestamp is None
    """
    if timestamp is None:
        return ""

    now = dt.datetime.now()
    diff = now - timestamp

    if diff.total_seconds() < 0:
        return "in the future"

    seconds = int(diff.total_seconds())

    if seconds < 60:
        return f"{seconds}s ago" if seconds != 1 else "1s ago"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago" if minutes != 1 else "1m ago"

    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago" if hours != 1 else "1h ago"

    days = hours // 24
    if days < 7:
        return f"{days}d ago" if days != 1 else "1d ago"

    weeks = days // 7
    if weeks < 4:
        return f"{weeks}w ago" if weeks != 1 else "1w ago"

    months = days // 30
    if months < 12:
        return f"{months}mo ago" if months != 1 else "1mo ago"

    years = days // 365
    return f"{years}y ago" if years != 1 else "1y ago"


def load_module(module_path: Path, module_name: str):
    """Dynamically load a Python module from file path."""
    spec = spec_from_file_location(module_name, module_path)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
