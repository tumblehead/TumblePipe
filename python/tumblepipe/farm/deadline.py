"""Farm-side Deadline helpers: the job that runs a farm worker.

`tumblepipe.apps.deadline` is a generic Deadline submission wrapper. The farm
layer is what knows a worker runs from an HPM package, so HPM manifest
generation lives here, not in the generic wrapper: :func:`farm_job` builds a
Job and attaches the hpm.toml the HPM plugin installs on a worker cache miss.
The generic `submit()` only writes `job.manifest` into the shared job dir and
hands the plugin its path.
"""
import os
import re
from pathlib import Path

import tomli_w

from tumblepipe.api import default_client, path_str
from tumblepipe.apps.deadline import Job, hpm_package_spec
from tumblepipe.apps.houdini import resolve_houdini_version
from tumblepipe.farm.env import get_base_env
from tumblepipe.naming import random_name
from tumblepipe.util.io import store_json

# Default for hpm_task_manifest's ``project``: read the project's manifest.
_LOOK_UP = object()

# The worker scripts a farm job runs (``tumblepipe.farm.workers``).
WORKERS = Path(__file__).parent / 'workers'

# Captures the package-root prefix (…/.hpm/packages/<name>@<version>) of a
# script path.
_PACKAGE_ROOT_RE = re.compile(r'^(.*?/\.hpm/packages/[^/]+@[^/]+)/')


class FarmManifestError(RuntimeError):
    """The farm job's hpm.toml cannot be built from what this machine has."""


def _package_full_name(script_path) -> str:
    """The registry-qualified `creator/slug` name of the running package.

    The HPM store keys packages by bare slug (`tumblepipe@1.12.2`), but the
    registry resolves by `creator/slug` (`tumblehead/tumblepipe`). A bare-name
    dependency 404s on a fresh worker that has no cached registry index, so the
    manifest must use the full name — read from the package's own hpm.toml
    `[package].path`. An installed package always has one; not finding it
    raises rather than shipping a name the worker cannot resolve.
    """
    norm = str(script_path).replace('\\', '/')
    match = _PACKAGE_ROOT_RE.match(norm)
    if match is None:
        raise FarmManifestError(
            f'{script_path} is not under an HPM package store '
            '(…/.hpm/packages/<name>@<version>/)'
        )
    manifest_path = Path(match.group(1)) / 'hpm.toml'
    text = manifest_path.read_text(encoding='utf-8')
    path_match = re.search(r'(?m)^\s*path\s*=\s*["\']([^"\']+)["\']', text)
    if path_match is None:
        raise FarmManifestError(f'{manifest_path} has no [package].path')
    return path_match.group(1)


def _parse_requirements(requirements_path) -> list:
    """Pip requirement specs from a task's requirements.txt, minus comments.

    These per-task requirements were consumed by the old hand-built uv venv;
    under the HPM `package-env` flow they're merged into the package env as the
    script's "extra requirements" (see `hpm_task_manifest`). Strips blank lines,
    full-line `#` comments, and trailing ` # …` inline comments. An all-comments
    file yields `[]`, so no `requirements` is emitted.
    """
    if requirements_path is None:
        return []
    text = Path(requirements_path).read_text()
    requirements = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        # Inline comment: pip requires whitespace before the '#'.
        hash_index = line.find(' #')
        if hash_index != -1:
            line = line[:hash_index].strip()
        if line:
            requirements.append(line)
    return requirements


def _project_runtime() -> dict:
    """`[runtime]` for the job manifest, satisfying TumblePipe's required env vars.

    TumblePipe's hpm.toml declares `TH_PROJECT_PATH` as a required placeholder
    (`required = true`, no value), so every consuming project must supply it in
    its own `[runtime]`. This synthetic manifest IS that project as far as the
    worker's hpm is concerned, so without this the worker's `hpm install` fails
    with `Required env var 'TH_PROJECT_PATH' for package 'tumblepipe' has no
    value` before the task ever runs.

    The task itself does not read the var from here — `tasks.env` gets it from
    the Deadline job env — but hpm resolves the placeholder against the manifest
    alone, not the process environment. Written in the submitter's Windows form,
    matching how the job env carries the other pipeline paths.
    """
    return {
        'TH_PROJECT_PATH': {
            'method': 'set',
            'value': path_str(default_client().PROJECT_PATH),
        },
    }


