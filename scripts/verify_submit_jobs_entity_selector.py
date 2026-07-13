"""Headless verify: SubmitJobsDialog entity selector.

Run under a project hython (``TH_PROJECT_PATH`` pointing at a project with
at least one shot and one asset), e.g. via TumbleTrove Desktop's run_hython
with dev overrides:

    hython scripts/verify_submit_jobs_entity_selector.py

Checks:
 1. Single-entity open shows the selector, populated with exactly the
    project's terminal entities, defaulting to the opened entity.
 2. Picking a different entity re-targets the dialog (uris/names/context)
    and updates the header.
 3. A context switch (shots -> assets) repopulates the department combos.
 4. Frame seeds follow the newly picked entity's properties.
 5. Multi-entity opens get no selector.

Qt runs on the offscreen platform; no project data is written.
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
    if not shots or not assets:
        print(
            "SKIP: project needs >=1 shot and >=1 asset "
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

    # 1. Single-entity open: selector present, complete, defaulted.
    shot = shots[0]
    dlg = mod.SubmitJobsDialog([shot], [shot.segments[-1]], "shots")
    combo = dlg._entity_combo
    check("selector present on single-entity open", combo is not None)
    if combo is None:
        print("FAILURES")
        return 1
    check(
        "selector lists exactly the terminal entities",
        combo.count() == len(terminal),
        f"{combo.count()} != {len(terminal)}",
    )
    default_data = combo.itemData(combo.currentIndex())
    check(
        "default is the opened entity",
        default_data is not None and str(default_data[0]) == str(shot),
    )

    # 2 + 3 + 4. Pick an asset: retarget, header, dept repopulation, seeds.
    target = assets[0]
    idx = next(
        (
            i for i in range(combo.count())
            if str(combo.itemData(i)[0]) == str(target)
        ),
        None,
    )
    check("picked asset present in selector", idx is not None)
    if idx is None:
        print("FAILURES")
        return 1
    combo.setCurrentIndex(idx)
    check(
        "retargets entity uris",
        [str(u) for u in dlg._entity_uris] == [str(target)],
    )
    check("context switched to assets", dlg._context == "assets")
    check("header names the picked entity", target.segments[-1] in dlg._header.text())

    expected_depts = mod._list_dept_names(
        "assets", only_publishable=False, only_renderable=True,
    )
    actual_depts = [dlg._rnd_dept.itemText(i) for i in range(dlg._rnd_dept.count())]
    check(
        "render department list follows context",
        actual_depts == expected_depts,
        f"{actual_depts} != {expected_depts}",
    )

    props = mod._safe_get_properties(target)
    check(
        "frame seeds follow the picked entity",
        dlg._rnd_first.value() == int(mod._nested(props, "frame_start", 1001))
        and dlg._rnd_last.value() == int(mod._nested(props, "frame_end", 1100)),
    )

    # 5. Multi-entity open: no selector.
    two_shots = (shots * 2)[:2]
    dlg2 = mod.SubmitJobsDialog(
        two_shots, [u.segments[-1] for u in two_shots], "shots",
    )
    check("no selector on multi-entity open", dlg2._entity_combo is None)

    print("ALL PASS" if all(results) else "FAILURES")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
