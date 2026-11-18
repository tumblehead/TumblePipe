"""Drive the entity picker window offscreen and save what it looks like.

    QT_QPA_PLATFORM=offscreen python verify_entity_picker.py <out_dir>

Runs on Houdini's bundled python313 (PySide6, no licence) with this
checkout's python/ and a tumbletrove python/ on PYTHONPATH; no project is
needed: the window is given a made-up shot list and thumbnails. It checks
the keyboard flow (type to filter, arrows, Enter picks, From context) and
writes picker.png, picker_search.png and picker_assets.png to <out_dir>.
It also breaks TumbleTrove's card renderer (gone, then a changed paint()
signature) and checks the picker still lists, paints and picks, and fuzzes
random entity sets and key sequences (--seed, --rounds): every pick must be
an entity on offer and visible, or from_context where offered. Multi-select
(th::import_assets' top Browse...) is driven with real Ctrl-, Shift- and
plain clicks, searches and Select all in view, writes picker_multi.png, and
is fuzzed: what it returns is entities on offer, once each, in picker order.

Last, the ways a picker ends (LIFETIMES), each in a process of its own
because getting one wrong is an access violation, not a failed check: it is
freed as soon as it is dropped (nothing under it keeps it alive), and it
survives the cyclic collector freeing it, Qt deleting it first, and its
parent's deletion. TumblePipe 1.112.0 made the card delegate the grid's
child while it still referred to the picker; the collector then freed the
grid first, and the picker deleted the half-destroyed grid a second time.
``--part window`` and ``--part lifetimes`` run one half.
"""

import os
import subprocess
import sys
from pathlib import Path

if os.environ.get('HOUDINI_BIN'):
    os.add_dll_directory(os.environ['HOUDINI_BIN'])

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SHOTS = [
    f'entity:/shots/{seq}/{shot}'
    for seq, shots in {
        '001': ['sh010', 'sh020', 'sh030', 'sh040'],
        '010': ['sh010', 'sh020', 'sh030'],
        'ShotExamples': ['intro', 'chase', 'finale'],
    }.items()
    for shot in shots
]
ASSETS = [
    'entity:/assets/CHAR/remy', 'entity:/assets/CHAR/magnus',
    'entity:/assets/PROP/lantern', 'entity:/assets/SET/forest',
]


