"""Telling the artist something through the OS: a desktop notification.

Windows shows a toast (WinRT, through Windows PowerShell), macOS a
Notification Center banner (``osascript``), Linux a libnotify bubble
(``notify-send``, when it is installed). Nothing beyond the standard library
and what each OS ships.

The words never pass through a shell or a script's source: PowerShell reads
them from environment variables and escapes them for the toast's XML, and
``osascript`` and ``notify-send`` take them as arguments. A notification is
a courtesy, so :func:`notify` never raises: it returns why it did not show.

**Clicking it.** On Windows a click on the toast opens the folder it was
given (the toast's launch link is the folder's ``file:///`` URL,
:func:`folder_link`). macOS and Linux cannot do that with what they ship:
``osascript``'s ``display notification`` has no click action (a click opens
Script Editor), ``terminal-notifier`` is not part of macOS, and
``notify-send --action`` only works while the process that sent it waits
for the answer. There the notification is words only.

**Whose it is.** A Windows toast must come from a registered app, and its
header names that app. The first toast of a session registers TumblePipe's
own AppUserModelID for this user (:func:`register_app_id`):
``HKCU\\Software\\Classes\\AppUserModelId\\TumbleTrove.TumblePipe`` with a
``DisplayName`` and an ``IconUri``, which Windows 10 1903 and later accept
for an app that is not packaged, no Start menu shortcut needed. No admin
rights, the same for every TumblePipe version (the icon is copied to a
fixed place under ``%LOCALAPPDATA%``), and a no-op once the values are
there. Should that fail, or a toast under our id fail to show, the toast
comes from Windows PowerShell's id instead, which every Windows 10 and 11
has: the header then says "Windows PowerShell", but it shows.

Pure but for :func:`notify`'s ``subprocess.run`` and
:func:`register_app_id`'s registry and file writes, which tests replace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

WINDOWS = 'windows'
MACOS = 'macos'
LINUX = 'linux'

#: The name the notification says it comes from.
APP_NAME = 'TumblePipe'

#: TumblePipe's own AppUserModelID, registered per user at the first toast.
TUMBLEPIPE_APP_ID = 'TumbleTrove.TumblePipe'

#: Windows PowerShell's AppUserModelID: the fallback when ours cannot be
#: registered. Every Windows 10 and 11 has it.
POWERSHELL_APP_ID = (
    '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'
)
#: The id every toast came from before TumblePipe had its own.
WINDOWS_APP_ID = POWERSHELL_APP_ID

#: Where an unpackaged app's AppUserModelID is registered, under HKCU.
APP_ID_ROOT = 'Software\\Classes\\AppUserModelId'

#: The toast header's icon, shipped with the package; copied to
#: :func:`icon_home` so the registry names a path no upgrade moves.
PACKAGE_ICON = Path(__file__).resolve().parent.parent / 'asset_browser' / 'icons' / 'tumblepipe.png'
ICON_NAME = 'tumblepipe.png'

#: How long a notifier may take before it is given up on, in seconds.
TIMEOUT_SECONDS = 20

# The environment variables the toast script reads its words from.
TITLE_ENV = 'TH_NOTIFY_TITLE'
BODY_ENV = 'TH_NOTIFY_BODY'
LINK_ENV = 'TH_NOTIFY_LINK'
APP_ENV = 'TH_NOTIFY_APP'

# A ToastGeneric toast with a title line and a body; clicking it opens the
# link (a folder's file:/// URL) when there is one. The app it comes from
# is read from the environment too.
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
    f"$env:{APP_ENV}).Show($toast)"
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


# ASCII characters a link keeps as they are; every other ASCII character
# (space, %, #, ?, quotes, ...) is percent-encoded.
_LINK_SAFE = frozenset(
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    "-._~/:@!$&'()*+,;="
)


def _link_text(path_text: str) -> str:
    """Percent-encode the ASCII characters a link cannot hold; keep letters
    beyond ASCII as they are (an IRI)."""
    return ''.join(
        char if char in _LINK_SAFE or ord(char) > 127 else ''.join(f'%{b:02X}' for b in char.encode('utf-8'))
        for char in path_text
    )


def folder_link(folder: str) -> str:
    """``folder`` as a ``file:///`` link a toast can open, '' for none.

    ``C:\\Dailies 1\\anim`` is ``file:///C:/Dailies%201/anim``; a UNC share
    ``\\\\server\\share\\x`` is ``file://server/share/x``. Spaces, ``%``,
    ``#`` and the like are percent-encoded, but letters beyond ASCII
    (``æøå``) are kept as they are: Windows opens a toast's link through
    the shell, which reads ``%C3%A6`` in the ANSI code page and so finds no
    such folder (seen on Windows 11, 2026-11-14), while ``æ`` works. A
    folder that is neither a drive path nor a share is taken from the
    current folder. Never raises.
    """
    if not folder:
        return ''
    try:
        text = str(folder)
        if '\0' in text or not text.strip():
            return ''
        from pathlib import PureWindowsPath
        path = PureWindowsPath(text)
        if not (path.drive and path.root):
            absolute = Path(text).absolute()
            if os.name != 'nt':
                return 'file://' + _link_text(absolute.as_posix())
            path = PureWindowsPath(absolute)
        if path.drive.startswith(('\\\\', '//')):
            server_share = path.drive.replace('\\', '/').strip('/')
            rest = '/'.join(path.parts[1:])
            return 'file:' + _link_text(f'//{server_share}/{rest}')
        return 'file:///' + _link_text(path.as_posix())
    except (ValueError, OSError, TypeError):
        return ''


# ── TumblePipe's own toast id (Windows) ───────────────────

def app_id_key(app_id: str = TUMBLEPIPE_APP_ID) -> str:
    """The HKCU key that registers ``app_id``."""
    return f'{APP_ID_ROOT}\\{app_id}'


def icon_home(local_app_data: str) -> Path | None:
    """Where the toast icon is kept for every TumblePipe version:
    ``%LOCALAPPDATA%\\TumbleTrove\\TumblePipe\\tumblepipe.png``; None
    without ``%LOCALAPPDATA%``."""
    if not local_app_data:
        return None
    return Path(local_app_data) / 'TumbleTrove' / 'TumblePipe' / ICON_NAME


def app_id_values(icon_path: str = '') -> dict[str, str]:
    """The registry values that make the toast header say TumblePipe (and
    show its icon, when there is one)."""
    values = {'DisplayName': APP_NAME}
    if icon_path:
        values['IconUri'] = str(icon_path)
    return values


def values_to_write(current: dict | None, wanted: dict) -> dict[str, str]:
    """The values of ``wanted`` that ``current`` (what the key holds; None
    for no key) lacks or holds differently: {} when it is up to date."""
    current = current or {}
    return {name: value for name, value in wanted.items() if current.get(name) != value}


def app_id_for(registered: bool) -> str:
    """The id a toast comes from: ours once registered, else PowerShell's."""
    return TUMBLEPIPE_APP_ID if registered else POWERSHELL_APP_ID


