"""Audit: every HDA callback must reach a function that exists.

    python scripts/verify_hda_callbacks.py

A parm's callback does not reach `python/tumblepipe/...` directly. It names a
function on the HDA's own PythonModule section, which is a hand-maintained
shim of one-line forwarders:

    DialogScript          PythonModule              python/tumblepipe/...
    hou.phm().select()  ->  def select():       ->  def select():
                                th_cache.select()       ...

Three files, and the middle one is the one nobody edits. Add the parm and the
backing function and both ends look finished while the button is dead: the
callback is a string, so nothing resolves it until an artist clicks and gets

    AttributeError: 'module' object has no attribute 'select'

That is exactly how th::cache (SOP *and* LOP) shipped its Entity button broken
in 93e4dc1, which touched only the two DialogScripts. Nothing else catches it:
no lint or build gate resolves callbacks, and a stale otls/<name>.hda can
hide a fix until compile-hdas runs.

Both directions of the shim are checked:

1. Dangling callback — a name a DialogScript calls that the PythonModule does
   not define (including a PythonModule that does not exist at all).
2. Dangling forwarder — a forwarder whose backing function is gone from the
   module it delegates to. This is the near-miss class (get_asset_uri vs
   get_entity_uri): the wrapper renames, the shim keeps calling the old name.

Runs headlessly against the repo sources: it reads the expanded otls/ sections
and the Python wrappers as text, so it needs no Houdini. Reading rather than
importing is also what keeps it honest — importing a PythonModule outside a
GUI Houdini fails for reasons that have nothing to do with the callback (see
KNOWN_GAPS: lop_th.image_plane_painter imports nodegraphutils, which touches
hou.ui at import time), and those failures look exactly like a missing name.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OTLS = REPO_ROOT / 'otls'
PACKAGE = REPO_ROOT / 'python'

# HDAs with a known, pre-existing dangling callback, and why they are not a
# regression. Anything NOT listed here is a new break and fails the audit.
KNOWN_GAPS = {
    # Has no PythonModule section at all (confirmed via Sections.list) and has
    # never had one — it shipped this way in the initial commit 52df04b, so
    # these buttons have never worked for anyone. renameattrib/resetattrib/
    # updateattrib exist nowhere in the repo, and its DialogScript also calls
    # hdaViewerStateModule().updateattribute with no ViewerStateModule section.
    # Repairing it means reconstructing an attribute-paint UI from its internal
    # network, not reconnecting a forwarder — a feature task, not a fix.
    'sop_th.mesh_blender.1.0': (
        'no PythonModule since 52df04b; buttons never worked. '
        'Needs the attribute-paint module reconstructed, not a forwarder.'
    ),
}

# Every idiom used to reach an HDA's PythonModule from a callback or a menu.
_CALL = re.compile(r'(?:hou\.phm|(?:\w+\.)?hdaModule)\(\)\.(\w+)')
# Callbacks forward to plain functions; forwarders also construct wrapper
# classes (`import_shot.ImportShot(...)`), so both count as "defined".
_DEF = re.compile(r'^(?:def|class)\s+(\w+)', re.M)
# `import tumblepipe.pipe.houdini.lops.cache as th_cache`
_IMPORT_AS = re.compile(r'^import\s+([\w.]+)\s+as\s+(\w+)', re.M)
# `from tumblepipe.pipe.houdini.lops import layer_split`
_FROM_IMPORT = re.compile(r'^from\s+([\w.]+)\s+import\s+([\w,\s]+)$', re.M)
# a forwarder body's delegation: `th_cache.select()`
_DELEGATE = re.compile(r'\b(\w+)\.(\w+)\s*\(')


def _sections():
    """(hda_name, dialog_script_path, python_module_path_or_None)."""
    for dialog in sorted(OTLS.glob('*/*/DialogScript')):
        pymod = dialog.parent / 'PythonModule'
        yield dialog.parents[1].name, dialog, (pymod if pymod.exists() else None)


def _read(path):
    return path.read_text(encoding='utf-8', errors='replace')


def _module_path(dotted: str) -> Path | None:
    """Resolve `tumblepipe.pipe.houdini.lops.cache` to its .py file."""
    candidate = PACKAGE / Path(*dotted.split('.'))
    for path in (candidate.with_suffix('.py'), candidate / '__init__.py'):
        if path.exists():
            return path
    return None


def _aliases(pymod_text: str) -> dict[str, Path]:
    """Map each imported alias in a PythonModule to the .py file behind it."""
    out = {}
    for dotted, alias in _IMPORT_AS.findall(pymod_text):
        path = _module_path(dotted)
        if path is not None:
            out[alias] = path
    for dotted, names in _FROM_IMPORT.findall(pymod_text):
        for name in (n.strip() for n in names.split(',')):
            if not name:
                continue
            path = _module_path(f'{dotted}.{name}')
            if path is not None:
                out[name] = path
    return out


def check_callbacks_resolve() -> list[str]:
    """Every name a DialogScript calls is defined in the PythonModule."""
    failures = []
    for hda, dialog, pymod in _sections():
        called = set(_CALL.findall(_read(dialog)))
        if not called:
            continue
        defined = set(_DEF.findall(_read(pymod))) if pymod else set()
        missing = sorted(called - defined)
        if not missing:
            continue
        if hda in KNOWN_GAPS:
            continue
        where = 'has no PythonModule section' if pymod is None else 'PythonModule'
        failures.append(
            f"{hda}: {where} does not define {', '.join(missing)} "
            f"(called from DialogScript)"
        )
    return failures


def check_forwarders_resolve() -> list[str]:
    """Every forwarder's backing function still exists in the module it calls."""
    failures = []
    for hda, _dialog, pymod in _sections():
        if pymod is None or hda in KNOWN_GAPS:
            continue
        text = _read(pymod)
        aliases = _aliases(text)
        if not aliases:
            continue
        backing = {
            alias: set(_DEF.findall(_read(path)))
            for alias, path in aliases.items()
        }
        for alias, attr in _DELEGATE.findall(text):
            if alias not in backing or attr in backing[alias]:
                continue
            failures.append(
                f"{hda}: forwarder calls {alias}.{attr}(), which "
                f"{aliases[alias].relative_to(REPO_ROOT).as_posix()} "
                f"does not define"
            )
    return failures


def report_known_gaps() -> None:
    if not KNOWN_GAPS:
        return
    print()
    print('known gaps (not regressions, excluded from the checks above):')
    for hda, reason in sorted(KNOWN_GAPS.items()):
        print(f'  {hda}: {reason}')


CHECKS = (
    ('every DialogScript callback resolves in its PythonModule', check_callbacks_resolve),
    ('every PythonModule forwarder reaches a real function', check_forwarders_resolve),
)


def main() -> int:
    failed = 0
    for title, check in CHECKS:
        failures = check()
        if failures:
            failed += 1
            print(f'FAIL  {title}')
            for failure in failures:
                print(f'        {failure}')
        else:
            print(f'ok    {title}')

    report_known_gaps()

    print()
    if failed:
        print(f'{failed}/{len(CHECKS)} checks failed')
        return 1
    print(f'{len(CHECKS)}/{len(CHECKS)} checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
