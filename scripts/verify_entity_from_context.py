"""Audit: every th:: HDA that addresses an entity must default to 'from_context'.

    python scripts/verify_entity_from_context.py

A node's entity/shot/asset parm is what binds it to a pipeline entity. When
that parm holds a concrete URI, the node is pinned to whatever entity it
happened to be born in: copy the scene to another asset, rename the entity,
or build a shot from a template, and the node keeps publishing to the old
one. The 'from_context' sentinel instead resolves the entity from the
workfile the node lives in, every time it is evaluated.

This checks the four ways that contract has been broken before:

1. HDA parm defaults — an entity-addressing parm whose default is a concrete
   URI, or the empty string (which several wrappers silently resolve to the
   *first entity in the project*, quietly operating on the wrong one).
2. Baked-in on_created hooks — a wrapper that writes the workfile's URI into
   the parm at creation, defeating the sentinel it just defaulted to.
3. Department templates — the single-entity branch (_create_entity) stamping
   a specific entity URI, which only the multi-entity group branch needs.
4. Group URIs — a resolver that reads the workfile's context.json and uses
   its 'entity_uri' as an entity, without first checking that it *is* one.
   A Multi's workfile records the Multi ('groups:/shots/<name>'), which is
   not an entity: the config layer raises 'Not an entity URI' on it, and a
   node that lets it through addresses a thing with no frame range, no
   export folder and no single camera.

Runs headlessly against the repo sources: it reads the expanded otls/ HDA
DialogScripts and the Python wrappers as text, so it needs no Houdini.
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OTLS = REPO_ROOT / 'otls'
WRAPPERS = REPO_ROOT / 'python' / 'tumblepipe' / 'pipe' / 'houdini'
TEMPLATES = REPO_ROOT / 'scripts' / 'project_template' / '_config' / 'templates'

SENTINEL = 'from_context'

# Parm names that address a pipeline entity.
ENTITY_PARMS = {'entity', 'asset', 'shot'}

# HDAs exempt from the parm-default rule, with the reason. These address an
# entity through a different mechanism, not by pinning a URI at birth.
EXEMPT = {
    # Multiparm rows: each row names a *different* entity to import, so a
    # single 'from_context' default is meaningless. The rows start empty.
    'lop_th.import_assets.2.0': 'multiparm rows name distinct entities',
    'sop_th.import_rigs.2.0': 'multiparm rows name distinct entities',
}

# Resolvers that read the pointer but deliberately accept a group, with the
# reason. They must handle it, not merely tolerate it.
GROUP_AWARE = {
    'cops/build_comp.py::_entity_from_context_json':
        'the comp builder runs in a Multi workfile and branches on the group',
}


_PARM_BLOCK = re.compile(
    r'parm\s*\{(?P<body>.*?)\n(?P<indent>\s*)\}', re.DOTALL
)
_NAME = re.compile(r'name\s+"(?P<name>[^"]+)"')
_DEFAULT = re.compile(r'default\s*\{\s*"(?P<default>[^"]*)"')


def _dialog_scripts():
    for path in sorted(OTLS.glob('*/*/DialogScript')):
        yield path.parents[1].name, path


def check_hda_defaults() -> list[str]:
    """Every entity-addressing parm defaults to the sentinel."""
    failures = []
    for hda, path in _dialog_scripts():
        if hda in EXEMPT:
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for block in _PARM_BLOCK.finditer(text):
            body = block.group('body')
            name = _NAME.search(body)
            default = _DEFAULT.search(body)
            if name is None or default is None:
                continue
            if name.group('name') not in ENTITY_PARMS:
                continue
            value = default.group('default')
            if value == SENTINEL:
                continue
            shown = repr(value) if value else "'' (resolves to the first entity in the project)"
            failures.append(
                f"{hda}: parm '{name.group('name')}' defaults to {shown}, "
                f"expected '{SENTINEL}'"
            )
    return failures


def check_no_baked_on_created() -> list[str]:
    """No wrapper writes a concrete URI into the parm at creation.

    Two shapes, both of which shipped:
      - the workfile's own URI ("helpfully" pre-filling the sentinel), and
      - the *first entity in the project*, which pins a fresh node to an
        arbitrary entity that looks indistinguishable from a real choice.
    """
    from_context_bake = re.compile(
        r'\.set_(entity|shot|asset)_uri\(\s*(context|ctx)\.entity_uri'
    )
    first_entity_bake = re.compile(
        r'\.(set_(entity|shot|asset)_uri|parm\(.(entity|shot|asset).\)\.set)\('
        r'[^)]*(asset|shot|entity)_uris\[[01]\]'
    )
    failures = []
    for path in sorted(WRAPPERS.rglob('*.py')):
        for lineno, line in enumerate(
            path.read_text(encoding='utf-8').splitlines(), start=1
        ):
            rel = path.relative_to(REPO_ROOT)
            if from_context_bake.search(line):
                failures.append(
                    f'{rel}:{lineno}: bakes the workfile URI into the parm — '
                    f'leave it at the {SENTINEL!r} default'
                )
            elif first_entity_bake.search(line):
                failures.append(
                    f'{rel}:{lineno}: pins the node to the first entity in the '
                    f'project — leave it at the {SENTINEL!r} default'
                )
    return failures


def check_templates_single_entity() -> list[str]:
    """_create_entity (single-entity workfile) never pins an entity URI.

    _create_group must, and does — it holds several entities at once, so the
    sentinel cannot resolve to one of them.
    """
    pinning = re.compile(r'set_entity_uri\(|_pin_entity\(|parm\(.entity.\)\.set\(')
    failures = []
    for path in sorted(TEMPLATES.rglob('template.py')):
        lines = path.read_text(encoding='utf-8').splitlines()
        in_single = False
        for lineno, line in enumerate(lines, start=1):
            if line.startswith('def '):
                in_single = line.startswith('def _create_entity')
                continue
            if in_single and pinning.search(line):
                rel = path.relative_to(REPO_ROOT)
                failures.append(
                    f'{rel}:{lineno}: _create_entity pins an entity URI — '
                    f'a single-entity workfile should rely on {SENTINEL!r}'
                )
    return failures


def _called_name(call: ast.Call) -> str:
    """'get_workfile_context', 'uri.startswith', ... for a call node."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ''


