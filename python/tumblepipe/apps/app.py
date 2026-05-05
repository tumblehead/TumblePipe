from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import concurrent.futures
import subprocess
import platform
import asyncio
import logging
import signal
import sys
import os

Command = list[str]

def _wsl_patch_env(env):
    keys = os.environ.get('WSLENV', '').split(':')
    keys += [key for key in env if key not in keys]
    return env.copy() | { 'WSLENV': ':'.join(keys) }

def run(
    command: Command,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None
    ) -> int:

    # Prepare env
    _env = os.environ.copy()
    _env['PYTHONUNBUFFERED'] = '1'
    if env is not None:
        _env.update(_wsl_patch_env(env))

    # Prepare args
    _args = dict(
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
        text = True,
        bufsize = 1,
        env = _env
    )
    if cwd is not None:
        _args['cwd'] = str(cwd)

    # Run command
    try:
        logging.debug(' '.join(command))
        process = subprocess.Popen(command, **_args)
        for line in process.stdout:
            print(line, end='')
            sys.stdout.flush()
        return process.wait()
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        return 1

def call(
    command: Command,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None
    ) -> str:

    # Prepare env
    _env = os.environ.copy()
    _env['PYTHONUNBUFFERED'] = '1'
    if env is not None:
        _env.update(_wsl_patch_env(env))

    # Prepare args
    _args = dict(
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
        text = True,
        bufsize = 1,
        env = _env
    )
    if platform.system() == 'Windows':
        _args['creationflags'] = subprocess.CREATE_NO_WINDOW
    if cwd is not None:
        _args['cwd'] = str(cwd)

    # Run command
    try:
        logging.debug(' '.join(command))
        process = subprocess.Popen(command, **_args)
        result = ''
        with process.stdout:
            for line in process.stdout:
                result += line
        process.wait()
        return result
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        return None

async def run_async(
    command: Command,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None
    ) -> int:

    # Prepare env
    _env = os.environ.copy()
    _env['PYTHONUNBUFFERED'] = '1'
    if env is not None:
        _env.update(_wsl_patch_env(env))

    # Prepare args
    _args = dict(
        stdout = asyncio.subprocess.PIPE,
        stderr = asyncio.subprocess.STDOUT,
        env = _env
    )
    if cwd is not None:
        _args['cwd'] = str(cwd)

    # Run command
    logging.debug(' '.join(command))
    process = await asyncio.create_subprocess_exec(*command, **_args)
    async def _read_lines():
        while True:
            line = await process.stdout.readline()
            if not line: break
            yield line
    async for line in aiter(_read_lines()):
        print(line.decode('utf-8'), end='')
        sys.stdout.flush()
    return await process.wait()

async def call_async(
    command: Command,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None
    ) -> str:

    # Prepare env
    _env = os.environ.copy()
    _env['PYTHONUNBUFFERED'] = '1'
    if env is not None:
        _env.update(_wsl_patch_env(env))
    
    # Prepare args
    _args = dict(
        stdout = asyncio.subprocess.PIPE,
        stderr = asyncio.subprocess.STDOUT,
        env = _env
    )
    if platform.system() == 'Windows':
        _args['creationflags'] = subprocess.CREATE_NO_WINDOW
    if cwd is not None:
        _args['cwd'] = str(cwd)
    
    # Run command
    logging.debug(' '.join(command))
    process = await asyncio.create_subprocess_exec(*command, **_args)
    result = ''
    async def _read_lines():
        while True:
            line = await process.stdout.readline()
            if not line: break
            yield line
    async for line in aiter(_read_lines()):
        result += line.decode('utf-8')
    await process.wait()
    return result

@dataclass
class Task:
    command: Command
    cwd: Optional[Path] = None
    env: Optional[dict[str, str]] = None

    def run(self) -> int:
        return run(self.command, self.cwd, self.env)

async def run_tasks(tasks: list[Task], num_workers: int) -> list[int]:
    loop = asyncio.get_running_loop()
    with concurrent.futures.ProcessPoolExecutor(num_workers) as pool:
        return await asyncio.gather(*[
            loop.run_in_executor(pool, task.run)
            for task in tasks
        ])