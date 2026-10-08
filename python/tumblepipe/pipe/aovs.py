"""Which AOVs carry colour and which carry data.

A colour AOV (beauty, its light groups, albedo, the LPE splits) is an image a
person looks at, so lossy DWA compression is fine for it. Everything else is
data a comp reads numerically -- mattes, depth, normals, alpha, position --
and DWA's quantisation shows up there as soft matte edges and banded depth, so
those passes are written with lossless ZIP compression instead
(``DATA_AOV_COMPRESSION``). Unknown AOVs count as data: an unnecessarily
lossless colour pass costs disk, a lossy data pass costs a comp.

Hou-free and import-light on purpose: the plain-python farm tasks (denoise)
and ``pipe/usd.py`` both read it.
"""

# Lossless EXR compression for data passes, as husk and oiiotool spell it.
DATA_AOV_COMPRESSION = 'zip'

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
