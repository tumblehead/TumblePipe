"""Headless verify: the farm playblast job family.

Run under a project hython (``TH_PROJECT_PATH`` at a project with at least
one shot), e.g. via TumbleTrove Desktop's run_hython:

    hython scripts/verify_playblast_job.py

Nothing is submitted to Deadline. Checks:

 1. The task config validator (tasks/playblast/_spec) accepts a well-formed
    config and rejects malformed ones.
 2. The job config validator (jobs/houdini/playblast/job) does the same for
    the {entity, settings} shape the dialog builds.
 3. The versioned playblast + rolling daily output paths resolve for a real
    shot and land under <shot>/<dept>/ (i.e. the department is passed --
    the arity bug this feature shipped a fix for stays fixed).
 4. build() populates the batch with a 'playblast' task + a 'playblast_notify'
    that depends on it, the task carries the dedicated 'playblast' group, and
    its output paths are the versioned playblast + daily. Skipped (not failed)
    if Task construction can't run from a dev-override checkout (hpm requires
    the script under ~/.hpm/packages/<name>@<version>/).
"""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    from tumblepipe.api import default_client
    from tumblepipe.config.entities import is_terminal_entity
    from tumblepipe.farm.tasks.playblast import _spec
    import tumblepipe.farm.jobs.houdini.playblast.job as playblast_job
    from tumblepipe.pipe.paths import (
        get_next_playblast_path,
        get_daily_path,
    )

    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(bool(ok))
        line = f"{'PASS' if ok else 'FAIL'}: {name}"
        if detail and not ok:
            line += f" — {detail}"
        print(line)

    # A real shot to resolve paths against.
    config = default_client().config
    shots = [
        u for u in config.list_entity_uris(closure=True)
        if u.segments and u.segments[0] == "shots"
        and is_terminal_entity(config, u)
    ]
    if not shots:
        print("SKIP: project has no shots")
        return 1
    shot = sorted(shots, key=str)[0]
    department = "playblast"  # arbitrary output label; need not be a real dept

    # 1. Task config validator.
    good_task = dict(
        title="t", priority=50, pool_name="general",
        first_frame=1, last_frame=10, step_size=1, fps=25,
        res=[1280, 720], input_path="stage.usda",
        output_paths=["a.mp4", "b.mp4"],
    )
    check("task _spec accepts a well-formed config", _spec.is_valid_config(good_task))
    check("task _spec rejects a sparse config", not _spec.is_valid_config({"title": "t"}))
    check(
        "task _spec rejects a malformed resolution",
        not _spec.is_valid_config({**good_task, "res": [1280]}),
    )
    check(
        "task _spec rejects empty output_paths",
        not _spec.is_valid_config({**good_task, "output_paths": []}),
    )

    # 2. Job config validator.
    good_job = dict(
        entity=dict(uri=str(shot), department=department),
        settings=dict(
            user_name="tester", purpose="render", pool_name="general",
            priority=50, input_path="stage.usda", first_frame=1,
            last_frame=10, step_size=1, fps=25, res=[1280, 720],
            channel_name="renders",
        ),
    )
    check("job validator accepts a well-formed config", playblast_job._is_valid_config(good_job))
    check(
        "job validator rejects a missing entity",
        not playblast_job._is_valid_config({"settings": good_job["settings"]}),
    )
    check(
        "job validator rejects missing settings keys",
        not playblast_job._is_valid_config(
            {"entity": good_job["entity"], "settings": {"user_name": "x"}}
        ),
    )

    # 3. Output paths resolve under <shot>/<dept>/ (department is passed).
    pb_path = get_next_playblast_path(shot, department, "render")
    daily_path = get_daily_path(shot, department, "render")
    check(
        "versioned playblast lands under the department",
        pb_path.parent.name == department and pb_path.suffix == ".mp4",
        str(pb_path),
    )
    check(
        "rolling daily carries the department, not the constant 'render'",
        department in daily_path.parts and daily_path.suffix == ".mp4",
        str(daily_path),
    )

    # 4. build() structure (skipped if Task can't build off a dev checkout).
    import tempfile
    jobs: dict = {}
    deps: dict = {}
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            playblast_job.build(good_job, {}, Path(temp_dir), jobs, deps)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "hpm" in msg.lower() or "packages" in msg.lower():
            print(f"SKIP: build() needs an installed package, not a dev checkout ({msg})")
        else:
            check("build() populates the batch", False, msg)
    else:
        check("build() adds a playblast task", "playblast" in jobs)
        check("build() adds a notify task", "playblast_notify" in jobs)
        check(
            "notify depends on the playblast task",
            deps.get("playblast_notify") == ["playblast"],
            str(deps.get("playblast_notify")),
        )
        pb_task = jobs.get("playblast")
        check(
            "playblast task uses the dedicated 'playblast' group",
            getattr(pb_task, "group", None) == "playblast",
            str(getattr(pb_task, "group", None)),
        )
        out = [str(p) for p in getattr(pb_task, "output_paths", [])]
        check(
            "playblast task outputs the versioned playblast + daily",
            any(pb_path.name in p for p in out)
            and any(daily_path.name in p for p in out),
            str(out),
        )

    print("ALL PASS" if all(results) else "FAILURES")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