class WinRegistry:
    """HKCU through ``winreg``; tests pass their own with the same methods."""

    def read(self, key: str) -> dict | None:
        import winreg
        try:
            handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            return None
        values = {}
        with handle:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(handle, index)
                except OSError:
                    break
                values[name] = value
                index += 1
        return values

    def write(self, key: str, values: dict) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            for name, value in values.items():
                winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, str(value))

    def delete(self, key: str) -> None:
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass


def place_icon(source: Path, target: Path | None) -> str:
    """Copy the shipped icon to ``target`` unless an equal-sized copy is
    there; the path to name in the registry, '' when there is none."""
    if target is None:
        return ''
    try:
        if not source.is_file():
            return str(target) if target.is_file() else ''
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return str(target)
    except OSError:
        return ''


_REGISTERED: bool | None = None


def register_app_id(
    registry=None,
    *,
    environ: dict | None = None,
    icon: Path = PACKAGE_ICON,
    ) -> bool:
    """Make sure TumblePipe's toast id is registered for this user; whether
    it is. Writes only what is missing or different, under our own key.
    Never raises."""
    try:
        registry = registry or WinRegistry()
        env = os.environ if environ is None else environ
        icon_path = place_icon(icon, icon_home(env.get('LOCALAPPDATA', '')))
        wanted = app_id_values(icon_path)
        missing = values_to_write(registry.read(app_id_key()), wanted)
        if missing:
            registry.write(app_id_key(), missing)
            return not values_to_write(registry.read(app_id_key()), wanted)
        return True
    except Exception:
        return False