def _thumbnails(folder: Path, uris) -> dict:
    """A gradient 'render' for every other entity; the rest show the
    placeholder, as entities without a thumbnail.png do."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for i, uri in enumerate(uris):
        if i % 2:
            continue
        image = QImage(320, 180, QImage.Format_RGB32)
        painter = QPainter(image)
        gradient = QLinearGradient(0, 0, 320, 180)
        gradient.setColorAt(0, QColor.fromHsv((i * 37) % 360, 120, 150))
        gradient.setColorAt(1, QColor.fromHsv((i * 37 + 60) % 360, 160, 60))
        painter.fillRect(image.rect(), gradient)
        painter.end()
        path = folder / f'thumb{i}.png'
        image.save(str(path))
        paths[uri] = path
    return paths


# How a picker ends -> what must hold. Each runs alone, in _lifetime().
LIFETIMES = {
    'dropped': 'is freed as soon as it is dropped: nothing under it keeps it alive',
    'collected': 'survives the cyclic collector freeing it',
    'deleted': 'survives Qt deleting it first and the collector after',
    'parented': "survives its parent's deletion (Houdini's main window, at exit)",
}


def _lifetime(mode: str) -> int:
    """End a single-pick and a multi-pick picker the ``mode`` way. Prints
    ``survived`` when both did; a crash ends the process before that."""
    import gc
    import weakref

    import shiboken6
    from PySide6.QtWidgets import QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    from tumblepipe.pipe.houdini.ui.widgets.entity_selector import EntitySelectorDialog

    def shown(multi: bool, parent=None):
        dialog = EntitySelectorDialog(
            api=None, entity_uris=SHOTS + ASSETS, recent=[],
            multi_select=multi, parent=parent,
        )
        dialog.show()
        app.processEvents()
        dialog.grab()  # paints every card, so the delegate has run
        return dialog

    def read_kept_references(grid) -> None:
        # What PySide keeps on the grid (model, delegate, selection model):
        # one of them freed crashes here every time, where the collector
        # crashes only once the memory was reused.
        for _ in range(200):
            shiboken6.dump(grid)

    gc.disable()  # only the collections asked for below
    for multi in (False, True):
        if mode == 'dropped':
            dialog = shown(multi)
            dialog.reject()
            alive = weakref.ref(dialog)
            del dialog
            if alive() is not None:
                print(f'still alive (multi={multi}): something it owns refers back to it')
                return 1
        elif mode == 'collected':
            for _ in range(3):
                dialog = shown(multi)
                dialog.keeps_itself = dialog  # as any cycle through it would
                del dialog
                gc.collect()
                app.processEvents()
        elif mode == 'deleted':
            dialog = shown(multi)
            dialog.keeps_itself = dialog
            grid = dialog._grid
            shiboken6.delete(dialog)
            app.processEvents()
            read_kept_references(grid)
            del dialog, grid
            gc.collect()
        elif mode == 'parented':
            parent = QWidget()
            dialog = shown(multi, parent)
            dialog.keeps_itself = dialog
            grid = dialog._grid
            shiboken6.delete(parent)
            app.processEvents()
            read_kept_references(grid)
            del dialog, grid, parent
            gc.collect()
        else:
            raise SystemExit(f'unknown lifetime {mode!r}')
        app.processEvents()
    print('survived', flush=True)
    return 0


def _check_lifetimes(check):
    """Run every LIFETIMES mode in its own process."""
    for mode, holds in LIFETIMES.items():
        proc = subprocess.run(
            [sys.executable, __file__, '--lifetime', mode],
            check=False, capture_output=True, text=True, timeout=300,
        )
        ok = proc.returncode == 0 and 'survived' in proc.stdout
        said = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        check(ok, f'lifetime {mode}: the picker {holds}'
              + ('' if ok else f' (exit {proc.returncode}: {" | ".join(said)})'))


def main(out: Path, seed: int = 0, rounds: int = 60, part: str = 'all') -> int:
    failures = []

    def check(ok, message):
        print(('ok   ' if ok else 'FAIL ') + message, flush=True)
        if not ok:
            failures.append(message)

    if part != 'window':
        _check_lifetimes(check)
    if part == 'lifetimes':
        return 1 if failures else 0

    app = QApplication.instance() or QApplication(sys.argv)
    from tumblepipe.pipe.houdini.ui.widgets.entity_selector import EntitySelectorDialog

    thumbs = _thumbnails(out / 'thumbs', SHOTS + ASSETS)

    def picker(uris, **kwargs):
        dialog = EntitySelectorDialog(
            api=None, entity_uris=uris, thumbnail_path=thumbs.get,
            recent=['entity:/shots/010/sh020', 'entity:/shots/001/sh030'],
            **kwargs,
        )
        dialog.show()
        app.processEvents()
        return dialog

    dialog = picker(
        SHOTS, include_from_context=True, current_selection='from_context',
        from_context_uri='entity:/shots/010/sh020', title='Select Shot',
    )
    check(dialog._from_context.text() == 'From context (010/sh020)',
          f'From context row reads {dialog._from_context.text()!r}')
    check(dialog.get_selected_uri() == 'from_context', 'opens on From context')
    dialog.grab().save(str(out / 'picker.png'))

    QTest.keyClicks(dialog._search, '010 sh03')
    app.processEvents()
    check(dialog._model.rowCount() == 1, f'"010 sh03" leaves {dialog._model.rowCount()} card(s)')
    check(dialog.get_selected_uri() == 'entity:/shots/010/sh030', 'the match is selected')
    dialog.grab().save(str(out / 'picker_search.png'))
    QTest.keyClick(dialog._search, Qt.Key_Return)
    check(dialog.result() == 1 and dialog.get_selected_uri() == 'entity:/shots/010/sh030',
          'Enter picks the match')

    dialog = picker(SHOTS, current_selection='entity:/shots/001/sh020')
    check(dialog.get_selected_uri() == 'entity:/shots/001/sh020', 'opens on the current pick')
    QTest.keyClick(dialog._search, Qt.Key_Right)
    app.processEvents()
    check(dialog.get_selected_uri() == 'entity:/shots/001/sh030', 'arrow keys move through the cards')
    dialog.reject()

    dialog = picker(
        ASSETS, include_from_context=False, current_selection='entity:/assets/CHAR/remy',
        title='Select Asset',
    )
    check(not dialog._from_context.isVisible(), 'no From context row where none is offered')
    dialog.grab().save(str(out / 'picker_assets.png'))
    dialog.reject()

    _check_fallback(app, picker, check, out)
    _check_multi(app, picker, check, out)
    _fuzz(app, picker, check, seed, rounds)
    _fuzz_multi(app, picker, check, seed, max(10, rounds // 3))

    print(f'images in {out}')
    return 1 if failures else 0


def _check_fallback(app, picker, check, out: Path):
    """The picker builds, lists and paints every entity when TumbleTrove's
    card renderer is gone or has changed shape: picking must not depend on
    a TumbleTrove internal."""
    import tumbletrove.asset_browser.ui.card_renderer as card_renderer

    name = 'tumbletrove.asset_browser.ui.card_renderer'
    saved_module = sys.modules[name]
    saved_paint = card_renderer.GridCardRenderer.paint
    cases = {
        'missing': lambda: sys.modules.__setitem__(name, None),
        'signature': lambda: setattr(
            card_renderer.GridCardRenderer, 'paint', lambda self, painter, rect: None,
        ),
    }
    for case, breakit in cases.items():
        breakit()
        try:
            dialog = picker(SHOTS + ASSETS, title=f'Fallback ({case})')
            image = dialog.grab()  # paints every visible card
            image.save(str(out / f'picker_fallback_{case}.png'))
            check(dialog._model.rowCount() == len(SHOTS + ASSETS),
                  f'renderer {case}: lists all {len(SHOTS + ASSETS)} entities')
            check(dialog._delegate._renderer is None,
                  f'renderer {case}: falls back to the plain card')
            QTest.keyClicks(dialog._search, 'remy')
            QTest.keyClick(dialog._search, Qt.Key_Return)
            check(dialog.get_selected_uri() == 'entity:/assets/CHAR/remy',
                  f'renderer {case}: still picks')
        finally:
            sys.modules[name] = saved_module
            card_renderer.GridCardRenderer.paint = saved_paint


def _click(app, dialog, uri, modifier=Qt.NoModifier):
    """Click the card of ``uri`` (shown) with ``modifier`` held."""
    row = dialog._state.visible_uris().index(uri)
    index = dialog._model.index(row, 0)
    rect = dialog._grid.visualRect(index)
    QTest.mouseClick(dialog._grid.viewport(), Qt.LeftButton, modifier, rect.center())
    app.processEvents()


def _search(app, dialog, text):
    dialog._search.clear()
    if text:
        QTest.keyClicks(dialog._search, text)
    app.processEvents()


def _check_multi(app, picker, check, out: Path):
    """th::import_assets' top Browse...: Ctrl-click and Shift-click pick
    several, picks survive a search, Select all in view adds what is
    shown, and the picks come back in the picker's order."""
    order = [
        'entity:/assets/CHAR/magnus', 'entity:/assets/CHAR/remy',
        'entity:/assets/PROP/lantern', 'entity:/assets/SET/forest',
    ]
    dialog = picker(ASSETS, include_from_context=True, title='Add Assets', multi_select=True)
    check(not dialog._from_context.isVisible(), 'multi: no From context row')
    check(dialog._select_all is not None and dialog._select_all.isVisible(),
          'multi: a Select all in view button')
    _click(app, dialog, 'entity:/assets/SET/forest')
    _click(app, dialog, 'entity:/assets/CHAR/remy', Qt.ControlModifier)
    check(dialog._state.picked == ['entity:/assets/CHAR/remy', 'entity:/assets/SET/forest'],
          f'multi: click then Ctrl-click picks two ({dialog._state.picked})')
    _search(app, dialog, 'lantern')
    _click(app, dialog, 'entity:/assets/PROP/lantern', Qt.ControlModifier)
    _search(app, dialog, '')
    check(dialog._state.picked == order[1:],
          f'multi: a pick made in a search keeps the others ({dialog._state.picked})')
    check(dialog._status.text() == '3 assets selected', f'multi: footer reads {dialog._status.text()!r}')
    selected = {i.data(Qt.UserRole + 1) for i in dialog._grid.selectionModel().selectedIndexes()}
    check(selected == set(order[1:]), 'multi: the grid shows the picks selected')
    dialog.grab().save(str(out / 'picker_multi.png'))
    _click(app, dialog, 'entity:/assets/PROP/lantern', Qt.ControlModifier)
    check(dialog._state.picked == [order[1], order[3]], 'multi: Ctrl-click again unpicks')
    _search(app, dialog, 'CHAR')
    dialog._select_all.click()
    app.processEvents()
    _search(app, dialog, '')
    check(dialog._state.picked == [order[0], order[1], order[3]],
          f'multi: Select all in view adds the shown cards ({dialog._state.picked})')
    dialog._ok_button.click()
    app.processEvents()
    check(dialog.result() == 1 and dialog.get_selected_uris() == [order[0], order[1], order[3]],
          f'multi: Select returns the picks in picker order ({dialog.get_selected_uris()})')

    dialog = picker(ASSETS, title='Add Assets', multi_select=True)
    _click(app, dialog, order[0])
    _click(app, dialog, order[2], Qt.ShiftModifier)
    check(dialog._state.picked == order[:3], f'multi: Shift-click picks a range ({dialog._state.picked})')
    dialog.reject()

    dialog = picker(ASSETS, title='Add Assets', multi_select=True)
    _search(app, dialog, 'forest')
    QTest.keyClick(dialog._search, Qt.Key_Return)
    app.processEvents()
    check(dialog.result() == 1 and dialog.get_selected_uris() == ['entity:/assets/SET/forest'],
          'multi: with nothing picked, Enter takes the best match')

    dialog = picker(ASSETS, title='Select Asset', current_selection=order[1])
    check(dialog._select_all is None, 'single: no Select all in view')
    _click(app, dialog, order[2], Qt.ControlModifier)
    check(dialog.get_selected_uris() == [order[2]], 'single: Ctrl-click still picks one')
    dialog.reject()


