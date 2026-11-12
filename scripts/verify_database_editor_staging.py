"""Drive the real Database Editor offscreen: a Save restages the shots.

    QT_QPA_PLATFORM=offscreen python verify_database_editor_staging.py --seed N --rounds N

Runs on Houdini's bundled python313 (PySide6, no licence) with this
checkout's python/ on PYTHONPATH, against the project ``TH_PROJECT_PATH``
names (the test harness's template copy). ``pipe.root_staging`` is replaced
by a recorder before the window is built, so this checks the WIRING only:
every Save through ``DatabaseWindow`` (its own adapter, built in its own
``__init__``) calls ``root_staging.stage_after_database_save`` with the node
saved, after the edit is on disk. What that hook does to the root layers
is pinned in-process by tests/test_render_settings_staging.py.

Prints PASS, or FAIL with what went wrong, per round; exits 1 on a failure.
"""

import argparse
import json
import os
import random
import sys
import types
from pathlib import Path

if os.environ.get('HOUDINI_BIN'):
    os.add_dll_directory(os.environ['HOUDINI_BIN'])

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

CALLS = []
SEEN_ON_DISK = []


def _recorder(uri):
    # What the hook would read: the database as saved.
    db = Path(os.environ['TH_PROJECT_PATH']) / '_config' / 'db' / 'entity.json'
    SEEN_ON_DISK.append(json.loads(db.read_text(encoding='utf-8')))
    CALLS.append(str(uri))
    return ''


_fake = types.ModuleType('tumblepipe.pipe.root_staging')
_fake.stage_after_database_save = _recorder
sys.modules['tumblepipe.pipe.root_staging'] = _fake

from PySide6.QtWidgets import QApplication  # noqa: E402

from tumblepipe.api import default_client  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.config_editor.database_window import DatabaseWindow  # noqa: E402


def _node(tree: dict, uri: Uri) -> dict:
    for segment in uri.segments:
        tree = tree['children'][segment]
    return tree['properties']


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    QApplication.instance() or QApplication([])
    client = default_client()
    for seq in ('010', '020'):
        uri = Uri.parse(f'entity:/shots/{seq}')
        if client.config.get_own_properties(uri) is None:
            client.config.add_entity(uri, {})
    window = DatabaseWindow(client)
    targets = ['entity:/', 'entity:/shots', 'entity:/shots/010', 'entity:/shots/020']
    failed = False
    for round_ in range(args.rounds):
        uri = Uri.parse(rng.choice(targets))
        CALLS.clear()
        SEEN_ON_DISK.clear()
        window.select_entity(uri)
        props = window._json_view.to_json()
        render = dict(props.get('render') or {})
        resolution = [rng.randint(16, 4096), rng.randint(16, 4096)]
        render['resolution'] = resolution
        props['render'] = render
        window._json_view.set_value(props, inherited_data=window._inherited_properties)
        window._on_save_changes()
        saved = (_node(SEEN_ON_DISK[-1], uri).get('render') or {}).get('resolution') if SEEN_ON_DISK else None
        if CALLS != [str(uri)] or saved != resolution:
            print(f'FAIL round {round_}: saved {uri} with {resolution}; hook calls {CALLS}, on disk at the call {saved}')
            failed = True
        else:
            print(f'PASS round {round_}: {uri}')
    window.close()
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
