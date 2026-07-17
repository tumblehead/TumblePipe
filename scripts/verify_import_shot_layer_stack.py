"""Verify that import_shot's Layer Stack folder stays put across re-imports.

Pins the fix for the "parms changing places after import" bug (33b34a8):
``hou.Node.setParmTemplateGroup()`` on an HDA *instance* spare-ifies the
definition's container folders under renamed names (``selection`` ->
``selection2``), so any folder-name anchor lookup goes stale after the
first import — the Layer Stack folder then fell through to ``append()``
and jumped below Actions on every re-import. The fix anchors the insert
on definition parm names via ``containingFolder()``, which Houdini never
renames.

Run under any project hython with tumblepipe on the package path, e.g.
via TumbleTrove Desktop's run_hython or a dev launch:

    hython scripts/verify_import_shot_layer_stack.py

Needs no project data: it drives ``_update_layer_stack_ui`` directly
with synthetic layer infos, never composing or resolving anything.

Checks:
  1. First update inserts the Layer Stack folder right after Selection.
  2. Re-update keeps the layout identical (the fixed bug: it moved to
     the bottom) and keeps it stable on a third pass (no rename cascade).
  3. An unchecked layer toggle survives a re-update.
  4. The Sublayer LOP enable parms are wired to the HDA checkboxes.
  5. The asset inspector button lands on asset rows and only asset rows,
     is wired through the HDA's PythonModule to a method that exists, and
     survives a re-update like the toggles do.
"""

import sys

import hou

FAILURES = []


def check(label, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(label)


def top_level(node):
    return [t.name() for t in node.parmTemplateGroup().entries()]


LAYERS = [
    {
        "path": "entity:/shots/seq/shot?dept=root&variant=default&version=v0001",
        "type": "root",
        "department": "root",
        "variant": "default",
    },
    {
        "path": "entity:/shots/seq/shot?dept=animation&variant=default&version=v0003",
        "type": "shot_department",
        "department": "animation",
        "variant": "default",
    },
    {
        "path": "entity:/assets/PROP/Crate?variant=default&version=v0002",
        "type": "asset",
        "department": None,
        "variant": "default",
    },
]

from tumblepipe.pipe.houdini.lops import import_shot  # noqa: E402

stage = hou.node("/stage")
raw = stage.createNode("th::import_shot::1.0", "verify_layer_stack")
node = import_shot.ImportShot(raw)

# execute() sizes the Sublayer LOP before rebuilding the UI; mirror that.
raw.node("import").parm("num_files").set(len(LAYERS))

node._update_layer_stack_ui(LAYERS)
first = top_level(raw)
check(
    "Layer Stack inserted after Selection",
    "layer_stack" in first and first.index("layer_stack") == 1,
    f"order={first}",
)

# Artist unchecks a layer, then re-imports.
raw.parm("layer_1_enable").set(0)
node._update_layer_stack_ui(LAYERS)
second = top_level(raw)
check("layout stable on re-update", second == first, f"order={second}")
check(
    "unchecked layer toggle survives re-update",
    raw.parm("layer_1_enable").eval() == 0,
)

node._update_layer_stack_ui(LAYERS)
third = top_level(raw)
check("layout stable on third update (no rename cascade)", third == first,
      f"order={third}")

expr = raw.node("import").parm("enable2").expression()
check(
    "Sublayer enables wired to HDA checkboxes",
    expr == 'ch("../layer_1_enable")',
    f"expr={expr!r}",
)

# --- the asset inspector button ------------------------------------------
# LAYERS[2] is the only asset row; 0 is root and 1 is a shot department.
check(
    "inspect button on the asset row",
    raw.parm("layer_2_inspect") is not None,
)
check(
    "no inspect button on the root row",
    raw.parm("layer_0_inspect") is None,
)
check(
    "no inspect button on a shot department row",
    raw.parm("layer_1_inspect") is None,
)

inspect_parm = raw.parm("layer_2_inspect")
if inspect_parm is not None:
    callback = inspect_parm.parmTemplate().scriptCallback()
    # Routed through the HDA's PythonModule rather than a baked module path:
    # spare-parm callbacks are serialized into the artist's .hip, and this
    # indirection is what ships with the package.
    check(
        "inspect button calls the PythonModule with its row index",
        callback == "hou.phm().inspect_asset_layer(2)",
        f"callback={callback!r}",
    )
    check(
        "inspect callback is Python",
        inspect_parm.parmTemplate().scriptCallbackLanguage()
        == hou.scriptLanguage.Python,
    )

# The button is a spare parm like the rest of the folder, so it must survive
# the same rebuild the toggles do.
node._update_layer_stack_ui(LAYERS)
check(
    "inspect button survives a re-update",
    raw.parm("layer_2_inspect") is not None,
)
check(
    "layout still stable with the button present",
    top_level(raw) == first,
    f"order={top_level(raw)}",
)

# The PythonModule side of the callback — a near-miss name here is swallowed
# as a silent no-op, so assert the method actually exists.
phm = raw.hdaModule()
check(
    "PythonModule exposes inspect_asset_layer",
    hasattr(phm, "inspect_asset_layer"),
)
check(
    "ImportShot implements inspect_asset_layer",
    callable(getattr(import_shot.ImportShot, "inspect_asset_layer", None)),
)

print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("All checks passed.")
