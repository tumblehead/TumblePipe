"""Headless verify: the Farm Submit grid, its status scan and its background runner.

Needs a real project and Houdini's bundled Python — no licence, no Houdini
session, nothing submitted to the farm. The easy way to get both is to run it
through the Desktop launcher, which builds the environment a Houdini session
would have for a Desktop project:

    HOUDINI_PACKAGE_DIR=~/.tumbletrove/projects/<id>/.hpm/packages \\
        python scripts/farm_launcher.py --run scripts/verify_farm_grid.py

Set ``FARM_VERIFY_SCREENSHOT=<png>`` to also grab the dialog to an image.

Checks:
 1. The grid lists the context's terminal entities, one publish column per
    publishable department in pool order, then Playblast and Render.
 2. Opened for one shot, only that shot's Render cell starts ticked, and the
    opened-from department pins the preview cuts.
 3. The status scan finishes and leaves no cell pending.
 4. A mouse click on a column header's checkbox ticks every tickable
    visible cell, a second click clears them, and a click on a partly
    ticked column fills it.
 5. A sequence row ticks its shots' tickable cells.
 6. A filter hides rows, and a header toggle then only reaches visible ones.
 7. Select stale ticks exactly the stale and never-done cells.
 8. A ticked row resolves to exactly its ticked publish departments
    (``pub_departments``, no ``pub_department``) and its own frame range.
 9. A real staged stage collapses for a playblast in this interpreter — the
    snapshot the background runner takes when nothing is published first.
10. The background runner starts, runs in Houdini's Python, and reports a
    refused entity through the progress file (a Multi, which batch_submit
    refuses before it ever contacts Deadline).
11. The mouse wheel over an unfocused settings field scrolls the panel
    instead of editing (and so pinning) the field.
12. The settings panel shows only the sections of the ticked step kinds,
    and a hint when nothing is ticked.
13. Opened the way the Farm Submit quick action opens it — the workfile's
    shot, Publish, its department — only that department's publish cell
    is ticked; opened with no workfile, every row and nothing ticked.
14. A department whose only workfile is a Multi's that has never exported
    the entity is unclaimed: tickable, but Select stale leaves it alone.
15. A closed dialog is deleted, not kept hidden under its parent (Houdini's
    main window) until Houdini exits.
16. The review before submitting shows over the window: Submit is its one
    default button; a preview over stale, unticked departments lists them,
    and "Tick stale publishes" ticks exactly those and returns to the grid
    without submitting; Back submits nothing; Enter on the window under it
    does not submit again.
17. Opened from an asset, the dialog sits on the Assets tab; the Shots tab
    lists every shot with nothing ticked and no cut pinned, the Assets tab
    keeps its ticks while hidden, and Submit sends only the tab in front.
18. Columns collapse: one with nothing to do on any row collapses by itself
    once the status is read (never a ticked one); a collapsed column is out
    of reach of All, its rows' boxes and Select stale, keeps its ticks, and
    its header click opens it again.
19. What the window remembers: a pinned setting, the Render batch choices
    and a collapsed column come back on the next opening; the frame range
    does not, nor does a value the form no longer offers.

The prefs file is a throwaway one (``HOUDINI_USER_PREF_DIR`` points at a
temporary folder), so a run never changes what an artist's window remembers.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # The window reads and writes what it remembers in the prefs file:
    # never the artist's own here.
    os.environ["HOUDINI_USER_PREF_DIR"] = tempfile.mkdtemp(prefix="farm_verify_prefs_")
    python_dir = str(REPO / "python")
    if python_dir not in sys.path:
        sys.path.insert(0, python_dir)
    hfs = os.environ.get("HFS")
    if hfs and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(Path(hfs) / "bin"))

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from tumblepipe.api import default_client, api
    from tumblepipe.util.uri import Uri
    from tumblepipe.config.entities import is_terminal_entity
    from tumblepipe.farm.submit import grid as grid
    from tumblepipe.asset_browser import submit_jobs_dialog as mod
    from tumblepipe.farm.submit import plan as submit_plan
    from tumblepipe.farm.pipelines import preview as _preview
    from tumblepipe.pipe.paths import get_latest_staged_file_path

    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(bool(ok))
        line = f"{'PASS' if ok else 'FAIL'}: {name}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)

    def wait_for_scan(dlg, timeout: float = 300.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if not dlg._probe_queue and not dlg._pending:
                app.processEvents()
                return True
            time.sleep(0.05)
        return False

    config = default_client().config
    shots = sorted(
        (u for u in config.list_entity_uris(Uri.parse("entity:/shots"), closure=True)
         if is_terminal_entity(config, u)),
        key=str,
    )
    if len(shots) < 2:
        print(f"SKIP: project needs >=2 shots (got {len(shots)})")
        return 1
    print(f"project: {api.PROJECT_PATH}  shots: {len(shots)}", flush=True)

    # ── 1-3: open for one shot ────────────────────────────
    shot = shots[0]
    started = time.monotonic()
    renderable = mod._list_dept_names("shots", only_publishable=False, only_renderable=True)
    open_dept = renderable[-1] if renderable else None
    # The grid internals live on the tab's pane; showing, sizing and closing
    # are the dialog's.
    win = mod.SubmitJobsDialog([shot], [shot.segments[-1]], "shots", department=open_dept)
    dlg = win._panes["shots"]
    uris = dlg._uris
    check("1. rows are the context's terminal entities",
          set(uris) >= {str(u) for u in shots}, f"{len(uris)} rows")
    publish = mod._list_dept_names("shots", only_publishable=True, only_renderable=False)
    check("1. publish columns in pool order, then Playblast and Render",
          [c.label for c in dlg._columns] == publish + ["Playblast", "Render"],
          ", ".join(c.label for c in dlg._columns))
    check("2. only the opened shot's Render cell starts ticked",
          dlg._ticks == {(str(shot), grid.RENDER)})
    if open_dept:
        check("2. the opened-from department pins both preview cuts",
              dlg._fields["render_department"].pinned
              and dlg._fields["pb_department"].pinned
              and dlg._fields["render_department"].value() == open_dept)
    finished = wait_for_scan(dlg)
    pending = [k for k, s in dlg._states.items() if s == grid.PENDING]
    counts = {s: sum(1 for v in dlg._states.values() if v == s)
              for s in (grid.NONE, grid.NEVER, grid.STALE, grid.CURRENT)}
    errors = [s.uri for s in dlg._statuses.values() if s.error]
    check("3. the status scan finishes with no cell pending",
          finished and not pending,
          f"{time.monotonic() - started:.1f}s, {counts}, {len(pending)} pending, "
          f"{len(errors)} row errors")

    # ── 4-7: tick operations ──────────────────────────────
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    def click_header(logical: int) -> None:
        # A real click, not _on_header_clicked(): the header must be
        # clickable for Qt to emit sectionClicked at all, and once was not.
        header = dlg._tree.header()
        x = header.sectionViewportPosition(logical) + header.sectionSize(logical) // 2
        QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier,
                         QPoint(x, header.height() - 12))
        app.processEvents()

    win.show()
    app.processEvents()
    dlg._clear_ticks()
    render_col = next(i for i, c in enumerate(dlg._columns, 1) if c.kind == grid.RENDER)
    click_header(render_col)
    tickable = {(u, grid.RENDER) for u in uris if dlg._tickable(u, grid.RENDER)}
    check("4. clicking a column header ticks every tickable cell in it",
          dlg._ticks == tickable, f"{len(tickable)} cells")
    click_header(render_col)
    check("4. ...and clears them on the second click", not dlg._ticks)
    dlg._ticks = {(str(shot), grid.RENDER)}
    click_header(render_col)
    check("4. ...and a click on a partly ticked column fills it",
          dlg._ticks == tickable, f"{len(dlg._ticks)} cells")
    dlg._clear_ticks()
    win.hide()

    group = dlg._group_key(str(shot))
    dlg._toggle(dlg._group_keys(group))
    expected = {
        (u, c.key) for u in dlg._group_uris[group] for c in dlg._columns
        if dlg._tickable(u, c.key)
    }
    check("5. a sequence row ticks its shots' tickable cells",
          dlg._ticks == expected, f"{group}: {len(expected)} cells")
    dlg._clear_ticks()

    dlg._filter.setText(shot.segments[-1])
    app.processEvents()
    visible = [u for u in uris if u not in dlg._hidden]
    dlg._on_header_clicked(render_col)
    check("6. a filter hides rows and headers only reach visible ones",
          {u for u, _k in dlg._ticks} <= set(visible) and len(visible) < len(uris),
          f"{len(visible)} visible of {len(uris)}")
    dlg._filter.setText("")
    dlg._clear_ticks()

    dlg._select_stale()
    check("7. Select stale ticks exactly the stale and never-done cells",
          dlg._ticks == set(grid.stale_keys(dlg._states)), f"{len(dlg._ticks)} cells")

    # ── 14: a Multi's workfile only claims what it exported ──
    unclaimed = {
        (uri, grid.publish_key(dept))
        for uri, status in dlg._statuses.items()
        for dept in status.shared
        if status.hip_mtimes.get(dept) is not None
        and status.export_mtimes.get(dept) is None
    }
    check("14. a Multi's never-exported members are unclaimed and not selected as stale",
          all(dlg._states.get(key) == grid.UNCLAIMED for key in unclaimed)
          and not (unclaimed & dlg._ticks)
          and all(dlg._tickable(*key) for key in unclaimed),
          f"{len(unclaimed)} unclaimed cells")
    dlg._clear_ticks()

    # ── 8: what a row resolves to ─────────────────────────
    target = next(
        (u for u in uris
         if sum(1 for c in dlg._columns
                if c.kind == grid.PUBLISH and dlg._tickable(u, c.key)) >= 2
         and dlg._tickable(u, grid.RENDER)),
        None,
    )
    if target is None:
        print("SKIP: 8. no shot with two publishable departments and a render cell")
    else:
        departments = [
            c.department for c in dlg._columns
            if c.kind == grid.PUBLISH and dlg._tickable(target, c.key)
        ][:2]
        dlg._ticks = {(target, grid.publish_key(d)) for d in departments} | {(target, grid.RENDER)}
        dlg._refresh()
        rows = dlg._resolved_rows()
        settings = rows[0][1] if rows else {}
        props = dlg._props[target]
        check("8. a row publishes exactly its ticked departments",
              settings.get("pub_departments") == departments
              and "pub_department" not in settings
              and settings.get("publish") and settings.get("render")
              and not settings.get("playblast"),
              f"{settings.get('pub_departments')}")
        frame_start = props.get("frame_start")
        check("8. ...over its own frame range",
              frame_start is None or settings.get("first_frame") == frame_start,
              f"{settings.get('first_frame')}-{settings.get('last_frame')}")

    # ── 12: only the ticked kinds' settings show ─────────
    def shown() -> list:
        return [k for k, box in dlg._sections.items() if not box.isHidden()]

    dlg._ticks = {(str(shot), grid.RENDER)}
    dlg._refresh()
    check("12. a Render tick shows only the Render settings",
          shown() == [grid.RENDER] and dlg._no_settings.isHidden(), f"{shown()}")
    dlg._ticks = {(str(shot), grid.PLAYBLAST), (str(shots[1]), grid.publish_key(publish[0]))}
    dlg._refresh()
    check("12. Publish and Playblast ticks show those two sections",
          shown() == [grid.PUBLISH, grid.PLAYBLAST], f"{shown()}")
    dlg._clear_ticks()
    dlg._refresh()
    check("12. nothing ticked shows the hint and no section",
          not shown() and not dlg._no_settings.isHidden(), f"{shown()}")

    # ── 11: the wheel scrolls the settings, never edits them ──
    # Every section showing, so the panel is tall enough to scroll.
    dlg._ticks = {
        (str(shot), grid.RENDER), (str(shot), grid.PLAYBLAST),
        (str(shot), grid.publish_key(publish[0])),
    }
    dlg._refresh()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent

    def wheel(widget, dy):
        pos = QPointF(widget.width() / 2, widget.height() / 2)
        event = QWheelEvent(
            pos, QPointF(widget.mapToGlobal(pos.toPoint())), QPoint(0, 0),
            QPoint(0, dy), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
        )
        app.sendEvent(widget, event)

    win.resize(1400, 600)  # short enough that the settings panel scrolls
    win.show()
    app.processEvents()
    spin, entry = dlg._rnd_samples, dlg._fields["samples"]
    bar = dlg._forms_scroll.verticalScrollBar()
    bar.setValue(0)
    value = spin.value()
    for _ in range(3):
        wheel(spin, -120)
    check("11. the wheel over an unfocused spin box scrolls instead of editing",
          spin.value() == value and not entry.pinned and bar.value() > 0,
          f"value {value}->{spin.value()}, pinned {entry.pinned}, scroll {bar.value()}")
    win.hide()

    shot_path = os.environ.get("FARM_VERIFY_SCREENSHOT")
    if shot_path:
        dlg._select_stale()
        dlg._refresh()
        win.resize(1400, 820)
        win.show()
        app.processEvents()
        win.grab().save(shot_path)
        print(f"screenshot: {shot_path}")
    win.reject()

    # ── 13: how the Farm Submit quick action opens it ────
    farm_dept = publish[-1]
    farm_win = mod.SubmitJobsDialog(
        [shot], [shot.segments[-1]], "shots",
        department=farm_dept, tick_kinds=(grid.PUBLISH,),
    )
    farm = farm_win._panes["shots"]
    check("13. from a workfile only its department's publish cell is ticked",
          farm._ticks == {(str(shot), grid.publish_key(farm_dept))}
          and [k for k, b in farm._sections.items() if not b.isHidden()] == [grid.PUBLISH],
          f"{sorted(farm._ticks)}")
    farm_win.reject()
    bare_win = mod.SubmitJobsDialog([], [], "shots", tick_kinds=(grid.PUBLISH,))
    bare = bare_win._panes["shots"]
    check("13. with no workfile every row is listed and nothing ticked",
          not bare._ticks and set(bare._uris) >= {str(u) for u in shots},
          f"{len(bare._uris)} rows")
    bare_win.reject()

    # ── 15: closing deletes the dialog ────────────────────
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QWidget
    main_window = QWidget()  # stands in for hou.qt.mainWindow()
    owned = mod.SubmitJobsDialog([], [], "shots", parent=main_window, tick_kinds=())
    owned.show()
    app.processEvents()
    owned.reject()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    left = main_window.findChildren(mod.SubmitJobsDialog)
    check("15. a closed dialog is deleted, not left under its parent",
          not left, f"{len(left)} left")
    main_window.deleteLater()

    # ── 16: the review before submitting ──────────────────
    import tumblepipe.apps.deadline as deadline_mod
    from tumblepipe.asset_browser.farm_submit_ui import ReviewSheet
    real_problem = deadline_mod.deadline_setup_problem
    deadline_mod.deadline_setup_problem = lambda environ=None: None
    confirm_win = mod.SubmitJobsDialog([], [], "shots", tick_kinds=())
    confirm_dlg = confirm_win._panes["shots"]
    wait_for_scan(confirm_dlg)
    preview_shot = next(
        (u for u in confirm_dlg._uris
         if confirm_dlg._tickable(u, grid.PLAYBLAST)
         and any(confirm_dlg._states.get((u, c.key)) in grid.NEEDS_WORK
                 for c in confirm_dlg._columns if c.kind == grid.PUBLISH)),
        None,
    )
    if preview_shot is None:
        print("SKIP: 16. no shot with a playblast over stale publishes")
    else:
        submitted: list = []
        real_start = mod.SubmitJobsDialog.start
        mod.SubmitJobsDialog.start = lambda self, configs, steps, pane: submitted.append(configs)
        try:
            confirm_dlg._ticks = {(preview_shot, grid.PLAYBLAST)}
            confirm_dlg._refresh()
            settings = confirm_dlg._resolved_rows()[0][1]
            missing = confirm_dlg._stale_upstream(preview_shot, settings)
            confirm_win._on_submit()
            sheet = confirm_win._overlay.content()
            on_overlay = isinstance(sheet, ReviewSheet) and confirm_win._overlay.isVisibleTo(confirm_win)
            default = [b.text() for b in sheet.findChildren(mod.QPushButton) if b.isDefault()] if on_overlay else []
            # Enter on the window under the card must not start a second review.
            confirm_win._on_submit()
            again = confirm_win._overlay.content() is sheet
            sheet.back_requested.emit()
            back_ok = (
                not submitted and confirm_dlg._ticks == {(preview_shot, grid.PLAYBLAST)}
                and confirm_win._overlay.content() is None
            )
            confirm_win._on_submit()
            confirm_win._overlay.content().tick_stale_requested.emit()
            added = confirm_dlg._ticks - {(preview_shot, grid.PLAYBLAST)}
        finally:
            mod.SubmitJobsDialog.start = real_start
        check("16. the review shows over the window, Submit its one default button",
              on_overlay and len(default) == 1 and default[0].startswith("Submit"),
              f"{type(sheet).__name__}: {default}")
        check("16. Submit on the window under the review does nothing", again)
        check("16. Back submits nothing, changes no tick and closes the card", back_ok)
        check("16. Tick stale publishes ticks exactly the stale departments, submits nothing",
              missing and not submitted and confirm_win._overlay.content() is None
              and added == {(preview_shot, grid.publish_key(d)) for d in missing},
              f"{preview_shot.split('/')[-1]}: {missing}")
    confirm_win.reject()
    deadline_mod.deadline_setup_problem = real_problem

    # ── 17: Assets and Shots tabs ─────────────────────────
    assets = sorted(
        (u for u in config.list_entity_uris(Uri.parse("entity:/assets"), closure=True)
         if is_terminal_entity(config, u)),
        key=str,
    )
    if not assets:
        print("SKIP: 17. the project has no assets")
    else:
        asset = assets[0]
        asset_renderable = mod._list_dept_names("assets", only_publishable=False, only_renderable=True)
        asset_dept = asset_renderable[-1] if asset_renderable else None
        tabs_win = mod.SubmitJobsDialog(
            [asset], [asset.segments[-1]], "assets", department=asset_dept,
        )
        check("17. opened from an asset, the dialog is on the Assets tab and builds only it",
              tabs_win.current_context() == "assets" and list(tabs_win._panes) == ["assets"]
              and tabs_win._panes["assets"]._ticks == {(str(asset), grid.RENDER)},
              f"{tabs_win.current_context()}, panes {list(tabs_win._panes)}")
        tabs_win._tabs.setCurrentIndex(grid.CONTEXTS.index("shots"))
        app.processEvents()
        shots_pane = tabs_win._panes.get("shots")
        check("17. the Shots tab lists every shot, nothing ticked, no cut pinned, with Playblast",
              shots_pane is not None
              and tabs_win._stack.currentWidget() is shots_pane
              and set(shots_pane._uris) >= {str(u) for u in shots}
              and not shots_pane._ticks
              and not any(e.pinned for e in shots_pane._fields.values())
              and any(c.kind == grid.PLAYBLAST for c in shots_pane._columns),
              f"{len(shots_pane._uris) if shots_pane else 0} rows")
        check("17. the Assets tab keeps its ticks and says so in its label",
              tabs_win._panes["assets"]._ticks == {(str(asset), grid.RENDER)}
              and tabs_win._tabs.tabText(0) == "Assets (1)",
              tabs_win._tabs.tabText(0))
        wait_for_scan(shots_pane)
        target_shot = next(
            (u for u in shots_pane._uris if shots_pane._tickable(u, grid.RENDER)), None,
        )
        sent: list = []
        refused: list = []
        import tumblepipe.apps.deadline as deadline_mod
        real_start = mod.SubmitJobsDialog.start
        real_problem = deadline_mod.deadline_setup_problem
        real_notice = mod.SubmitJobsDialog.notice
        mod.SubmitJobsDialog.start = lambda self, configs, steps, pane: sent.extend(configs)
        mod.SubmitJobsDialog.notice = lambda self, title, text: refused.append(text)
        deadline_mod.deadline_setup_problem = lambda environ=None: None
        try:
            shots_pane._ticks = {(target_shot, grid.RENDER)}
            shots_pane._refresh()
            shots_pane._rnd_channels.set_checked(shots_pane._rnd_channels.options())
            tabs_win._on_submit()
            review = tabs_win._overlay.content()
            if review is not None and hasattr(review, "submit_requested"):
                review.submit_requested.emit()
        finally:
            mod.SubmitJobsDialog.start = real_start
            mod.SubmitJobsDialog.notice = real_notice
            deadline_mod.deadline_setup_problem = real_problem
        check("17. Submit on the Shots tab sends only its shots, as shots",
              [c["entity"]["uri"] for c in sent] == [target_shot]
              and all(c["entity"]["context"] == "shots" for c in sent),
              f"{[(c['entity']['name'], c['entity']['context']) for c in sent]} {refused}")
        tabs_win.reject()

    # ── 18: collapsing columns ────────────────────────────
    col_win = mod.SubmitJobsDialog([], [], "shots", tick_kinds=())
    col = col_win._panes["shots"]
    wait_for_scan(col)
    empty = grid.empty_columns(col._uris, col._columns, col._states)
    check("18. empty columns collapse by themselves once the status is read",
          col._collapsed == empty and col._auto_collapsed == empty,
          f"empty {sorted(empty)}, collapsed {sorted(col._collapsed)}")
    target = next(
        (i for i, c in enumerate(col._columns, start=1)
         if c.key not in col._collapsed
         and any(col._tickable(u, c.key) for u in col._uris)),
        None,
    )
    if target is None:
        print("SKIP: 18. no column with a tickable cell")
    else:
        key = col._columns[target - 1].key
        first = next(u for u in col._uris if col._tickable(u, key))
        col._ticks = {(first, key)}
        col._set_collapsed(target, True)
        header = col._tree.header()
        check("18. a collapsed column is a sliver and keeps its ticks",
              header.sectionSize(target) == mod.COLLAPSED_WIDTH and (first, key) in col._ticks)
        col._on_header_clicked(0)  # All: ticks every shown column, never the collapsed one
        reached = {k for _u, k in col._ticks} - {key}
        col._select_stale()
        check("18. All and Select stale leave a collapsed column alone",
              all(k != key for _u, k in col._ticks - {(first, key)})
              and all(k not in col._collapsed for k in reached),
              f"{sorted(reached)}")
        col._clear_ticks()
        col._on_header_clicked(target)
        check("18. clicking a collapsed column's header opens it, ticking nothing",
              key not in col._collapsed and header.sectionSize(target) > mod.COLLAPSED_WIDTH
              and not col._ticks)
        col._set_collapsed(target, True)
        col._ticks = {(first, key)}
        col._auto_collapse()
        check("18. a column the artist collapsed stays collapsed through the auto rule",
              key in col._collapsed and key not in col._auto_collapsed)
    col_win.reject()

    # ── 19: what the window remembers ─────────────────────
    from tumblepipe.asset_browser import prefs as prefs_mod
    mem_win = mod.SubmitJobsDialog([], [], "shots", tick_kinds=())
    mem = mem_win._panes["shots"]
    priority = mem._fields["render_priority"]
    priority.widget.setValue(37)
    priority.pin()
    frames = mem._fields["first_frame"]
    frames.widget.setValue(1234)
    frames.pin()
    mem._rnd_preview.setChecked(True)
    mem._rnd_standalone.setChecked(True)
    column = mem._columns[0]
    mem._set_collapsed(1, True)
    mem_win.resize(1234, 777)
    mem_win.reject()
    stored = prefs_mod.load_prefs().farm_submit
    # A department the project no longer has is dropped, not guessed at.
    record = stored["tabs"][mod.remembered.scope(mod._project_key(), "shots")]
    record["pinned"]["render_department"] = "no_such_department"
    prefs_mod.update_prefs(farm_submit=stored)
    again_win = mod.SubmitJobsDialog([], [], "shots", tick_kinds=())
    again = again_win._panes["shots"]
    check("19. a pinned setting comes back pinned",
          again._fields["render_priority"].pinned and again._fields["render_priority"].value() == 37)
    check("19. the frame range is not remembered",
          not again._fields["first_frame"].pinned)
    check("19. a value the form no longer offers is dropped",
          not again._fields["render_department"].pinned)
    check("19. the batch choices come back",
          again._rnd_preview.isChecked() and again._rnd_standalone.isChecked())
    check("19. a collapsed column comes back collapsed, and the size",
          column.key in again._collapsed
          and stored["ui"]["size"] == [1234, 777],
          f"{sorted(again._collapsed)} {stored['ui'].get('size')}")
    again_win.reject()

    # ── 9: a real snapshot in this interpreter ────────────
    staged = next(
        (u for u in shots if (p := get_latest_staged_file_path(u, "default")) and p.exists()),
        None,
    )
    if staged is None:
        print("SKIP: 9. no shot has a staged 'default' build")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            paths: dict = {}
            try:
                name = _preview.collapse_playblast_input(
                    staged, renderable[-1], Path(tmp), paths,
                )
                text = (Path(tmp) / name).read_text(encoding="utf-8")
                ok = "subLayers" in text and _preview.PLAYBLAST_INPUT_NAME == name
                detail = f"{staged}: {text.count('@')//2} layer refs"
            except _preview.PreviewError as error:
                # A project problem (no camera, missing layer) is still a
                # composed stage: the interpreter and resolver worked.
                ok, detail = True, f"{staged}: refused as a project issue — {error}"
            check("9. a staged stage collapses in Houdini's bundled Python", ok, detail)

    # ── 10: the runner, end to end, without the farm ──────
    group_uri = "groups:/shots/verify_farm_grid"
    plan = submit_plan.make_plan(
        [{"entity": {"uri": group_uri, "name": "verify", "context": "shots"},
          # Every key an on section needs (batch_submit raises on a gap,
          # before it gets to refusing the Multi).
          "settings": {"publish": True, "pub_pool": "general", "pub_priority": 50,
                       "render": False, "playblast": False}}],
        env=dict(os.environ), houdini_version=os.environ.get("TH_HOUDINI_VERSION"),
    )
    folder = Path(api.storage.resolve(Uri.parse("temp:/farm_submissions")))
    plan_path = submit_plan.write_plan(folder / f"verify_{int(time.time())}", plan)
    process = submit_plan.launch(plan_path)
    try:
        process.wait(timeout=180)
    except Exception:
        process.kill()
    events, _ = submit_plan.read_events(plan_path.parent / submit_plan.PROGRESS_NAME)
    state = submit_plan.fold_events(events)
    row = state["rows"].get(group_uri, {})
    passed = (
        state["finished"] and row.get("status") == "failed"
        and "is a Multi" in row.get("error", "")
    )
    check("10. the runner reports a refused entity through the progress file",
          passed,
          row.get("error", f"no row event; see {plan_path.parent / submit_plan.LOG_NAME}"))
    if passed:
        # Leave the folder behind only when it explains a failure.
        import shutil
        shutil.rmtree(plan_path.parent, ignore_errors=True)

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
