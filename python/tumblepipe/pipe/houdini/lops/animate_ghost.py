"""th::animate: the ghost stage its anim_dive shows behind the rigs.

The dive's SOP viewer ghosts the LOP stage (anim_dive's `showlopstage`).
That stage still holds the USD model of every character being animated,
standing in its rest pose, so each character showed up twice: the posed
rig and its T-posed USD copy. The HDA's `ghost_stage` Python LOP runs
`prune_rigged_assets` to deactivate those copies. It feeds only the ghost;
the node's output still merges the untouched input stage.
"""

from tumblepipe.pipe.houdini import prim_metadata
from tumblepipe.util.uri import Uri


def _th_sop_type(node) -> str | None:
    """'import_rig' for a th::import_rig SOP, and so on; None otherwise."""
    type_name = node.type().name().lower().split('::')
    if len(type_name) < 2 or type_name[0] != 'th': return None
    if node.type().category().name() != 'Sop': return None
    return type_name[1]


def _is_import_rig(node) -> bool:
    return _th_sop_type(node) == 'import_rig'


def _watch(node) -> None:
    """Evaluate every parm on ``node`` so this cook depends on them.

    Houdini re-cooks a Python LOP when a parm it evaluated changes, but it
    does not see the switch inside a locked th::import_rig (what flips when
    a rig is imported). Both nodes' Import updates their own parms (the
    version label, the rows), so depending on all of them keeps the ghost
    from going stale. Rig nodes created after this cook are not watched
    yet; their Import bumps `ghost_refresh` (`refresh_ghost`) instead.
    """
    for parm in node.parms():
        try:
            parm.eval()
        except Exception:
            pass


def _is_live(node, stop) -> bool:
    """False when the node or an ancestor below `stop` is bypassed, or
    import_rig bypassed itself because the rig has no version yet."""
    ancestor = node
    while ancestor is not None and ancestor != stop:
        if ancestor.isBypassed(): return False
        ancestor = ancestor.parent()
    bypass_node = node.node('bypass')
    if bypass_node is None: return True
    bypass_parm = bypass_node.parm('input')
    return bypass_parm is None or bypass_parm.eval() != 0


def _sop_node_types():
    import hou
    return hou.sopNodeTypeCategory().nodeTypes().values()


def _rig_nodes(dive_node):
    """The th::import_rig(s) nodes anywhere under `dive_node`.

    Found by node type, not by listing the dive's children: a Python LOP
    that lists a network's children recooks whenever any node is created
    in it, so placing an unconnected node in the dive rebuilt the ghost
    (and the viewport re-synced the shot stage). A rig node created after
    this cook is caught by its Import bumping `ghost_refresh` instead.
    """
    prefix = dive_node.path().rstrip('/') + '/'
    for node_type in _sop_node_types():
        name = node_type.name().lower().split('::')
        if len(name) < 2 or name[0] != 'th': continue
        if name[1] not in ('import_rig', 'import_rigs'): continue
        for node in node_type.instances():
            if node.path().startswith(prefix):
                yield node


def list_rigged_asset_uris(dive_node) -> set[Uri]:
    """The assets whose rigs a live th::import_rig under `dive_node` loads.

    Reads the 'entity' parm as written. 'from_context' never names an asset
    in a shot workfile (import_rig only addresses assets), so it is skipped.
    """
    asset_uris = set()
    for node in _rig_nodes(dive_node):
        _watch(node)
        if not _is_import_rig(node): continue
        if not _is_live(node, dive_node): continue
        raw_uri = node.parm('entity').eval()
        if not raw_uri.startswith('entity:/assets/'): continue
        asset_uris.add(Uri.parse(raw_uri))
    return asset_uris


def prune_rigged_assets(lop_node, dive_node):
    """Deactivate, in `lop_node`'s editable stage, every asset prim whose
    rig is imported in `dive_node`."""
    parent = lop_node.parent() if hasattr(lop_node, 'parent') else None
    refresh = parent.parm(GHOST_REFRESH) if parent is not None else None
    if refresh is not None:
        refresh.eval()  # so refresh_ghost re-cooks this
    asset_uris = list_rigged_asset_uris(dive_node)
    if len(asset_uris) == 0: return
    stage = lop_node.editableStage()
    root = stage.GetPseudoRoot()
    for asset_info in prim_metadata.list_assets(root):
        asset_uri = Uri.parse(asset_info['metadata']['uri'])
        if asset_uri not in asset_uris: continue
        stage.GetPrimAtPath(asset_info['prim_path']).SetActive(False)


GHOST_REFRESH = 'ghost_refresh'


def refresh_ghost(node) -> bool:
    """Re-cook the ghost of the th::animate holding `node` (a th::import_rig
    or th::import_rigs that just imported): the ghost finds rig nodes by
    type when it cooks, so one made since then is not watched yet. False
    when `node` is not inside a th::animate."""
    ancestor = node.parent()
    while ancestor is not None:
        name = ancestor.type().name().lower().split('::')
        if len(name) >= 2 and name[0] == 'th' and name[1] == 'animate':
            parm = ancestor.parm(GHOST_REFRESH)
            if parm is None: return False
            parm.set(parm.eval() + 1)
            return True
        ancestor = ancestor.parent()
    return False


def on_loaded(node):
    """anim_dive is an editable node, so a scene saved before ghost_stage
    existed keeps ghosting the raw input stage. Repoint it."""
    dive_node = node.node('anim_dive')
    if dive_node is None: return
    parm = dive_node.parm('showlopstage')
    if parm is None or parm.unexpandedString() != '../IN_stage': return
    parm.set('../ghost_stage')
