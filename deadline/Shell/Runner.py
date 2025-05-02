from dotenv import dotenv_values
from typing import Optional
from pathlib import Path
import subprocess
import sys
import os

def _error(msg):
    print(msg)
    return 1

def _wsl_patch_env(env):
    patch = ':'.join([k for k in env])
    return env.copy() | { 'WSLENV': patch }

def main(
    cwd_path: Optional[Path],
    env_file_path: Optional[Path],
    script_file_path: Path,
    args: list[str]
    ):

    # Load environment variables
    env = None if env_file_path is None else dotenv_values(env_file_path)

    # Prepare env
    _env = os.environ.copy()
    _env['PYTHONUNBUFFERED'] = '1'
    if env is not None:
        _env.update(_wsl_patch_env(env))
    
    # Prepare args
    _args = dict(
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
        env = _env
    )
    if cwd_path is not None:
        _args['cwd'] = str(cwd_path)
    
    # Run the script
    command = [sys.executable, str(script_file_path), *args]
    process = subprocess.Popen(command, **_args)
    with process.stdout:
        for line in iter(process.stdout.readline, b''):
            print(line.decode('utf-8').strip())
    return process.wait()

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('script', type=str, help='Path to script file.')
    parser.add_argument('args', nargs='*', help='Arguments to pass to the script.')
    parser.add_argument('--env', type=str, help='Path to .env file.')
    parser.add_argument('--cwd', type=str, help='Path to set as the current working directory.')
    args = parser.parse_args()
    
    # Check script path
    script_file_path = Path(args.script)
    if not script_file_path.exists():
        return _error(f'Script file not found: {script_file_path}')

    # Check env file path
    env_file_path = Path(args.env) if args.env is not None else None
    if env_file_path is not None and not env_file_path.exists():
        return _error(f'Environment file not found: {env_file_path}')

    # Check cwd path
    cwd_path = Path(args.cwd) if args.cwd is not None else None
    if cwd_path is not None and not cwd_path.exists():
        return _error(f'CWD path not found: {cwd_path}')

    # Run main
    return main(
        cwd_path,
        env_file_path,
        script_file_path,
        args.args
    )

if __name__ == '__main__':
    sys.exit(cli())