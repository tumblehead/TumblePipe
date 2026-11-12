"""Telling the artist something through the OS: a desktop notification.

Windows shows a toast (WinRT, through Windows PowerShell), macOS a
Notification Center banner (``osascript``), Linux a libnotify bubble
(``notify-send``, when it is installed). Nothing beyond the standard library
and what each OS ships.

The words never pass through a shell or a script's source: PowerShell reads
them from environment variables and escapes them for the toast's XML, and
``osascript`` and ``notify-send`` take them as arguments. A notification is
a courtesy, so :func:`notify` never raises: it returns why it did not show.

Pure but for :func:`notify`'s one ``subprocess.run``, which tests replace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Callable

WINDOWS = 'windows'
MACOS = 'macos'
LINUX = 'linux'

#: The name the notification says it comes from.
APP_NAME = 'TumblePipe'

#: Windows PowerShell's AppUserModelID. A toast needs a registered app to
#: come from; every Windows 10 and 11 has this one, so no shortcut or
#: registry entry of ours is needed.
WINDOWS_APP_ID = (
    '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'
)

#: How long a notifier may take before it is given up on, in seconds.
TIMEOUT_SECONDS = 20

# The environment variables the toast script reads its words from.
TITLE_ENV = 'TH_NOTIFY_TITLE'
BODY_ENV = 'TH_NOTIFY_BODY'
LINK_ENV = 'TH_NOTIFY_LINK'

# A ToastGeneric toast with a title line and a body; clicking it opens the
# link (a folder's file:/// URL) when there is one.
_TOAST_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] > $null; "
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
    "ContentType = WindowsRuntime] > $null; "
    "$e = [Security.SecurityElement]; "
    "$launch = ''; "
    f"if ($env:{LINK_ENV}) {{ $launch = \" activationType='protocol' launch='\" + "
    f"$e::Escape($env:{LINK_ENV}) + \"'\" }}; "
    "$xml = \"<toast$launch><visual><binding template='ToastGeneric'><text>\" + "
    f"$e::Escape($env:{TITLE_ENV}) + '</text><text>' + "
    f"$e::Escape($env:{BODY_ENV}) + '</text></binding></visual></toast>'; "
    "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument; "
    "$doc.LoadXml($xml); "
    "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc); "
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    f"'{WINDOWS_APP_ID}').Show($toast)"
)

_APPLESCRIPT = (
    'on run argv',
    'display notification (item 2 of argv) with title (item 1 of argv)',
    'end run',
)


def backend(platform: str = sys.platform, which: Callable = shutil.which) -> str | None:
    """Which notifier this platform has: ``windows``, ``macos``, ``linux``,
    or None (a Linux without ``notify-send``, any other OS)."""
    if platform == 'win32':
        return WINDOWS
    if platform == 'darwin':
        return MACOS
    if platform.startswith('linux') or platform.startswith('freebsd'):
        return LINUX if which('notify-send') else None
    return None


def folder_link(folder: str) -> str:
    """``folder`` as a ``file:///`` URL a toast can open, '' for none."""
    if not folder:
        return ''
    from pathlib import Path
    try:
        return Path(folder).absolute().as_uri()
    except ValueError:
        return ''


def command(kind: str, title: str, body: str, link: str = '') -> tuple[list[str], dict]:
    """``(argv, extra environment)`` that shows ``title`` and ``body`` with
    notifier ``kind``."""
    if kind == WINDOWS:
        return (
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-WindowStyle', 'Hidden', '-Command', _TOAST_SCRIPT],
            {TITLE_ENV: title, BODY_ENV: body, LINK_ENV: link},
        )
    if kind == MACOS:
        argv = ['osascript']
        for line in _APPLESCRIPT:
            argv += ['-e', line]
        return argv + [title, body], {}
    if kind == LINUX:
        return ['notify-send', f'--app-name={APP_NAME}', '--', title, body], {}
    raise ValueError(f'No such notifier: {kind!r}')


def notify(
    title: str,
    body: str,
    folder: str = '',
    *,
    platform: str = sys.platform,
    which: Callable = shutil.which,
    run: Callable = subprocess.run,
    environ: dict | None = None,
    ) -> str:
    """Show a desktop notification; '' when it showed, else why not.

    Never raises: a notifier that is missing, fails or hangs is reported in
    the returned text, for the caller to log.
    """
    try:
        kind = backend(platform, which)
        if kind is None:
            return f'no desktop notifier on {platform} (install libnotify\'s notify-send)'
        argv, extra = command(kind, str(title), str(body), folder_link(folder) if kind == WINDOWS else '')
        env = dict(os.environ if environ is None else environ)
        env.update(extra)
        kwargs = {}
        if platform == 'win32' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = run(
            argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=TIMEOUT_SECONDS, **kwargs,
        )
        code = getattr(result, 'returncode', 0)
        if code:
            output = getattr(result, 'stdout', b'') or b''
            if isinstance(output, bytes):
                output = output.decode('utf-8', errors='replace')
            last = ' '.join(str(output).split())[-300:]
            return f'{argv[0]} exited {code}' + (f': {last}' if last else '')
        return ''
    except subprocess.TimeoutExpired:
        return f'the {platform} notifier did not answer within {TIMEOUT_SECONDS} s'
    except Exception as error:  # A courtesy must not take its caller down.
        return f'{type(error).__name__}: {error}'