def _fuzz_multi(app, picker, check, seed: int, rounds: int):
    """Random clicks with and without Ctrl/Shift, searches, scopes, Select
    all and Enter on the multi window: what it returns is entities on
    offer, each once, in the picker's order."""
    import random

    from tumblepipe.pipe import entity_picker as ep

    rng = random.Random(seed ^ 0x5EED)
    pool = SHOTS + ASSETS
    modifiers = [Qt.NoModifier, Qt.ControlModifier, Qt.ShiftModifier]
    bad = []
    picks = 0
    for round_ in range(rounds):
        uris = rng.sample(pool, rng.randint(0, len(pool)))
        entries = ep.entries_from_uris(uris)
        order = [e.uri for e in entries]
        dialog = picker(uris, multi_select=True)
        for _ in range(rng.randint(1, 20)):
            action = rng.random()
            visible = dialog._state.visible_uris()
            if action < 0.45 and visible:
                _click(app, dialog, rng.choice(visible), rng.choice(modifiers))
            elif action < 0.6:
                _search(app, dialog, rng.choice(['', 's', 'sh0', '010', 'CHAR', 'remy', 'x']))
            elif action < 0.7:
                rows = dialog._sidebar.topLevelItemCount()
                if rows:
                    dialog._sidebar.setCurrentItem(dialog._sidebar.topLevelItem(rng.randrange(rows)))
            elif action < 0.8:
                dialog._select_all.click()
            elif action < 0.9:
                QTest.keyClick(dialog._search, rng.choice([Qt.Key_Right, Qt.Key_Down, Qt.Key_Left]))
            else:
                QTest.keyClick(dialog._search, Qt.Key_Return)
                app.processEvents()
                if dialog.result() == 1:
                    got = dialog.get_selected_uris()
                    picks += 1
                    if not got or got != [u for u in order if u in set(got)] or len(set(got)) != len(got):
                        bad.append((seed, round_, got))
                    break
        dialog.reject()
    check(not bad and picks > 0,
          f'multi fuzz seed {seed}, {rounds} rounds, {picks} picks: on offer, once, in order {bad[:3]}')