# ── the project's own manifest ────────────────────────────

# `[package].path` of the synthetic manifest below. A worker-side submission
# (a collapse task submitting its playblast) recognises its own job by it.
JOB_PACKAGE_PATH = 'local/deadline-hpm-job'


def project_manifest_candidates(env) -> list:
    """Where the project's hpm.toml may be, most specific first.

    - ``$HPM_PACKAGE_ROOT/hpm.toml`` when it is a farm job's own manifest: a
      task submitting further jobs from a worker (collapse → playblast) runs
      inside the job `hpm run` set up, whose manifest already carries the
      project's dependencies. Any other package root is not the project.
    - ``$TT_PROJECT_DIR/hpm.toml``: the project TumbleTrove Desktop launched.
    - ``<project>/.hpm/packages`` on ``HOUDINI_PACKAGE_DIR`` → ``<project>/hpm.toml``:
      how a Desktop-launched Houdini finds its packages.
    """
    candidates = []
    root = env.get('HPM_PACKAGE_ROOT')
    if root:
        candidates.append((Path(root) / 'hpm.toml', True))
    project_dir = env.get('TT_PROJECT_DIR')
    if project_dir:
        candidates.append((Path(project_dir) / 'hpm.toml', False))
    for entry in (env.get('HOUDINI_PACKAGE_DIR') or '').split(os.pathsep):
        parts = Path(entry).parts if entry else ()
        if len(parts) >= 3 and parts[-2:] == ('.hpm', 'packages'):
            candidates.append((Path(entry).parents[1] / 'hpm.toml', False))
    return candidates


def read_project_manifest(env=None) -> dict | None:
    """The project's parsed hpm.toml, or None when none can be found.

    A job manifest only counts under ``$HPM_PACKAGE_ROOT``; see
    :func:`project_manifest_candidates`. A candidate that exists but does not
    parse raises.
    """
    import tomllib
    env = os.environ if env is None else env
    for path, must_be_job in project_manifest_candidates(env):
        try:
            text = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            continue  # Candidates are guesses; most do not exist.
        data = tomllib.loads(text)
        is_job = (data.get('package') or {}).get('path') == JOB_PACKAGE_PATH
        if must_be_job and not is_job:
            continue
        return data
    return None


def job_dependencies(project: dict, full_name: str, version: str) -> dict:
    """The job's `[dependencies]`: the project's, with the task's package pinned.

    A farm task needs the environment the artist's Houdini had — a rigged
    shot's animation does not cook without TumbleRig's APEX components, and a
    job that carried TumblePipe alone failed every animation publish with an
    uncookable rig. The task's own package is pinned to the version running
    it, whatever the project lists, because the task script belongs to it.
    """
    slug = full_name.split('/')[-1]
    dependencies = {
        name: pinned
        for name, pinned in (project.get('dependencies') or {}).items()
        # The project's entry for the same package, full name or bare slug.
        if name.split('/')[-1] != slug
    }
    dependencies[full_name] = version
    return dependencies


def job_runtime(project: dict, project_runtime: dict) -> dict:
    """The job's `[runtime]`: the project's, with ``project_runtime`` on top."""
    runtime = dict(project.get('runtime') or {})
    runtime.update(project_runtime)
    return runtime


def job_registries(project: dict, submitter: list) -> list:
    """The submitter's registries, then any other the project declares (by name).

    Render nodes are never `hpm registry add`-ed, so the manifest must carry
    the registries itself; none at all raises.
    """
    registries = list(submitter)
    names = {r['name'] for r in registries}
    for registry in project.get('registries') or []:
        if registry['name'] not in names:
            registries.append(registry)
            names.add(registry['name'])
    if not registries:
        raise FarmManifestError(
            'No hpm registry to put in the farm job: neither ~/.hpm/config.toml '
            "nor the project's hpm.toml declares one. Add one with "
            '`hpm registry add`.'
        )
    return registries


