"""Acceptance: Duplicate copies an asset's workfile to a new asset and repoints it.

    hython scripts/verify_duplicate_asset.py

Run under a project hython with the local source (TumbleTrove Desktop
run_hython with use_dev_overrides=true). Creates PROP/dupsrc with one model
workfile, duplicates it to PROP/dupsrc_copy through the same worker the
Asset Browser runs (``pipe.houdini.duplicate_workfiles``), checks the copy,
then removes both assets and their folders.

Background: an external user made variations of an asset by copying its
folder in Explorer; the copies' context.json and nodes still named the
original, so publishing from a copy overwrote the original.

Checks on the copied workfile:
  1. it is the copy's v0001 in the copy's model folder;
  2. its context names the copy;
  3. an export node pinned to the source entity now follows the context;
  4. a hand-typed /PROP/dupsrc path in a string parm now reads /PROP/dupsrc_copy,
     and a /PROP/dupsrc_other path is left alone.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import hou
from tumblepipe.api import api
from tumblepipe.pipe import duplicate
from tumblepipe.pipe.houdini import duplicate_workfiles
from tumblepipe.pipe.houdini.lops import export_layer
from tumblepipe.pipe.paths import get_workfile_context, reserve_next_hip_file_path
from tumblepipe.util.uri import Uri

SOURCE = Uri.parse_unsafe('entity:/assets/PROP/dupsrc')
TARGET = Uri.parse_unsafe('entity:/assets/PROP/dupsrc_copy')
FAILURES = []


def check(label, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f'  ({detail})' if detail else ''))
    if not ok:
        FAILURES.append(label)


def make_source_workfile() -> Path:
    from tumblepipe.pipe.context import (
        save_context,
        save_entity_context,
        save_hip_file,
        workfile_context,
    )
    hou.hipFile.clear(suppress_save_prompt=True)
    stage = hou.node('/stage')
    ex = export_layer.create(stage, 'export_model')
    ex.parm('entity').set(str(SOURCE))
    note = stage.createNode('null', 'paths')
    group = note.parmTemplateGroup()
    group.append(hou.StringParmTemplate('mtl', 'mtl', 1))
    group.append(hou.StringParmTemplate('other', 'other', 1))
    note.setParmTemplateGroup(group)
    note.parm('mtl').set('/PROP/dupsrc/mtl/')
    note.parm('other').set('/PROP/dupsrc_other/geo')
    path = reserve_next_hip_file_path(SOURCE, 'model')
    path.parent.mkdir(parents=True, exist_ok=True)
    path = save_hip_file(path)
    ctx = workfile_context(SOURCE, 'model', path)
    save_context(path.parent, None, ctx, file_extension=path.suffix.lstrip('.'))
    save_entity_context(path.parent, ctx)
    return path


def cleanup():
    from tumblepipe.pipe.entity_files import entity_folders
    for uri in (TARGET, SOURCE):
        if api.config.get_properties(uri) is not None:
            api.config.remove_entity(uri)
        for _root, folder in entity_folders(uri):
            shutil.rmtree(folder, ignore_errors=True)


def main() -> int:
    cleanup()
    try:
        api.config.add_entity(SOURCE, {'name': 'dupsrc'})
        make_source_workfile()
        api.config.add_entity(TARGET, {'name': 'dupsrc_copy'})
        plan = duplicate.plan_duplicate(SOURCE, ['model', 'lookdev'])
        check('plan holds the model workfile only', [d for d, _ in plan] == ['model'], str(plan))

        job = Path(tempfile.mkdtemp()) / 'job.json'
        job.write_text(json.dumps({
            'source': str(SOURCE), 'target': str(TARGET), 'nc_type': None,
            'workfiles': [[d, str(p)] for d, p in plan],
        }))
        # Exactly how the Asset Browser runs it: a separate hython.
        import os
        import subprocess
        hython = Path(os.environ['HFS']) / 'bin' / ('hython.exe' if os.name == 'nt' else 'hython')
        run = subprocess.run(
            [str(hython), duplicate_workfiles.__file__, str(job)],
            capture_output=True, text=True, env=os.environ.copy(), check=False,
        )
        print(run.stdout.strip())
        check('worker succeeded', run.returncode == 0, run.stderr.strip()[-500:])

        from tumblepipe.pipe.paths import latest_hip_file_path
        copied = latest_hip_file_path(TARGET, 'model')
        check('copy is v0001 of the copy', copied is not None and copied.stem.endswith('dupsrc_copy_model_v0001'), str(copied))
        if copied is None:
            return 1
        ctx = get_workfile_context(copied)
        check('context names the copy', ctx is not None and ctx.entity_uri == TARGET, str(ctx))
        hou.hipFile.load(str(copied), suppress_save_prompt=True, ignore_load_warnings=True)
        entity = hou.node('/stage/export_model').parm('entity').eval()
        check('pinned export node follows the context', entity == 'from_context', entity)
        mtl = hou.node('/stage/paths').parm('mtl').eval()
        other = hou.node('/stage/paths').parm('other').eval()
        check('typed prim path repointed', mtl == '/PROP/dupsrc_copy/mtl/', mtl)
        check('longer name left alone', other == '/PROP/dupsrc_other/geo', other)
    finally:
        hou.hipFile.clear(suppress_save_prompt=True)
        cleanup()

    print('RESULT:', 'FAILED ' + ', '.join(FAILURES) if FAILURES else 'ALL CHECKS PASSED')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
