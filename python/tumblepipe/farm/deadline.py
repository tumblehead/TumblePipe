"""Farm-side Deadline helpers.

`tumblepipe.apps.deadline` is a generic Deadline submission wrapper. The farm
layer is what knows a task runs from an HPM package, so HPM manifest generation
lives here, not in the generic wrapper: the `Task` factory builds a Job and
attaches the hpm.toml the HPM plugin installs on a worker cache miss. The
generic `submit()` only writes `job.manifest` into the shared job dir and hands
the plugin its path.
"""
import os
import re
from pathlib import Path

import tomli_w

from tumblepipe.api import to_windows_path
from tumblepipe.apps.deadline import Job, hpm_package_spec

# Captures the package-root prefix (…/.hpm/packages/<name>@<version>) of a
# script path, whether Windows (C:/…) or WSL (/mnt/c/…) form.
_PACKAGE_ROOT_RE = re.compile(r'^(.*?/\.hpm/packages/[^/]+@[^/]+)/')

# Registry to declare if the submitter's config can't be read (the studio's one
# registry). Render nodes are never `hpm registry add`-ed, so the manifest must
# carry the registries itself — a manifest [[registries]] block is additive to
# (and sufficient without) any per-user config.
_FALLBACK_REGISTRY = {
    'name': 'tumbletrove',
    'url': 'https://api.tumbletrove.com/v1/registry',
    'type': 'api',
}


def _package_full_name(script_path, bare_name) -> str:
    """The registry-qualified `creator/slug` name of the running package.

    The HPM store keys packages by bare slug (`tumblepipe@1.12.2`), but the
    registry resolves by `creator/slug` (`tumblehead/tumblepipe`). A bare-name
    dependency 404s on a fresh worker that has no cached registry index, so the
    manifest must use the full name — read it from the package's own hpm.toml
    `[package].path`. Falls back to the bare slug if it can't be read.
    """
    norm = str(script_path).replace('\\', '/')
    match = _PACKAGE_ROOT_RE.match(norm)
    if match is None:
        return bare_name
    root = Path(to_windows_path(Path(match.group(1))))
    try:
        text = (root / 'hpm.toml').read_text()
    except OSError:
        return bare_name
    path_match = re.search(r'(?m)^\s*path\s*=\s*["\']([^"\']+)["\']', text)
    return path_match.group(1) if path_match is not None else bare_name


def _script_module(relative_script: str) -> str:
    """Dotted module path for `python -m` from a package-relative script path.

    The HPM plugin runs the task as `hpm run task`, whose `[scripts.task]` cmd is
    `python -m <module>` executed inside the package-env (the package's python/
    dir is on PYTHONPATH). Strip the leading `python/` source-root, drop the
    `.py`, and turn slashes into dots:
    `python/tumblepipe/farm/tasks/render/render.py` -> `tumblepipe.farm.tasks.render.render`.
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
    config_path = Path(os.path.expanduser('~')) / '.hpm' / 'config.toml'
    try:
        import tomllib
        registries = tomllib.loads(config_path.read_text()).get('registries', [])
    except Exception:
        registries = []
    return registries if registries else [_FALLBACK_REGISTRY]


def hpm_task_manifest(script_path) -> str:
    """hpm.toml that resolves the package a farm task runs from.

    A synthetic envelope package whose single dependency is the task's package
    at its exact version — hpm has no "install <pkg>@<ver>" verb, so you declare
    it as a dependency and `hpm install` resolves it into the shared store.

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
    """
    package_spec, relative_script = hpm_package_spec(script_path)
    bare_name, _, version = package_spec.partition('@')
    full_name = _package_full_name(script_path, bare_name)
    module = _script_module(relative_script)
    return tomli_w.dumps({
        'package': {
            'path': 'local/deadline-hpm-job',
            'name': 'deadline-hpm-job',
            'version': '0.0.0',
        },
        'compat': {'houdini': '>=21, <99'},
        'registries': _submitter_registries(),
        'dependencies': {full_name: version},
        'scripts': {
            'task': {
                'cmd': f'python -m {module}',
                'package-env': True,
            },
        },
    })


def Task(script_path, requirements_path, *args, **kwargs):
    """Create a farm Job and attach its HPM manifest.

    Drop-in replacement for `apps.deadline.Job` — the farm tasks import this as
    `Task`. For HPM jobs it generates and provides the hpm.toml so the generic
    submit layer never has to know about packages.
    """
    job = Job(script_path, requirements_path, *args, **kwargs)
    if job._plugin == 'HPM':
        job.manifest = hpm_task_manifest(script_path)
    return job
