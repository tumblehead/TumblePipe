"""Headless Qt harness for the Farm Submit dialog's Playblast settings.

Builds the real Playblast section of ``asset_browser.submit_jobs_dialog`` on
a bare pane (only what the section builder touches) and pins:

  1. Materials is a three-row menu, ``⟨per entity⟩`` / Scene materials /
     Clay, not a tri-state checkbox: partially checked read as checked, and
     two submissions that looked identical rendered differently
     (tumblepipe__bugs 2026-10-30, wifi_retina).
  2. Seeding shows a value without pinning it, and reads it back: per entity
     on the placeholder row, yes on row 1, no on row 2.
  3. Picking a row pins it; picking the placeholder goes back to per entity.
  4. Only the chosen Renderer's rows show, label included: Shading / Blur
     under viewport, Light / Samples / Materials under karma. With Renderer
     per entity every row shows, each label naming its renderer, under a
     line saying why (greyed-out rows read as locked, 2026-11).
  5. The viewport renderer shows as TumbleView and reads back as
     ``viewport``, the name looks and jobs keep.

Run (no Houdini, no project):
    QT_QPA_PLATFORM=offscreen TUMBLETROVE_PYTHON=<tumbletrove>/python \\
        uv run --python 3.12 --with pyside6 python scripts/verify_submit_playblast_section.py

``TUMBLETROVE_PYTHON`` defaults to the newest ``.tools/hpm-fetch/
tumbletrove-tumbletrove-*/python`` (``.ci/fetch_hpm_packages.py`` lays it
out). Set ``PLAYBLAST_SECTION_SCREENSHOT=<png>`` to also save the section
under each renderer (``<png>`` gets ``_viewport`` / ``_karma`` suffixes).
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))


def _tumbletrove_python() -> Path | None:
    configured = os.environ.get('TUMBLETROVE_PYTHON')
    if configured:
        return Path(configured)
    fetched = sorted((REPO_ROOT / '.tools' / 'hpm-fetch').glob('tumbletrove-tumbletrove-*/python'))
    return fetched[-1] if fetched else None


_tt = _tumbletrove_python()
if _tt is None or not _tt.is_dir():
    sys.exit("No tumbletrove python found: set TUMBLETROVE_PYTHON (see the docstring).")
sys.path.insert(0, str(_tt))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from tumblepipe.asset_browser import submit_jobs_dialog as sjd  # noqa: E402
from tumblepipe.farm.submit import settings as resolve  # noqa: E402

FAILED = []


def check(name: str, ok: bool, info: str = '') -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"  ({info})" if info and not ok else ''))
    if not ok:
        FAILED.append(name)


class _Timer:
    def start(self) -> None:
        pass


def build_pane():
    """A _ContextPane with only what _build_playblast_section touches."""
    pane = sjd._ContextPane.__new__(sjd._ContextPane)
    QWidget.__init__(pane)
    pane._fields = {}
    pane._seeding = False
    pane._renderable_departments = ['animation', 'render']
    pane._refresh_timer = _Timer()
    pane._changed = lambda: None
    pane._on_cut_changed = lambda: None
    pane._seed_values = lambda: {}
    pane._reverts = []
    return pane, pane._build_playblast_section()


def main() -> int:
    pane, box = build_pane()

    # 1. The menu
    materials = pane._fields['pb_materials']
    combo = materials.widget
    rows = [combo.itemText(i) for i in range(combo.count())]
    check("Materials is a ⟨per entity⟩ / Scene materials / Clay menu",
          materials.kind == 'toggle' and rows == [sjd.PER_ENTITY_TEXT, 'Scene materials', 'Clay'],
          str(rows))

    # 2. Seeding
    for seeded, row in ((resolve.MIXED, 0), (True, 1), (False, 2)):
        materials.seed(seeded)
        check(f"seeding {seeded if seeded is not resolve.MIXED else 'per entity'} shows row {row}, unpinned, reads back",
              combo.currentIndex() == row and not materials.pinned and materials.value() is seeded
              if seeded is resolve.MIXED else
              combo.currentIndex() == row and not materials.pinned and materials.value() == seeded,
              f"row={combo.currentIndex()} pinned={materials.pinned} value={materials.value()!r}")

    # 3. Picking
    combo.setCurrentIndex(2)
    combo.activated.emit(2)
    check("picking Clay pins False", materials.pinned and materials.value() is False)
    combo.setCurrentIndex(1)
    combo.activated.emit(1)
    check("picking Scene materials pins True", materials.pinned and materials.value() is True)
    combo.setCurrentIndex(0)
    combo.activated.emit(0)
    check("picking ⟨per entity⟩ goes back to per entity",
          not materials.pinned and materials.value() is resolve.MIXED)

    # 4. Only the chosen renderer's rows
    renderer = pane._fields['pb_renderer']
    form = pane._pb_form

    def shown_rows(name):
        return {form.isRowVisible(row) for _label, row, _text in pane._pb_rows[name]}

    def labels(name):
        return [label.text() for label, _row, _text in pane._pb_rows[name]]

    for chosen, viewport_on, karma_on in (
        ('viewport', True, False), ('karma', False, True), (resolve.MIXED, True, True),
    ):
        renderer.seed(chosen)
        pane._scope_playblast_look()
        name = 'per entity' if chosen is resolve.MIXED else chosen
        check(f"Renderer {name}: viewport rows {'shown' if viewport_on else 'hidden'}, "
              f"karma rows {'shown' if karma_on else 'hidden'}",
              shown_rows('viewport') == {viewport_on} and shown_rows('karma') == {karma_on},
              f"viewport={shown_rows('viewport')} karma={shown_rows('karma')}")
        mixed = chosen is resolve.MIXED
        check(f"Renderer {name}: labels {'name their renderer' if mixed else 'are plain'}, "
              f"the why-line {'shows' if mixed else 'is hidden'}",
              form.isRowVisible(pane._pb_hint) == mixed
              and all(('(' in text) == mixed for text in labels('viewport') + labels('karma')),
              f"{labels('viewport') + labels('karma')}")

    # 5. The renderer shows as TumbleView and stays 'viewport' in looks and jobs
    renderer.seed('viewport')
    check("Renderer viewport shows as TumbleView and reads back as viewport",
          renderer.widget.currentText() == 'TumbleView' and renderer.value() == 'viewport',
          f"{renderer.widget.currentText()!r} -> {renderer.value()!r}")

    shot = os.environ.get('PLAYBLAST_SECTION_SCREENSHOT')
    if shot:
        box.resize(520, box.sizeHint().height())
        for chosen in ('viewport', 'karma'):
            renderer.seed(chosen)
            pane._scope_playblast_look()
            box.grab().save(shot.replace('.png', f'_{chosen}.png'))

    total = 1 + 3 + 3 + 6 + 1
    print(f"\n{total - len(FAILED)}/{total} checks passed")
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
