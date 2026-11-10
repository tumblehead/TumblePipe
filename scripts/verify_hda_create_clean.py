"""Audit: every pipeline HDA creates without a stale-parameter warning.

    hython scripts/verify_hda_create_clean.py

QA 2026-11-10: dropping a th::lookdev_studio warned

    Skipping unrecognized parameter "viewport_folder".

twice, on every creation. Two Camera LOPs inside the HDA (turntable_camera,
create_turntable_camera) still stored a value for a folder the H22 Camera
LOP no longer has. It costs nothing at cook time, but the node carries a
warning badge from the moment it is dropped, and a warning an artist sees on
every drop teaches them to ignore the badge.

This installs every expanded HDA under otls/ (the tracked source; no compile
step), creates the latest version of each type in a parent of its context and
fails on any "Skipping unrecognized parameter" warning on the node or inside
it. A type that fails to create at all also fails.

Needs Houdini (hython). Nothing outside a temp dir is written.
"""

import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

TEMP_ROOT = Path(tempfile.mkdtemp(prefix='verify-hda-create-'))
os.environ.setdefault('TH_EVENTS_DIR', str(TEMP_ROOT / 'events'))

import hou  # noqa: E402

STALE = re.compile(r'Skipping unrecognized parameter "([^"]+)"')
BAD_TYPE = re.compile(r'Bad node type found: (\S+) in')

# Types whose creation needs something a bare hython cannot give (a GUI, a
# pipeline workfile), keyed by type name, with why. They are still installed.
KNOWN_GAPS = {}

FAILURES = []


def check(ok, what):
    print(('PASS ' if ok else 'FAIL ') + what)
    if not ok:
        FAILURES.append(what)


def expanded_hdas():
    for item in sorted((REPO / 'otls').iterdir()):
        if item.is_dir() and (item / 'Sections.list').exists():
            yield item


def parent_for(category, parents):
    name = category.name()
    if name not in parents:
        if name == 'Lop':
            parents[name] = hou.node('/stage')
        elif name == 'Sop':
            parents[name] = hou.node('/obj').createNode('geo', 'sops')
        elif name == 'Cop':
            parents[name] = hou.node('/obj').createNode('copnet', 'cops')
        elif name == 'Vop':
            parents[name] = hou.node('/mat')
        else:
            parents[name] = None
    return parents[name]


def stale_parms(node):
    found = set()
    for each in [node] + list(node.allSubChildren()):
        for message in each.warnings() + each.errors():
            found.update(STALE.findall(message))
    return sorted(found)


def install_houdini_packages():
    """Houdini's own packages (apex, kinefx, sculpt...) hold types our HDAs
    use inside (th::animate's apex::sceneinvoke::2.0); a
    HOUDINI_PACKAGE_SKIP=1 hython does not load them."""
    packages = Path(hou.text.expandString('$HFS')) / 'packages'
    for otl in sorted(packages.glob('*/otls/*.hda')):
        hou.hda.installFile(str(otl))


def main():
    install_houdini_packages()
    definitions = {}
    for path in expanded_hdas():
        # Run from the repo root, hython also scans otls/ itself, where a
        # compile-hdas run leaves collapsed .hda files that can be older than
        # the sources; the expanded source must win.
        hou.hda.installFile(str(path), force_use_assets=True)
        for definition in hou.hda.definitionsInFile(str(path)):
            definitions[definition.nodeTypeName()] = definition
    stale_binaries = sorted(
        name for name, definition in definitions.items()
        if definition.nodeType() is not None
        and Path(definition.nodeType().definition().libraryFilePath()).suffix == '.hda'
    )
    check(not stale_binaries, f'the expanded sources are the definitions in use {stale_binaries}')
    check(len(definitions) > 0, f'{len(definitions)} HDA types installed from otls/')

    parents = {}
    for type_name, definition in sorted(definitions.items()):
        node_type = definition.nodeType()
        if node_type is None or node_type.hidden():
            continue
        if type_name in KNOWN_GAPS:
            print(f'SKIP {type_name}: {KNOWN_GAPS[type_name]}')
            continue
        parent = parent_for(node_type.category(), parents)
        if parent is None:
            print(f'SKIP {type_name}: no parent for {node_type.category().name()}')
            continue
        try:
            node = parent.createNode(type_name)
        except hou.Error as exc:
            # A type Houdini only ships as a DSO in one of its packages
            # (apex::sceneinvoke::2.0 is in apex_sop.dll) cannot be
            # installed from a file; say so instead of failing.
            missing = [
                name for name in BAD_TYPE.findall(str(exc))
                if not name.startswith('th::')
            ]
            if missing:
                print(f'SKIP {type_name}: needs {sorted(set(missing))}, not loaded here')
            else:
                check(False, f'{type_name}: creates ({exc})')
            continue
        stale = stale_parms(node)
        check(not stale, f'{type_name}: no stale parameters {stale if stale else ""}')
        node.destroy()

    print('FAILED: ' + '; '.join(FAILURES) if FAILURES else 'ALL PASS')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
