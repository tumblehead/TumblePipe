from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import datetime as dt
import json
import sys

import hou

from tumblehead.api import get_user_name
from tumblehead.util.io import store_json
from tumblehead.pipe.paths import (
    list_asset_hip_file_paths,
    list_shot_hip_file_paths,
    list_kit_hip_file_paths,
    get_asset_hip_file_path,
    get_shot_hip_file_path,
    get_kit_hip_file_path,
    latest_asset_hip_file_path,
    latest_shot_hip_file_path,
    latest_kit_hip_file_path,
    next_asset_hip_file_path,
    next_shot_hip_file_path,
    next_kit_hip_file_path,
    latest_asset_export_path,
    latest_shot_export_path,
    latest_kit_export_path,
    AssetEntity,
    ShotEntity,
    KitEntity,
    AssetContext,
    ShotContext,
    KitContext,
)

from .constants import Section


def latest_asset_context(category_name, asset_name, department_name):
    file_path = latest_asset_hip_file_path(category_name, asset_name, department_name)
    version_name = None if file_path is None else file_path.stem.rsplit("_", 1)[-1]
    return AssetContext(department_name, category_name, asset_name, version_name)


def latest_shot_context(sequence_name, shot_name, department_name):
    file_path = latest_shot_hip_file_path(sequence_name, shot_name, department_name)
    version_name = None if file_path is None else file_path.stem.rsplit("_", 1)[-1]
    return ShotContext(department_name, sequence_name, shot_name, version_name)


def latest_kit_context(category_name, kit_name, department_name):
    file_path = latest_kit_hip_file_path(category_name, kit_name, department_name)
    version_name = None if file_path is None else file_path.stem.rsplit("_", 1)[-1]
    return KitContext(department_name, category_name, kit_name, version_name)


def next_file_path(context):
    match context:
        case AssetContext(department_name, category_name, asset_name, _):
            return next_asset_hip_file_path(category_name, asset_name, department_name)
        case ShotContext(department_name, sequence_name, shot_name, _):
            return next_shot_hip_file_path(sequence_name, shot_name, department_name)
        case KitContext(department_name, category_name, kit_name, _):
            return next_kit_hip_file_path(category_name, kit_name, department_name)
    assert False, f"Invalid context: {context}"


def latest_file_path(context):
    match context:
        case AssetContext(department_name, category_name, asset_name, _):
            return latest_asset_hip_file_path(
                category_name, asset_name, department_name
            )
        case ShotContext(department_name, sequence_name, shot_name, _):
            return latest_shot_hip_file_path(sequence_name, shot_name, department_name)
        case KitContext(department_name, category_name, kit_name, _):
            return latest_kit_hip_file_path(category_name, kit_name, department_name)
    assert False, f"Invalid context: {context}"


def list_file_paths(context):
    match context:
        case AssetContext(department_name, category_name, asset_name, _):
            return list_asset_hip_file_paths(category_name, asset_name, department_name)
        case ShotContext(department_name, sequence_name, shot_name, _):
            return list_shot_hip_file_paths(sequence_name, shot_name, department_name)
        case KitContext(department_name, category_name, kit_name, _):
            return list_kit_hip_file_paths(category_name, kit_name, department_name)
    assert False, f"Invalid context: {context}"


def latest_export_path(context):
    match context:
        case AssetContext(department_name, category_name, asset_name, _):
            return latest_asset_export_path(category_name, asset_name, department_name)
        case ShotContext(department_name, sequence_name, shot_name, _):
            return latest_shot_export_path(sequence_name, shot_name, department_name)
        case KitContext(department_name, category_name, kit_name, _):
            return latest_kit_export_path(category_name, kit_name, department_name)
    assert False, f"Invalid context: {context}"


def entity_from_path(path):
    match path:
        case None:
            return None
        case ["Assets", category_name, asset_name, *_]:
            return AssetEntity(category_name, asset_name)
        case ["Shots", sequence_name, shot_name, *_]:
            return ShotEntity(sequence_name, shot_name)
        case ["Kits", category_name, kit_name, *_]:
            return KitEntity(category_name, kit_name)
    assert False, f"Invalid path: {path}"


