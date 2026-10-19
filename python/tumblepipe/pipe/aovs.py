"""Which AOVs carry colour and which carry data.

A colour AOV (beauty, its light groups, albedo, the LPE splits) is an image a
person looks at, so lossy DWA compression is fine for it. Everything else is
data a comp reads numerically -- mattes, depth, normals, position --
and DWA's quantisation shows up there as soft matte edges and banded depth.

This is the rule the render department's nodes ship their per-AOV
compression defaults by (``th::render_vars``, ``th::mattes``): ZIP for
data, DWAB for colour. The nodes own the setting -- an artist can change it
on the node, and nothing downstream rewrites it. Unknown AOVs count as data:
an unnecessarily lossless colour pass costs disk, a lossy data pass costs a
comp.

It also names the mattes and distance ramps, which the farm's denoise
publishes untouched and ``build_comp`` imports as masks and mono passes.
"""

COLOR_AOV_NAMES = frozenset({
    'beauty',
    'albedo',
    'diffuse',
    'specular',
    'volume',
    'emission',
})
COLOR_AOV_PREFIXES = ('beauty_',)


def is_data_aov(aov_name: str) -> bool:
    """True when ``aov_name`` holds data rather than a viewable colour."""
    name = aov_name.lower()
    if name in COLOR_AOV_NAMES:
        return False
    if name.startswith(COLOR_AOV_PREFIXES):
        return False
    return True


# Mattes: masks a comp reads edge for edge. `objid_*` comes from th::mattes
# (one channel); `holdout_*` from a render layer's holdout setup.
MATTE_AOV_PREFIXES = ('objid_', 'holdout_')

# Distance ramps from th::mattes: one channel, 0 at the camera to 1 at the
# node's cap distance (sky = 1). A grading utility, not a picture.
RAMP_AOV_PREFIX = 'ramp_'


def is_matte_aov(aov_name: str) -> bool:
    """True for an ``objid_*`` / ``holdout_*`` matte."""
    return aov_name.lower().startswith(MATTE_AOV_PREFIXES)


def is_ramp_aov(aov_name: str) -> bool:
    """True for a ``ramp_*`` distance ramp."""
    return aov_name.lower().startswith(RAMP_AOV_PREFIX)


def is_edit_aov(aov_name: str) -> bool:
    """True for the AOVs *Copy to edit* syncs into the edit folder: the
    beauty, the mattes and the distance ramps -- what the grade reads."""
    if aov_name.lower() == 'beauty': return True
    if is_matte_aov(aov_name): return True
    if is_ramp_aov(aov_name): return True
    return False
