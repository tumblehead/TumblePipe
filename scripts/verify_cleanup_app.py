"""Drive the Clean Up Project window headless: build, fill, list, delete.

    uv run --no-project --python 3.12 --with pyside6 --with usd-core python scripts/verify_cleanup_app.py [shot.png]

(``.ci/preflight.py --only qt`` runs it so, with TumbleTrove's python/ on
PYTHONPATH; Houdini's python313 runs it too.) Offscreen, no licence, no
``hou``. It checks that the window (``tumblepipe.cleanup.app``):

- fills its overview from a made-up listing: the total, a warning banner for
  every warning note (".hiplc ... NOT protected"), a row per category with
  its counts, opened when it warns, and its reasons, biggest-first groups
  and errors under it; the Delete button says how much;
- goes stale when a setting changes: Delete is off until the next List;
- lists a small real project (old ``_deleted`` folders and backups; Deadline
  "could not be asked", so the job folders warn), deletes nothing on Back
  from the confirmation card, and on Delete removes exactly what was listed.

With a path argument it saves a screenshot of the filled window there.
Exits 1 on a failure.
"""

import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('TH_PIPELINE_PATH', str(REPO))
if os.environ.get('TUMBLETROVE_PYTHON'):
    sys.path.insert(1, os.environ['TUMBLETROVE_PYTHON'])

_DAY = 86400


def _age(path: Path, days: float) -> None:
    stamp = time.time() - days * _DAY
    os.utime(path, (stamp, stamp))


def build_project(base: Path) -> tuple[Path, list[Path], list[Path]]:
    """A project with two old ``_deleted`` folders and a new one, and a
    shot's backups: of an older workfile version (old: go) and of the
    latest (new: stay). Returns it, what must go, what must stay."""
    project = base / 'Demo'
    (project / '_config').mkdir(parents=True)
    (project / '_config' / 'storage.json').write_bytes(
        (REPO / 'scripts' / 'project_template' / '_config' / 'storage.json').read_bytes())
    (project / '_config' / 'version.json').write_bytes(b'{"version": 22}')
    goes, stays = [], []
    now = time.time()
    for days in (400, 90, 1):
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(now - days * _DAY))
        folder = project / '_deleted' / stamp
        (folder / 'shots' / 's0' / 'gone').mkdir(parents=True)
        (folder / 'shots' / 's0' / 'gone' / 'x.bin').write_bytes(b'gone' * 100)
        (goes if days >= 30 else stays).append(folder)
    comp = project / 'shots' / 's0' / 'h0' / 'comp'
    (comp / 'backup').mkdir(parents=True)
    for version in (1, 2):
        (comp / f'h0_comp_v000{version}.hip').write_bytes(b'HIP\n')
    for version, days in ((1, 30), (2, 1)):
        backup = comp / 'backup' / f'comp_v000{version}_bak1.hip'
        backup.write_bytes(b'b' * 50)
        _age(backup, days)
        (goes if days >= 7 else stays).append(backup)
    return project, goes, stays


def synthetic_plan():
    from tumblepipe.cleanup import core
    entries = [
        core.Entry(Path(f'/x/{i}'), f'export/assets/CHAR/Hero/default/model/v{i:04d}', 'assets/CHAR/Hero',
                   1000 * (i + 1), i < 4, 'older than the newest 3' if i < 4 else 'one of the newest 3')
        for i in range(7)
    ]
    entries += [core.Entry(Path('/y'), 'export/shots/s0/h0/default/anim/v0001', 'shots/s0/h0', 5_000_000, True,
                           'older than the newest 3')]
    exports = core.CategoryPlan('exports', 'Old export versions', 'version', entries, notes=[
        'Read 12 latest workfile(s) for pinned versions.',
        '40 latest workfile(s) are .hiplc/.hipnc and cannot be read: versions they pin are NOT protected.',
    ])
    caches = core.CategoryPlan('caches', 'Old cache versions', 'version', [
        core.Entry(Path('/z'), 'shots/s0/h0/fx/cache/smoke/v0001', 'shots/s0/h0/fx', 300, False, 'named by a workfile'),
    ], notes=['Read 3 latest workfile(s) for the cache versions they name.'],
        errors=['P:/Demo/export/broken.usda: could not parse'])
    roots = SimpleNamespace(project_path=Path('Demo'), get=lambda purpose: None)
    return core.Plan(roots, core.Settings(), [exports, caches])


