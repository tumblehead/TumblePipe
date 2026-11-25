"""Headless Qt harness: what Farm Submit's grid cells and resolved rows are.

Drives the dialog's own logic on a bare ``_ContextPane`` (only the state it
reads) over seeded rows: random columns, entity properties (frame ranges,
turned-off departments, cuts, channels), scan statuses (errors included),
ticks and forms. It pins:

  1. The cell states ``_apply_status`` gives every row, and the rows
     ``_resolved_rows`` resolves (settings, warnings, errors, the stale
     upstream line first), reproduce the run recorded in
     ``tests/data/farm_grid_rows.json``.
  2. The record is broad: it holds errored rows, turned-off cells, stale
     upstream lines, blocking errors and every cell state.
  3. A scan error makes every cell of its row pending; a department turned
     off on the entity is off on its publish column only.
  4. A preview-only rescan keeps the publish half of the row's status and
     takes the preview half from the new scan; a failed rescan's error stays
     on the row, so its cells show it.

The logic itself is ``farm.submit.grid`` / ``status``
(``tests/test_farm_grid_rows.py`` checks the same record without Qt); this
harness checks the dialog's wiring to it.

Run (no Houdini, no project):
    QT_QPA_PLATFORM=offscreen TUMBLETROVE_PYTHON=<tumbletrove>/python \\
        uv run --python 3.12 --with pyside6 python scripts/verify_submit_cell_states.py [--record]
"""

import hashlib
import json
import os
import random
import sys
from concurrent.futures import Future
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'python'))
RECORD = REPO_ROOT / 'tests' / 'data' / 'farm_grid_rows.json'


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
from tumblepipe.farm.submit import grid  # noqa: E402
from tumblepipe.farm.submit import settings as resolve  # noqa: E402
from tumblepipe.farm.submit import status as farm_status  # noqa: E402

SEEDS = range(150)
POOL = ['layout', 'animation', 'fx', 'light']
RENDERABLE = ['animation', 'fx', 'light']

FAILED = []


def check(name: str, ok: bool, info: str = '') -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"  ({info})" if info and not ok else ''))
    if not ok:
        FAILED.append(name)


class _SceneComp:
    def __init__(self, aovs):
        self.aovs = aovs

    def settings(self):
        return {'path': 'comp.hip', 'exports': [], 'aovs': list(self.aovs)}


def _mtime(rng):
    return None if rng.random() < 0.3 else float(rng.randint(100, 110))


def _status(rng, uri):
    if rng.random() < 0.12:
        return farm_status.RowStatus(uri=uri, error='share did not answer')
    return farm_status.RowStatus(
        uri=uri,
        hip_mtimes={d: _mtime(rng) for d in POOL},
        export_mtimes={d: _mtime(rng) for d in POOL},
        shared={d for d in POOL if rng.random() < 0.15},
        playblast_mtime=_mtime(rng),
        render_mtime=_mtime(rng),
        comp_workfile=rng.random() < 0.6,
        comp_mtime=_mtime(rng),
        staged={c for c in ('default', 'fx') if rng.random() < 0.7},
        rendered=None if rng.random() < 0.5 else (1001, 1001 + rng.randint(0, 30)),
    )


def _props(rng):
    props = {}
    if rng.random() < 0.85:
        props.update(frame_start=1001, frame_end=1001 + rng.randint(5, 40),
                     roll_start=rng.randint(0, 2), roll_end=rng.randint(0, 2))
    if rng.random() < 0.4:
        props['disabled_departments'] = rng.sample(POOL, rng.randint(1, 2))
    if rng.random() < 0.5:
        props['render_department'] = rng.choice(RENDERABLE)
    if rng.random() < 0.3:
        props['pb_department'] = rng.choice(RENDERABLE)
    if rng.random() < 0.5:
        props['channels'] = rng.sample(['fx', 'chars'], rng.randint(1, 2))
    if rng.random() < 0.5:
        props['departments'] = rng.sample(POOL, rng.randint(1, 4))
    return props


def build(seed: int):
    """A bare pane holding seed's rows, statuses applied."""
    rng = random.Random(seed)
    context = 'shots' if rng.random() < 0.8 else 'assets'
    pane = sjd._ContextPane.__new__(sjd._ContextPane)
    QWidget.__init__(pane)
    pane._context = context
    pane._publish_departments = list(POOL)
    pane._columns = grid.columns_for(context, POOL)
    pane._fields = {}
    pane._fallbacks = {'render_department': grid.preview_department_fallback(RENDERABLE)}
    pane._fallbacks['pb_department'] = pane._fallbacks['render_department']
    pane._uris = [f"entity:/{context}/010/e{i:02d}0" for i in range(rng.randint(1, 4))]
    pane._props = {uri: _props(rng) for uri in pane._uris}
    pane._statuses = {uri: _status(rng, uri) for uri in pane._uris}
    pane._scene_comps = {}
    if context == 'shots' and rng.random() < 0.3:
        pane._scene_comps[rng.choice(pane._uris)] = _SceneComp(rng.sample(['beauty', 'depth'], rng.randint(0, 2)))
    pane._states = {}
    for uri in pane._uris:
        pane._apply_status(uri)
    pane._ticks = {
        (uri, column.key) for uri in pane._uris for column in pane._columns
        if rng.random() < 0.35 and pane._states.get((uri, column.key)) in grid.TICKABLE
    }
    form = {
        resolve.CHANNEL_LIST_KEY: rng.sample(['default', 'fx', 'chars'], rng.randint(0, 2)),
        'render_mode': rng.choice(['full', 'first_middle_last', 'preview_then_full']),
        'frame_list': rng.choice(['', '', '1003-1006', '1002-1003 1006-1008', 'abc']),
        'render_step': rng.choice([1, 1, 2]),
    }
    pinned = []
    if rng.random() < 0.3:
        form['render_department'] = rng.choice(RENDERABLE)
        pinned.append('render_department')
    if rng.random() < 0.3:
        form['comp_first_frame'] = rng.randint(995, 1010)
        pinned.append('comp_first_frame')
    pane._form_values = lambda: dict(form)
    pane._pinned_keys = lambda: list(pinned)
    return pane


