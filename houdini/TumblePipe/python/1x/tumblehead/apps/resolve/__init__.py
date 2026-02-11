"""
DaVinci Resolve integration for the TumbleHead pipeline.

This package provides:
- ResolveClient: Core API wrapper for Resolve scripting
- ResolveService: Background service for processing commands
- Command queue utilities for async operations
- Daily generation utilities

Usage:
    from tumblehead.apps.resolve import ResolveClient

    client = ResolveClient()
    if client.connect():
        project = client.get_current_project()
        print(f"Current project: {project.GetName()}")
"""

# Client and core types
from tumblehead.apps.resolve.client import (
    ResolveClient,
    ResolveInstallation,
    MediaPoolItemInfo,
    TimelineInfo,
    ProjectInfo,
    get_default_client,
)

# Command queue
from tumblehead.apps.resolve.queue import (
    ResolveCommand,
    write_command,
    read_command,
    read_pending_commands,
    move_to_running,
    mark_command_complete,
    mark_command_failed,
    cleanup_old_commands,
    create_import_command,
    create_relink_command,
    create_timeline_command,
    create_render_command,
    create_daily_command,
)

# Service
from tumblehead.apps.resolve.service import (
    ResolveService,
)

# Dailies
from tumblehead.apps.resolve.dailies import (
    DailyConfig,
    RenderInfo,
    DailyGenerator,
    create_daily_config_from_project,
)

__all__ = [
    # Client
    'ResolveClient',
    'ResolveInstallation',
    'MediaPoolItemInfo',
    'TimelineInfo',
    'ProjectInfo',
    'get_default_client',
    # Queue
    'ResolveCommand',
    'write_command',
    'read_command',
    'read_pending_commands',
    'move_to_running',
    'mark_command_complete',
    'mark_command_failed',
    'cleanup_old_commands',
    'create_import_command',
    'create_relink_command',
    'create_timeline_command',
    'create_render_command',
    'create_daily_command',
    # Service
    'ResolveService',
    # Dailies
    'DailyConfig',
    'RenderInfo',
    'DailyGenerator',
    'create_daily_config_from_project',
]