def _script_module(relative_script: str) -> str:
    """Dotted module path for `python -m` from a package-relative script path.

    The HPM plugin runs the task as `hpm run task`, whose `[scripts.task]` cmd is
    `python -m <module>` executed inside the package-env (the package's python/
    dir is on PYTHONPATH). Strip the leading `python/` source-root, drop the
    `.py`, and turn slashes into dots:
    `python/tumblepipe/farm/workers/render.py` -> `tumblepipe.farm.workers.render`.
    """
    norm = relative_script.replace('\\', '/').strip('/')
    if norm.startswith('python/'):
        norm = norm[len('python/'):]
    if norm.endswith('.py'):
        norm = norm[:-len('.py')]
    return norm.replace('/', '.')


def _submitter_registries() -> list:
    """The [[registries]] from the submitter's own ~/.hpm/config.toml.

    Embedding them in the job manifest makes the worker resolve against the same
    registries the submitter used, with no `hpm registry add` on the node.
    """
    import tomllib
    config_path = Path(os.path.expanduser('~')) / '.hpm' / 'config.toml'
    try:
        text = config_path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return []  # No per-user config: the project's registries must do.
    return tomllib.loads(text).get('registries') or []


def _job_houdini_compat() -> str:
    """The job runs against the Houdini major it was created in (the
    USD/resolver ABI is per-major), so its manifest admits exactly that major."""
    major = int(resolve_houdini_version().split('.')[0])
    return f'>={major}, <{major + 1}'


def hpm_task_manifest(script_path, requirements_path=None, project=_LOOK_UP) -> str:
    """hpm.toml that sets up the environment a farm task runs in.

    A synthetic envelope package whose dependencies are the project's, with the
    task's package at its exact version — hpm has no "install <pkg>@<ver>" verb,
    so you declare them as dependencies and `hpm install` resolves them into the
    shared store.

    Gotchas baked in:
    - `[package].path` is required or hpm refuses to load the manifest.
    - `registries` is declared in the manifest itself — render nodes are never
      `hpm registry add`-ed, and a manifest registry is additive to (and
      sufficient without) per-user config.
    - the dependency key is the full `creator/slug`; the bare slug 404s on a
      fresh worker with no cached registry index.
    - the version is BARE (an exact registry get_version fetch); a "=" prefix
      is sent verbatim into the registry query and 404s.
    - `[scripts.task]` runs the task module inside the package-env (hpm >=0.22.2):
      `package-env = true` resolves the full env (the dependency package
      importable + its [python_dependencies]) and runs `python -m <module>`. The
      HPM plugin invokes it as `hpm run task -- <context> <first> <last>`; this
      is what replaces the old hand-built uv venv + PYTHONPATH reconstruction.
    - a task's per-task requirements.txt (e.g. notify's `discord.py`) becomes the
      script's `requirements` — package-env merges them on top of the package's
      [python_dependencies] as "extra requirements". Without this the package-env
      resolves only the package deps and the task ImportErrors on its own libs
      (the old uv-venv flow consumed requirements.txt; package-env does not unless
      it's threaded here).
    - `[runtime]` supplies TumblePipe's required `TH_PROJECT_PATH` placeholder;
      without it the worker's `hpm install` errors before the task runs. See
      `_project_runtime`.
    - the dependencies, runtime and registries are the PROJECT's (its hpm.toml,
      found by :func:`read_project_manifest`), so the worker's hpm sets up the
      packages the artist's Houdini had — TumbleRig included — and writes one
      Houdini package file per dependency into ``<job>/.hpm/packages``, which
      ``farm.env.get_hython_env`` hands to hython. ``project`` overrides the
      lookup (tests). Without a project manifest it raises: a job carrying the
      task's package alone fails on the first rig it cooks.
    """
    if project is _LOOK_UP:
        project = read_project_manifest()
    if project is None:
        raise FarmManifestError(
            "No project hpm.toml found (HPM_PACKAGE_ROOT / TT_PROJECT_DIR / "
            "HOUDINI_PACKAGE_DIR), so the farm job cannot carry the project's "
            'packages. Submit from a Houdini TumbleTrove Desktop launched.'
        )
    package_spec, relative_script = hpm_package_spec(script_path)
    _, _, version = package_spec.partition('@')
    full_name = _package_full_name(script_path)
    module = _script_module(relative_script)
    task_script = {
        'cmd': f'python -m {module}',
        'package-env': True,
    }
    requirements = _parse_requirements(requirements_path)
    if requirements:
        task_script['requirements'] = requirements
    return tomli_w.dumps({
        'package': {
            'path': JOB_PACKAGE_PATH,
            'name': 'deadline-hpm-job',
            'version': '0.0.0',
        },
        'compat': {'houdini': _job_houdini_compat()},
        'registries': job_registries(project, _submitter_registries()),
        'dependencies': job_dependencies(project, full_name, version),
        'runtime': job_runtime(project, _project_runtime()),
        'scripts': {
            'task': task_script,
        },
    })


