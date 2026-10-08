"""Which AOVs carry colour and which carry data.

A colour AOV (beauty, its light groups, albedo, the LPE splits) is an image a
person looks at, so lossy DWA compression is fine for it. Everything else is
data a comp reads numerically -- mattes, depth, normals, alpha, position --
and DWA's quantisation shows up there as soft matte edges and banded depth.

This is the rule the render department's nodes ship their per-AOV
compression defaults by (``th::render_vars``, ``th::puzzlemattes``): ZIP for
data, DWAB for colour. The nodes own the setting -- an artist can change it
on the node, and nothing downstream rewrites it. Unknown AOVs count as data:
an unnecessarily lossless colour pass costs disk, a lossy data pass costs a
comp.
"""

# The compression defaults the render department's nodes ship with, in the
# spelling of their Compression menus (husk's OpenEXR compression tokens).
DATA_AOV_COMPRESSION = 'zip'
COLOR_AOV_COMPRESSION = 'dwab'

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


def default_compression(aov_name: str) -> str:
    """The compression a node defaults ``aov_name`` to."""
    return DATA_AOV_COMPRESSION if is_data_aov(aov_name) else COLOR_AOV_COMPRESSION
