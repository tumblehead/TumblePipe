"""Farm-side Deadline helpers.

`tumblepipe.apps.deadline` is a generic Deadline submission wrapper. The farm
layer is what knows a task runs from an HPM package, so HPM manifest generation
lives here, not in the generic wrapper: the `Task` factory builds a Job and
attaches the hpm.toml the HPM plugin installs on a worker cache miss. The
generic `submit()` only writes `job.manifest` into the shared job dir and hands
the plugin its path.
"""
import re
from pathlib import Path

from tumblepipe.api import to_windows_path
from tumblepipe.apps.deadline import Job, hpm_package_spec

# Captures the package-root prefix (…/.hpm/packages/<name>@<version>) of a
# script path, whether Windows (C:/…) or WSL (/mnt/c/…) form.
_PACKAGE_ROOT_RE = re.compile(r'^(.*?/\.hpm/packages/[^/]+@[^/]+)/')


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


def hpm_task_manifest(script_path) -> str:
    """hpm.toml that resolves the package a farm task runs from.

    A synthetic envelope package whose single dependency is the task's package
    at its exact version — hpm has no "install <pkg>@<ver>" verb, so you declare
    it as a dependency and `hpm install` resolves it into the shared store.

    Gotchas baked in:
    - `[package].path` is required or hpm refuses to load the manifest.
    - the dependency key is the full `creator/slug` (quoted, for the slash);
      the bare slug 404s on a fresh worker with no cached registry index.
    - the version is BARE (an exact registry get_version fetch); a "=" prefix
      is sent verbatim into the registry query and 404s.
    """
    package_spec, _ = hpm_package_spec(script_path)
    bare_name, _, version = package_spec.partition('@')
    full_name = _package_full_name(script_path, bare_name)
    return (
        '[package]\n'
        'path = "local/deadline-hpm-job"\n'
        'name = "deadline-hpm-job"\n'
        'version = "0.0.0"\n\n'
        '[compat]\n'
        'houdini = ">=21, <99"\n\n'
        '[dependencies]\n'
        f'"{full_name}" = "{version}"\n'
    )


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
