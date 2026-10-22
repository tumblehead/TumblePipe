"""Re-make the caches an archive left out, from the workfiles that wrote them.

An archive leaves caches out because a workfile can write them again; the
manifest's ``derived`` list says which (``pack._derived_entries``): each
cache version with the sidecar ``th::cache`` wrote (``pipe/cache_context``),
naming the workfile version and node that produced it.

``plan_rebuild`` turns that list into jobs against a restored project:

- an entry is **ready** when its workfile came back with the archive;
- **missing workfile** when it did not (a ``latest`` archive keeps only the
  newest workfile per department, and an older version wrote the cache);
- **no record** when the sidecar did not name a workfile and node.

Ready entries are grouped per workfile (one ``hython`` opens it once) and
ordered by when they were first cached, within and across workfiles: a
cache that feeds another was made before it, so replaying the original
order makes inputs before the caches that read them.

Each cache is written back into **its own version** (``th::cache``'s
``cache(version_name=…)``): downstream workfiles load caches by version, so
a rebuild that wrote a new ``vNNNN`` would leave them pointing at nothing.

``run`` starts ``hython`` per job with ``rebuild_worker`` and needs a
Houdini licence and the TumblePipe package loaded (``HOUDINI_PACKAGE_DIR``,
as TumbleTrove Desktop's Scripts panel provides) so the ``th::cache`` nodes
exist.
"""

from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

#: Where ``pack.restore`` keeps the manifest of the archive a project came from.
RESTORED_DIR = '_archive'


class State:
    READY = 'ready'
    MISSING_WORKFILE = 'missing workfile'
    NO_RECORD = 'no record of the workfile'


@dataclass(frozen=True)
class CacheEntry:
    node: str | None
    cache_name: str
    version: str
    timestamp: str
    unit: str
    workfile: str | None      # project-relative workfile path
    state: str


@dataclass
class Job:
    workfile: str             # project-relative
    caches: list[CacheEntry] = field(default_factory=list)

    @property
    def first(self) -> str:
        return min(entry.timestamp for entry in self.caches)


def restored_manifest(project_path: Path) -> Path | None:
    """The newest archive manifest ``pack.restore`` left in the project."""
    folder = Path(project_path) / RESTORED_DIR
    manifests = sorted(folder.glob('*.manifest.json')) if folder.is_dir() else []
    return manifests[-1] if manifests else None


def cache_entries(manifest: dict, project_path: Path) -> list[CacheEntry]:
    entries = []
    for derived in manifest.get('derived', []):
        if derived.get('kind') != 'cache':
            continue
        context = derived.get('context') or {}
        producer = context.get('producer') or {}
        workfile = (producer.get('workfile') or {}).get('path')
        node = producer.get('node')
        version = context.get('version') or derived['unit'].rsplit('/', 1)[-1]
        if not workfile or not node or Path(workfile).is_absolute():
            state = State.NO_RECORD
        elif (Path(project_path) / workfile).is_file():
            state = State.READY
        else:
            state = State.MISSING_WORKFILE
        entries.append(CacheEntry(
            node=node,
            cache_name=context.get('cache') or derived['unit'].rsplit('/', 2)[-2],
            version=version,
            timestamp=context.get('timestamp') or '',
            unit=f"{derived['root']}:{derived['unit']}",
            workfile=workfile,
            state=state,
        ))
    return entries


def plan_rebuild(entries: list[CacheEntry]) -> list[Job]:
    """Ready entries as per-workfile jobs, in original cache order."""
    jobs: dict[str, Job] = {}
    for entry in entries:
        if entry.state != State.READY:
            continue
        jobs.setdefault(entry.workfile, Job(entry.workfile)).caches.append(entry)
    for job in jobs.values():
        job.caches.sort(key=lambda entry: (entry.timestamp, entry.node, entry.version))
    return sorted(jobs.values(), key=lambda job: (job.first, job.workfile))


