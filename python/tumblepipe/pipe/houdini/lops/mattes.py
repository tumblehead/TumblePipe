"""th::mattes: matte and distance-ramp AOVs for grading.

The HDA holds two multiparms, Mattes and Distance Ramps; an internal Python
Script LOP calls `cook`, which reads them, expands the prim patterns against
the input stage and hands everything to `tumblepipe.pipe.mattes.author`.
"""

import hou

import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe import mattes as pipe_mattes

class Mattes(ns.Node):
    def __init__(self, native):
        super().__init__(native)

def create(scene, name):
    return ns.create_node(scene, name, Mattes, 'mattes')

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def _expand(pattern, stage):
    if not pattern.strip(): return []
    rule = hou.LopSelectionRule()
    rule.setPathPattern(pattern)
    return [str(path) for path in rule.expandedPaths(stage=stage)]

def read_entries(hda_node, stage):
    """The HDA's multiparm rows as Matte and Ramp entries; unnamed rows are skipped."""
    mattes = []
    for index in range(1, hda_node.parm('mattes').evalAsInt() + 1):
        name = hda_node.parm(f'matte_name{index}').evalAsString().strip()
        if not name: continue
        mattes.append(pipe_mattes.Matte(
            name = name,
            prim_paths = _expand(hda_node.parm(f'matte_prims{index}').evalAsString(), stage),
            compression = hda_node.parm(f'matte_compression{index}').evalAsString()
        ))
    ramps = []
    for index in range(1, hda_node.parm('ramps').evalAsInt() + 1):
        name = hda_node.parm(f'ramp_name{index}').evalAsString().strip()
        if not name: continue
        ramps.append(pipe_mattes.Ramp(
            name = name,
            near = hda_node.parm(f'ramp_near{index}').evalAsFloat(),
            far = hda_node.parm(f'ramp_far{index}').evalAsFloat(),
            measure = hda_node.parm(f'ramp_measure{index}').evalAsString(),
            compression = hda_node.parm(f'ramp_compression{index}').evalAsString()
        ))
    return mattes, ramps

def cook(script_node):
    """Body of the HDA's internal Python Script LOP."""
    hda_node = script_node.parent()
    stage = script_node.editableStage()
    mattes, ramps = read_entries(hda_node, stage)
    settings_path = hda_node.parm('settings_path').evalAsString()
    try:
        pipe_mattes.author(stage, mattes, ramps, settings_path)
    except pipe_mattes.MattesError as error:
        raise hou.NodeError(str(error))
