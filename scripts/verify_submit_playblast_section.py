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
  6. Lighting and Culling (Martin, 2026-11-11) and Transparency (Martin,
     2026-11-12) are TumbleView rows whose menus offer the spec's values
     and read back as the look's tokens.
  7. The frames (Remy, 2026-11-26): Frames (First → Last, Step) and
     Pre / Post roll rows under Up to, seeded unpinned, pinned on an edit.
  8. Match Render copies the Render section's frames: a set field pins
     its playblast field, a per-entity one unpins it; a Frame list plays
     from its first frame to its last, rolls 0.

Run (no Houdini, no project):
    QT_QPA_PLATFORM=offscreen TUMBLETROVE_PYTHON=<tumbletrove>/python \\
        uv run --python 3.12 --with pyside6 python scripts/verify_submit_playblast_section.py

``TUMBLETROVE_PYTHON`` defaults to the newest TumbleTrove hpm has
fetched or installed (``.ci/hpm_packages.py``; ``TEST_HPM_FETCH_DIR``
names a folder ``.ci/fetch_hpm_packages.py`` laid out). Set ``PLAYBLAST_SECTION_SCREENSHOT=<png>`` to also save the section
under each renderer (``<png>`` gets ``_viewport`` / ``_karma`` suffixes).
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))


sys.path.insert(0, str(REPO_ROOT / '.ci'))
from hpm_packages import tumbletrove_python  # noqa: E402

_tt = tumbletrove_python()
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

    # 6. Lighting, Culling and Transparency (Martin, 2026-11-11/12):
    # TumbleView rows, menus of the spec's values, read back as the look's
    # tokens
    from tumblepipe.farm.specs.playblast import (
        PLAYBLAST_CULLS, PLAYBLAST_LIGHTINGS, PLAYBLAST_TRANSPARENCIES,
    )
    for key, label, values in (('pb_lighting', 'Lighting', PLAYBLAST_LIGHTINGS),
                               ('pb_cull', 'Culling', PLAYBLAST_CULLS),
                               ('pb_transparency', 'Transparency',
                                PLAYBLAST_TRANSPARENCIES)):
        field = pane._fields[key]
        widget = field.widget
        items = [widget.itemText(i) for i in range(widget.count())]
        offered = [item for item in items if item != sjd.PER_ENTITY_TEXT]
        check(f"{label} offers {list(values)}", offered == list(values), str(items))
        renderer.seed('viewport')
        pane._scope_playblast_look()
        texts = [text for _label, _row, text in pane._pb_rows['viewport']]
        check(f"{label} is a TumbleView row", label in texts, str(texts))
        for value in values:
            field.seed(value)
            check(f"{label} seeded {value} reads back {value}", field.value() == value,
                  repr(field.value()))

    # 7. The frames (Remy, 2026-11-26): First → Last, Step and Pre / Post
    # roll, spin fields that seed unpinned (each shot's own) and pin on an
    # edit
    frame_keys = ('pb_first_frame', 'pb_last_frame', 'pb_step', 'pb_pre_roll', 'pb_post_roll')
    check("the playblast has First, Last, Step and Pre / Post roll fields",
          all(key in pane._fields and pane._fields[key].kind == 'spin' for key in frame_keys))
    labels = [pane._pb_form.itemAt(i, sjd.QFormLayout.LabelRole).widget().text()
              for i in range(pane._pb_form.rowCount())
              if pane._pb_form.itemAt(i, sjd.QFormLayout.LabelRole) is not None]
    check("Frames and Pre / Post roll rows sit under Up to",
          labels[:3] == ['Up to:', 'Frames:', 'Pre / Post roll:'], str(labels[:4]))
    seeded = dict(pb_first_frame=1001, pb_last_frame=1065, pb_step=1, pb_pre_roll=0, pb_post_roll=0)
    pane._seeding = True    # as the dialog seeds (_reseed_form)
    for key, value in seeded.items():
        pane._fields[key].seed(value)
    pane._seeding = False
    check("seeded frames show the shot's range, unpinned",
          all(pane._fields[k].value() == v and not pane._fields[k].pinned for k, v in seeded.items()))
    pane._pb_last.setValue(1030)
    check("editing Last pins it", pane._fields['pb_last_frame'].pinned
          and pane._fields['pb_last_frame'].value() == 1030)

    # 8. Match Render copies the Render's frames: a set field pins its
    # playblast field to that value, a per-entity one unpins it; a Frame
    # list plays from its first frame to its last, rolls 0
    render = {key: pane._spin(key, low, 1_000_000) for key, low in (
        ('first_frame', -1_000_000), ('last_frame', -1_000_000), ('render_step', 1),
        ('pre_roll', 0), ('post_roll', 0),
    )}
    pane._rnd_frame_list = sjd.QLineEdit()
    for key, value in (('first_frame', 1010), ('render_step', 2), ('pre_roll', 4)):
        render[key].setValue(value)                 # pins, as an edit does
    pane._seeding = True
    pane._fields['last_frame'].seed(1065)            # per entity
    pane._fields['post_roll'].seed(0)
    pane._seeding = False
    pane._pb_match.click()
    got = {key: pane._fields[key].value() for key in frame_keys}
    pins = {key: pane._fields[key].pinned for key in frame_keys}
    check("Match Render pins the Render's set fields and unpins its per-entity ones",
          got['pb_first_frame'] == 1010 and got['pb_step'] == 2 and got['pb_pre_roll'] == 4
          and pins == dict(pb_first_frame=True, pb_last_frame=False, pb_step=True,
                           pb_pre_roll=True, pb_post_roll=False),
          f"{got} {pins}")
    pane._rnd_frame_list.setText('1020-1030 1040-1050')
    pane._pb_match.click()
    got = {key: pane._fields[key].value() for key in frame_keys}
    check("Match Render with a Frame list plays its first to last frame, rolls 0",
          (got['pb_first_frame'], got['pb_last_frame'], got['pb_pre_roll'], got['pb_post_roll'])
          == (1020, 1050, 0, 0) and all(pane._fields[k].pinned for k in frame_keys
                                        if k != 'pb_step'),
          str(got))

    shot = os.environ.get('PLAYBLAST_SECTION_SCREENSHOT')
    if shot:
        box.resize(520, box.sizeHint().height())
        for chosen in ('viewport', 'karma'):
            renderer.seed(chosen)
            pane._scope_playblast_look()
            box.grab().save(shot.replace('.png', f'_{chosen}.png'))

    total = 1 + 3 + 3 + 6 + 1 + 3 * 4 + 4 + 2
    print(f"\n{total - len(FAILED)}/{total} checks passed")
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
