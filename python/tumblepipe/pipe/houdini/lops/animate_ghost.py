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
    a rig is imported), nor import_rig nodes that th::import_rigs creates
    after this cook. Both nodes' Import updates their own parms (the
    version label, the rows), so depending on all of them keeps the ghost
    from going stale.
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


def list_rigged_asset_uris(dive_node) -> set[Uri]:
    """The assets whose rigs a live th::import_rig under `dive_node` loads.

    Reads the 'entity' parm as written. 'from_context' never names an asset
    in a shot workfile (import_rig only addresses assets), so it is skipped.
    """
    asset_uris = set()
    for node in dive_node.allSubChildren():
        if _th_sop_type(node) in ('import_rig', 'import_rigs'):
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
    asset_uris = list_rigged_asset_uris(dive_node)
    if len(asset_uris) == 0: return
    stage = lop_node.editableStage()
    root = stage.GetPseudoRoot()
    for asset_info in prim_metadata.list_assets(root):
        asset_uri = Uri.parse(asset_info['metadata']['uri'])
        if asset_uri not in asset_uris: continue
        stage.GetPrimAtPath(asset_info['prim_path']).SetActive(False)


def on_loaded(node):
    """anim_dive is an editable node, so a scene saved before ghost_stage
    existed keeps ghosting the raw input stage. Repoint it."""
    dive_node = node.node('anim_dive')
    if dive_node is None: return
    parm = dive_node.parm('showlopstage')
    if parm is None or parm.unexpandedString() != '../IN_stage': return
    parm.set('../ghost_stage')
