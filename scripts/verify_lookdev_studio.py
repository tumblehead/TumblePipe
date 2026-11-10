"""Audit: th::lookdev_studio's Submit says what is missing before it submits.

    hython scripts/verify_lookdev_studio.py

QA 2026-11-10: a th::lookdev_studio dropped after an asset's LOOKDEV node
has Selection > Primitives empty. Its inner network then errors ("No input
prims assigned.") while the node itself shows no badge, and an errored LOP
has no stage, so Submit Render To Farm died in get_aov_names with

    AttributeError: 'NoneType' object has no attribute 'GetPseudoRoot'

This builds a stand-in asset (a box at /CHAR/hero/geo/body) feeding a
lookdev_studio and checks:

- with Primitives empty, the AOV lookup Submit runs first raises a NodeError
  naming Selection > Primitives;
- with Primitives set to the asset, the stage cooks and the turntable's AOVs
  (beauty among them) are found.

Needs Houdini (hython). Nothing outside a temp dir is written.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
os.environ.setdefault('TH_EVENTS_DIR', tempfile.mkdtemp(prefix='verify-lookdev-studio-'))

import hou  # noqa: E402

FAILURES = []


def check(ok, what):
    print(('PASS ' if ok else 'FAIL ') + what)
    if not ok:
        FAILURES.append(what)


def install_sources():
    # Expanded sources win over any collapsed otls/*.hda a compile left behind.
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            hou.hda.installFile(str(item), force_use_assets=True)


def stand_in_asset(stage_node):
    sop = stage_node.createNode('sopcreate', 'ASSET')
    sop.parm('pathprefix').set('/CHAR/hero/geo/')
    inner = sop.node('sopnet/create')
    box = inner.createNode('box')
    name = inner.createNode('name')
    name.parm('name1').set('body')
    name.setInput(0, box)
    out = inner.createNode('output')
    out.setInput(0, name)
    out.setDisplayFlag(True)
    return sop


def main():
    install_sources()
    from tumblepipe.pipe.houdini.nodes import NodeError
    from tumblepipe.pipe.houdini.lops.lookdev_studio import LookdevStudio

    stage_node = hou.node('/stage')
    studio = stage_node.createNode('th::lookdev_studio', 'TURNTABLE')
    studio.setInput(0, stand_in_asset(stage_node))
    node = LookdevStudio(studio)

    try:
        node.get_aov_names()
        error = None
    except NodeError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - the old AttributeError
        error = f'{type(exc).__name__}: {exc}'
    check(error is not None and 'Primitives' in error,
          f'empty Primitives: Submit says to set Selection > Primitives ({error})')

    studio.parm('primpattern').set('/CHAR/hero')
    try:
        aovs = node.get_aov_names()
    except Exception as exc:  # noqa: BLE001 - reported as a FAIL
        aovs = f'{type(exc).__name__}: {exc}'
    check(isinstance(aovs, list) and 'beauty' in aovs,
          f'Primitives set: the turntable AOVs are found ({aovs})')

    print('FAILED: ' + '; '.join(FAILURES) if FAILURES else 'ALL PASS')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
