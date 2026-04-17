"""AOV (Arbitrary Output Variable) utilities for render layer management."""

from typing import Sequence


def sort_aovs(
    aovs: Sequence[str],
    include_combined: bool = False
) -> list[str]:
    """Sort AOVs by category: beauty, LPEs, mattes, others.

    Args:
        aovs: List of AOV names (case-insensitive matching)
        include_combined: If True, also treat 'c' as beauty and 'c_*' as LPEs
                         (used by puzzlemattes for combined AOVs)

    Returns:
        Sorted list with:
        1. 'beauty' or 'c' (if present, case-insensitive)
        2. 'beauty_*' or 'c_*' LPE variants
        3. 'objid_*' and 'holdout_*' (mattes)
        4. All other AOVs
    """
    beauty = None
    lpes = []
    mattes = []
    other = []

    beauty_names = {'beauty', 'c'} if include_combined else {'beauty'}
    lpe_prefixes = ('beauty_', 'c_') if include_combined else ('beauty_',)

    for aov in aovs:
        _aov = aov.lower()
        if _aov in beauty_names:
            beauty = aov
        elif _aov.startswith(lpe_prefixes):
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


def get_ordered_render_vars(stage, include_combined: bool = False) -> str:
    """Get space-separated ordered RenderVar paths from a USD stage.

    This is the full expression used in HDA channel parameters.

    Args:
        stage: USD stage to query
        include_combined: If True, also treat 'c' as beauty

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

    order = sort_aovs(aovs.keys(), include_combined=include_combined)
    return ' '.join([aovs[aov] for aov in order])
