from typing import Optional
from pathlib import Path
import platform

from tumblehead.apps import app

SHELL_COMMAND = ['C:\\Windows\\System32\\wsl.exe', '--shell-type', 'login']

def _fix_command(command):
    if platform.system() == 'Windows':
        return SHELL_COMMAND + command
    return command

def run(
    command: app.Command,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None
    ) -> int:
    return app.run(_fix_command(command), cwd, env)

def call(
    command: app.Command,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None
    ) -> str:
    return app.call(_fix_command(command), cwd, env)

async def run_tasks(tasks: list[app.Task], num_workers: int) -> list[int]:
    return await app.run_tasks([
        app.Task(
            _fix_command(task.command),
            task.cwd,
            task.env
        )
        for task in tasks
    ], num_workers)