READERS = ('get_workfile_context', 'load_entity_context')


def _reads_the_pointer(func: ast.AST, helpers: frozenset = frozenset()) -> bool:
    """Does this function load the workfile's context.json?

    Directly, or through one of the module's own reader ``helpers`` — the
    LOP playblast reached it through ``self._get_context()``, and a check
    that only followed direct calls would have missed the bug it is here
    to catch. One hop is enough for the shapes in the tree.
    """
    return any(
        isinstance(node, ast.Call)
        and _called_name(node) in (READERS + tuple(helpers))
        for node in ast.walk(func)
    )


def _reader_helpers(tree: ast.AST) -> frozenset:
    """The module's own functions that read the pointer directly."""
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _reads_the_pointer(node)
    )


def _vouching_helpers(tree: ast.AST, module_key: str) -> frozenset:
    """Reader helpers that check the URI *and* turn a group away.

    A caller of one of these needs no check of its own — import_shot's
    ``_entity_from_context_json`` already returns None for a group. A
    reader listed in GROUP_AWARE vouches for nobody: it hands groups back
    on purpose, so whoever turns its result into an entity must check.
    """
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _reads_the_pointer(node)
        and _checks_what_it_got(node)
        and f'{module_key}::{node.name}' not in GROUP_AWARE
    )


def _uses_the_uri(func: ast.AST) -> bool:
    """Does it go on to use that context's entity URI?"""
    return any(
        isinstance(node, ast.Attribute) and node.attr == 'entity_uri'
        for node in ast.walk(func)
    )


def _checks_what_it_got(func: ast.AST) -> bool:
    """Does it ask what kind of URI it is before using it?

    Three shapes are in the tree, all of them honest: comparing
    ``uri.purpose``, asking ``is_group_uri()``, and testing the string
    against an ``entity:`` prefix. Matched through the AST rather than the
    text, so an unrelated ``purpose='turntable'`` keyword does not pass.
    """
    for node in ast.walk(func):
        if isinstance(node, ast.Compare):
            left = node.left
            if isinstance(left, ast.Attribute) and left.attr == 'purpose':
                return True
            for comparator in node.comparators:
                if (isinstance(comparator, ast.Constant)
                        and isinstance(comparator.value, str)
                        and comparator.value.startswith('entity:')):
                    return True
        if isinstance(node, ast.Call):
            if _called_name(node) == 'is_group_uri':
                return True
            if _called_name(node) == 'startswith':
                first = node.args[0] if node.args else None
                if (isinstance(first, ast.Constant)
                        and isinstance(first.value, str)
                        and first.value.startswith('entity:')):
                    return True
    return False


def check_group_uris_rejected() -> list[str]:
    """A resolver that uses the workfile's URI checks that it is an entity."""
    failures = []
    for path in sorted(WRAPPERS.rglob('*.py')):
        rel = path.relative_to(REPO_ROOT)
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError as exc:
            failures.append(f'{rel}: could not be parsed ({exc})')
            continue
        module_key = path.relative_to(WRAPPERS).as_posix()
        helpers = _reader_helpers(tree)
        vouching = _vouching_helpers(tree, module_key)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if f'{module_key}::{node.name}' in GROUP_AWARE:
                continue
            if not (_reads_the_pointer(node, helpers) and _uses_the_uri(node)):
                continue
            if _checks_what_it_got(node):
                continue
            if _reads_the_pointer(node, vouching) and not _reads_the_pointer(node):
                continue  # its reader already turned the group away
            failures.append(
                f'{rel}:{node.lineno}: {node.name}() uses the workfile URI '
                f"without checking it is an entity — a Multi's workfile "
                f'records a groups:/ URI'
            )
    return failures


CHECKS = (
    ('HDA entity parms default to from_context', check_hda_defaults),
    ('no on_created bakes the workfile URI', check_no_baked_on_created),
    ('templates leave single-entity graphs on from_context', check_templates_single_entity),
    ('from_context resolvers reject a group URI', check_group_uris_rejected),
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

    print()
    if failed:
        print(f'{failed}/{len(CHECKS)} checks failed')
        return 1
    print(f'{len(CHECKS)}/{len(CHECKS)} checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
