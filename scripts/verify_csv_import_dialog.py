"""Drive Import Shots from CSV…'s plan dialog (#32) headless.

Runs under Houdini's python313 (PySide6 and tumbletrove's theme on
PYTHONPATH, Houdini's bin on the DLL path, no licence, no ``hou``) against a
temp project made by ``tests/_harness``, like ``verify_farm_defaults.py``.

Covers: the dialog lists the plan's [ADD SEQ] / [ADD] / [ERROR] lines, its
Import button names the shot count, and the button is off when the sheet
adds nothing. Exits 1 on a failure.
"""

import csv
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))

import _harness  # noqa: E402  (temp project + TH_* env)

from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> int:
    _harness.reset_project()
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from tumblepipe.api import default_client
    from tumblepipe.asset_browser import csv_import
    from tumblepipe.tools import csv_shot_import as tool

    failures = []

    def check(what, ok, detail=''):
        print(('OK   ' if ok else 'FAIL ') + what + (f'  ({detail})' if detail and not ok else ''))
        if not ok:
            failures.append(what)

    config = default_client().config
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'sheet.csv'
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            csv.writer(handle).writerows([
                ['Shot list'], [], ['Cut #', 'Sequence', 'ShotName', 'Start', 'End'],
                ['', 'vq010 - Opening', '', '', ''],
                ['1', 'vq010', 'SH010', '1001', '1048'],
                ['2', 'vq010', 'SH020', '1001', '1072'],
                ['3', 'vq010', 'SH 030', '1001', '1024'],
            ])
        sheet = tool.plan_sheet(path, config)
        dialog = csv_import.make_dialog('verify', str(path), sheet)
        text = dialog.listing.toPlainText()
        check('[ADD SEQ], [ADD] and [ERROR] lines are listed',
              '[ADD SEQ] entity:/shots/vq010' in text and '[ADD] entity:/shots/vq010/SH020' in text
              and '[ERROR] entity:/shots/vq010/SH 030' in text, text)
        check('Import names the shot count and is on',
              dialog.import_button.text() == 'Import 2 Shots' and dialog.import_button.isEnabled(),
              dialog.import_button.text())
        tool.write_plan(config, sheet.plan)
        again = tool.plan_sheet(path, config)
        dialog = csv_import.make_dialog('verify', str(path), again)
        check('a sheet adding nothing turns Import off',
              not dialog.import_button.isEnabled() and dialog.import_button.text() == 'Import 0 Shots')

    print('ALL OK' if not failures else f'{len(failures)} FAILED')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
