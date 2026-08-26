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

A menu script skips the shim entirely — it constructs the wrapper itself and
calls it — so it needs its own check:

3. Dangling menu call — a menu block does

       node = import_shot.ImportShot(hou.pwd())
       items = node.list_channel_names()

   and nothing resolves `list_channel_names` until an artist opens the menu
   and gets an empty list or a traceback in the console. Rename the wrapper
   method and every DialogScript saying the old name goes quietly stale. The
   variant -> channel rename touched nine of these plus two menu blocks buried
   in `Contents.dir/.OPdummydefs`, which grep reports as binary files.

Runs headlessly against the repo sources: it reads the expanded otls/ sections
and the Python wrappers as text, so it needs no Houdini. Reading rather than
importing is also what keeps it honest — importing a PythonModule outside a
GUI Houdini fails for reasons that have nothing to do with the callback (see
KNOWN_GAPS: lop_th.image_plane_painter imports nodegraphutils, which touches
hou.ui at import time), and those failures look exactly like a missing name.
"""

import ast
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
# one line of an HDA menu/callback script: `[ "node = x.Y(hou.pwd())" ]`
_SCRIPT_LINE = re.compile(r'^\s*\[\s*"(.*)"\s*\]\s*$', re.M)
# `node = import_shot.ImportShot(hou.pwd())`
_WRAP = re.compile(r'\b(\w+)\s*=\s*(\w+)\.(\w+)\s*\(')


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


def _classes_in(path: Path) -> dict[str, tuple[set[str], list[str]]]:
    """Classes defined in one module: name -> (its own members, base names)."""
    try:
        tree = ast.parse(_read(path))
    except SyntaxError:
        return {}
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        members = {
            child.name for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        members |= {
            target.id for child in node.body
            if isinstance(child, ast.Assign)
            for target in child.targets if isinstance(target, ast.Name)
        }
        # Keep the base's written form: `ns.Node` has to resolve through the
        # defining module's own imports, because the bare name `Node` is
        # ambiguous across the package (pipe/graph.py and pipe/houdini/nodes.py).
        bases = [ast.unparse(b) for b in node.bases]
        out[node.name] = (members, [b for b in bases if b and b != 'object'])
    return out


def _class_index() -> dict[str, list[Path]]:
    """Bare class name -> every module defining it.

    Deliberately a list: `Cache` and `ImportAsset` each exist in a LOP and a
    SOP module (and `Cache` again in util/), so a name alone does not identify
    a class. A base that resolves ambiguously makes the whole lookup give up
    rather than guess - a wrong guess here reports a working menu as broken.
    """
    index = {}
    for path in sorted(PACKAGE.rglob('*.py')):
        for name in _classes_in(path):
            index.setdefault(name, []).append(path)
    return index


def _resolve_methods(path: Path, name: str, index, _seen=None) -> set[str] | None:
    """Members of `name` as defined in `path`, plus inherited ones.

    None means "cannot tell" - the class or one of its bases is not resolvable
    from the sources alone, so the caller must not report a missing method.
    """
    _seen = _seen or set()
    key = (path, name)
    if key in _seen:
        return set()
    _seen.add(key)

    classes = _classes_in(path)
    if name not in classes:
        candidates = index.get(name, [])
        if len(candidates) != 1:
            return None            # unknown, or ambiguous across modules
        return _resolve_methods(candidates[0], name, index, _seen)

    members, bases = classes[name]
    out = set(members)
    module_aliases = _aliases(_read(path))
    for base in bases:
        head, _, tail = base.rpartition('.')
        if head:
            # `ns.Node`: the alias names the module the base lives in.
            base_path = module_aliases.get(head.split('.')[0])
            if base_path is None:
                return None
            inherited = _resolve_methods(base_path, tail, index, _seen)
        elif base in classes:
            inherited = _resolve_methods(path, base, index, _seen)
        elif base in module_aliases:
            inherited = _resolve_methods(module_aliases[base], base, index, _seen)
        else:
            candidates = index.get(base, [])
            if len(candidates) != 1:
                return None        # base outside the package, or ambiguous
            inherited = _resolve_methods(candidates[0], base, index, _seen)
        if inherited is None:
            return None
        out |= inherited
    return out


def _script_sections():
    """(hda_name, path) for every section that can hold a menu script."""
    for pattern in ('*/*/DialogScript', '*/*/Contents.dir/.OPdummydefs'):
        for path in sorted(OTLS.glob(pattern)):
            hda = next(p for p in path.parents if p.parent == OTLS)
            yield hda.name, path


def check_menu_wrappers_resolve() -> list[str]:
    """Every wrapper method a menu script calls exists on the wrapper class."""
    index = _class_index()
    failures = []
    for hda, path in _script_sections():
        if hda in KNOWN_GAPS:
            continue
        text = _read(path)
        script = '\n'.join(_SCRIPT_LINE.findall(text))
        if 'tumblepipe' not in script:
            continue  # no wrapper reachable from here (or a pre-rename module)
        aliases = _aliases(script)
        wrapped = {
            var: (aliases[alias], cls)
            for var, alias, cls in _WRAP.findall(script)
            if alias in aliases
        }
        if not wrapped:
            continue
        for var, attr in _DELEGATE.findall(script):
            target = wrapped.get(var)
            if target is None:
                continue
            module, cls = target
            methods = _resolve_methods(module, cls, index)
            if methods is None or attr in methods:
                continue
            failures.append(
                f'{hda}: menu script calls {var}.{attr}() on {cls}, '
                f'which does not define it '
                f'({path.relative_to(REPO_ROOT).as_posix()})'
            )
    return failures


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
    ('every menu script reaches a real wrapper method', check_menu_wrappers_resolve),
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
