"""Check TumblePipe's own toast id on this Windows machine, for real
(``util.desktop_notify``).

Registers ``HKCU\\Software\\Classes\\AppUserModelId\\TumbleTrove.TumblePipe``
(the only key it touches; no admin rights), checks the values, that a
second registration writes nothing, and then shows one real toast under
our id whose click opens a folder (by default a temp folder with a space
and non-ASCII letters in its name). Look at the toast: its header should
say "TumblePipe" with the TumblePipe icon, not "Windows PowerShell", and a
click should open the folder in Explorer.

    python scripts/verify_toast_app_id.py [--folder PATH] [--no-toast] [--remove]

``--remove`` deletes our key (and nothing else) and exits; the next toast
registers it again. Exit code 0 when every check passed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))

from tumblepipe.util import desktop_notify as dn  # noqa: E402


class _CountingRegistry(dn.WinRegistry):
    def __init__(self):
        self.writes = 0

    def write(self, key, values):
        self.writes += 1
        super().write(key, values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--folder', default='')
    parser.add_argument('--no-toast', action='store_true')
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32':
        print('Windows only: macOS and Linux have no toast id to register.')
        return 0
    registry = dn.WinRegistry()
    key = dn.app_id_key()
    if args.remove:
        registry.delete(key)
        print(f'Removed HKCU\\{key}')
        return 0

    checks: list[tuple[str, bool, str]] = []
    first = _CountingRegistry()
    ok = dn.register_app_id(first)
    values = registry.read(key) or {}
    checks.append(('registered', ok, f'HKCU\\{key}'))
    checks.append(('DisplayName is TumblePipe', values.get('DisplayName') == dn.APP_NAME,
                   repr(values.get('DisplayName'))))
    icon = values.get('IconUri', '')
    checks.append(('IconUri is a file outside the package', bool(icon) and Path(icon).is_file()
                   and dn.PACKAGE_ICON.parent not in Path(icon).parents, icon))
    again = _CountingRegistry()
    checks.append(('a second registration writes nothing',
                   dn.register_app_id(again) and again.writes == 0, f'{again.writes} writes'))

    if not args.no_toast:
        folder = args.folder
        if not folder:
            folder = str(Path(tempfile.mkdtemp(prefix='tp toast ')) / 'Dailies æøå SH0170')
            Path(folder).mkdir(parents=True, exist_ok=True)
        link = dn.folder_link(folder)
        used: list[str] = []

        def run(argv, **kwargs):
            used.append(kwargs['env'].get(dn.APP_ENV, ''))
            return subprocess.run(argv, **kwargs)

        why = dn.notify(
            'Farm job finished',
            'Seq_0010/SH0170 playblast done: 18 frames · 27 s on this machine',
            folder, run=run, app_id=lambda: dn.TUMBLEPIPE_APP_ID,
        )
        checks.append(('the toast showed under our id (no fallback)',
                       why == '' and used == [dn.TUMBLEPIPE_APP_ID], why or f'{used} {link}'))
        print(f'Toast shown; a click should open {folder}\n  ({link})')

    for name, passed, detail in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    return 0 if all(passed for _, passed, _ in checks) else 1


if __name__ == '__main__':
    sys.exit(main())
