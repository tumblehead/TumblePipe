"""Drive the archive window end to end, headless, in Houdini's Python.

    "<HFS>/python313/python.exe" scripts/verify_archive_app.py

Builds a small project in a temp folder, then clicks through the window
(``tumblepipe.archive.app``) offscreen: Read project, Write archive, Check
archive, Restore — and checks the restored workfile is byte-identical.
Message boxes are intercepted and recorded rather than shown. Needs only
PySide6 (Houdini's bundled Python has it); takes no licence.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['TH_PIPELINE_PATH'] = str(REPO)
hfs = Path(sys.executable).resolve().parents[1]
os.environ.setdefault('HFS', str(hfs))


def build_project(base: Path) -> Path:
    project = base / 'Demo'
    files = {
        '_config/storage.json': (REPO / 'scripts/project_template/_config/storage.json').read_bytes(),
        '_config/version.json': b'{"version": 13}',
        'assets/CHAR/Hero/model/CHAR_Hero_model_v0001.hip': b'hip 1',
        'assets/CHAR/Hero/model/CHAR_Hero_model_v0002.hip': b'hip 2',
        'assets/CHAR/Hero/model/context.json': b'{}',
        'assets/CHAR/Hero/model/_context/v0002.json': b'{"houdini_version": "22.0.368"}',
        'assets/CHAR/Hero/model/backup/CHAR_Hero_model_v0001_bak1.hip': b'backup',
        'export/assets/CHAR/Hero/default/model/v0002/context.json': b'{}',
        'export/assets/CHAR/Hero/default/model/v0002/model.usd': b'#usda 1.0\n',
        'resources/client_plate.exr': b'client',
    }
    for rel, data in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return project


def main() -> int:
    from tumblepipe.archive.app import _add_houdini_dll_directory, build_window
    _add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication, QMessageBox

    boxes = []
    for kind in ('information', 'warning', 'critical'):
        setattr(QMessageBox, kind, staticmethod(
            lambda parent, title, text, _k=kind: boxes.append((_k, title, text))
        ))
    app = QApplication.instance() or QApplication([])
    base = Path(tempfile.mkdtemp(prefix='archive-app-'))
    project = build_project(base)
    os.environ['TH_PROJECT_PATH'] = str(project)
    window = build_window()
    window.show()

    def wait(label: str, timeout: float = 60.0):
        deadline = time.monotonic() + timeout
        while window.cancel_button.isEnabled() or window._thread is None:
            app.processEvents()
            if time.monotonic() > deadline:
                raise SystemExit(f'timed out: {label}')
            time.sleep(0.01)
        if window._thread is not None:
            window._thread.wait(5000)
        app.processEvents()

    failures = []

    def check(condition, message):
        print(('ok    ' if condition else 'FAIL  ') + message)
        if not condition:
            failures.append(message)

    window._read_project()
    wait('read project')
    check(window._plan is not None, 'Read project made a plan')
    check(window.create_button.isEnabled(), 'Write archive is enabled after reading')
    check('backup' in window.plan_text.toPlainText(), 'the plan text lists the backup kind')

    out = base / 'out' / 'Demo_proof.zip'
    out.parent.mkdir()
    window.out_edit.setText(str(out))
    window.part_edit.setText('nonsense')
    window._create()
    check(any(k == 'warning' for k, _, _ in boxes), 'a bad part size is refused')
    check(window._thread is None or not window._thread.isRunning(), 'and nothing started')
    window.part_edit.setText('20')
    window._create()
    wait('create')
    parts = sorted(out.parent.glob('Demo_proof.part*.zip'))
    check(len(parts) > 1, f'the archive was written in {len(parts)} parts')
    out = parts[0]
    check(any(k == 'information' and 'Archive written' in t for k, t, _ in boxes), 'it said so')

    window.archive_edit.setText(str(out))
    window._verify()
    wait('verify')
    check(window.status.text().startswith('OK:'), f'Check archive: {window.status.text()}')

    dest = base / 'restored'
    window.dest_edit.setText(str(dest))
    window.name_edit.setText('Demo2')
    window._restore()
    wait('restore')
    restored = dest / 'Demo2' / 'assets/CHAR/Hero/model/CHAR_Hero_model_v0002.hip'
    check(restored.is_file() and restored.read_bytes() == b'hip 2', 'the workfile came back renamed')
    check(not (dest / 'Demo2' / 'resources').exists(), 'client material stayed out')
    check(not any(k == 'critical' for k, _, _ in boxes), f'no error boxes ({boxes})')
    window.close()
    print('PASS' if not failures else f'{len(failures)} FAILED')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