def find_hython(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    hfs = os.environ.get('HFS')
    if hfs:
        for name in ('hython.exe', 'hython'):
            candidate = Path(hfs) / 'bin' / name
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(
        'hython not found: set HFS to a Houdini install, or pass --hython'
    )


def run(
    jobs: list[Job],
    project_path: Path,
    hython: Path,
    *,
    log=print,
) -> list[dict]:
    """Run each job in ``hython``; returns one result per cache."""
    project_path = Path(project_path)
    package_python = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env.setdefault('TH_PROJECT_PATH', str(project_path))
    env.setdefault('TH_CONFIG_PATH', str(project_path / '_config'))
    env.setdefault('TH_EXPORT_PATH', str(project_path / 'export'))
    env.setdefault('TH_PIPELINE_PATH', str(package_python.parent))
    env['PYTHONPATH'] = os.pathsep.join(
        [str(package_python)] + [p for p in env.get('PYTHONPATH', '').split(os.pathsep) if p]
    )
    worker = Path(__file__).with_name('rebuild_worker.py')
    results = []
    for index, job in enumerate(jobs, start=1):
        log(f'[{index}/{len(jobs)}] {job.workfile}: {len(job.caches)} cache(s)')
        with tempfile.TemporaryDirectory() as temp:
            request = Path(temp) / 'request.json'
            response = Path(temp) / 'response.json'
            request.write_text(json.dumps(dict(
                hip=str(project_path / job.workfile),
                caches=[
                    dict(node=c.node, cache=c.cache_name, version=c.version)
                    for c in job.caches
                ],
            )), encoding='utf-8')
            code = subprocess.call(
                [str(hython), str(worker), str(request), str(response)], env=env,
            )
            if response.is_file():
                job_results = json.loads(response.read_text(encoding='utf-8'))
            else:
                job_results = [
                    dict(node=c.node, version=c.version, ok=False,
                         error=f'hython exited {code} without a report')
                    for c in job.caches
                ]
        for result in job_results:
            log(f"    {'ok  ' if result['ok'] else 'FAIL'} {result['node']} {result['version']}"
                + ('' if result['ok'] else f"  {result.get('error', '')}"))
        results.extend(job_results)
    return results


def main_rebuild(args) -> int:
    """The ``rebuild`` command of ``python -m tumblepipe.archive``."""
    project = Path(args.project or os.environ.get('TH_PROJECT_PATH') or '')
    if not project.is_dir():
        print('no project: pass the restored project folder or set TH_PROJECT_PATH', file=sys.stderr)
        return 2
    manifest_file = Path(args.manifest) if args.manifest else restored_manifest(project)
    if manifest_file is None or not manifest_file.is_file():
        print(f'no archive manifest in {project / RESTORED_DIR}; pass --manifest', file=sys.stderr)
        return 2
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    entries = cache_entries(manifest, project)
    jobs = plan_rebuild(entries)
    by_state: dict[str, int] = {}
    for entry in entries:
        by_state[entry.state] = by_state.get(entry.state, 0) + 1
    print(f'{len(entries)} cache version(s) were left out of {manifest_file.name}:')
    for state, count in sorted(by_state.items()):
        print(f'  {count:>5}  {state}')
    for entry in entries:
        if entry.state == State.MISSING_WORKFILE:
            print(f'  cannot re-make {entry.unit}: {entry.workfile} is not in the project')
    print(f'{len(jobs)} workfile(s) to open.')
    if args.dry_run or not jobs:
        for job in jobs:
            print(f'  {job.workfile}')
            for cache in job.caches:
                print(f'      {cache.node}  {cache.cache_name} {cache.version}')
        return 0
    results = run(jobs, project, find_hython(args.hython))
    failed = [r for r in results if not r['ok']]
    print(f'{len(results) - len(failed)} re-made, {len(failed)} failed')
    return 1 if failed else 0
