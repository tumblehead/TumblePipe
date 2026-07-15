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

Qt runs on the offscreen platform; no project data is written and nothing
is submitted to the farm.
"""

from __future__ import annotations

import importlib.util
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
    actual_depts = [
        dlg3._rnd_dept.itemText(i) for i in range(dlg3._rnd_dept.count())
    ]
    check(
        "asset-context tree + department list",
        actual_depts == expected_depts
        and [str(u) for u in dlg3._entity_uris] == [str(asset)],
        f"{actual_depts} != {expected_depts}",
    )

    # 11. Playblast section is shots-only, with the renderable-department list.
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
            dlg._pb_dept.itemText(i) for i in range(dlg._pb_dept.count())
        ]
        rnd_depts = mod._list_dept_names(
            "shots", only_publishable=False, only_renderable=True,
        )
        check(
            "playblast department list == renderable shot departments",
            pb_depts == rnd_depts,
            f"{pb_depts} != {rnd_depts}",
        )
        check(
            "playblast section starts unchecked (opt-in)",
            dlg._playblast_box.isChecked() is False,
        )

    print("ALL PASS" if all(results) else "FAILURES")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