def entity_from_context(context):
    match context:
        case None:
            return None
        case AssetContext(_, category_name, asset_name, _):
            return AssetEntity(category_name, asset_name)
        case ShotContext(_, sequence_name, shot_name, _):
            return ShotEntity(sequence_name, shot_name)
        case KitContext(_, category_name, kit_name, _):
            return KitEntity(category_name, kit_name)
    assert False, f"Invalid context: {context}"


def path_from_context(context):
    match context:
        case None:
            return None
        case AssetContext(_, category_name, asset_name, _):
            return ["Assets", category_name, asset_name]
        case ShotContext(_, sequence_name, shot_name, _):
            return ["Shots", sequence_name, shot_name]
        case KitContext(_, category_name, kit_name, _):
            return ["Kits", category_name, kit_name]
    assert False, f"Invalid context: {context}"


def path_from_entity(entity):
    match entity:
        case None:
            return None
        case AssetEntity(category_name, asset_name):
            return ["Assets", category_name, asset_name]
        case ShotEntity(sequence_name, shot_name):
            return ["Shots", sequence_name, shot_name]
        case KitEntity(category_name, kit_name):
            return ["Kits", category_name, kit_name]
    assert False, f"Invalid entity: {entity}"


def file_path_from_context(context):
    match context:
        case None:
            return None
        case AssetContext(department_name, category_name, asset_name, version_name):
            file_path = get_asset_hip_file_path(
                category_name, asset_name, department_name, version_name
            )
        case ShotContext(department_name, sequence_name, shot_name, version_name):
            file_path = get_shot_hip_file_path(
                sequence_name, shot_name, department_name, version_name
            )
        case KitContext(department_name, category_name, kit_name, version_name):
            file_path = get_kit_hip_file_path(
                category_name, kit_name, department_name, version_name
            )
        case _:
            assert False, f"Invalid context: {context}"
    if not file_path.exists():
        return None
    return file_path


def get_timestamp_from_context(context):
    file_path = file_path_from_context(context)
    if file_path is None:
        return None
    return dt.datetime.fromtimestamp(file_path.stat().st_mtime)


def save_context(target_path, prev_context, next_context):
    def _get_version_name(context):
        if context is None:
            return "v0000"
        if context.version_name is None:
            return "v0000"
        return context.version_name

    timestamp = get_timestamp_from_context(next_context)
    prev_version_name = _get_version_name(prev_context)
    next_version_name = _get_version_name(next_context)
    context_path = target_path / "_context" / f"{next_version_name}.json"
    store_json(
        context_path,
        dict(
            user=get_user_name(),
            timestamp="" if timestamp is None else timestamp.isoformat(),
            from_version=prev_version_name,
            to_version=next_version_name,
            houdini_version=hou.applicationVersionString(),
        ),
    )


def save_entity_context(target_path, context):
    entity_context_path = target_path / "context.json"
    match context:
        case AssetContext(department_name, category_name, asset_name, _version_name):
            context_data = dict(
                entity='asset',
                category=category_name,
                asset=asset_name,
                department=department_name,
                timestamp=dt.datetime.now().isoformat(),
                user=get_user_name(),
            )
        case ShotContext(department_name, sequence_name, shot_name, _version_name):
            context_data = dict(
                entity='shot',
                sequence=sequence_name,
                shot=shot_name,
                department=department_name,
                timestamp=dt.datetime.now().isoformat(),
                user=get_user_name(),
            )
        case KitContext(department_name, category_name, kit_name, _version_name):
            context_data = dict(
                entity='kit',
                category=category_name,
                kit=kit_name,
                department=department_name,
                timestamp=dt.datetime.now().isoformat(),
                user=get_user_name(),
            )
        case _:
            return
    store_json(entity_context_path, context_data)


def get_user_from_context(context):
    """Get the username who saved the workfile for the given context.

    Returns:
        str: Username if found in context data
        None: If no workfile exists for this context
        "Unknown": If workfile exists but user data is missing or corrupted
    """
    if context is None or context.version_name is None:
        return None

    # Get the directory path for this context
    file_path = file_path_from_context(context)
    if file_path is None:
        return None

    # Look for the context JSON file
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

    # Handle negative differences (future times)
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
    spec = spec_from_file_location(module_name, module_path)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module