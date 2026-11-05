"""th::render_settings: the shot's Karma settings, and the one place its
AOVs are chosen.

The AOVs are th::render_vars' toggles, promoted onto this node: each var is
named as the pipeline reads it, its plane alike (``pipe.usd.render_vars``).
The Karma Render Properties inside keeps its own AOV toggles, which name
vars Karma's way (Beauty as plane ``C``, ``Albedo``, ``N``...) and collide
with ours, so they stay off: ``lock_karma_aovs`` switches off any a saved
scene has on when it loads, ticking ours instead and saying so on the node,
and ``check_render_vars`` makes the node error while one is on.
"""


from tumblepipe.api import api
from tumblepipe.util.io import load_json, store_json
from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns

class RenderSettings(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_preset_paths(self):
        preset_path = api.storage.resolve(Uri.parse('preset:/houdini/lops/render_settings'))
        return {
            preset_path.stem: preset_path
            for preset_path in preset_path.glob('*.json')
        }
    
    def list_preset_names(self):
        preset_names = list(self.list_preset_paths().keys())
        if 'default' not in preset_names:
            preset_names.insert(0, 'default')
        return preset_names
    
    def get_preset_name(self):
        return self.parm('preset').eval()
    
    def set_save_lock(self, state):
        self.parm('unlock_save').set(not state)
    
    def load(self):
        preset_name = self.get_preset_name()
        preset_path = api.storage.resolve(Uri.parse(f'preset:/houdini/lops/render_settings/{preset_name}.json'))
        if not preset_path.exists(): return
        self.native().setParmsFromData(load_json(preset_path))
        self.set_save_lock(True)

    def save(self):
        preset_name = self.get_preset_name()
        preset_path = api.storage.resolve(Uri.parse(f'preset:/houdini/lops/render_settings/{preset_name}.json'))
        preset_path.parent.mkdir(parents=True, exist_ok=True)
        store_json(preset_path, self.native().parmsAsData())
        self.set_save_lock(True)

def create(scene, name):
    return ns.create_node(scene, name, RenderSettings, 'render_settings')

def load(kwargs):
    raw_node = kwargs['node']
    node = RenderSettings(raw_node)
    node.load()

def save(kwargs):
    raw_node = kwargs['node']
    node = RenderSettings(raw_node)
    node.save()


# Karma Render Properties' AOV toggles (Houdini 22.0): parm -> the
# th::render_vars toggle for the same AOV, None where there is none.
KARMA_AOV_TOGGLES = {
    'beauty': 'beauty',
    'beautyunshadowed': 'unshadowed',
    'shadow': 'shadow',
    'combineddiffuse': 'diffuse',
    'directdiffuse': 'diffuse_direct',
    'indirectdiffuse': 'diffuse_indirect',
    'combineddiffuseunshadowed': None,
    'directdiffuseunshadowed': None,
    'indirectdiffuseunshadowed': None,
    'combineddiffuseshadow': None,
    'directdiffuseshadow': None,
    'indirectdiffuseshadow': None,
    'combinedglossyreflection': 'specular',
    'directglossyreflection': 'specular_direct',
    'indirectglossyreflection': 'specular_indirect',
    'glossytransmission': 'transmission',
    'coat': 'coat',
    'combinedemission': 'emission',
    'directemission': None,
    'indirectemission': None,
    'visiblelights': None,
    'combinedvolume': 'volume',
    'directvolume': None,
    'indirectvolume': None,
    'sss': 'sss',
    'albedo': 'albedo',
    'ambientocclusion': 'ao',
    'hitP': 'position',
    'hitP_camera': None,
    'hitPz': 'depth',
    'element': None,
    'primid': None,
    'hituv': 'uv',
    'hitN': 'normal',
    'hitNg': None,
    'hitN_facingratio': None,
    'hitNg_facingratio': None,
    'motionvectors': 'motionvectors',
    'velocity': 'velocity',
}

KARMA_NODE = 'settings'


def karma_aovs_on(karma) -> list[str]:
    """The Karma AOV toggles switched on on ``karma`` (Karma Render
    Properties), by parm name."""
    on = []
    for name in KARMA_AOV_TOGGLES:
        parm = karma.parm(name)
        if parm is not None and parm.eval():
            on.append(name)
    return on


def lock_karma_aovs(node) -> list[str]:
    """Switch off the Karma AOV toggles on ``node``'s inner Karma Render
    Properties, ticking this node's toggle for the same AOV. Says what it
    did on the node's comment; returns the Karma toggles switched off."""
    import hou

    karma = node.node(KARMA_NODE)
    if karma is None:
        return []
    switched = karma_aovs_on(karma)
    if not switched:
        return []
    ticked, without = [], []
    for name in switched:
        karma.parm(name).set(0)
        ours = KARMA_AOV_TOGGLES[name]
        label = karma.parm(name).parmTemplate().label()
        if ours is not None and node.parm(ours) is not None:
            node.parm(ours).set(1)
            ticked.append(f'{label} -> {ours}')
        else:
            without.append(label)
    lines = ["Karma's own AOV toggles were on inside this node; switched off "
             "(they name planes Karma's way: Beauty as C)."]
    if ticked:
        lines.append('Ticked here instead: ' + ', '.join(ticked) + '.')
    if without:
        lines.append('No TumblePipe AOV for: ' + ', '.join(without) + '.')
    message = '\n'.join(lines)
    comment = node.comment()
    node.setComment(f'{comment}\n{message}' if comment else message)
    node.setGenericFlag(hou.nodeFlag.DisplayComment, True)
    print(f'{node.path()}: {message}')
    return switched


def on_loaded(kwargs):
    """OnLoaded: a scene saved with Karma's own AOV toggles on loads with
    them off and ours on (``lock_karma_aovs``)."""
    lock_karma_aovs(kwargs['node'])


def render_var_problems(stage) -> list[str]:
    """Every RenderVar on ``stage`` whose plane the farm cannot read, and
    every pair making one AOV (``pipe.usd.render_vars``)."""
    from tumblepipe.pipe.usd.render_vars import (
        AOV_NAME_ATTR,
        plane_problem,
        twin_problems,
    )

    paths = []
    problems = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != 'RenderVar':
            continue
        path = str(prim.GetPath())
        paths.append(path)
        attr = prim.GetAttribute(AOV_NAME_ATTR)
        plane = attr.Get() if attr and attr.HasValue() else None
        problem = plane_problem(path, plane)
        if problem is not None:
            problems.append(problem)
    return problems + twin_problems(paths)


def check_render_vars(lop) -> None:
    """The inner check LOP's cook: error naming the Karma toggles that are
    on, or any render var the farm would refuse."""
    import hou

    node = lop.parent()
    karma = node.node(KARMA_NODE)
    on = karma_aovs_on(karma) if karma is not None else []
    if on:
        labels = ', '.join(
            f"{karma.parm(name).parmTemplate().label()}"
            + (f" (tick '{KARMA_AOV_TOGGLES[name]}' in the AOVs section)"
               if KARMA_AOV_TOGGLES[name] else '')
            for name in on
        )
        raise hou.NodeError(
            f"Karma's own AOV toggles are on inside this node: {labels}. "
            "They name planes Karma's way (Beauty as C) and the farm refuses "
            "them: switch them off and use the AOVs section."
        )
    stage = lop.inputs()[0].stage() if lop.inputs() and lop.inputs()[0] else None
    if stage is None:
        return
    problems = render_var_problems(stage)
    if problems:
        from tumblepipe.pipe.usd.render_vars import FIX
        raise hou.NodeError(
            'Render vars the farm cannot read:\n' + '\n'.join(problems) + '\n' + FIX
        )
