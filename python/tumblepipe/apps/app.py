"""Run an external program: the one subprocess entry point of the app wrappers.

Every wrapper in ``tumblepipe.apps`` (hython, husk, hoiiotool, idenoise,
deadlinecommand, ...) runs its tool through :func:`run`. A non-zero exit
raises :class:`CommandFailed`, carrying the output; a caller that tolerates a
failing tool (the denoise isolating one bad AOV, the playblast encoding what
husk managed to write) catches it explicitly.
"""

from pathlib import Path
from typing import Optional
import subprocess
import platform
import logging
import os

Command = list[str]


class CommandFailed(Exception):
    """A command exited non-zero."""

    def __init__(self, command: Command, returncode: int, output: str):
        self.command = command
        self.returncode = returncode
        self.output = output
        super().__init__(
            f'Command exited with code {returncode}: {" ".join(command)}'
        )


def run(
    command: Command,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    echo: bool = True
    ) -> str:
    """Run ``command`` to completion and return its combined stdout/stderr.

    ``env`` is layered over this process's environment. With ``echo`` the
    output is streamed to stdout as it arrives (a farm task's log); without,
    it is only returned (a query whose answer the caller parses).

    Raises :class:`CommandFailed` when the command exits non-zero.
    """
    _env = os.environ.copy()
    _env['PYTHONUNBUFFERED'] = '1'
    if env is not None:
        _env.update(env)
    _args = dict(
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
        text = True,
        errors = 'replace',
        bufsize = 1,
        env = _env
    )
    if cwd is not None:
        _args['cwd'] = str(cwd)
    if platform.system() == 'Windows':
        _args['creationflags'] = subprocess.CREATE_NO_WINDOW

    logging.debug(' '.join(command))
    process = subprocess.Popen(command, **_args)
    lines = []
    try:
        with process.stdout:
            for line in process.stdout:
                lines.append(line)
                if echo:
                    print(line, end='', flush=True)
        returncode = process.wait()
    except BaseException:
        # Ctrl+C (or any error reading the output): do not leave the tool
        # running unattended.
        process.kill()
        process.wait()
        raise
    output = ''.join(lines)
    if returncode != 0:
        raise CommandFailed(command, returncode, output)
    return output