def registered_app_id() -> str:
    """The id this session's toasts come from: registered once, at the
    first toast, then remembered."""
    global _REGISTERED
    if _REGISTERED is None:
        _REGISTERED = register_app_id()
    return app_id_for(_REGISTERED)


# ── showing it ────────────────────────────────────────────

def command(kind: str, title: str, body: str, link: str = '',
            app_id: str = POWERSHELL_APP_ID) -> tuple[list[str], dict]:
    """``(argv, extra environment)`` that shows ``title`` and ``body`` with
    notifier ``kind`` (on Windows from ``app_id``, opening ``link`` on a click)."""
    if kind == WINDOWS:
        return (
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-WindowStyle', 'Hidden', '-Command', _TOAST_SCRIPT],
            {TITLE_ENV: title, BODY_ENV: body, LINK_ENV: link, APP_ENV: app_id or POWERSHELL_APP_ID},
        )
    if kind == MACOS:
        argv = ['osascript']
        for line in _APPLESCRIPT:
            argv += ['-e', line]
        return argv + [title, body], {}
    if kind == LINUX:
        return ['notify-send', f'--app-name={APP_NAME}', '--', title, body], {}
    raise ValueError(f'No such notifier: {kind!r}')


def toast_ids(first: str) -> list[str]:
    """The ids a toast is tried under, in turn: ``first``, then PowerShell's
    when ``first`` is another (or empty)."""
    first = first or POWERSHELL_APP_ID
    return [first] if first == POWERSHELL_APP_ID else [first, POWERSHELL_APP_ID]


def _why_failed(argv: list[str], result) -> str:
    """'' when ``result`` succeeded, else what went wrong."""
    code = getattr(result, 'returncode', 0)
    if not code:
        return ''
    output = getattr(result, 'stdout', b'') or b''
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    last = ' '.join(str(output).split())[-300:]
    return f'{argv[0]} exited {code}' + (f': {last}' if last else '')


def notify(
    title: str,
    body: str,
    folder: str = '',
    *,
    platform: str = sys.platform,
    which: Callable = shutil.which,
    run: Callable = subprocess.run,
    environ: dict | None = None,
    app_id: Callable[[], str] | None = None,
    ) -> str:
    """Show a desktop notification; '' when it showed, else why not.

    On Windows a click opens ``folder`` (when given), and the toast comes
    from ``app_id()``: :func:`registered_app_id` by default, PowerShell's
    id when ``run`` is faked (a test never touches the registry). A toast
    that fails under our id is shown again under PowerShell's.

    Never raises: a notifier that is missing, fails or hangs is reported in
    the returned text, for the caller to log.
    """
    try:
        kind = backend(platform, which)
        if kind is None:
            return f'no desktop notifier on {platform} (install libnotify\'s notify-send)'
        ids = [POWERSHELL_APP_ID]
        link = ''
        if kind == WINDOWS:
            link = folder_link(folder)
            if app_id is None:
                app_id = registered_app_id if run is subprocess.run else (lambda: POWERSHELL_APP_ID)
            try:
                ids = toast_ids(app_id())
            except Exception:
                ids = [POWERSHELL_APP_ID]
        kwargs = {}
        if platform == 'win32' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        why = ''
        for each in ids:
            argv, extra = command(kind, str(title), str(body), link, each)
            env = dict(os.environ if environ is None else environ)
            env.update(extra)
            result = run(
                argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=TIMEOUT_SECONDS, **kwargs,
            )
            why = _why_failed(argv, result)
            if not why:
                return ''
        return why
    except subprocess.TimeoutExpired:
        return f'the {platform} notifier did not answer within {TIMEOUT_SECONDS} s'
    except Exception as error:  # A courtesy must not take its caller down.
        return f'{type(error).__name__}: {error}'