def worker_requirements(worker: str) -> Path | None:
    """The worker's own pip requirements (``workers/<worker>_requirements.txt``),
    None when it needs nothing beyond the package's dependencies."""
    path = WORKERS / f'{worker}_requirements.txt'
    return path if path.exists() else None


def farm_job(
    worker: str,
    context: dict,
    staging_path: Path,
    *,
    name: str,
    pool: str,
    group: str,
    priority: int,
    max_minutes: int,
    paths: dict[Path, Path] | None = None
    ) -> Job:
    """A Deadline job that runs farm worker ``worker`` on ``context``.

    ``context`` is written to ``<staging>/<worker>_<random>/context.json``,
    bundled into the job's data dir, and its bundle-relative path is the
    worker's config argument. ``paths`` (source -> bundle-relative path) are
    bundled with it. The job carries the farm's base environment and the HPM
    manifest that sets up the worker's package env. The caller sets the
    frames: :func:`run_once` or :func:`run_batched`.
    """
    script_path = WORKERS / f'{worker}.py'
    requirements_path = worker_requirements(worker)
    context_path = staging_path / f'{worker}_{random_name(8)}' / 'context.json'
    store_json(context_path, context)
    relative_context_path = context_path.relative_to(staging_path)
    job = Job(script_path, path_str(relative_context_path))
    job.name = name
    job.pool = pool
    job.group = group
    job.priority = priority
    job.max_frame_time = max_minutes
    if paths is not None:
        job.paths.update(paths)
    job.paths[context_path] = relative_context_path
    job.env.update(get_base_env())
    job.manifest = hpm_task_manifest(script_path, requirements_path)
    return job


def run_once(job: Job, first_frame: int, last_frame: int) -> None:
    """Schedule ``job`` as one Deadline task spanning ``first..last``.

    For a worker that processes its whole range itself (an encode, a stage,
    a publish): a second chunk would be a second worker redoing the whole
    job. The span, not the stepped frame count, is the chunk.
    """
    job.start_frame = first_frame
    job.end_frame = last_frame
    job.step_size = 1
    job.chunk_size = last_frame - first_frame + 1


def run_batched(
    job: Job,
    first_frame: int,
    last_frame: int,
    step_size: int,
    batch_size: int,
    frames: list[int]
    ) -> None:
    """Schedule ``job`` as Deadline tasks of ``batch_size`` frames each:
    ``frames`` when given, else ``first..last`` by ``step_size``."""
    if len(frames) > 0:
        job.frames.extend(frames)
    else:
        job.start_frame = first_frame
        job.end_frame = last_frame
    job.step_size = step_size
    job.chunk_size = batch_size