def _fuzz(app, picker, check, seed: int, rounds: int):
    """Random entity sets, searches and key sequences on the real window:
    whatever it returns is an entity on offer and visible when picked, or
    from_context where offered; Enter with no match returns nothing."""
    import random

    from tumblepipe.pipe import entity_picker as ep

    rng = random.Random(seed)
    pool = SHOTS + ASSETS + ['entity:/edits/cut01', 'entity:/edits/cut02']
    keys = [Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right,
            Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Backspace, Qt.Key_Return]
    bad = []
    picks = 0
    for round_ in range(rounds):
        uris = rng.sample(pool, rng.randint(0, len(pool)))
        offer_context = rng.random() < 0.5
        dialog = picker(
            uris, include_from_context=offer_context,
            current_selection=rng.choice(pool + ['from_context', '', 'entity:/shots/x/gone']),
        )
        offered = {ep.entry_for(u).uri for u in uris}
        result = None
        for _ in range(rng.randint(1, 25)):
            action = rng.random()
            if action < 0.45:
                QTest.keyClicks(dialog._search, rng.choice(['s', 'h', '0', '1', ' ', '/', 'a', 'x', 'remy', 'q']))
            elif action < 0.55 and offer_context:
                dialog._from_context.click()
            elif action < 0.62:
                rows = dialog._sidebar.topLevelItemCount()
                if rows:
                    dialog._sidebar.setCurrentItem(dialog._sidebar.topLevelItem(rng.randrange(rows)))
            elif action < 0.7:
                dialog._grid.setFocus()
                QTest.keyClick(dialog._grid, rng.choice([Qt.Key_Right, Qt.Key_Down, Qt.Key_Left]))
                dialog._search.setFocus()
            else:
                key = rng.choice(keys)
                visible = set(dialog._state.visible_uris())
                QTest.keyClick(dialog._search, key)
                app.processEvents()
                if key == Qt.Key_Return:
                    if dialog.result() == 1:
                        result = dialog.get_selected_uri()
                        picks += 1
                        ok = (result in visible and result in offered) or (
                            result == 'from_context' and offer_context)
                        if not ok:
                            bad.append((seed, round_, result, dialog._search.text()))
                        break
                    if not visible and not dialog._from_context.isChecked():
                        if dialog.get_selected_uri() is not None:
                            bad.append((seed, round_, 'returned with no match', dialog._search.text()))
        dialog.reject()
    check(not bad and picks > 0, f"fuzz seed {seed}, {rounds} rounds, {picks} picks: every pick on offer and visible {bad[:3]}")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('out', nargs='?', default='.')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--rounds', type=int, default=60)
    parser.add_argument('--part', choices=['all', 'window', 'lifetimes'], default='all')
    parser.add_argument('--lifetime', choices=list(LIFETIMES), help='(internal) end one picker this way')
    args = parser.parse_args()

    if args.lifetime:
        sys.exit(_lifetime(args.lifetime))
    sys.exit(main(Path(args.out), args.seed, args.rounds, args.part))