def main() -> int:
    from tumblepipe.cleanup.app import _add_houdini_dll_directory, build_window
    _add_houdini_dll_directory()
    from PySide6.QtWidgets import QApplication
    from tumblepipe import prune
    from tumblepipe.cleanup import jobs, overview

    app = QApplication.instance() or QApplication([])
    failures = []

    def check(condition, message):
        print(('ok    ' if condition else 'FAIL  ') + message)
        if not condition:
            failures.append(message)

    # ── a made-up listing ─────────────────────────────────
    window = build_window()
    window.show()
    app.processEvents()
    check(set(window.sections) == set(window.checks) and len(window.sections) == 6,
          'a section per category, each with its tick')
    check(not window.delete_button.isEnabled(), 'Delete is off before a listing')
    check(all(spin.lineEdit().width() > 20 for spin in window.spins.values()),
          "every setting's number has room to show")
    plan = synthetic_plan()
    window.show_plan(plan)
    app.processEvents()
    check(window.headline.text() == overview.headline(window.view) and 'to free' in window.headline.text(),
          f'the total heads the overview ({window.headline.text()!r})')
    banners = window.banner_texts()
    check(any('NOT protected' in text for text in banners) and len(banners) == 1,
          f'the .hiplc warning is a banner, and only it ({banners})')
    tree = window.tree
    check(tree.topLevelItemCount() == 2, 'a row per category listed')
    exports_row, caches_row = tree.topLevelItem(0), tree.topLevelItem(1)
    check(exports_row.text(1) == '8 versions' and exports_row.text(2) == '5',
          f'the exports row counts found and to delete ({exports_row.text(1)!r}, {exports_row.text(2)!r})')
    check(exports_row.isExpanded() and 'NOT protected' in exports_row.child(0).text(0),
          'the warning category is open with its warning first')
    child_texts = [caches_row.child(i).text(0) for i in range(caches_row.childCount())]
    check(any(text.startswith('Could not be read: 1') for text in child_texts), f'errors are listed ({child_texts})')
    why = exports_row.child(1)
    check(why.text(0) == 'Why' and why.childCount() == 2 and why.child(0).text(2) == '5',
          'Why groups the reasons, the deleting one first')
    groups = exports_row.child(2)
    check(groups.childCount() == 2 and groups.child(0).text(0) == 'shots/s0/h0', 'biggest group first')
    check(window.delete_button.text() == overview.delete_label(window.view)
          and window.delete_button.text().startswith('Delete 5 item(s)'),
          f'Delete says how much ({window.delete_button.text()!r})')
    if len(sys.argv) > 1:
        window.resize(1180, 780)
        app.processEvents()
        window.grab().save(sys.argv[1])
        print(f'saved {sys.argv[1]}')
    window.spins['keep_exports'].setValue(5)
    app.processEvents()
    check(window.plan is None and not window.delete_button.isEnabled() and not tree.isEnabled(),
          'a changed setting makes the listing stale: no Delete until List')
    window.checks['jobs'].setChecked(False)
    check(not window.spins['job_days'].isEnabled() and 'not looked at' in window.sections['jobs']._header._summary,
          'an unticked section greys its settings and says so')
    window.close()

    # ── a real project, listed and deleted ────────────────
    base = Path(tempfile.mkdtemp(prefix='cleanup-app-'))
    project, goes, stays = build_project(base)
    jobs._ask_deadline = lambda: (None, 'Deadline could not be asked (verify): no job folder will be deleted.')
    prune.local_queue_folders = lambda root_path=None: set()
    (project / 'export' / 'other' / 'jobs' / 'aaaa').mkdir(parents=True)
    os.environ['TH_PROJECT_PATH'] = str(project)
    window = build_window()
    window.show()

    def wait(label: str, timeout: float = 120.0):
        deadline = time.monotonic() + timeout
        while window._thread is None or window._thread.isRunning() or not window.list_button.isEnabled():
            app.processEvents()
            if time.monotonic() > deadline:
                raise SystemExit(f'timed out: {label}')
            time.sleep(0.01)
        app.processEvents()

    window._list()
    wait('list')
    check(window.plan is not None, f'List made a plan ({window.status.text()})')
    listed = {entry.path for entry in window.plan.prunable} if window.plan else set()
    check(listed == {p.resolve() for p in goes} or listed == set(goes),
          f'it lists exactly the old folders and backups ({sorted(map(str, listed))})')
    check(window.tree.topLevelItemCount() == 6, 'a row per category')
    check(any('Deadline could not be asked' in text for text in window.banner_texts()),
          'Deadline that cannot be asked is a warning banner')
    check(window.delete_button.isEnabled(), 'Delete is on after a listing with something to delete')
    window._delete()
    app.processEvents()
    check(window.card is not None and window.overlay.isVisible(), 'Delete asks on a card first')
    if len(sys.argv) > 1:
        shot = Path(sys.argv[1])
        window.grab().save(str(shot.with_name(shot.stem + '-confirm' + shot.suffix)))
    window.card.back_button.click()
    app.processEvents()
    check(window.card is None and all(p.exists() for p in goes), 'Back deletes nothing')
    window._delete()
    app.processEvents()
    window.card.delete_button.click()
    wait('delete')
    check(not any(p.exists() for p in goes) and all(p.exists() for p in stays),
          'Delete removes exactly what was listed')
    check(window.headline.text().endswith('freed') and window.plan is None, 'and says what it freed')
    window.close()
    print('PASS' if not failures else f'{len(failures)} FAILED')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
