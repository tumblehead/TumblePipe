"""Headless Qt harness: Farm Submit's Render Up to, Camera field and stale legend.

Remy, 2026-11-20 (tumblepipe rfe): the Render **Up to** defaulting to the
open workfile's department was a common source of errors; the Camera was
read-only and once showed ``/cameras/camera1``, which nothing defined; and
"stale" was explained only in a tooltip. The decisions are arithmetic
(``tests/test_farm_submit_up_to.py``, ``test_farm_submit_camera.py``,
``test_farm_submit_stale.py``); this pins the wiring on the real dialog,
shown, over the template project:

  1. Opened from a workfile's department, only the Playblast cut is pinned
     to it; the Render cut stays per entity, on the shots' latest
     department. Opened with a Render cut asked for outright
     (th::import_render), the Render cut is pinned to it.
  2. The Up to menus list only the departments on for every ticked shot,
     and list a department again once the shot that turns it off is
     unticked; a pinned cut survives the change of ticks.
  3. Typing a camera pins the Camera and the ticked rows send it; emptying
     the field unpins it and nothing is sent.
  4. Shown a department's ``/cameras/camera1``, the empty field says so as
     its placeholder, the note under it names Houdini's default, the menu
     offers the shot's configured camera, and a typed camera hides the note.
  5. The stale legend sits under the grid, in words.

Run (no Houdini; the preflight ``qt`` stage sets TH_PROJECT_PATH at a copy
of scripts/project_template)::

    QT_QPA_PLATFORM=offscreen TUMBLETROVE_PYTHON=<tumbletrove>/python \\
        uv run --no-project --python 3.12 --with pyside6 python scripts/verify_submit_up_to_camera.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Whatever the window remembers goes to a temp prefs dir, never the artist's.
os.environ["HOUDINI_USER_PREF_DIR"] = tempfile.mkdtemp(prefix="up_to_camera_prefs_")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from tumblepipe.asset_browser import submit_jobs_dialog as mod  # noqa: E402
from tumblepipe.farm.submit import camera as render_camera  # noqa: E402
from tumblepipe.farm.submit import grid  # noqa: E402
from tumblepipe.farm.submit import settings as resolve  # noqa: E402
from tumblepipe.farm.submit import stale as stale_words  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""), flush=True)


def settle() -> None:
    for _ in range(6):
        app.processEvents()


def refresh(pane) -> None:
    pane._refresh()
    settle()


def menu(pane, key: str) -> list[str]:
    box = pane._fields[key].widget
    return [box.itemData(i) or box.itemText(i) for i in range(1, box.count())]


def main() -> int:
    from tumblepipe.api import default_client
    config = default_client().config
    renderable = mod._list_dept_names("shots", only_publishable=False, only_renderable=True)
    if len(renderable) < 3:
        print(f"SKIP: the template needs 3+ renderable shot departments (got {renderable})")
        return 1
    first, last = renderable[0], renderable[-1]
    config.add_entity(Uri.parse("entity:/shots/010"), {})
    plain = "entity:/shots/010/sh010"
    without_last = "entity:/shots/010/sh020"
    config.add_entity(Uri.parse(plain), {
        "frame_start": 1001, "frame_end": 1010,
        "render": {"apply_config": True, "camera": "/cameras/render_camera"},
    })
    config.add_entity(Uri.parse(without_last), {
        "frame_start": 1001, "frame_end": 1010, "disabled_departments": [last],
    })

    # ── 1: what pins the cuts ─────────────────────────────
    win = mod.SubmitJobsDialog([Uri.parse(plain)], "shots", department=first)
    win.show()
    settle()
    pane = win._pane("shots")
    render_cut, pb_cut = pane._fields["render_department"], pane._fields["pb_department"]
    check("1. the workfile's department pins the Playblast cut",
          pb_cut.pinned and pb_cut.value() == first, f"{pb_cut.pinned} {pb_cut.value()}")
    check("1. the Render cut is not pinned, and shows the shot's latest department",
          not render_cut.pinned and render_cut.value() == last,
          f"{render_cut.pinned} {render_cut.value()}")
    check("1. the row renders up to its latest department",
          pane._cut(plain, "render_department") == last)

    # ── 5: the stale legend ───────────────────────────────
    check("5. the stale legend is under the grid, in words",
          pane._stale_legend.isVisible() and pane._stale_legend.text() == stale_words.STALE_LEGEND)

    # ── 2: the menus follow the ticked shots ──────────────
    check("2. one shot with everything on: the menu lists every renderable department",
          menu(pane, "render_department") == renderable, str(menu(pane, "render_department")))
    pane._ticks = {(plain, grid.RENDER), (without_last, grid.RENDER)}
    refresh(pane)
    check("2. a ticked shot with a department off: neither menu lists it",
          last not in menu(pane, "render_department") and last not in menu(pane, "pb_department"),
          str(menu(pane, "render_department")))
    check("2. a pinned Playblast cut survives the change of ticks",
          pb_cut.pinned and pb_cut.value() == first)
    pane._ticks = {(plain, grid.RENDER)}
    refresh(pane)
    check("2. unticking that shot lists the department again",
          menu(pane, "render_department") == renderable)

    # ── 3: typing a camera pins and sends it ──────────────
    camera = pane._fields[resolve.CAMERA_KEY]
    edit = pane._rnd_camera.lineEdit()
    edit.setText("/cameras/shotcam")
    edit.textEdited.emit("/cameras/shotcam")
    refresh(pane)
    rows = pane._resolved_rows()
    check("3. a typed camera pins the field", camera.pinned and camera.value() == "/cameras/shotcam")
    check("3. the ticked row sends it",
          [r.settings.get(resolve.CAMERA_KEY) for r in rows] == ["/cameras/shotcam"],
          str([r.settings.get(resolve.CAMERA_KEY) for r in rows]))
    edit.setText("")
    edit.textEdited.emit("")
    refresh(pane)
    rows = pane._resolved_rows()
    check("3. an emptied field is unpinned and sends nothing",
          not camera.pinned and all(resolve.CAMERA_KEY not in r.settings for r in rows))

    # ── 4: what the field says about the scene's camera ───
    keys = pane._camera_keys()
    for key in keys:
        pane._cameras[key] = render_camera.RenderCamera(
            key[0], "/cameras/camera1", "light",
            cameras=("/cameras/render_camera",), root_cameras=("/cameras/render_camera",),
        )
    pane._camera_pending = {}
    pane._show_camera(keys)
    settle()
    note = pane._rnd_camera_note
    check("4. the empty field shows the scene's camera",
          edit.placeholderText() == "from scene: /cameras/camera1", edit.placeholderText())
    check("4. the note under it names Houdini's default and the department",
          note.isVisible() and "Houdini" in note.text() and "light" in note.text(), note.text())
    items = [pane._rnd_camera.itemText(i) for i in range(pane._rnd_camera.count())]
    check("4. the menu offers the shot's configured camera first",
          items[:1] == ["/cameras/render_camera"] and "/cameras/camera1" in items, str(items))
    pane._rnd_camera.setCurrentIndex(0)
    pane._rnd_camera.activated.emit(0)
    refresh(pane)
    check("4. picking a camera from the menu pins it and hides the note",
          camera.pinned and camera.value() == "/cameras/render_camera" and not note.isVisible(),
          f"{camera.pinned} {camera.value()} {note.isVisible()}")
    win.reject()

    # ── 1b: th::import_render's Render cut ────────────────
    win = mod.SubmitJobsDialog(
        [Uri.parse(plain)], "shots", department=first, render_department=first,
    )
    win.show()
    settle()
    pane = win._pane("shots")
    render_cut = pane._fields["render_department"]
    check("1. a Render cut asked for outright pins the Render cut",
          render_cut.pinned and render_cut.value() == first, f"{render_cut.pinned} {render_cut.value()}")
    check("1. a row renders up to the pinned Render cut",
          pane._cut(plain, "render_department") == first)
    win.reject()

    # ── 6: two shots: the camera and scene values per entity ──
    win = mod.SubmitJobsDialog([Uri.parse(plain), Uri.parse(without_last)], "shots")
    win.show()
    settle()
    pane = win._pane("shots")
    pane._ticks = {(plain, grid.RENDER), (without_last, grid.RENDER)}
    refresh(pane)
    keys = pane._camera_keys()
    check("6. both ticked shots are read", sorted(k[0] for k in keys) == [plain, without_last], str(keys))
    by_uri = {k[0]: k for k in keys}
    edit = pane._rnd_camera.lineEdit()

    def show(cameras: dict) -> None:
        pane._cameras.clear()
        pane._cameras.update({by_uri[u]: c for u, c in cameras.items()})
        pane._camera_pending = {}
        pane._show_camera(keys)
        settle()

    a = render_camera.RenderCamera(plain, "/cameras/a", "light",
                                   settings={"samples": (64, "light"), "mblur": (True, "light")})
    b = render_camera.RenderCamera(without_last, "/cameras/b", "anim",
                                   settings={"samples": (256, "anim")})
    show({plain: a, without_last: b})
    tip = pane._rnd_camera.toolTip()
    check("6. differing cameras: the empty field says they vary",
          edit.placeholderText() == f"varies: {mod.PER_ENTITY_TEXT}", edit.placeholderText())
    check("6. the tooltip names each shot's camera, department and channel",
          f"sh010: /cameras/a  (light, {by_uri[plain][1]})" in tip
          and f"sh020: /cameras/b  (anim, {by_uri[without_last][1]})" in tip, tip)
    samples = pane._rnd_samples
    check("6. Samples says the scene's values vary, low to high",
          samples.specialValueText() == resolve.scene_placeholder([64, 256], "int"),
          samples.specialValueText())
    check("6. Samples' tooltip names each shot's value and where it is authored",
          "sh010: 64  (light)" in samples.toolTip() and "sh020: 256  (anim)" in samples.toolTip(),
          samples.toolTip())
    mblur_label = pane._scene_fields["mblur"][1]
    check("6. Motion blur's label carries the scene's values",
          pane._rnd_mblur.text() == f"{mblur_label} ({resolve.scene_placeholder([True, None], 'bool')})",
          pane._rnd_mblur.text())
    show({plain: a, without_last: render_camera.RenderCamera(without_last, None, error="boom")})
    check("6. a shot whose lookup failed is named, and left out of the placeholder",
          edit.placeholderText() == "from scene: /cameras/a"
          and "sh020: not read  boom" in pane._rnd_camera.toolTip()
          and "sh020: not read  boom" in samples.toolTip(),
          f"{edit.placeholderText()} | {pane._rnd_camera.toolTip()}")
    show({plain: a, without_last: render_camera.RenderCamera(without_last, None, "anim")})
    check("6. a shot naming no camera says so",
          "sh020: none named" in pane._rnd_camera.toolTip(), pane._rnd_camera.toolTip())
    show({plain: a})
    check("6. one shot still being read: the field says so, the scene values are plain",
          edit.placeholderText() == "Reading…" and samples.specialValueText() == mod.FROM_SCENE_TEXT,
          f"{edit.placeholderText()} {samples.specialValueText()}")

    # ── 7: what Submit refuses before the review ─────────
    notices: list[str] = []
    win.notice = lambda title, text: notices.append(title)
    pane._ticks = set()
    refresh(pane)
    pane.submit()
    pane._ticks = {(plain, grid.RENDER)}
    refresh(pane)
    pane._rnd_preview.setChecked(False)
    pane._rnd_full.setChecked(False)
    refresh(pane)
    pane.submit()
    pane._rnd_full.setChecked(True)
    checked = pane._rnd_channels.checked_items()
    pane._rnd_channels.set_checked([])
    refresh(pane)
    pane.submit()
    pane._rnd_channels.set_checked(checked)
    refresh(pane)
    check("7. Submit refuses no ticks, no render range and no channel, in that order",
          notices == ["Nothing ticked", "No render range", "No channel to render"], str(notices))
    win.reject()

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
