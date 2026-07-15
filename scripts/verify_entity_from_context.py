"""Audit: every th:: HDA that addresses an entity must default to 'from_context'.

    python scripts/verify_entity_from_context.py

A node's entity/shot/asset parm is what binds it to a pipeline entity. When
that parm holds a concrete URI, the node is pinned to whatever entity it
happened to be born in: copy the scene to another asset, rename the entity,
or build a shot from a template, and the node keeps publishing to the old
one. The 'from_context' sentinel instead resolves the entity from the
workfile the node lives in, every time it is evaluated.

This checks the three ways that contract has been broken before:

1. HDA parm defaults — an entity-addressing parm whose default is a concrete
   URI, or the empty string (which several wrappers silently resolve to the
   *first entity in the project*, quietly operating on the wrong one).
2. Baked-in on_created hooks — a wrapper that writes the workfile's URI into
   the parm at creation, defeating the sentinel it just defaulted to.
3. Department templates — the single-entity branch (_create_entity) stamping
   a specific entity URI, which only the multi-entity group branch needs.

Runs headlessly against the repo sources: it reads the expanded otls/ HDA
DialogScripts and the Python wrappers as text, so it needs no Houdini.
"""

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
    # Uses an explicit entity_source menu ('from_context'/'from_settings')
    # instead of a sentinel in the shot parm; the shot parm is only read in
    # from_settings mode.
    'lop_th.playblast.1.0': 'entity_source menu gates the shot parm',
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


CHECKS = (
    ('HDA entity parms default to from_context', check_hda_defaults),
    ('no on_created bakes the workfile URI', check_no_baked_on_created),
    ('templates leave single-entity graphs on from_context', check_templates_single_entity),
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
