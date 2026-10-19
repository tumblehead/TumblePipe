"""AOV (Arbitrary Output Variable) utilities for render layer management."""

from typing import Sequence


def sort_aovs(aovs: Sequence[str]) -> list[str]:
    """Sort AOVs by category: beauty, LPEs, mattes, others.

    Args:
        aovs: List of AOV names (case-insensitive matching)

    Returns:
        Sorted list with:
        1. 'beauty' (if present, case-insensitive)
        2. 'beauty_*' LPE channels
        3. 'objid_*' and 'holdout_*' (mattes)
        4. All other AOVs
    """
    beauty = None
    lpes = []
    mattes = []
    other = []

    for aov in aovs:
        _aov = aov.lower()
        if _aov == 'beauty':
            beauty = aov
        elif _aov.startswith('beauty_'):
            lpes.append(aov)
        elif _aov.startswith('objid_') or _aov.startswith('holdout_'):
            mattes.append(aov)
        else:
            other.append(aov)

    result = [] if beauty is None else [beauty]
    result.extend(lpes)
    result.extend(mattes)
    result.extend(other)
    return result


def get_ordered_render_vars(stage) -> str:
    """Get space-separated ordered RenderVar paths from a USD stage.

    This is the full expression used in HDA channel parameters.

    Args:
        stage: USD stage to query

    Returns:
        Space-separated string of RenderVar prim paths, sorted by category
    """
    root = stage.GetPseudoRoot()
    vars_prim = root.GetPrimAtPath('/Render/Products/Vars')
    if not vars_prim.IsValid():
        return ''

    aovs = {
        aov_prim.GetName(): str(aov_prim.GetPath())
        for aov_prim in vars_prim.GetChildren()
        if aov_prim.GetTypeName() == 'RenderVar'
    }

    order = sort_aovs(aovs.keys())
    return ' '.join([aovs[aov] for aov in order])
