"""Headless verify: SubmitJobsDialog entity tree.

Run under a project hython (``TH_PROJECT_PATH`` pointing at a project with
at least one shot and one asset), e.g. via TumbleTrove Desktop's run_hython
with dev overrides:

    hython scripts/verify_submit_jobs_entity_tree.py

Replaces the old entity-*selector* harness: the single-entity combobox is
now a checkable tree on every open, so one open can fan out to a batch.

Checks:
 1. The tree is scoped to the dialog's context and lists exactly that
    context's terminal entities.
 2. The entity the dialog was opened for starts checked, and it alone.
 3. Checking a second entity fans the submission out to both.
 4. A branch check cascades to its leaves; a partial check rolls the branch
    up to PartiallyChecked.
 5. Group leaves mirror the context-root leaves for the same entity.
 6. The filter hides non-matching leaves without touching check state.
 7. All / None check and clear the visible leaves.
 8. Reseeding is keyed on the primary entity: adding entities to the batch
    does not clobber an already-tuned form.
 9. Unchecking everything empties the target and says so in the header.
10. A multi-entity open starts with all of them checked.
11. The Playblast section is present for shots and absent for assets, and
    its department list is the renderable shot departments.
12. The department the dialog was opened *from* seeds the Render and
    Playblast department combos; one that is not renderable is ignored.
13. The Render channel menu offers the entities' configured channels
    (union over the batch, ``default`` first), opens with the primary's
    own list checked, keeps picks when the batch grows, and reads the
    batch's properties inside one coherency scope.
14. Tri-state fields: a single entity shows its own value, unpinned and
    italic; two entities that disagree park the field on ⟨per entity⟩.
15. **The regression**: each checked entity resolves to its OWN frame
    range. The form used to seed from the first checked entity and send
    those numbers for the whole batch.
16. Pinning applies one value to every entity, renders upright, leaves
    its neighbours alone, and ↺ hands the field back to the entities.
17. Reseeding on a selection change never clobbers a pinned field, and
    always re-derives the unpinned ones.
18. The pre-flight table lists one row per checked entity, columns for
    what varies, and drops a column once it is pinned.
19. An undefined channel is still submitted (visible failure is the
    contract) but every affected entity is warned about it up front.
20. An entity with no frame range omits the frame keys rather than
    letting batch_submit guess 1001-1100.
21. The ProcessDialog submission path: one farm-only task per entity, each
    carrying and submitting its OWN settings (a lambda closing over the
    loop variable would submit the last entity's N times), and an executor
    that sequences on the main thread rather than a worker.

Qt runs on the offscreen platform; no project data is written and nothing
is submitted to the farm.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_dialog_module():
    """Load the catalog dialog by file path, mirroring the catalog's own
    spec_from_file_location loading (the catalog dir is not a package)."""
    dlg_path = REPO / "asset_browser_catalogs" / "submit_jobs_dialog.py"
    spec = importlib.util.spec_from_file_location(
        "verify_submit_jobs_dialog_mod", dlg_path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    mod = _load_dialog_module()
    # The dialog puts the catalog dir on sys.path at import time, so the
    # pure resolver is importable by name from here too.
    import submit_jobs_resolve as resolve

    from tumblepipe.api import default_client
    from tumblepipe.config.entities import is_terminal_entity
    config = default_client().config
    terminal = [
        u for u in config.list_entity_uris(closure=True)
        if u.segments
        and u.segments[0] in ("shots", "assets")
        and is_terminal_entity(config, u)
    ]
    shots = [u for u in terminal if u.segments[0] == "shots"]
    assets = [u for u in terminal if u.segments[0] == "assets"]
    if len(shots) < 2 or not assets:
        print(
            "SKIP: project needs >=2 shots and >=1 asset "
            f"(got {len(shots)} shots, {len(assets)} assets)",
        )
        return 1

    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(bool(ok))
        line = f"{'PASS' if ok else 'FAIL'}: {name}"
        if detail and not ok:
            line += f" — {detail}"
        print(line)

    def leaf_of(dlg, uri):
        """The context-root leaf item for ``uri`` (the first registered)."""
        return dlg._leaves[str(uri)].items[0]

    # ── Single-entity open ────────────────────────────────
    shot = sorted(shots, key=str)[0]
    other = sorted(shots, key=str)[1]
    dlg = mod.SubmitJobsDialog([shot], [shot.segments[-1]], "shots")

    # 1. Context scoping + completeness.
    listed = sorted(dlg._leaves)
    expected = sorted(str(u) for u in shots)
    check(
        "tree lists exactly the context's terminal entities",
        listed == expected,
        f"{len(listed)} leaves != {len(expected)} shots",
    )
    check(
        "tree is scoped to the context (no assets in a shots tree)",
        not any(k.startswith("entity:/assets") for k in dlg._leaves),
    )

    # 2. Opened entity starts checked, alone.
    check(
        "opened entity starts checked, and it alone",
        [str(u) for u in dlg._entity_uris] == [str(shot)],
        f"{[str(u) for u in dlg._entity_uris]}",
    )

    # 3. Fan out from a single-entity open.
    leaf_of(dlg, other).setCheckState(0, Qt.Checked)
    check(
        "checking a second entity fans the submission out to both",
        sorted(str(u) for u in dlg._entity_uris) == sorted([str(shot), str(other)]),
        f"{[str(u) for u in dlg._entity_uris]}",
    )
    check(
        "header reports the new count",
        "2 entities" in dlg._header.text(),
        dlg._header.text(),
    )

    # 8. Reseed is keyed on the primary — the form survives a fan-out.
    #    (Checked here because the batch from check 3 is still in place.)
    dlg._rnd_priority.setValue(97)
    third = sorted(shots, key=str)[-1]
    leaf_of(dlg, third).setCheckState(0, Qt.Checked)
    check(
        "adding to the batch does not clobber a tuned form",
        dlg._rnd_priority.value() == 97,
        f"priority reseeded to {dlg._rnd_priority.value()}",
    )

    # 4. Branch cascade + roll-up.
    dlg._set_all_checked(False)
    root = dlg._tree.topLevelItem(0)
    root.setCheckState(0, Qt.Checked)
    check(
        "checking the context root checks every entity",
        len(dlg._entity_uris) == len(shots),
        f"{len(dlg._entity_uris)} != {len(shots)}",
    )
    check(
        "a fully-checked branch reads as Checked",
        root.checkState(0) == Qt.Checked,
    )
    leaf_of(dlg, shot).setCheckState(0, Qt.Unchecked)
    check(
        "a partially-checked branch rolls up to PartiallyChecked",
        root.checkState(0) == Qt.PartiallyChecked,
        str(root.checkState(0)),
    )

    # 5. Group mirroring — only meaningful if the project authors groups.
    mirrored = [
        leaf for leaf in dlg._leaves.values() if len(leaf.items) > 1
    ]
    if not mirrored:
        print("SKIP: no groups in this project — mirror check not exercised")
    else:
        leaf = mirrored[0]
        dlg._set_all_checked(False)
        leaf.items[-1].setCheckState(0, Qt.Checked)  # check via the group leaf
        check(
            "checking a group leaf mirrors to the context-root leaf",
            all(item.checkState(0) == Qt.Checked for item in leaf.items),
        )
        check(
            "a mirrored entity is submitted once, not once per group",
            [str(u) for u in dlg._entity_uris] == [str(leaf.uri)],
            f"{[str(u) for u in dlg._entity_uris]}",
        )

    # 6. Filter narrows the view, not the submission.
    dlg._set_all_checked(False)
    leaf_of(dlg, shot).setCheckState(0, Qt.Checked)
    before = [str(u) for u in dlg._entity_uris]
    dlg._filter.setText(other.segments[-1])
    check(
        "filter hides non-matching leaves",
        leaf_of(dlg, other).isHidden() is False
        and leaf_of(dlg, shot).isHidden() is True,
    )
    check(
        "filter leaves check state (and the submission) alone",
        [str(u) for u in dlg._entity_uris] == before,
        f"{[str(u) for u in dlg._entity_uris]} != {before}",
    )

    # 7. All / None operate on the visible set.
    dlg._set_all_checked(True)
    check(
        "All checks only the visible (filtered) leaves",
        sorted(str(u) for u in dlg._entity_uris)
        == sorted([str(shot), str(other)]),
        f"{[str(u) for u in dlg._entity_uris]}",
    )
    dlg._filter.setText("")
    dlg._set_all_checked(True)
    check(
        "All with no filter checks everything",
        len(dlg._entity_uris) == len(shots),
    )

    # 9. Empty selection.
    dlg._set_all_checked(False)
    check("None clears the target", dlg._entity_uris == [])
    check(
        "header says nothing is checked",
        "No shots checked" in dlg._header.text(),
        dlg._header.text(),
    )

    # 10. Multi-entity open.
    two = [shot, other]
    dlg2 = mod.SubmitJobsDialog(two, [u.segments[-1] for u in two], "shots")
    check(
        "multi-entity open starts with all of them checked",
        sorted(str(u) for u in dlg2._entity_uris)
        == sorted(str(u) for u in two),
        f"{[str(u) for u in dlg2._entity_uris]}",
    )

    # 11. Coherent-read contract. The entity sweep vets every URI with
    #     is_terminal_entity — one read per URI — so it must run inside a
    #     coherent() scope or opening the dialog stamps the config file once
    #     per entity (the v1.16.5 stat-storm bug class). Pinned by counting
    #     config stats: flat, not proportional to the entity count.
    real_stat = os.stat
    counted = {"n": 0}

    def counting_stat(path, *a, **k):
        if "_config" in str(path):
            counted["n"] += 1
        return real_stat(path, *a, **k)

    os.stat = counting_stat
    try:
        swept = mod._list_selectable_entities("assets")
    finally:
        os.stat = real_stat
    check(
        "entity sweep is coherent (stats don't scale with entity count)",
        counted["n"] <= 4,
        f"{counted['n']} config stats for {len(swept)} assets — "
        "is the coherent() scope still there?",
    )

    # Asset context still works (department lists follow the context).
    asset = assets[0]
    dlg3 = mod.SubmitJobsDialog([asset], [asset.segments[-1]], "assets")
    expected_depts = mod._list_dept_names(
        "assets", only_publishable=False, only_renderable=True,
    )
    # Index 0 is the ⟨per entity⟩ placeholder, which is also the way back
    # to "let each entity use its own department".
    actual_depts = [
        dlg3._rnd_dept.itemText(i) for i in range(1, dlg3._rnd_dept.count())
    ]
    check(
        "asset-context tree + department list",
        actual_depts == expected_depts
        and dlg3._rnd_dept.itemText(0) == mod.PER_ENTITY_TEXT
        and [str(u) for u in dlg3._entity_uris] == [str(asset)],
        f"{actual_depts} != {expected_depts}",
    )

    # 11. Playblast section is shots-only, with the renderable-department list.
    rnd_depts = mod._list_dept_names(
        "shots", only_publishable=False, only_renderable=True,
    )
    check(
        "playblast section present in a shots dialog",
        dlg._playblast_box is not None,
    )
    check(
        "playblast section absent in an assets dialog",
        dlg3._playblast_box is None,
    )
    if dlg._playblast_box is not None:
        pb_depts = [
            dlg._pb_dept.itemText(i) for i in range(1, dlg._pb_dept.count())
        ]
        check(
            "playblast department list == renderable shot departments",
            pb_depts == rnd_depts,
            f"{pb_depts} != {rnd_depts}",
        )
        check(
            "playblast section starts unchecked (opt-in)",
            dlg._playblast_box.isChecked() is False,
        )

    # 12. The opened-from department seeds the Render (and Playblast)
    #     department; a department that is not renderable is ignored.
    if rnd_depts:
        opened_from = rnd_depts[-1]
        dlg4 = mod.SubmitJobsDialog(
            [shot], [shot.segments[-1]], "shots", department=opened_from,
        )
        check(
            "opened-from department seeds the render department",
            dlg4._rnd_dept.currentText() == opened_from,
            f"{dlg4._rnd_dept.currentText()!r} != {opened_from!r}",
        )
        if dlg4._playblast_box is not None:
            check(
                "opened-from department seeds the playblast department",
                dlg4._pb_dept.currentText() == opened_from,
                f"{dlg4._pb_dept.currentText()!r} != {opened_from!r}",
            )
        dlg5 = mod.SubmitJobsDialog(
            [shot], [shot.segments[-1]], "shots",
            department="not_a_department",
        )
        check(
            "an unrenderable opened-from department is ignored",
            dlg5._rnd_dept.currentText() in rnd_depts,
            f"{dlg5._rnd_dept.currentText()!r} not in {rnd_depts}",
        )

    # 13. Channel menu. It replaced a free-text csv field, so what it
    #     offers has to come from config rather than from the artist's
    #     typing: the union over the checked batch, default first.
    from tumblepipe.config.channels import DEFAULT_CHANNEL
    multi = next(
        (u for u in shots
         if len((config.get_properties(u) or {}).get("variants") or []) > 1),
        None,
    )
    if multi is None:
        print("SKIP: no shot with more than one channel — menu not exercised")
    else:
        dlg6 = mod.SubmitJobsDialog([multi], [multi.segments[-1]], "shots")
        combo = dlg6._rnd_channels
        own = list((config.get_properties(multi) or {}).get("variants") or [])
        expected = [DEFAULT_CHANNEL] + [v for v in own if v != DEFAULT_CHANNEL]
        check(
            "channel menu lists the entity's channels, default first",
            combo.options() == expected,
            f"{combo.options()} != {expected}",
        )
        check(
            "channel menu opens with the entity's own list checked",
            combo.checked_items() == [v for v in expected if v in own],
            f"{combo.checked_items()} vs {own}",
        )

        # Narrowing the pick is the whole point of the menu — drive it
        # the way a click in the popup does, through the item's check state.
        combo.model().item(0).setCheckState(Qt.Checked)
        for row in range(1, combo.model().rowCount()):
            combo.model().item(row).setCheckState(Qt.Unchecked)
        check(
            "unchecking narrows what will be submitted",
            combo.checked_items() == [DEFAULT_CHANNEL],
            f"{combo.checked_items()}",
        )
        check(
            "the closed combo summarises the pick",
            combo.lineEdit().text() == DEFAULT_CHANNEL,
            f"{combo.lineEdit().text()!r}",
        )

        # A second entity joins its channels to the menu without joining
        # them to the selection — growing the batch must not grow the
        # render behind the artist's back.
        second = next((u for u in shots if str(u) != str(multi)), None)
        if second is not None:
            leaf_of(dlg6, second).setCheckState(0, Qt.Checked)
            union = set(combo.options())
            second_own = set(
                (config.get_properties(second) or {}).get("variants") or []
            )
            check(
                "a checked-in entity's channels join the menu",
                second_own <= union,
                f"{sorted(second_own - union)} missing from {sorted(union)}",
            )
            check(
                "but not the selection",
                combo.checked_items() == [DEFAULT_CHANNEL],
                f"{combo.checked_items()}",
            )

        # All / None on the channel row.
        combo.set_checked([])
        check("None clears every channel", combo.checked_items() == [])
        combo.set_checked(combo.options())
        check(
            "All checks every channel",
            combo.checked_items() == combo.options(),
            f"{combo.checked_items()} != {combo.options()}",
        )

        # Same coherent-read contract as the entity sweep: the menu reads
        # properties for every checked entity, so an "All" must not stat
        # the config file once per entity.
        os.stat = counting_stat
        counted["n"] = 0
        try:
            mod._properties_for(shots)
        finally:
            os.stat = real_stat
        check(
            "the channel read is coherent (stats don't scale with the batch)",
            counted["n"] <= 4,
            f"{counted['n']} config stats for {len(shots)} shots — "
            "is the coherent() scope still there?",
        )


    # ── 14. Tri-state fields: unpinned follows the entity ──
    #
    # The regression this whole section exists for: the form used to seed
    # from the first checked entity and send those numbers for the whole
    # batch, so submitting six shots rendered all six at shot #1's length.
    two = sorted(shots, key=str)[:2]
    ranges = {}
    for uri in two:
        properties = mod._properties_for([uri])[0]
        ranges[str(uri)] = (
            properties.get('frame_start'), properties.get('frame_end'),
        )
    # Which frame field actually disagrees decides what the MIXED display
    # can be asserted on: two shots often share a start and differ only in
    # length, which is exactly the case the old shared form got wrong.
    starts = {v[0] for v in ranges.values()}
    ends = {v[1] for v in ranges.values()}
    mixed_key = (
        'first_frame' if len(starts) > 1
        else 'last_frame' if len(ends) > 1 else None
    )
    differ = mixed_key is not None

    dlg7 = mod.SubmitJobsDialog(
        [two[0]], [two[0].segments[-1]], "shots",
    )
    first_entry = dlg7._fields['first_frame']
    check(
        "a single-entity open leaves every field unpinned",
        not any(e.pinned for k, e in dlg7._fields.items()
                if k not in ('render_department', 'pb_department')),
        f"pinned: {[k for k, e in dlg7._fields.items() if e.pinned]}",
    )
    check(
        "a single entity shows its own frame range, not a placeholder",
        first_entry.value() == ranges[str(two[0])][0],
        f"{first_entry.value()} != {ranges[str(two[0])][0]}",
    )
    check(
        "an unpinned field renders italic (it follows the entity)",
        first_entry.widget.font().italic(),
    )
    # The unset sentinel is the spin box's minimum, one step below the
    # field's real lower bound — not a far-away constant, which would let a
    # bounded field be typed below its bound.
    pre_roll = dlg7._fields['pre_roll'].widget
    check(
        "the unset sentinel is exactly one step below the real minimum",
        pre_roll.minimum() == -1 and pre_roll.maximum() == 1000,
        f"range {pre_roll.minimum()}..{pre_roll.maximum()}",
    )
    check(
        "the sentinel displays as the placeholder, not as its number",
        pre_roll.specialValueText() == mod.PER_ENTITY_TEXT,
    )

    # Check the second shot in: if the two disagree, the field must stop
    # showing either one's number.
    leaf_of(dlg7, two[1]).setCheckState(0, Qt.Checked)
    if differ:
        mixed_entry = dlg7._fields[mixed_key]
        check(
            f"two entities that disagree park {mixed_key} on ⟨per entity⟩",
            mixed_entry.value() is resolve.MIXED,
            f"{mixed_entry.value()!r} (ranges: {ranges})",
        )
        check(
            "the spin box actually displays the placeholder text",
            mixed_entry.widget.text() == mod.PER_ENTITY_TEXT,
            f"{mixed_entry.widget.text()!r}",
        )
        # The field they *agree* on keeps showing the shared value: only
        # a genuine disagreement is worth hiding behind a placeholder.
        agreed_key = (
            'last_frame' if mixed_key == 'first_frame' else 'first_frame'
        )
        if len({v[0 if agreed_key == 'first_frame' else 1]
                for v in ranges.values()}) == 1:
            check(
                f"the agreed field ({agreed_key}) still shows its value",
                dlg7._fields[agreed_key].value() is not resolve.MIXED,
                f"{dlg7._fields[agreed_key].value()!r}",
            )
    else:
        print("SKIP: the first two shots share a frame range — "
              "MIXED display not exercised")

    # ── 15. The fix: each entity resolves to its OWN frame range ──
    rows = dlg7._resolved_batch()
    check(
        "the batch resolves one settings dict per checked entity",
        len(rows) == 2,
        f"{len(rows)} rows",
    )
    resolved_ranges = {
        name: (settings.get('first_frame'), settings.get('last_frame'))
        for name, settings, _w in rows
    }
    expected_ranges = {
        uri.segments[-1]: ranges[str(uri)] for uri in two
    }
    check(
        "each entity is submitted with its own frame range",
        resolved_ranges == expected_ranges,
        f"{resolved_ranges} != {expected_ranges}",
    )

    # ── 16. Pinning applies one value to the whole batch ──
    first_entry.widget.setValue(1234)
    first_entry.pin()  # setValue is programmatic; a real edit pins itself
    pinned_rows = dlg7._resolved_batch()
    check(
        "a pinned field applies to every entity in the batch",
        all(s.get('first_frame') == 1234 for _n, s, _w in pinned_rows),
        f"{[s.get('first_frame') for _n, s, _w in pinned_rows]}",
    )
    check(
        "a pinned field renders upright (it is the artist's choice)",
        not first_entry.widget.font().italic(),
    )
    check(
        "pinning first_frame leaves last_frame following the entity",
        not dlg7._fields['last_frame'].pinned,
    )

    # ↺ hands the field back to the entities.
    dlg7._unpin(('first_frame',))
    reverted = {
        name: settings.get('first_frame')
        for name, settings, _w in dlg7._resolved_batch()
    }
    check(
        "↺ reverts a pinned field to each entity's own value",
        not first_entry.pinned
        and reverted == {k: v[0] for k, v in expected_ranges.items()},
        f"{reverted}",
    )

    # ── 17. Reseeding never clobbers a pinned field ──
    dlg7._fields['render_priority'].widget.setValue(77)
    dlg7._fields['render_priority'].pin()
    dlg7._set_all_checked(True)
    check(
        "growing the batch leaves a pinned field alone",
        dlg7._fields['render_priority'].value() == 77,
        f"{dlg7._fields['render_priority'].value()}",
    )
    check(
        "growing the batch re-derives the unpinned fields",
        not dlg7._fields['first_frame'].pinned,
    )

    # ── 18. Pre-flight table ──
    dlg7._set_all_checked(False)
    for uri in two:
        leaf_of(dlg7, uri).setCheckState(0, Qt.Checked)
    dlg7._preflight_box.setChecked(True)
    dlg7._refresh_preflight()
    table = dlg7._preflight
    check(
        "pre-flight shows one row per checked entity",
        table.topLevelItemCount() == 2,
        f"{table.topLevelItemCount()} rows",
    )
    headers = [
        table.headerItem().text(i) for i in range(table.columnCount())
    ]
    check(
        "pre-flight is bracketed by the entity name and a warnings column",
        headers[0] == "Shot" and headers[-1] == "Warnings",
        f"{headers}",
    )
    if differ:
        check(
            "a field the batch disagrees on becomes a pre-flight column",
            "First" in headers or "Last" in headers,
            f"{headers}",
        )
        shown = {
            table.topLevelItem(i).text(0) for i in range(2)
        }
        check(
            "pre-flight names the entities it will submit",
            shown == {u.segments[-1] for u in two},
            f"{shown}",
        )

    # A pinned field is the same for everyone, so it stops being a column.
    dlg7._fields['first_frame'].widget.setValue(1010)
    dlg7._fields['first_frame'].pin()
    dlg7._refresh_preflight()
    headers = [
        table.headerItem().text(i) for i in range(table.columnCount())
    ]
    check(
        "a pinned field is not a pre-flight column (it cannot vary)",
        "First" not in headers,
        f"{headers}",
    )
    dlg7._unpin(('first_frame',))

    # ── 19. Pre-flight warnings fire before the submit loop ──
    combo7 = dlg7._rnd_channels
    invented = "no_such_channel_zzz"
    if invented not in combo7.options():
        # Force a channel none of the entities define, the way a union pick
        # across a heterogeneous batch does.
        combo7.set_options(list(combo7.options()) + [invented], [invented])
        rows = dlg7._resolved_batch()
        check(
            "an undefined channel is still submitted (visible failure wins)",
            all(s.get('variants') == [invented] for _n, s, _w in rows),
            f"{[s.get('variants') for _n, s, _w in rows]}",
        )
        check(
            "…but every affected entity is warned about it up front",
            all(any('not defined here' in w for w in warnings)
                for _n, _s, warnings in rows),
            f"{[w for _n, _s, w in rows]}",
        )
        combo7.set_options(combo7.options()[:-1], [DEFAULT_CHANNEL])

    # ── 20. A frame-less entity omits the keys rather than guessing ──
    empty_rows = [
        (name, settings) for name, settings, _w in [
            (
                "synthetic",
                resolve.resolve_settings(
                    {}, sections=('render',),
                    form=dlg7._form_values(),
                    pinned=dlg7._pinned_keys(),
                    fallbacks=dlg7._fallbacks,
                ),
                [],
            )
        ]
    ]
    check(
        "an entity with no frame range omits first_frame/last_frame",
        'first_frame' not in empty_rows[0][1]
        and 'last_frame' not in empty_rows[0][1],
        f"{sorted(empty_rows[0][1])}",
    )


    # ── 21. ProcessDialog submission path ──
    #
    # Needs hpm.toml's [python_dependencies] (qtpy, tomli_w) on the path —
    # Houdini's bundled interpreter alone has neither, and the dialog then
    # correctly falls back to the inline loop. Skipped rather than failed so
    # this harness still runs in a bare environment.
    dlg8 = mod.SubmitJobsDialog(
        two, [u.segments[-1] for u in two], "shots",
    )
    proc_rows = dlg8._resolved_batch()
    tasks = dlg8._submit_tasks(proc_rows)
    if not tasks:
        print(
            "SKIP: qtpy/tomli_w not importable — ProcessDialog path not "
            "exercised (the dialog falls back to the inline loop)"
        )
    else:
        check(
            "one ProcessTask per checked entity",
            len(tasks) == len(two),
            f"{len(tasks)} tasks for {len(two)} entities",
        )
        check(
            "tasks are farm-only, which pins ProcessDialog to farm mode",
            all(t.execute_local is None and t.execute_farm is not None
                for t in tasks),
        )
        check(
            "each task carries its own resolved frame range",
            [(t.first_frame, t.last_frame) for t in tasks]
            == [(s.get('first_frame'), s.get('last_frame'))
                for _n, s, _w in proc_rows],
            f"{[(t.first_frame, t.last_frame) for t in tasks]}",
        )
        check(
            "each task is addressed to its own entity",
            [str(t.uri) for t in tasks] == [str(u) for u in two],
        )

        # The late-binding trap: a lambda closing over the loop variable
        # would submit the LAST entity's settings N times. Every task must
        # carry its own.
        import tumblepipe.farm.jobs.houdini.batch_submit as bs
        captured = []
        real_submit = bs.submit_entity_batch
        bs.submit_entity_batch = lambda config: (
            captured.append(config) or ["job"]
        )
        try:
            for task in dlg8._submit_tasks(proc_rows):
                task.execute_farm()
        finally:
            bs.submit_entity_batch = real_submit
        submitted = {
            c['entity']['name']:
                (c['settings'].get('first_frame'),
                 c['settings'].get('last_frame'))
            for c in captured
        }
        expected_submitted = {
            name: (s.get('first_frame'), s.get('last_frame'))
            for name, s, _w in proc_rows
        }
        check(
            "each task submits exactly once",
            len(captured) == len(two),
            f"{len(captured)}",
        )
        check(
            "each task submits ITS OWN settings, not a shared dict",
            submitted == expected_submitted,
            f"{submitted} != {expected_submitted}",
        )

        try:
            from tumblepipe.pipe.houdini.ui.process_dialog import (
                ProcessDialog,
            )
            from tumblepipe.pipe.houdini.ui.process_executor import (
                ProcessExecutor,
            )
            pd = ProcessDialog(
                title="Submit to Farm", tasks=tasks,
                current_department=None, parent=None,
            )
            check("ProcessDialog constructs with farm-only tasks", pd is not None)
            # The whole reason this reuse is safe on the GUI thread: the
            # executor sequences with QTimer.singleShot on the main thread,
            # so there is no worker thread and none of the Qt affinity
            # hazards that come with moving pipeline work off it.
            src = inspect.getsource(ProcessExecutor.execute)
            check(
                "the executor sequences on the main thread (no worker thread)",
                "singleShot" in src and "Thread" not in src,
                src.strip(),
            )
        except ImportError as exc:
            print(f"SKIP: ProcessDialog not importable ({exc})")

    print("ALL PASS" if all(results) else "FAILURES")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