def outcome(pane) -> dict:
    """What the record holds of one pane: cell states and resolved rows."""
    rows = pane._resolved_rows()
    return {
        'states': sorted(f"{uri}|{key}|{state}" for (uri, key), state in pane._states.items()),
        'rows': [[r.uri, r.settings, r.shown()[len(r.errors):], list(r.errors)] for r in rows],
    }


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=repr).encode()).hexdigest()


def main() -> int:
    record = '--record' in sys.argv
    outcomes = {seed: outcome(build(seed)) for seed in SEEDS}
    digests = {str(seed): digest(value) for seed, value in outcomes.items()}
    if record:
        RECORD.write_bytes((json.dumps(digests, indent=1, sort_keys=True) + '\n').encode())
        print(f"recorded {len(digests)} seeds to {RECORD}")
        return 0

    # 1. The record
    recorded = json.loads(RECORD.read_bytes())
    differ = [seed for seed in digests if recorded.get(seed) != digests[seed]]
    check("1. cell states and resolved rows reproduce the recorded run", not differ, f"seeds {differ[:5]}")

    # 2. The record is broad
    states = {s.rsplit('|', 1)[1] for o in outcomes.values() for s in o['states']}
    rows = [row for o in outcomes.values() for row in o['rows']]
    check("2. every cell state is reached",
          {grid.PENDING, grid.OFF, grid.NEVER, grid.STALE, grid.CURRENT, grid.NONE} <= states, str(states))
    check("2. rows with a stale upstream line, warnings and blocking errors are reached",
          any(w and 'latest export used' in w[0] for _u, _s, w, _e in rows)
          and any(w for _u, _s, w, _e in rows) and any(e for _u, _s, _w, e in rows))

    # 3. Errors and off departments
    pending_ok, off_ok = True, True
    for seed in SEEDS:
        pane = build(seed)
        for uri in pane._uris:
            off = set(pane._props[uri].get('disabled_departments', ()))
            for column in pane._columns:
                state = pane._states[(uri, column.key)]
                if pane._statuses[uri].error:
                    pending_ok &= state == grid.PENDING
                else:
                    is_off = column.kind == grid.PUBLISH and column.department in off
                    off_ok &= (state == grid.OFF) == is_off
    check("3. a scan error makes every cell of its row pending", pending_ok)
    check("3. a turned-off department is off on its publish column only", off_ok)

    # 4. The preview-only merge
    pane = build(3)
    uri = pane._uris[0]
    old = farm_status.RowStatus(
        uri=uri, hip_mtimes={'layout': 5.0}, export_mtimes={'layout': 6.0},
        playblast_mtime=7.0, render_mtime=8.0, comp_workfile=True, comp_mtime=9.0,
        staged={'default'}, rendered=(1001, 1010),
    )

    class _Stub:
        def stop(self):
            pass

        def setText(self, _text):
            pass

    def drain(new):
        pane._statuses[uri] = farm_status.RowStatus(**{k: getattr(old, k) for k in old.__dataclass_fields__})
        future = Future()
        future.set_result(new)
        pane._probe_queue, pane._pending = [], {future: (uri, True)}
        pane._poll, pane._status_label = _Stub(), _Stub()
        pane._changed = pane._auto_collapse = lambda: None
        pane._drain_scans()
        return pane._statuses[uri]

    new = farm_status.RowStatus(
        uri=uri, hip_mtimes={'layout': 1.0}, export_mtimes={}, playblast_mtime=17.0,
        render_mtime=18.0, comp_workfile=False, comp_mtime=19.0, staged=set(), rendered=None,
    )
    merged = drain(new)
    check("4. a preview-only rescan keeps the publish half and takes the preview half",
          merged.hip_mtimes == old.hip_mtimes and merged.export_mtimes == old.export_mtimes
          and (merged.playblast_mtime, merged.render_mtime, merged.comp_workfile,
               merged.comp_mtime, merged.staged, merged.rendered)
          == (17.0, 18.0, False, 19.0, set(), None))
    failed = drain(farm_status.RowStatus(uri=uri, error='share did not answer'))
    check("4. a failed preview-only rescan keeps its error on the row, and its cells pending",
          failed.error == 'share did not answer' and failed.hip_mtimes == old.hip_mtimes
          and all(pane._states[(uri, c.key)] == grid.PENDING for c in pane._columns))

    print(f"{len(FAILED)} failed" if FAILED else "all passed")
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
