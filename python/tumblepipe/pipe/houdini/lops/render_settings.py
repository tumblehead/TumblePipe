"""th::render_settings: the shot's Karma settings (quality and camera).

AOVs are chosen on th::render_vars only (issue #72). Up to 1.103 this node
had an AOVs section copying th::render_vars' toggles and Compression menus;
those parms are still on the node, in a hidden folder (``OLD_AOVS``), and
still drive the embedded render_vars inside, so a scene that ticked AOVs
there renders exactly as before. Houdini drops the saved values of parms an
HDA no longer defines, so removing them would have silently dropped those
AOVs from old shots. A node with any ticked says so in its comment
(``old_aovs_note``, on load and after a preset load) and shows **Drop Old
AOVs** (``drop_old_aovs``) to clear them once they are ticked on
th::render_vars. (A warning from the inner check LOP would stay on that
LOP: an HDA does not show its children's warnings.) A fresh node leaves
them all off: its embedded render_vars then renders only the fallback
beauty, when nothing upstream has one.

The Karma Render Properties inside keeps its own AOV toggles, which name
vars Karma's way (Beauty as plane ``C``, ``Albedo``, ``N``...) and collide
with ours, so they stay off: ``lock_karma_aovs`` switches off any a saved
scene has on when it loads, ticking the old AOV of the same name instead
(so the render keeps the AOV) and saying so on the node, and
``check_render_vars`` makes the node error while one is on.
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
        # A preset saved up to 1.103 may tick the old AOVs.
        old_aovs_note(self.native())

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


# The old AOVs section (up to 1.103), frozen: toggle -> its Compression
# menu. Hidden on the node; each still drives the embedded render_vars'
# parm of the same name. New AOVs go on th::render_vars only.
OLD_AOVS = {
    'beauty': 'beauty_compression',
    'beauty_mse': 'beauty_mse_compression',
    'sample_count': 'samples_compression',
    'albedo': 'albedo_compression',
    'albedo_mse': 'albedo_mse_compression',
    'normal': 'normal_compression',
    'normal_mse': 'normal_mse_compression',
    'uv': 'uv_compression',
    'position': 'position_compression',
    'depth': 'depth_compression',
    'diffuse': 'diffuse_compression',
    'specular': 'specular_compression',
    'emission': 'emission_compression',
    'volume': 'volume_compression',
    'diffuse_direct': 'diffuse_direct_compression',
    'diffuse_indirect': 'diffuse_indirect_compression',
    'specular_direct': 'specular_direct_compression',
    'specular_indirect': 'specular_indirect_compression',
    'transmission': 'transmission_compression',
    'sss': 'sss_compression',
    'coat': 'coat_compression',
    'unshadowed': 'unshadowed_compression',
    'shadow': 'shadow_compression',
    'ao': 'ao_compression',
    'motionvectors': 'motionvectors_compression',
    'velocity': 'velocity_compression',
    'cryptoprimitives': 'cryptoprimitives_compression',
    'cryptomaterials': 'cryptomaterials_compression',
}

# The fallback beauty (rendered when nothing upstream has one) reads the
# old beauty menu even unticked; DWA-B is its default.
OLD_BEAUTY_COMPRESSION_DEFAULT = 1

OLD_AOVS_NOTE = "AOVs ticked on this node's old AOVs section still render"


def old_aovs(node) -> list[str]:
    """What the hidden old AOVs section still renders on ``node``: the
    ticked toggles, plus ``beauty_compression`` when the fallback beauty's
    menu was changed. Empty on a fresh node."""
    ticked = []
    for name in OLD_AOVS:
        parm = node.parm(name)
        if parm is not None and parm.eval():
            ticked.append(name)
    menu = node.parm('beauty_compression')
    if (menu is not None and 'beauty' not in ticked
            and menu.eval() != OLD_BEAUTY_COMPRESSION_DEFAULT):
        ticked.append('beauty_compression')
    return ticked


def old_aovs_message(ticked) -> str:
    return (f"{OLD_AOVS_NOTE}: {', '.join(ticked)}. Tick them on "
            "th::render_vars, then press Drop Old AOVs.")


def old_aovs_note(node) -> list[str]:
    """Keep ``node``'s comment saying which old AOVs still render: one line,
    replaced when they change, gone when none are left. Returns them."""
    import hou

    ticked = old_aovs(node)
    comment = node.comment()
    lines = [line for line in (comment.split('\n') if comment else [])
             if not line.startswith(OLD_AOVS_NOTE)]
    if ticked:
        lines.append(old_aovs_message(ticked))
    new = '\n'.join(lines)
    if new != comment:
        node.setComment(new)
    if ticked:
        node.setGenericFlag(hou.nodeFlag.DisplayComment, True)
    return ticked


def drop_old_aovs(kwargs) -> None:
    """Drop Old AOVs: every old AOV parm back to its default, so the
    embedded render_vars renders only the fallback beauty again."""
    node = kwargs['node']
    for toggle, menu in OLD_AOVS.items():
        for name in (toggle, menu):
            parm = node.parm(name)
            if parm is not None:
                parm.revertToDefaults()
    old_aovs_note(node)


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
        lines.append("Ticked on this node's old AOVs instead, so they still "
                     "render (move them to th::render_vars): "
                     + ', '.join(ticked) + '.')
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
    them off and the old AOVs of the same name on (``lock_karma_aovs``),
    and a node whose old AOVs section still renders says so
    (``old_aovs_note``). Parm and comment edits only, no network edits."""
    node = kwargs['node']
    lock_karma_aovs(node)
    old_aovs_note(node)


def render_var_problems(stage) -> list[str]:
    """Every RenderVar on ``stage`` whose plane the farm cannot read, and
    every pair making one AOV (``pipe.usd.render_vars``)."""
    from tumblepipe.pipe.usd.render_vars import twin_problems, var_problems

    paths = []
    problems = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != 'RenderVar':
            continue
        path = str(prim.GetPath())
        paths.append(path)
        problems.extend(var_problems(prim))
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
            + (f" (tick '{KARMA_AOV_TOGGLES[name]}' on th::render_vars)"
               if KARMA_AOV_TOGGLES[name] else '')
            for name in on
        )
        raise hou.NodeError(
            f"Karma's own AOV toggles are on inside this node: {labels}. "
            "They name planes Karma's way (Beauty as C) and the farm refuses "
            "them: switch them off and tick the AOVs on th::render_vars."
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
