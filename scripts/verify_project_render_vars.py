"""Audit: a project's AOVs are the RenderVars th::render_vars cooks.

    hython scripts/verify_project_render_vars.py

A project names AOVs for every shot in ``render.aovs``; the shot's root
layer authors them from a table (``pipe.usd.standard_aovs``) with no node
to cook. The table is only right while it says what ``th::render_vars``
authors, so this cooks the node with every toggle ticked and compares each
RenderVar it makes with the table's, property for property and schema for
schema, both ways.

Then the two together, as a shot has them: the template's
``root_default_prims.usda`` under a root layer override that adds project
AOVs, and a ``th::render_vars`` after it with one more toggle ticked. The
product must order the defaults' vars, the project's and the shot's, each
once, and the farm's plane check must pass.

Needs Houdini (otls/ on HOUDINI_OTLSCAN_PATH, python/ on PYTHONPATH) and a
project whose ``_config/usd/root_default_prims.usda`` is the template's;
writes one layer to the working directory.
"""

import sys
from pathlib import Path

import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.usd import render_overrides as ro
from tumblepipe.pipe.usd import standard_aovs as sa
from tumblepipe.pipe.usd.render_vars import render_var_problems

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok   ' if ok else 'FAIL '} {label}")
    if not ok:
        if isinstance(got, dict) and isinstance(want, dict):
            # Only what differs: a var has a dozen properties.
            keys = sorted(k for k in set(got) | set(want) if got.get(k, '-') != want.get(k, '-'))
            got = {k: got.get(k, '-') for k in keys}
            want = {k: want.get(k, '-') for k in keys}
        print(f"        got:  {got!r}")
        print(f"        want: {want!r}")
        _failures.append(label)


BUILD_STAGE = '''
node = hou.pwd(); stage = node.editableStage()
from pxr import UsdRender
UsdRender.Product.Define(stage, "/Render/Products/renderproduct")
settings = UsdRender.Settings.Define(stage, "/Render/rendersettings")
settings.CreateProductsRel().SetTargets(["/Render/Products/renderproduct"])
'''

VARS = '/Render/Products/Vars'
PRODUCT = '/Render/Products/renderproduct'
SETTINGS = '/Render/rendersettings'


def toggles(node):
    return [
        parm for parm in node.parms()
        if parm.parmTemplate().type() == hou.parmTemplateType.Toggle
        and not parm.parmTemplate().isHidden()
    ]


def authored(prim):
    """Every property the prim has an authored value for, as Python values."""
    found = {}
    for attribute in prim.GetAuthoredAttributes():
        if attribute.HasAuthoredValue():
            found[attribute.GetName()] = attribute.Get()
    return found


def ordered(stage):
    from pxr import UsdRender
    product = UsdRender.Product(stage.GetPrimAtPath(PRODUCT))
    return [target.name for target in product.GetOrderedVarsRel().GetForwardedTargets()]


def main():
    stage_ctx = hou.node('/stage')
    source = stage_ctx.createNode('pythonscript', 'build_stage')
    source.parm('python').set(BUILD_STAGE)

    # Every toggle ticked: the node's vars against the table's.
    every = stage_ctx.createNode('th::render_vars::1.1', 'every_aov')
    every.setInput(0, source)
    for parm in toggles(every):
        parm.set(1)
    stage = every.stage()
    check('the node cooks without errors', every.errors(), ())
    cooked = [prim.GetName() for prim in stage.GetPrimAtPath(VARS).GetChildren()]
    check('the table names exactly the vars the node makes', sorted(sa.AOVS), sorted(cooked))
    for name, aov in sa.AOVS.items():
        prim = stage.GetPrimAtPath(f'{VARS}/{name}')
        if not prim:
            continue
        check(f'{name}: every property the node authors is the table\'s',
              authored(prim), aov.properties())
        check(f'{name}: the schemas the node applies are the table\'s',
              sorted(prim.GetAppliedSchemas()), sorted(aov.api_schemas()))
        check(f'{name}: a RenderVar', prim.GetTypeName(), 'RenderVar')

    # The table's USDA, read back, is those same vars.
    from pxr import Sdf, Usd
    override = ro.RenderOverride(SETTINGS, None, None, (), PRODUCT, tuple(sa.AOVS))
    layer = Sdf.Layer.CreateAnonymous('.usda')
    check('the override layer parses',
          layer.ImportFromString('#usda 1.0\n\n' + ro.override_usda(override) + '\n'), True)
    written = Usd.Stage.Open(layer)
    for name in sa.AOVS:
        check(f'{name}: the root layer\'s var is the node\'s',
              authored(written.GetPrimAtPath(f'{VARS}/{name}')),
              authored(stage.GetPrimAtPath(f'{VARS}/{name}')))
        types = {
            a.GetName(): (str(a.GetTypeName()), a.IsCustom(), str(a.GetVariability()))
            for a in written.GetPrimAtPath(f'{VARS}/{name}').GetAuthoredAttributes()
        }
        want = {
            a.GetName(): (str(a.GetTypeName()), a.IsCustom(), str(a.GetVariability()))
            for a in stage.GetPrimAtPath(f'{VARS}/{name}').GetAuthoredAttributes()
            if a.HasAuthoredValue()
        }
        check(f'{name}: declared as the node declares it', types, want)
        check(f'{name}: the root layer\'s var applies the node\'s schemas',
              sorted(written.GetPrimAtPath(f'{VARS}/{name}').GetAppliedSchemas()),
              sorted(stage.GetPrimAtPath(f'{VARS}/{name}').GetAppliedSchemas()))

    # A shot: the project's defaults, the project's AOVs from the root
    # layer, and its own on th::render_vars.
    defaults = api.storage.resolve(Uri.parse('config:/usd/root_default_prims.usda'))
    plan = ro.plan_for_defaults(
        {ro.APPLY_KEY: True}, defaults, {ro.AOVS_KEY: ['depth', 'coat', 'albedo']},
    )
    check('the plan adds the AOVs the defaults lack', plan and plan.aovs, ('depth', 'coat'))
    root_layer = Path.cwd() / 'project_aovs.usda'
    root_layer.write_bytes(('#usda 1.0\n\n' + ro.override_usda(plan) + '\n').encode('utf-8'))
    sublayer = stage_ctx.createNode('sublayer', 'root_layer')
    sublayer.parm('num_files').set(2)
    # The LOP's later files are the stronger ones.
    sublayer.parm('filepath1').set(defaults.as_posix())
    sublayer.parm('filepath2').set(root_layer.as_posix())
    check('the root layer orders the defaults\' vars, then the project\'s',
          ordered(sublayer.stage()), ['beauty', 'albedo', 'normal', 'depth', 'coat'])
    check('the farm reads every plane of the root layer',
          render_var_problems(sublayer.stage(), SETTINGS), [])

    shot = stage_ctx.createNode('th::render_vars::1.1', 'shot_aovs')
    shot.setInput(0, sublayer)
    shot.parm('uv').set(1)
    shot.parm('depth').set(1)     # one the project names too: the same prim
    check('the shot\'s node cooks without errors', shot.errors(), ())
    check('th::render_vars orders the defaults\', the project\'s and the shot\'s AOVs, each once',
          sorted(ordered(shot.stage())), ['albedo', 'beauty', 'coat', 'depth', 'normal', 'uv'])
    check('the farm reads every plane of the shot',
          render_var_problems(shot.stage(), SETTINGS), [])
    check('the project\'s coat reaches the shot as the table has it',
          authored(shot.stage().GetPrimAtPath(f'{VARS}/coat')), sa.AOVS['coat'].properties())

    if _failures:
        print("\n%d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
