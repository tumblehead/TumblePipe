"""Hython worker for the Asset Browser's Duplicate: copy workfiles to a new asset.

    hython duplicate_workfiles.py <job.json>

``job.json`` holds ``source`` and ``target`` entity URIs, ``nc_type`` (the
GUI session's workfile extension type) and ``workfiles``: a list of
``[department, hip path]`` from ``pipe.duplicate.plan_duplicate``. For each,
the hip is loaded, saved as the target's next version of that department,
given the target's context sidecars, and its nodes are pointed at it: a
concrete entity/department naming the source becomes 'from_context'
(``scene_retarget``), and the source's prim path typed into string parms
becomes the target's. Prints one JSON line per workfile and exits non-zero
if any failed.

Runs in its own process so the artist's open scene is never touched;
:func:`run_duplicate` is the launching side, called from the GUI session.
"""

import json
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

import hou

from tumblepipe.pipe.duplicate import prim_path_for, replace_prim_prefix
from tumblepipe.util.uri import Uri


@dataclass(frozen=True)
class DuplicateRun:
    """How a :func:`run_duplicate` job went: hython's exit code, one result
    dict per workfile (``error`` set on the ones that failed), and its log."""
    returncode: int
    results: list[dict]
    log_path: Path

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.results if 'error' in r]

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.failed


def run_duplicate(
    source_uri: Uri, target_uri: Uri,
    workfiles: list[tuple[str, Path]], nc_type: str | None,
) -> DuplicateRun:
    """Copy ``workfiles`` (``pipe.duplicate.plan_duplicate``) to ``target_uri``
    in a hython worker running this module; blocking, so call it off the
    main thread. The job file and log go to a fresh temp folder.
    """
    from tumblepipe.pipe.houdini.launch import run_hython

    job_dir = Path(tempfile.mkdtemp(prefix='th_duplicate_'))
    job_path = job_dir / 'job.json'
    job_path.write_text(json.dumps({
        'source': str(source_uri),
        'target': str(target_uri),
        'nc_type': nc_type,
        'workfiles': [[dept, str(path)] for dept, path in workfiles],
    }), encoding='utf-8')
    log_path = job_dir / 'duplicate.log'
    process = run_hython(Path(__file__), [str(job_path)], log_path)
    results = []
    for line in (process.stdout or '').splitlines():
        try:
            results.append(json.loads(line))
        except ValueError:
            # hython's stdout carries Houdini's own chatter beside the one
            # JSON line per workfile the job prints.
            continue
    return DuplicateRun(process.returncode, results, log_path)


def rewrite_prim_paths(old: str, new: str) -> int:
    """Rewrite ``old`` to ``new`` in every editable string parm; return the count.

    Parms driven by an expression or a channel reference, and nodes inside a
    locked asset, are left alone.
    """
    count = 0
    for node in hou.node('/').allSubChildren():
        if node.isInsideLockedHDA():
            continue
        for parm in node.parms():
            if parm.parmTemplate().type() != hou.parmTemplateType.String:
                continue
            if parm.keyframes() or parm.isLocked():
                continue
            raw = parm.unexpandedString()
            rewritten = replace_prim_prefix(raw, old, new)
            if rewritten != raw:
                parm.set(rewritten)
                count += 1
    return count


def duplicate_workfile(
    source_hip: Path, source_uri: Uri, target_uri: Uri,
    department_name: str, nc_type: str | None,
) -> dict:
    from tumblepipe.pipe.context import (
        save_context,
        save_entity_context,
        workfile_context,
    )
    from tumblepipe.pipe.houdini.workfile_save import save_hip_file
    from tumblepipe.pipe.houdini import playbar
    from tumblepipe.pipe.houdini.scene_retarget import retarget_scene
    from tumblepipe.pipe.paths import (
        get_workfile_context,
        release_reserved_version,
        reserve_next_hip_file_path,
    )

    hou.hipFile.load(
        str(source_hip), suppress_save_prompt=True, ignore_load_warnings=True,
    )
    prev_ctx = get_workfile_context(source_hip)
    next_path = reserve_next_hip_file_path(
        target_uri, department_name, nc_type=nc_type,
    )
    try:
        next_path.parent.mkdir(parents=True, exist_ok=True)
        next_path = save_hip_file(
            next_path, workfile_context(target_uri, department_name, next_path),
        )
    except Exception:
        release_reserved_version(next_path)
        raise
    new_ctx = workfile_context(target_uri, department_name, next_path)
    save_context(
        next_path.parent, prev_ctx, new_ctx,
        file_extension=next_path.suffix.lstrip('.'),
        note=f'Duplicated from {source_uri}',
    )
    save_entity_context(next_path.parent, new_ctx)
    with playbar.update_mode(hou.updateMode.Manual):
        retargeted = retarget_scene(prev_ctx, new_ctx) if prev_ctx else []
        rewritten = rewrite_prim_paths(
            prim_path_for(source_uri), prim_path_for(target_uri),
        )
    hou.hipFile.save(str(next_path))
    return {
        'department': department_name,
        'workfile': str(next_path),
        'retargeted': len(retargeted),
        'rewritten': rewritten,
    }


def main(job_path: str) -> int:
    job = json.loads(Path(job_path).read_text(encoding='utf-8'))
    source_uri = Uri.parse(job['source'])
    target_uri = Uri.parse(job['target'])
    failed = 0
    for department_name, hip_path in job['workfiles']:
        try:
            result = duplicate_workfile(
                Path(hip_path), source_uri, target_uri,
                department_name, job.get('nc_type'),
            )
        except Exception as exc:
            failed += 1
            traceback.print_exc()
            result = {'department': department_name, 'error': str(exc)}
        print(json.dumps(result), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
