"""Audit: th::lpe_tags builds a render var for every tag a light carries.

    hython scripts/verify_lpe_tags.py

The node does not build its render vars from the multiparm. A foreach loop
scrapes the lpetag attributes back off the *cooked stage*, so anything the
scrape cannot see produces no beauty_<tag> AOV — silently, with no node error
and no warning.

That is how mesh lights broke. LightAPI is an *applied* schema, so a mesh
light keeps the type name 'Mesh'; the scrape filtered candidates with
GetTypeName().endswith('Light') and dropped every one of them. A shot with
three configured tags rendered a single beauty_fill var and nothing said so.

The checks below pin both halves: that mesh lights and light-typed prims both
reach the loop, and that a tag nothing carries is reported in the Status field
instead of vanishing. The empty-pattern case matters most — an empty Lights
pattern is the parm's own default, it makes the LPE Tag LOP fall back to
tagging everything Untagged_Lights, and the loop skips that name by design, so
*every* tagged AOV disappears at once.

Needs Houdini (otls/ on HOUDINI_OTLSCAN_PATH, python/ on PYTHONPATH); builds
its stage in memory and touches no project data.
"""

import sys

import hou

_failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok   ' if ok else 'FAIL '} {label}")
    if not ok:
        print(f"        got:  {got!r}")
        print(f"        want: {want!r}")
        _failures.append(label)


# Two sphere lights sharing a leading name token, and two mesh lights — the
# shape that broke: /SET geometry carrying MeshLightAPI, not *Light prims.
BUILD_STAGE = '''
node = hou.pwd(); stage = node.editableStage()
from pxr import UsdGeom, UsdLux, UsdRender
UsdGeom.Scope.Define(stage, "/lights")
UsdLux.SphereLight.Define(stage, "/lights/fill_key")
UsdLux.SphereLight.Define(stage, "/lights/fill_rim")
for path in ("/SET/side_panel", "/SET/top_roof"):
    UsdLux.MeshLightAPI.Apply(UsdGeom.Mesh.Define(stage, path).GetPrim())
UsdRender.Var.Define(stage, "/Render/Products/Vars/beauty")
UsdRender.Product.Define(stage, "/Render/Products/renderproduct")
UsdRender.Settings.Define(stage, "/Render/rendersettings")
'''

WARNING = (
    "No light carries: %s. Check the Lights pattern - no beauty_<tag> AOV "
    "is built for a tag nothing carries."
)


def render_vars(node):
    vars_prim = node.stage().GetPrimAtPath('/Render/Products/Vars')
    if not vars_prim: return []
    return sorted(child.GetName() for child in vars_prim.GetChildren())


def build(parent, source, name, rows):
    node = parent.createNode('th::lpe_tags::1.0', name)
    node.setInput(0, source)
    node.parm('tags').set(len(rows))
    for i, (tag_name, prim_pattern) in enumerate(rows):
        node.parm(f'lpetag{i}').set(tag_name)
        node.parm(f'primpattern{i}').set(prim_pattern)
    return node


def main():
    stage_ctx = hou.node('/stage')
    source = stage_ctx.createNode('pythonscript', 'build_stage')
    source.parm('python').set(BUILD_STAGE)

    # Mesh lights and light-typed prims must both reach the var loop.
    every = build(stage_ctx, source, 'every_tag', [
        ('side', '/SET/side_panel'),
        ('top', '/SET/top_roof'),
        ('fill', '/lights/fill_*'),
    ])
    check("mesh lights and lights alike build their vars",
          render_vars(every),
          ['beauty', 'beauty_fill', 'beauty_side', 'beauty_top'])
    check("a healthy setup says nothing", every.parm('status').eval(), '')

    # A tag nothing carries must be named, not silently dropped.
    ghost = build(stage_ctx, source, 'ghost_tag', [
        ('fill', '/lights/fill_*'),
        ('ghost', '/nope/missing'),
    ])
    check("an unmatched tag builds no var",
          render_vars(ghost), ['beauty', 'beauty_fill'])
    check("an unmatched tag is reported",
          ghost.parm('status').eval(), WARNING % 'ghost')

    # The parm default is an empty pattern, which wipes every tagged AOV.
    empty = build(stage_ctx, source, 'empty_pattern',
                  [('side', ''), ('top', '')])
    check("empty patterns wipe every tagged var",
          render_vars(empty), ['beauty'])
    check("the wipe is reported",
          empty.parm('status').eval(), WARNING % 'side, top')

    # A row the artist switched off is not a mistake.
    disabled = build(stage_ctx, source, 'disabled_row', [
        ('fill', '/lights/fill_*'),
        ('off', '/nope/missing'),
    ])
    disabled.parm('enable1').set(0)
    check("a disabled row is not reported",
          disabled.parm('status').eval(), '')

    # Status is a convenience; a failed parm expression is reported as a node
    # error, and errors propagate out of an HDA, so it must never raise.
    orphan = stage_ctx.createNode('th::lpe_tags::1.0', 'no_input')
    orphan.parm('tags').set(1)
    orphan.parm('lpetag0').set('side')
    check("a node with no input reports no error", orphan.errors(), ())
    check("a node with no input has no status", orphan.parm('status').eval(), '')

    # Generate is name-driven: any case, anywhere in the stage, concrete paths.
    generated = stage_ctx.createNode('th::lpe_tags::1.0', 'generated')
    generated.setInput(0, source)
    generated.parm('generate_lpes').pressButton()
    rows = {
        generated.parm(f'lpetag{i}').eval(): generated.parm(f'primpattern{i}').eval()
        for i in range(generated.parm('tags').eval())
    }
    check("Generate finds lowercase names and mesh lights", rows, {
        'fill': '/lights/fill_key /lights/fill_rim',
        'side': '/SET/side_panel',
        'top': '/SET/top_roof',
    })
    check("Generate produces a working setup",
          render_vars(generated),
          ['beauty', 'beauty_fill', 'beauty_side', 'beauty_top'])

    if _failures:
        print("\n%d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
