"""Audit: the stock effects, environment and crowd templates pass publish validation.

    hython scripts/verify_shot_sop_templates.py

QA 2026-11-10: a new SQ020/SH020 effects workfile with one cached sphere in
its SOP Create failed its first publish:

    [ERROR] /effects: Disallowed root prim 'effects'. Only asset categories,
    collections, lights, cameras, Render, and scene are allowed.

The template drops a SOP Create whose Primitive Path and path prefix keep
Houdini's default, /<node name>, so everything an artist makes in it lands at
a root prim of its own, which validate_shot_root_prims refuses. environment
and crowd do the same. The templates now author under a category:
/FX/<node>, /SET/<node> and /CROWD/<node> (the stock channel_prims put fx at
/FX/** and env at /SET/**).

In a throwaway project this builds each template for a shot and for a Multi
of two shots, puts a named box in each SOP Create, and checks:

- the box is a Mesh under the department's category;
- the SOP Create's stage passes the shot root-prim validation.

Needs Houdini (hython) and this checkout's python/ on PYTHONPATH. Nothing
outside a temp dir is written.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

TEMP_ROOT = Path(tempfile.mkdtemp(prefix='verify-shot-sop-templates-'))
PROJECT = TEMP_ROOT / 'proj'
shutil.copytree(REPO / 'scripts' / 'project_template' / '_config', PROJECT / '_config')
os.environ['TH_PROJECT_PATH'] = str(PROJECT)
os.environ['TH_CONFIG_PATH'] = str(PROJECT / '_config')
os.environ['TH_PIPELINE_PATH'] = str(REPO)
os.environ['TH_EVENTS_DIR'] = str(TEMP_ROOT / 'events')
os.environ.setdefault('TH_USER_NAME', 'verify')
for name in list(sys.modules):
    if name == 'tumblepipe' or name.startswith('tumblepipe.'):
        del sys.modules[name]

import hou  # noqa: E402

from tumblepipe.api import api  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.config import groups  # noqa: E402
from tumblepipe.pipe.paths import Context  # noqa: E402
from tumblepipe.pipe.houdini import entity_node  # noqa: E402
from tumblepipe.pipe.houdini.validators.shot_structure import (  # noqa: E402
    validate_shot_root_prims,
)

TEMPLATES = REPO / 'scripts' / 'project_template' / '_config' / 'templates' / 'shots'

# department -> the category its SOP Create authors under
CATEGORIES = {'effects': 'FX', 'environment': 'SET', 'crowd': 'CROWD'}

FAILURES = []


def check(ok, what):
    print(('PASS ' if ok else 'FAIL ') + what)
    if not ok:
        FAILURES.append(what)


def load_template(department):
    path = TEMPLATES / department / 'template.py'
    spec = importlib.util.spec_from_file_location(f'{department}_template', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_box(sop_create):
    """What an artist does first: some named geometry in the SOP Create."""
    inner = sop_create.node('sopnet/create')
    box = inner.createNode('box')
    name = inner.createNode('name')
    name.parm('name1').set('thing')
    name.setInput(0, box)
    out = inner.createNode('output')
    out.setInput(0, name)
    out.setDisplayFlag(True)


def assert_column(sop_create, category, label):
    add_box(sop_create)
    # Only the SOP Create's own prims: a throwaway project has no staged
    # build for import_shot to read, so the stage holds nothing else.
    sop_create.setInput(0, None)
    stage = sop_create.stage()
    expected = f'/{category}/{sop_create.name()}/thing'
    prim = stage.GetPrimAtPath(expected)
    roots = [p.GetName() for p in stage.GetPseudoRoot().GetChildren()
             if p.GetName() != 'HoudiniLayerInfo']
    check(bool(prim) and prim.GetTypeName() == 'Mesh',
          f'{label}: the box is a Mesh at {expected} (roots {roots})')
    result = validate_shot_root_prims(stage.GetPseudoRoot())
    messages = [issue.message for issue in result.issues]
    check(result.passed, f'{label}: shot root-prim validation passes {messages}')


def main():
    sh010 = Uri.parse('entity:/shots/SQ010/SH010')
    sh020 = Uri.parse('entity:/shots/SQ010/SH020')
    for uri in (sh010, sh020):
        api.config.add_entity(uri, {'frame_start': 1001, 'frame_end': 1010})
    stage_node = hou.node('/stage')

    for department, category in CATEGORIES.items():
        template = load_template(department)

        for child in stage_node.children():
            child.destroy()
        entity_node.scene_context = lambda d=department: Context(sh010, d)
        template.create(stage_node, sh010, department)
        assert_column(stage_node.node(department), category, f'{department} single')

        for child in stage_node.children():
            child.destroy()
        group_uri = groups.add_group('shots', f'sq010{department}', [sh010, sh020], [department])
        entity_node.scene_context = lambda g=group_uri, d=department: Context(g, d)
        template.create(stage_node, group_uri, department)
        for member in ('SQ010_SH010', 'SQ010_SH020'):
            node = stage_node.node(f'{department}_{member}')
            check(node is not None, f'{department} multi: {department}_{member} exists')
            if node is not None:
                assert_column(node, category, f'{department} multi {member}')

    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    print('FAILED: ' + '; '.join(FAILURES) if FAILURES else 'ALL PASS')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
