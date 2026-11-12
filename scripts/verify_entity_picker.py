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
an entity on offer and visible, or from_context where offered.
"""

import os
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


def main(out: Path, seed: int = 0, rounds: int = 60) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from tumblepipe.pipe.houdini.ui.widgets.entity_selector import EntitySelectorDialog

    thumbs = _thumbnails(out / 'thumbs', SHOTS + ASSETS)
    failures = []

    def check(ok, message):
        print(('ok   ' if ok else 'FAIL ') + message)
        if not ok:
            failures.append(message)

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
    _fuzz(app, picker, check, seed, rounds)

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
    args = parser.parse_args()

    sys.exit(main(Path(args.out), args.seed, args.rounds))
