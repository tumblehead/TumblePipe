#!/usr/bin/env python3
"""Open the Farm Submit grid from TumbleTrove Desktop, without starting Houdini.

Run by the **Farm Submit** button in the Desktop's per-project Scripts panel
(``[scripts.farm]`` in hpm.toml). Standard library only: it runs in the
uv-managed Python the script entry asks for, and hands off to Houdini's own
bundled Python — which carries PySide6 and ``pxr`` and takes no licence — to
run ``python -m tumblepipe.farm_app``.

What the Desktop gives a script (``run_project_script``, houdini-hub-desktop
``src-tauri/src/commands/project/scripts.rs``): the working directory is the
Desktop project dir; the environment carries ``HOUDINI_PACKAGE_DIR`` (the
project's generated ``<owner>.<package>.json`` Houdini package files), the
project's own env vars and ``HPM_PACKAGE_ROOT``. It does *not* carry
TumblePipe's ``[runtime]`` values (``TH_PIPELINE_PATH``, ``OCIO``, the
resolver's ``PXR_PLUGINPATH_NAME``…), which only Houdini applies from those
package files, nor any Houdini install path. So this launcher:

1. finds a Houdini install that satisfies both the project's and
   TumblePipe's ``[compat] houdini`` ranges (``$HFS`` wins when set);
2. replays every package file the way Houdini's package loader would, so the
   Farm Submit window sees exactly the environment a Houdini session would;
3. starts the window in that Houdini's bundled Python and waits for it, so
   the Desktop's running state and Cancel keep meaning something.

``TH_USER`` comes from ``TT_USER_NAME``, which the Desktop does not yet pass
to scripts; until it does, submissions made from here carry no user name.

    python scripts/farm_launcher.py [--context shots|assets] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

_VAR = re.compile(r'\$\{(\w+)\}|\$(\w+)')


# ── version ranges ────────────────────────────────────────

def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r'\d+', text))


def _compare(left: tuple, right: tuple) -> int:
    # Compare on the requirement's own precision: "<23" means major < 23,
    # and 22.0.368 satisfies ">=22.0".
    left = left[:len(right)]
    return (left > right) - (left < right)


def satisfies(version: tuple[int, ...], requirement: str) -> bool:
    """Whether ``version`` meets a Cargo-style ``">=21, <23"`` requirement."""
    for clause in (c.strip() for c in requirement.split(',')):
        if not clause:
            continue
        match = re.fullmatch(r'(>=|<=|==|=|>|<)?\s*([\d.]+)', clause)
        if match is None:
            raise ValueError(f'Unsupported version requirement: {clause!r}')
        op = match.group(1) or '>='
        cmp = _compare(version, parse_version(match.group(2)))
        ok = {
            '>=': cmp >= 0, '>': cmp > 0, '<=': cmp <= 0, '<': cmp < 0,
            '==': cmp == 0, '=': cmp == 0,
        }[op]
        if not ok:
            return False
    return True


def read_compat(manifest: Path) -> str | None:
    """The ``[compat] houdini`` requirement of an hpm.toml, if any."""
    if tomllib is None or not manifest.is_file():
        return None
    with open(manifest, 'rb') as handle:
        data = tomllib.load(handle)
    return (data.get('compat') or {}).get('houdini')


# ── finding Houdini ───────────────────────────────────────

def installed_houdinis() -> list[tuple[tuple[int, ...], Path]]:
    """``(version, HFS)`` for every Houdini in the standard install places."""
    found = []
    system = platform.system()
    if system == 'Windows':
        roots = [Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Side Effects Software']
        for root in roots:
            for path in root.glob('Houdini *') if root.is_dir() else []:
                found.append((parse_version(path.name), path))
    elif system == 'Linux':
        for path in Path('/opt').glob('hfs*'):
            found.append((parse_version(path.name), path))
    elif system == 'Darwin':
        for path in Path('/Applications/Houdini').glob('Houdini*'):
            hfs = path / 'Frameworks' / 'Houdini.framework' / 'Versions' / 'Current' / 'Resources'
            found.append((parse_version(path.name), hfs))
    return [(v, p) for v, p in found if len(v) >= 2 and p.is_dir()]


def choose_houdini(requirements: list[str], env: dict) -> tuple[tuple[int, ...], Path]:
    """``$HFS`` when set, else the newest install meeting every requirement."""
    if env.get('HFS'):
        hfs = Path(env['HFS'])
        return parse_version(hfs.name) or (0,), hfs
    candidates = [
        (version, hfs) for version, hfs in installed_houdinis()
        if all(satisfies(version, r) for r in requirements if r)
    ]
    if not candidates:
        raise SystemExit(
            'No installed Houdini satisfies '
            + ' and '.join(repr(r) for r in requirements if r)
            + '. Install one, or set HFS to the Houdini to use.'
        )
    return max(candidates)


def houdini_python(hfs: Path) -> Path:
    """The bundled interpreter that matches Houdini's own ``python3.XXlibs``.

    A Houdini ships several (22.0 has python310, python311 and python313);
    only the one Houdini itself runs carries PySide6 and ``pxr``.
    """
    libs = sorted((hfs / 'houdini').glob('python3.*libs'))
    if not libs:
        raise SystemExit(f'No python3.*libs under {hfs / "houdini"}; is {hfs} a Houdini install?')
    minor = libs[-1].name[len('python3.'):-len('libs')]
    for candidate in (
        hfs / f'python3{minor}' / 'python.exe',
        hfs / 'python' / 'bin' / f'python3.{minor}',
        hfs / 'python' / 'bin' / 'python3',
    ):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Houdini's Python 3.{minor} was not found under {hfs}")


def forced_site_packages(python: Path) -> list[str]:
    """Where Houdini keeps PySide6 for that interpreter (Windows layout)."""
    forced = python.parent / 'lib' / 'site-packages-forced'
    return [str(forced)] if forced.is_dir() else []


# ── replaying the Houdini package files ───────────────────

def expand(value: str, env: dict) -> str:
    """``$VAR`` / ``${VAR}`` from ``env``; unknown names are left as written."""
    def replace(match):
        name = match.group(1) or match.group(2)
        return env.get(name, match.group(0))
    return _VAR.sub(replace, value)


def apply_package(data: dict, env: dict) -> None:
    """Apply one Houdini package file's ``env`` entries to ``env``, in order.

    ``{"VAR": "value"}`` sets; ``{"VAR": {"method": "prepend"|"append"|"set",
    "value": str | [str, ...]}}`` does what it says, path-joined.
    """
    entries = data.get('env') or []
    if isinstance(entries, dict):
        entries = [entries]
    for entry in entries:
        for name, spec in entry.items():
            if isinstance(spec, dict):
                method = spec.get('method', 'set')
                value = spec.get('value', '')
            else:
                method, value = 'set', spec
            values = value if isinstance(value, list) else [value]
            text = os.pathsep.join(expand(str(v), env) for v in values)
            existing = env.get(name)
            if method == 'prepend' and existing:
                env[name] = text + os.pathsep + existing
            elif method == 'append' and existing:
                env[name] = existing + os.pathsep + text
            else:
                env[name] = text


def resolve_references(env: dict, passes: int = 8) -> None:
    """Expand ``$VAR`` references left in values once every package is in.

    Houdini resolves package variables after loading all of them, so a
    package may name a variable a later file sets: TumblePipe's
    ``TH_CONFIG_PATH = $TH_PROJECT_PATH/_config`` comes before the project's
    overrides file supplies ``TH_PROJECT_PATH``. Repeats until nothing
    changes (a chain), bounded so a self-reference cannot loop.
    """
    for _ in range(passes):
        changed = False
        for name, value in list(env.items()):
            if '$' not in value:
                continue
            expanded = expand(value, env)
            if expanded != value:
                env[name] = expanded
                changed = True
        if not changed:
            return


def replay_packages(package_dir: Path, env: dict) -> list[str]:
    """Apply every ``*.json`` in ``package_dir``, name order (``~`` sorts last,
    which is where hpm puts the project's own overrides), then resolve the
    references between them. Returns the names applied."""
    applied = []
    for path in sorted(package_dir.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        apply_package(data, env)
        applied.append(path.name)
    resolve_references(env)
    return applied


# ── main ──────────────────────────────────────────────────

def build_env(base: dict, package_dir: Path, hfs: Path, version: tuple[int, ...]) -> dict:
    env = dict(base)
    env['HFS'] = str(hfs)
    env['HOUDINI_MAJOR_RELEASE'] = str(version[0]) if version else ''
    env['HOUDINI_MINOR_RELEASE'] = str(version[1]) if len(version) > 1 else ''
    env['HOUDINI_VERSION'] = '.'.join(str(v) for v in version)
    env['TH_HOUDINI_VERSION'] = env['HOUDINI_VERSION']
    # What Houdini derives from the TumbleTrove account; the Desktop passes
    # TT_USER_NAME to Houdini but not (yet) to scripts.
    if env.get('TT_USER_NAME'):
        env.setdefault('TH_USER', env['TT_USER_NAME'])
    replay_packages(package_dir, env)
    env['PATH'] = str(hfs / 'bin') + os.pathsep + env.get('PATH', '')
    env['PYTHONUTF8'] = '1'
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='farm_launcher')
    parser.add_argument('--context', choices=('shots', 'assets'), default='shots')
    parser.add_argument('--dry-run', action='store_true',
                        help='print what would run, without starting it')
    parser.add_argument('--run', metavar='SCRIPT',
                        help='run SCRIPT in the prepared Houdini Python instead '
                             'of the Farm Submit window (verify harnesses, debugging)')
    args = parser.parse_args(argv)

    package_dir = os.environ.get('HOUDINI_PACKAGE_DIR')
    if not package_dir or not Path(package_dir).is_dir():
        print('HOUDINI_PACKAGE_DIR is not set to the project\'s package folder. '
              'Run this from the TumbleTrove Desktop Scripts panel.', file=sys.stderr)
        return 2
    project_dir = Path(os.environ.get('TT_PROJECT_DIR') or Path(package_dir).parents[1])

    requirements = [
        read_compat(project_dir / 'hpm.toml'),
        read_compat(PACKAGE_ROOT / 'hpm.toml'),
    ]
    version, hfs = choose_houdini(requirements, os.environ)
    python = houdini_python(hfs)
    env = build_env(os.environ, Path(package_dir), hfs, version)
    pythonpath = [p for p in env.get('PYTHONPATH', '').split(os.pathsep) if p]
    env['PYTHONPATH'] = os.pathsep.join(
        [str(PACKAGE_ROOT / 'python')] + forced_site_packages(python) + pythonpath
    )
    if args.run:
        command = [str(python), str(Path(args.run).resolve())]
    else:
        command = [str(python), '-m', 'tumblepipe.farm_app', '--context', args.context]

    print(f'Houdini {".".join(map(str, version))} at {hfs}')
    print(f'Project: {env.get("TH_PROJECT_PATH", "<unset>")}')
    print('Running: ' + ' '.join(command))
    if args.dry_run:
        return 0
    # A neutral working directory: `-m` puts it first on sys.path.
    return subprocess.call(command, env=env, cwd=str(project_dir))


if __name__ == '__main__':
    sys.exit(main())
