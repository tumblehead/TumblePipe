from dataclasses import dataclass
from unittest.mock import patch
from typing import Optional
from pathlib import Path
import asyncio
import json
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

# Check if we are running under mayapy
exe_path = Path(sys.executable)
if exe_path.name == 'mayapy.exe':
    import maya.standalone
    maya.standalone.initialize(name="python")

from tumblehead.util import ipc
from tumblehead.api import fix_path

def _headline(msg):
    print(f' {msg} '.center(80, '='))

@dataclass
class Task:
    cwd: Optional[Path]
    env: Optional[dict[str, str]]
    path: Path
    args: list[str]

def _task_decode(data: str) -> Task:
    def _maybe_path(value: Optional[str]) -> Optional[Path]:
        if value is None: return None
        return Path(value)
    if data == '': return None
    raw_task = json.loads(data)
    return Task(
        cwd = _maybe_path(raw_task['cwd']),
        env = raw_task['env'],
        path = Path(raw_task['path']),
        args = raw_task['args']
    )

def _read_script_file(script_file_path: Path):
    with script_file_path.open("r") as script_file:
        return script_file.read()

def _run_script(script_file_path: Path, args: list[str]):
    _headline('MayaPy Runner: Running script')
    print(f'Script path: {script_file_path}')
    print(f'Arguments: {args}')
    script = _read_script_file(script_file_path)
    with patch.object(sys, 'argv', [sys.argv[0], str(script_file_path), *args]):
        exec(
            compile(script, script_file_path, "exec"),
            { '__name__': '__main__' }
        )
    _headline('MayaPy Runner: Done')

def _task_run(task: Task):
    
    # Set CWD
    if task.cwd is not None:
        os.chdir(fix_path(task.cwd))

    # Load environment variables
    if task.env is not None:
        _headline('MayaPy Runner: Setting environment variables')
        for key, value in task.env.items():
            print(f'{key} = {value}')
            os.environ[key] = value
        
    # Run the script
    _run_script(task.path, task.args)

async def _runner():

    # Connect to the server
    port = int(sys.argv[-1])
    async with ipc.Client('localhost', port) as client:
        
        # Get a task
        await client.send('ready')
        task = _task_decode(await client.receive())

        # Check the task
        if task is None: return 1

        # Run the task
        _task_run(task)

    # Done
    return 0

if __name__ == '__main__':
    sys.exit(asyncio.run(_runner()))