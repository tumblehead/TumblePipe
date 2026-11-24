"""The comp workflow's pure half: what the comp nodes and the composite job
decide, with no Houdini in sight.

A shot's comp is built by the composite department template from three COP
nodes (``th::import_render``, ``th::light_mix``, ``th::export_comp``; see
``docs/compositing.md``). Everything those nodes and the composite job
*decide* lives here, so it can be tested headless:

- which render an ``import_render`` reads (:func:`resolve_render`) and which
  AOVs it carries in its cable (:func:`aov_included`, :func:`cable_fields`);
- what stops a render being comped (:func:`render_problems`);
- the light mix's rows (:func:`light_groups`, :func:`sync_rows`);
- what a submit freezes (:func:`freeze_plan`) and records
  (:func:`provenance`);
- whether a comp of a check render goes to the farm and which frames it
  holds (:func:`check_render_refusal`, :func:`held_frames`), whether a
  comp is a check comp (:func:`is_check_comp`), and whether a comp version a daily or Copy to
  edit follows turned out unpublished when they run
  (:func:`unpublished_reason`);
- which shot channels no import reads (:func:`unread_channels`);
- the composite version a submit writes (:func:`next_version_name`);
- the template's layout (:func:`template_layout`).

The Houdini halves are ``pipe/houdini/cops/{import_render,light_mix,
export_comp}.py``; the farm half is ``farm/pipelines/composite.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from tumblepipe import naming
from tumblepipe.pipe.aovs import is_cryptomatte_aov, is_data_aov, is_matte_aov, is_ramp_aov

# The render department a comp writes, and the cross-channel layer of a
# render department's version (the render chain's slapcomp; the composite
# department's merged comp).
COMPOSITE_DEPARTMENT = 'composite'
MERGED_FOLDER = 'slapcomp'

# export_comp's Layer token for the merged comp (every channel over-merged).
MERGED_LAYER = 'comp'

# import_render's Department and Version tokens.
BEST = 'best'
LATEST = 'latest'
# import_render's Channel token for every channel of the shot in one node.
ALL_CHANNELS = '*'

# The AOV every channel's comp needs: the RGBA beauty, whose A is the
# channel's alpha.
BEAUTY = 'beauty'
LIGHT_GROUP_PREFIX = 'beauty_'


class CompError(Exception):
    """A comp cannot be made from what is on disk; the message says why."""


class NotRendered(CompError):
    """A channel has no render a comp can read in the departments asked
    (yet), neither complete nor a check render: an all-channels import
    leaves such a channel out instead of failing."""


###############################################################################
# AOVs
###############################################################################
# The LPE splits of the beauty a comp reads beside its light groups, on
# import_render's lpe output (Antoine, 2026-11-22): every AOV named
# ``<lobe>_<x>`` for a lobe of LPE_PREFIXES and any suffix ``x``, a light
# (``diffuse_key``, ``specular_fill``, ``diffuse_rimLight``: th::lpe_tags
# makes one per tag, whatever the lights are called; 2026-11-23, after
# diffuse_fill and specular_fill landed on extra) or a path split
# (``diffuse_direct``, ``specular_indirect``). Plain ``diffuse`` and
# ``specular``, with no suffix, stay extras (a decision, 2026-11-22). A
# light mix's rows stay the light groups alone (:func:`light_groups`).
LPE_PREFIXES = (LIGHT_GROUP_PREFIX, 'diffuse_', 'specular_')
# Examples of the splits (a key/dome rig's and th::render_vars'); the rule
# is LPE_PREFIXES, not this list.
LPE_AOV_NAMES = (
    'diffuse_key', 'specular_key', 'diffuse_dome', 'specular_dome',
    'diffuse_direct', 'specular_direct', 'diffuse_indirect', 'specular_indirect',
)
# The geometry and camera data a comp reads on the aov output.
DATA_AOV_NAMES = ('albedo', 'normal', 'depth', 'uv', 'position')
# Never read: the comp's alpha is the beauty's, and a variance pass
# (``<aov>_mse``) is the denoiser's input, not a picture.
SKIPPED_AOV_NAMES = ('alpha',)
VARIANCE_SUFFIX = '_mse'


def aov_included(aov_name: str) -> bool:
    """Whether a comp reads an AOV at all (it rides one of import_render's
    cables, :func:`aov_cable`): every AOV but an ``alpha`` and the variance
    passes. An AOV the comp has no cable of its own for is an *extra*
    (transmission, sss, emission, shadow, ...)."""
    name = aov_name.lower()
    if name in SKIPPED_AOV_NAMES: return False
    if name.endswith(VARIANCE_SUFFIX): return False
    return True


def is_lpe_aov(aov_name: str) -> bool:
    """Whether an AOV rides import_render's lpe output: a light group
    (``beauty_<tag>``) or an LPE split (``diffuse_<x>``, ``specular_<x>``)
    for any suffix ``x``, in any case (:data:`LPE_PREFIXES`). A prefix
    alone (``beauty_``, ``diffuse_``) is not one."""
    name = aov_name.lower()
    return any(name.startswith(prefix) and len(name) > len(prefix)
               for prefix in LPE_PREFIXES)


def aov_signature(aov_name: str) -> str:
    """The COP data type an AOV is read as (a File COP's ``type#`` token).

    The beauty is RGBA (its A is the channel's alpha); the LPEs, albedo,
    normals and position RGB; depth one channel (Mono: Antoine, 2026-11-22;
    a denoised depth is three equal channels, so Mono reads the same
    value); uv a vector2; mattes, holdouts and ramps one channel; a
    cryptomatte's rank planes RGBA (two id/coverage pairs each, see
    :func:`expand_cryptomattes`); any other AOV (an extra) RGB.
    """
    name = aov_name.lower()
    if not aov_included(name):
        raise ValueError(f'the comp reads no AOV named {aov_name!r}')
    if name == BEAUTY: return 'vector4'
    if is_cryptomatte_aov(name): return 'vector4'
    if is_lpe_aov(name): return 'vector'
    if name.startswith('objid_'): return 'float'
    if name.startswith('holdout_'): return 'float'
    if is_ramp_aov(name): return 'float'
    if name == 'depth': return 'float'
    if name == 'uv': return 'vector2'
    return 'vector'


def is_colour_aov(aov_name: str) -> bool:
    """Whether an AOV is colour, read in the scene's colour space: the
    beauty, the LPEs (:func:`is_lpe_aov`) and the extras ``pipe.aovs``
    names colour (emission, transmission, sss, shadow, ...). Everything
    else is data, read raw (albedo too, as ever)."""
    name = aov_name.lower()
    if name == BEAUTY or is_lpe_aov(name): return True
    return aov_cable(name) == EXTRAS and not is_data_aov(name)


@dataclass(frozen=True)
class CryptomatteRank:
    """One rank plane of a rendered cryptomatte AOV (``CryptoMaterials01``),
    which rides import_render's cable as a field of its own.

    A cryptomatte is one file (apps/exr.cryptomattes) holding a preview and
    its rank planes; COPs' Cryptomatte node takes the ranks on its
    ``crypto1``, ``crypto2``, ... inputs and the manifest from their
    metadata, which a cable carries. Stands in for the AOV: same file, a
    plane of it.
    """
    aov: object
    plane: str

    @property
    def label(self) -> str:
        return self.plane.lower()

    def get_frame_range(self):
        return self.aov.get_frame_range()

    def get_aov_frame_path(self, frame):
        return self.aov.get_aov_frame_path(frame)


def cryptomatte_rank_planes(aov_label: str, channel_names: Iterable[str]) -> list[str]:
    """The rank planes of the cryptomatte ``aov_label`` among its file's
    channel names (``CryptoMaterials00.R`` -> ``CryptoMaterials00``), in
    rank order. Case-blind against the label, the plane keeps its case."""
    base = aov_label.lower()
    planes = set()
    for channel in channel_names:
        plane = channel.rsplit('.', 1)[0] if '.' in channel else ''
        suffix = plane[len(base):]
        if plane.lower().startswith(base) and len(suffix) == 2 and suffix.isdigit():
            planes.add(plane)
    return sorted(planes, key=lambda plane: int(plane[-2:]))


def expand_cryptomattes(aovs: Mapping, channel_names=None) -> dict:
    """``aovs`` (``{label: AOV}``) with each cryptomatte replaced by its rank
    planes, ``{label + NN: CryptomatteRank}``. A cryptomatte whose header
    can't be read, or has no rank, drops out (nothing to comp from it).
    ``channel_names`` reads an AOV's EXR channel names (swapped in tests)."""
    channel_names = channel_names or aov_channel_names
    expanded = dict()
    for label, aov in aovs.items():
        if not is_cryptomatte_aov(label):
            expanded[label] = aov
            continue
        for plane in cryptomatte_rank_planes(label, channel_names(aov) or []):
            rank = CryptomatteRank(aov, plane)
            expanded[rank.label] = rank
    return expanded


def cable_fields(aov_labels: Iterable[str]) -> list[str]:
    """The fields of import_render's ``aovs`` cable for a version's AOVs:
    the included ones but the beauty, by name. Unique.

    The beauty is the node's ``rgba`` output, so the cable does not carry it
    a second time (a light mix takes it on its own ``rgba`` input)."""
    return sorted({
        label for label in aov_labels
        if aov_included(label) and label != BEAUTY
    })


def file_fields(aov_labels: Iterable[str]) -> list[str]:
    """Every AOV an import_render reads from disk: the beauty first (when
    there is one), then the cable's fields."""
    labels = list(aov_labels)
    beauty = [BEAUTY] if BEAUTY in labels else []
    return beauty + cable_fields(labels)


def layer_field(channel: str, field: str) -> str:
    """A field of an all-channels import: a channel's beauty is named after
    the channel (in the ``beauties`` cable), its AOVs ``<channel>_<aov>``
    (in the ``aovs`` cable)."""
    return channel if field == BEAUTY else f'{channel}_{field}'


def all_channels_fields(fields_by_channel: Mapping[str, Iterable[str]]) -> list[str]:
    """The ``aovs`` cable of an all-channels import (Channel ``*``): per
    channel, in the order given (the shot's), its AOVs, each named by
    :func:`layer_field`. No beauty: the beauties ride the ``beauties`` cable
    (Remy, 2026-11-10: a simple comp should not dig them out of the AOVs).
    Raises :class:`CompError` when two names collide (channel ``a`` with AOV
    ``b_c`` and channel ``a_b`` with AOV ``c``)."""
    fields: list[str] = []
    owner: dict[str, str] = dict()
    for channel, channel_fields in fields_by_channel.items():
        for field in [f for f in channel_fields if f != BEAUTY]:
            name = layer_field(channel, field)
            if name in owner:
                raise CompError(
                    f'channels "{owner[name]}" and "{channel}" both give a '
                    f'cable field "{name}"; read them with one import each'
                )
            owner[name] = channel
            fields.append(name)
    return fields


def light_groups(fields: Iterable[str]) -> list[str]:
    """The light groups among a cable's fields (``beauty_<tag>``), in order."""
    return [
        field for field in fields
        if field.lower().startswith(LIGHT_GROUP_PREFIX)
        and len(field) > len(LIGHT_GROUP_PREFIX)
    ]


# The cables an import_render splits its AOVs over (#67, Remy): the light
# groups a light mix grades, the mattes a grade keys on, the rest, and a
# cable per cryptomatte th::render_vars renders. A Cryptomatte COP reads
# every field of the cable on its crypto1 and needs all of one var's rank
# planes (each holds two ranks: only the first plane gave a mask off by up
# to 0.44, H22.0.429), and Houdini refuses nested cables: so each var's
# planes ride a cable of their own, whole, ready for crypto1.
LPES = 'lpes'
MATTES = 'mattes'
AOVS = 'aovs'
CRYPTOMATERIALS = 'cryptomaterials'
CRYPTOPRIMITIVES = 'cryptoprimitives'
CRYPTO_CABLES = (CRYPTOMATERIALS, CRYPTOPRIMITIVES)
# Every AOV no other cable takes (Antoine, 2026-11-22): 2.0's extra output,
# its last (appended, so a wire on the others stays put). A 1.0 node has no
# such output and reads none of them (:func:`dive_plan`'s ``extras``).
EXTRAS = 'extras'
AOV_CABLES = (LPES, MATTES, AOVS) + CRYPTO_CABLES + (EXTRAS,)


def aov_cable(aov_name: str) -> str:
    """Which of import_render's cables an AOV it reads rides.

    ``lpes``: a light group, ``beauty_<tag>`` (th::lpe_tags names an LPE
    tag's AOV so; what :func:`light_groups` finds and a light mix grades),
    and the LPE splits, ``diffuse_<x>`` and ``specular_<x>`` for any ``x``
    (``diffuse_key``, ``specular_fill``, ``diffuse_direct``, ...:
    :func:`is_lpe_aov`).
    ``cryptomaterials`` / ``cryptoprimitives``: the rank planes of that
    cryptomatte (``cryptomaterials00``, ``01``, ...). ``mattes``: what
    th::mattes renders (``objid_*`` mattes, ``ramp_*`` distance ramps),
    ``holdout_*`` holdouts and any other cryptomatte's planes. ``aovs``:
    the geometry and camera data, :data:`DATA_AOV_NAMES` (albedo, normal,
    depth, uv, position). ``extras``: everything else (transmission, sss,
    emission, shadow, motion vectors, ...). The beauty rides none: it is on
    the channel's output. Case never matters.
    """
    name = aov_name.lower()
    if is_lpe_aov(name):
        return LPES
    if is_cryptomatte_aov(name):
        var = name.rstrip('0123456789')
        if var in CRYPTO_CABLES:
            return var
        return MATTES
    if is_matte_aov(name) or is_ramp_aov(name):
        return MATTES
    if name in DATA_AOV_NAMES:
        return AOVS
    return EXTRAS


###############################################################################
# What stops a render being comped
###############################################################################
def beauty_alpha_problems(beauty_channel_names: Mapping) -> list[str]:
    """What stops the comp taking each channel's alpha from its beauty.

    ``beauty_channel_names`` maps a channel name to its beauty's EXR channel
    names, None when the header could not be read. The comp's alpha is the
    beauty's A, so a beauty without one (an RGB beauty, rendered before
    beauty went RGBA) cannot be comped. Empty when nothing stops it.
    """
    from tumblepipe.apps import exr
    problems = []
    for channel_name, channel_names in beauty_channel_names.items():
        if channel_names is None:
            problems.append(
                f'the beauty of channel "{channel_name}" could not be read'
            )
            continue
        if exr.alpha_channel_index(channel_names) is None:
            problems.append(
                f'the beauty of channel "{channel_name}" has no alpha '
                'channel (an RGB beauty; re-render it)'
            )
    return problems


def matte_channel_problems(matte_channel_names: Mapping) -> list[str]:
    """What stops the comp reading each ``objid_*`` matte as one mask.

    ``matte_channel_names`` maps ``(channel name, AOV label)`` to the matte's
    EXR channel names, None when the header could not be read. A matte is
    one channel (th::mattes); a three-channel puzzle matte, rendered by the
    th::puzzlemattes node th::mattes replaced, cannot be comped. Empty when
    nothing stops it.
    """
    problems = []
    for (channel_name, aov_label), channel_names in matte_channel_names.items():
        if channel_names is None:
            problems.append(
                f'the {aov_label} matte of channel "{channel_name}" could '
                'not be read'
            )
            continue
        if len(channel_names) != 1:
            problems.append(
                f'the {aov_label} matte of channel "{channel_name}" has '
                f'{len(channel_names)} channels, not one (a puzzle matte; '
                're-render it with th::mattes)'
            )
    return problems


# (frame path, mtime) -> channel names. A resolve reads each AOV's header
# more than once (the checks, the plane name), on a network share.
_CHANNEL_NAMES_CACHE: dict = dict()


def aov_channel_names(aov) -> Optional[list[str]]:
    """Channel names of a rendered AOV, from its first frame's EXR header.

    Every frame of a render shares one layout, so one header is enough. None
    when it can't be read. Cached per file and modification time.
    """
    from tumblepipe.api import path_str
    from tumblepipe.apps import exr
    render_range = aov.get_frame_range()
    if render_range is None: return None
    frame_path = aov.get_aov_frame_path(str(render_range.first_frame).zfill(4))
    if frame_path is None: return None
    try:
        mtime = frame_path.stat().st_mtime_ns
    except OSError:
        return None
    key = (path_str(frame_path), mtime)
    if key not in _CHANNEL_NAMES_CACHE:
        _CHANNEL_NAMES_CACHE[key] = exr.read_channel_names(frame_path) or None
    return _CHANNEL_NAMES_CACHE[key]


def aov_plane_name(aov) -> str:
    """The plane name a File COP finds this AOV's channels under: the AOV's
    label for a raw render (``beauty.R``), ``C`` for the denoise's bare
    ``R,G,B(,A)``."""
    from tumblepipe.apps import exr
    if isinstance(aov, CryptomatteRank): return aov.plane
    return exr.cop_plane_name(aov_channel_names(aov), aov.label)


def render_problems(aovs_by_channel: Mapping, channel_names=aov_channel_names) -> list[str]:
    """What in the rendered files stops the comp: a missing beauty, beauties
    without an alpha, mattes that are not one channel.

    ``aovs_by_channel`` maps a channel to ``{label: AOV}``; ``channel_names``
    reads an AOV's EXR channel names (swapped out in tests).
    """
    problems = [
        f'channel "{channel_name}" has no {BEAUTY} AOV'
        for channel_name, aovs in aovs_by_channel.items()
        if BEAUTY not in aovs
    ]
    beauties = {
        channel_name: channel_names(aovs[BEAUTY])
        for channel_name, aovs in aovs_by_channel.items()
        if BEAUTY in aovs
    }
    mattes = {
        (channel_name, aov.label): channel_names(aov)
        for channel_name, aovs in aovs_by_channel.items()
        for aov in aovs.values()
        if aov.label.lower().startswith('objid_')
    }
    return problems + beauty_alpha_problems(beauties) + matte_channel_problems(mattes)


###############################################################################
# Which render an import reads
###############################################################################
def readable_departments(render_department_names: Iterable[str]) -> list[str]:
    """The render departments a comp may read, in pipeline order: those
    below ``composite``, so a comp never reads itself. All of them when the
    project has no composite render department."""
    names = list(render_department_names)
    if COMPOSITE_DEPARTMENT in names:
        return names[:names.index(COMPOSITE_DEPARTMENT)]
    return names


@dataclass(frozen=True)
class Resolved:
    """What an ``import_render`` reads: one department's version of one
    channel, and the AOVs of it the comp carries (``{label: AOV}``)."""
    channel: str
    department: str
    version: str
    aovs: dict
    # The version's step when it is a check render (:func:`check_step_of`):
    # a version picked by hand, or ``latest`` when no department asked has
    # a complete version (:func:`resolve_render`).
    check_step: Optional[int] = None
    # ``latest`` read a complete version, but the department has a newer
    # check render: ``(version, step)``, which the label names (the
    # Version menu offers it by hand).
    newer_check: Optional[tuple[str, int]] = None
    # A check render's recorded ``(first, last)``: with ``check_step``, the
    # frames it has (:func:`check_frames`): a comp of it holds the frames
    # between, and its first and last frame before and after the range
    # (:func:`held_frames`).
    check_range: Optional[tuple[int, int]] = None

    @property
    def fields(self) -> list[str]:
        """The ``aovs`` cable's fields (no beauty: it is ``rgba``)."""
        return cable_fields(self.aovs)

    @property
    def file_fields(self) -> list[str]:
        """Every AOV read from disk, the beauty first."""
        return file_fields(self.aovs)

    @property
    def label(self) -> str:
        version = self.version if self.check_step is None else \
            check_version_label(self.version, self.check_step)
        label = f'{self.department} {version}, {len(self.fields)} AOVs'
        if self.newer_check is not None:
            newer = check_version_label(*self.newer_check)
            label += f'; newer check render {newer} not read (pick it in Version)'
        return label


def _complete_versions(render, channel: str) -> list[str]:
    """The complete versions of ``channel`` in ``render``, newest first."""
    if render is None or channel not in render.layers: return []
    layers = render.layers[channel]
    return [
        version_name
        for version_name in sorted(layers, key=naming.get_version_code, reverse=True)
        if layers[version_name].is_complete()
    ]


def list_versions(renders: Mapping, department: str, channel: str) -> list[str]:
    """The complete versions of ``channel`` a department has, newest first."""
    return _complete_versions(renders.get(department), channel)


def check_step_of(layer) -> Optional[int]:
    """The step of ``layer`` when it is a check render a comp can read: not
    published (Farm Submit's **Step**), and every frame it records on its
    step on disk. None for anything else.

    A check render is never a *complete* version (:func:`list_versions`).
    ``latest`` reads one only when there is no complete version to read
    (:func:`resolve_render`); an import pinned to one by hand reads it too.
    Either way it holds each rendered frame over the gaps
    (``pipe.hold_frames``).
    """
    is_published = getattr(layer, 'is_published', None)
    if is_published is None or is_published(): return None
    frame_range = layer.get_frame_range()
    if frame_range is None or not layer.is_complete(frame_range): return None
    return frame_range.step_size


def list_check_versions(renders: Mapping, department: str, channel: str) -> list[tuple[str, int]]:
    """The check renders of ``channel`` a department has that a comp can
    read by hand, newest first: ``(version, step)``."""
    render = renders.get(department)
    if render is None or channel not in render.layers: return []
    layers = render.layers[channel]
    found = []
    for version_name in sorted(layers, key=naming.get_version_code, reverse=True):
        step = check_step_of(layers[version_name])
        if step is not None:
            found.append((version_name, step))
    return found


def check_version_label(version: str, step: int) -> str:
    """A check render in the Version menu: ``v0014 (check, every 5th)``."""
    from tumblepipe.pipe.hold_frames import _ordinal
    return f'{version} (check, every {_ordinal(step)})' if step > 1 else f'{version} (check)'


def version_items(complete: Iterable[str], checks: Iterable[tuple[str, int]]) -> list[tuple[str, str]]:
    """The versions an import's Version menu offers under ``latest``, as
    ``(token, label)``, newest first: the complete ones by name, the check
    renders labelled (:func:`check_version_label`)."""
    labels = {version: version for version in complete}
    for version, step in checks:
        labels.setdefault(version, check_version_label(version, step))
    return [
        (version, labels[version])
        for version in sorted(labels, key=naming.get_version_code, reverse=True)
    ]


@dataclass(frozen=True)
class Newest:
    """A department's newest complete version of a channel, and where it
    came from: ``submitted``, when its ``context.json`` was written (at
    submit, so submission order); ``source``, the ``(department, version)``
    it was made from, when it records one (a denoise of a render)."""
    department: str
    version: str
    submitted: Optional[float] = None
    source: Optional[tuple[str, str]] = None


def is_stale(higher: Newest, lower: Newest) -> bool:
    """Whether ``lower``'s newest version supersedes ``higher``'s: ``higher``
    was not made from it, and ``lower`` is the newer one.

    A denoise of render v0003 is stale once render v0005 is rendered with
    Denoise off: best read the old denoise over the new render, so a comp
    came out denoised although its render was not (wifi_retina, 2026-11-10).
    """
    if higher.source is not None and higher.source[0] == lower.department:
        source_version = higher.source[1]
        return (source_version != lower.version
                and naming.get_version_code(lower.version)
                > naming.get_version_code(source_version))
    if higher.submitted is None or lower.submitted is None:
        return False
    return lower.submitted > higher.submitted


def best_departments(newest: Sequence[Newest]) -> list[str]:
    """``newest`` (highest department first, each with a version) without
    the departments a lower one supersedes (:func:`is_stale`): ``best``
    reads the first that is left."""
    return [
        entry.department for index, entry in enumerate(newest)
        if not any(is_stale(entry, lower) for lower in newest[index + 1:])
    ]


def layer_newest(department: str, version: str, layer, channel: str) -> Newest:
    """``layer`` (``department``'s ``version`` of ``channel``) as a
    :class:`Newest`, its provenance read from its ``context.json``."""
    submitted, source = None, None
    path = getattr(layer, 'path', None)
    if path is not None:
        context_path = Path(path) / 'context.json'
        try:
            submitted = context_path.stat().st_mtime
            reads = (json.loads(context_path.read_text(encoding='utf-8')) or {}).get('reads') or {}
            read = reads.get(channel)
            if read and read.get('department') and read.get('version'):
                source = (read['department'], read['version'])
        except (OSError, ValueError, AttributeError):
            pass
    return Newest(department, version, submitted, source)


def _check_names(renders: Mapping, department: str, channel: str) -> list[str]:
    """The check renders of ``channel`` a department has, newest first, by
    name (:func:`list_check_versions`)."""
    return [version for version, _ in list_check_versions(renders, department, channel)]


def _newest(renders: Mapping, department: str, channel: str,
            versions_of=list_versions) -> Optional[Newest]:
    versions = versions_of(renders, department, channel)
    if not versions: return None
    layer = renders[department].layers[channel][versions[0]]
    return layer_newest(department, versions[0], layer, channel)


def _fresh_candidates(renders: Mapping, candidates: list[str], channel: str,
                      versions_of=list_versions) -> list[str]:
    """``candidates`` (highest first) without the departments whose newest
    version (of those ``versions_of`` lists) a lower department's
    supersedes."""
    newest = [n for n in (_newest(renders, d, channel, versions_of) for d in candidates) if n]
    fresh = set(best_departments(newest))
    return [d for d in candidates if d in fresh or d not in {n.department for n in newest}]


def _read(renders: Mapping, channel: str, department: str, version: str,
          check_step: Optional[int] = None,
          newer_check: Optional[tuple[str, int]] = None) -> Resolved:
    """:class:`Resolved` for ``department``'s ``version`` of ``channel``; a
    check render's (``check_step``) with the range it records."""
    layer = renders[department].layers[channel][version]
    check_range = None
    if check_step is not None:
        frame_range = layer.get_frame_range()
        if frame_range is not None:
            check_range = (frame_range.first_frame, frame_range.last_frame)
    return Resolved(
        channel = channel,
        department = department,
        version = version,
        aovs = expand_cryptomattes({
            label: aov for label, aov in layer.aovs.items()
            if aov_included(label)
        }),
        check_step = check_step,
        newer_check = newer_check,
        check_range = check_range,
    )


def resolve_render(
    renders: Mapping,
    render_department_names: Iterable[str],
    channel: str,
    department: str = BEST,
    version: str = LATEST,
    ) -> Resolved:
    """The render an import_render reads, or :class:`CompError` saying why
    there is none.

    ``renders`` maps a render department to its ``Render`` (missing when it
    never rendered); ``render_department_names`` is the project's render
    departments in pipeline order. ``department`` is ``best`` (the highest
    readable department with a version to read) or one readable department;
    ``version`` is ``latest`` or a ``v####`` of it, which must be complete
    or a check render. A pin needs a concrete department, so ``best`` with
    a pin is refused.

    ``latest`` is the newest *complete* version, of the highest department
    that has one (``best``). Only when no department asked has a complete
    version does it read the newest check render (Farm Submit's **Step**:
    every Nth frame, whole on its step), its department picked the same
    way, and the import holds its frames over the gaps (Antoine,
    2026-11-22: a stepped first render read as nothing at all). A complete
    render is preferred over a newer check render: a check render is a
    look, never the shot's comp (a comp of one goes to the farm on any
    frames, :func:`check_render_refusal`, and is a check comp, never
    published, :func:`is_check_comp`), so it never takes ``latest`` from a
    finished render; the label names the newer check render instead
    (:attr:`Resolved.newer_check`), which the Version menu offers by hand.
    """
    readable = readable_departments(render_department_names)
    if department == BEST:
        if version != LATEST:
            raise CompError(
                f'version {version} is pinned, but the department is '
                f'"{BEST}": pick the department the version belongs to'
            )
        candidates = list(reversed(readable))
    else:
        if department not in readable:
            raise CompError(
                f'"{department}" is not a render department a comp can read '
                f'({", ".join(readable) or "none"})'
            )
        candidates = [department]
    if version != LATEST:
        (candidate,) = candidates
        if version in list_versions(renders, candidate, channel):
            return _read(renders, channel, candidate, version)
        # Picked by hand, a check render is read too (its frames held over
        # its gaps).
        check_step = dict(list_check_versions(renders, candidate, channel)).get(version)
        if check_step is None:
            raise CompError(
                f'{candidate} {version} of channel "{channel}" is not a '
                'complete render'
            )
        return _read(renders, channel, candidate, version, check_step)
    complete, checks = candidates, candidates
    if department == BEST:
        complete = _fresh_candidates(renders, candidates, channel)
        checks = _fresh_candidates(renders, candidates, channel, _check_names)
    for candidate in complete:
        versions = list_versions(renders, candidate, channel)
        if not versions: continue
        newer = [
            (name, step) for name, step in list_check_versions(renders, candidate, channel)
            if naming.get_version_code(name) > naming.get_version_code(versions[0])
        ]
        return _read(renders, channel, candidate, versions[0],
                     newer_check = newer[0] if newer else None)
    for candidate in checks:
        found = list_check_versions(renders, candidate, channel)
        if not found: continue
        name, step = found[0]
        return _read(renders, channel, candidate, name, step)
    where = ' or '.join(reversed(candidates)) or 'any render department'
    raise NotRendered(
        f'channel "{channel}" has no complete render in {where} yet '
        '(nor a check render)'
    )


def resolve_channels(
    renders: Mapping,
    render_department_names: Iterable[str],
    channels: Iterable[str],
    department: str = BEST,
    version: str = LATEST,
    pins: Optional[Mapping[str, tuple[str, str]]] = None,
    ) -> tuple[dict[str, Resolved], list[str]]:
    """What an all-channels import reads: ``({channel: Resolved}, channels
    left out)``, in ``channels``' order.

    Every channel resolves on ``department`` at ``latest``; one with no
    complete render there (:class:`NotRendered`) is left out, unless the
    farm pinned it (``pins``: ``{channel: (department, version)}``), which
    must resolve. Any other refusal is raised, and so is a version pin (each
    channel has versions of its own) and nothing rendered at all.
    """
    if version != LATEST:
        raise CompError(
            f'version {version} is pinned, but the node reads all channels, '
            'which have versions of their own: read the channel with an '
            'import of its own to pin it'
        )
    pins = pins or {}
    departments = list(render_department_names)
    reads: dict[str, Resolved] = dict()
    skipped: list[str] = []
    first_skip: Optional[NotRendered] = None
    for channel in channels:
        if channel in pins:
            reads[channel] = resolve_render(renders, departments, channel, *pins[channel])
            continue
        try:
            reads[channel] = resolve_render(renders, departments, channel, department, version)
        except NotRendered as error:
            skipped.append(channel)
            first_skip = first_skip or error
    if not reads:
        raise first_skip or CompError('the shot has no channels')
    all_channels_fields({c: r.fields for c, r in reads.items()})
    return reads, skipped


###############################################################################
# A comp of a check render (Antoine, 2026-11-22; Soren, 2026-11-23)
###############################################################################
# A comp's frames: ``(first, last, step)``, every ``step``-th frame from
# ``first`` up to ``last`` (export_comp's Frame Range and Step; a re-comp's
# frames and Step). A check render's are its recorded range on its step.
Frames = tuple[int, int, int]


def frames_of(frames: Frames) -> list[int]:
    """Every frame of ``frames``, in order."""
    first, last, step = frames
    return list(range(first, last + 1, max(1, step)))


def last_frame_of(frames: Frames) -> int:
    """The last frame ``frames`` renders: ``last`` itself only when it is
    on the step."""
    first, last, step = frames
    step = max(1, step)
    return first + (last - first) // step * step


def frames_in_range(frames: Frames, within: Frames) -> bool:
    """Whether the range of ``frames``, first to last, lies inside
    ``within``'s, on its step or not (a frame between two of ``within``'s
    is held from the nearer, ``pipe.hold_frames``). The range, not only
    the frames on its step, as Farm Submit keeps a comp inside the render
    beside it (``farm.specs.submission.comp_frame_problems``). An empty
    range is inside anything."""
    first, last, _ = frames
    if first > last: return True
    return within[0] <= first and last <= within[1]


def check_frames(read: Resolved) -> Optional[Frames]:
    """The frames a check render read has, ``(first, last, step)``; None
    for a complete read, or a check render whose range is not known."""
    if read.check_step is None or read.check_range is None: return None
    return read.check_range[0], read.check_range[1], read.check_step


def frames_held(frames: Frames, checks: Sequence[Frames]) -> list[int]:
    """The frames of ``frames`` some check render of ``checks`` does not
    have, in order: a comp shows each held from the nearest frame the check
    render has (``pipe.hold_frames.held_frame``), its first frame before
    its range and its last after it."""
    has = [set(frames_of(check)) for check in checks]
    return [
        frame for frame in frames_of(frames)
        if any(frame not in frames_has for frames_has in has)
    ]


def held_frames(reads: Iterable[Resolved], frames: Frames) -> list[int]:
    """:func:`frames_held` of the check renders among ``reads`` whose
    frames are known (:func:`check_frames`)."""
    checks = [check_frames(read) for read in reads]
    return frames_held(frames, [check for check in checks if check is not None])


def comp_frames_text(frames: Frames) -> str:
    """A comp's frames in a message: ``1001-1122, every frame``, ``1001-1122,
    every 2nd frame``."""
    from tumblepipe.pipe.hold_frames import _ordinal
    first, last, step = frames
    every = 'every frame' if step <= 1 else f'every {_ordinal(step)} frame'
    return f'{first}-{last}, {every}'


#: What a comp frame a check render lacks shows, for :func:`held_text`:
#: held from the nearest rendered frame; black, its import's Missing Frames
#: on Empty (``pipe.hold_frames.holds``); or, where the imports are not
#: known (Farm Submit never opens a re-comp's workfile), either.
HELD = 'held'
BLACK = 'black (Missing Frames: Empty)'
HELD_UNLESS_EMPTY = 'held (black on an import set to Missing Frames: Empty)'


def held_text(frames: Frames, checks: Sequence[Frames], shows: str = HELD) -> str:
    """What a comp of ``frames`` holds of the check renders ``checks``, for
    a warning (Soren, 2026-11-23): ``every frame of 1001-1122 from a render
    every 2nd frame: 61 frames held``. Empty when every comp frame is one
    each check render has. ``shows`` says what those frames show
    (:data:`HELD`, :data:`BLACK`, :data:`HELD_UNLESS_EMPTY`)."""
    from tumblepipe.pipe.hold_frames import _ordinal
    held = frames_held(frames, checks)
    if not held: return ''
    first, last, step = frames
    every = 'every frame' if step <= 1 else f'every {_ordinal(step)} frame'
    steps = sorted({
        max(1, check[2]) for check in checks
        if not set(frames_of(frames)) <= set(frames_of(check))
    })
    named = [f'every {_ordinal(each)} frame' if each > 1 else 'every frame' for each in steps]
    source = (
        f'a render {named[0]}' if len(named) == 1
        else f'renders {", ".join(named[:-1])} and {named[-1]}'
    )
    noun = 'frame' if len(held) == 1 else 'frames'
    return f'{every} of {first}-{last} from {source}: {len(held)} {noun} {shows}'


def held_note(reads: Iterable[Resolved], frames: Frames, shows: str = HELD) -> str:
    """:func:`held_text` of the check renders among ``reads`` whose frames
    are known."""
    checks = [check_frames(read) for read in reads]
    return held_text(frames, [check for check in checks if check is not None], shows)


def missing_note(reads: Iterable[tuple[str, Resolved]], frames: Frames) -> str:
    """What a comp of ``frames`` shows where the check renders it reads
    lack a frame, ``reads`` being ``(Missing Frames, read)`` of each
    import's reads: :func:`held_note` of the reads their import holds
    (``pipe.hold_frames.holds``), and of those it leaves empty, black
    (:data:`BLACK`), ``; `` between. Empty when no check render lacks a
    comp frame. The import's File COPs read exactly that
    (``cops.import_render.held_ranges``)."""
    from tumblepipe.pipe.hold_frames import holds
    holding, empty = [], []
    for setting, read in reads:
        (holding if holds(setting, read.check_step) else empty).append(read)
    notes = [held_note(holding, frames), held_note(empty, frames, BLACK)]
    return '; '.join(note for note in notes if note)


def _check_read_text(read: Resolved) -> str:
    label = check_version_label(f'{read.department} {read.version}', read.check_step)
    if read.check_range is not None:
        label += f' over {read.check_range[0]}-{read.check_range[1]}'
    return f'{read.channel}: {label}'


def check_render_refusal(reads: Iterable[Resolved], frames: Optional[Frames] = None) -> Optional[str]:
    """Why a comp of ``frames`` reading ``reads`` is not sent to the farm,
    None when it is.

    A check render has only every Nth frame. A comp of one goes to the
    farm on any frames (Soren, 2026-11-23: a Step 1 comp of a Step 2
    render; 2026-11-24: a 185-frame comp of a one-frame check render): a
    frame the check render lacks is held from its nearest rendered frame,
    its first before its range and its last after it, as th::import_render
    holds it in the session (:func:`held_frames`, which a Submit warns of;
    black on an import set to Missing Frames: Empty, :func:`missing_note`).
    Such a comp is a check comp, never published (:func:`is_check_comp`).
    A comp of a check render whose range is not known is refused, and so
    is any check render when ``frames`` is None (not known). A complete
    read never refuses.
    """
    checks = [read for read in reads if read.check_step is not None]
    if not checks: return None
    full = 'render the channel in full (Step 1), or pick a published version'
    if frames is None:
        named = ', '.join(_check_read_text(read) for read in checks)
        return (
            f'reads a check render ({named}), and the frames the comp renders '
            f'are not known: {full}'
        )
    unknown = [read for read in checks if read.check_range is None]
    if unknown:
        named = ', '.join(_check_read_text(read) for read in unknown)
        noun = 'check render' if len(unknown) == 1 else 'check renders'
        return (
            f'reads {noun} {named} whose frames are not known (no recorded '
            f'range): {full}'
        )
    return None


def follow_pins(
    follows: Iterable[Mapping],
    channel: str,
    all_channels: bool,
    version: str,
    ) -> dict[str, tuple[str, str]]:
    """What a re-comp beside a stepped render pins an import to, so it
    reads that check render and not the shot's older published render
    (Soren, 2026-11-22): ``follows`` is the render's ``[{channel,
    department, version}]``. An all-channels import is pinned on every
    channel the render made (it reads only ``latest``); a one-channel import
    on ``latest`` to its own channel's, when the render made it; a version
    the artist picked stays. ``{channel: (department, version)}``."""
    by_channel = {
        follow['channel']: (follow['department'], follow['version'])
        for follow in follows
    }
    if all_channels:
        return by_channel
    if version != LATEST or channel not in by_channel:
        return {}
    return {channel: by_channel[channel]}


def is_check_comp(step: int, reads_check_render: bool) -> bool:
    """Whether a shot's comp is a check comp, never published (no daily, no
    Copy to edit, ``"published": false`` in its ``context.json``): on a
    Step above 1, its frames have gaps, as a stepped render's do
    (``farm.pipelines.render.is_check_render``); and a comp that reads a
    check render is a look at one, whatever its step."""
    return step > 1 or reads_check_render


def check_comp_note(step: int, reads_check_render: bool, copy_to_edit: bool = False,
                    held: str = '') -> str:
    """Why a comp is a check comp (:func:`is_check_comp`) and what that
    skips, for a Submit to say; empty for a comp that publishes. ``held``
    is :func:`held_text` of the check renders it reads: when it holds
    frames, that is the why (``every frame of 1001-1122 from a render every
    2nd frame: 61 frames held, a check comp, not published, no daily``)."""
    from tumblepipe.pipe.hold_frames import _ordinal
    if not is_check_comp(step, reads_check_render or bool(held)): return ''
    if held:
        text = f'{held}, a check comp, not published, no daily'
        if copy_to_edit:
            text += ', Copy to edit skipped'
        return text
    why = []
    if step > 1:
        why.append(f'every {_ordinal(step)} frame')
    if reads_check_render:
        why.append('reads a check render')
    text = f'{" and ".join(why)}: a check comp, not published, no daily'
    if copy_to_edit:
        text += ', Copy to edit skipped'
    return text


def unpublished_reason(contexts: Iterable[tuple[str, Optional[Mapping]]]) -> Optional[str]:
    """Why a job that publishes a comp version (its daily mp4, its Copy to
    edit) must skip it, None when every version is published.

    ``contexts`` is ``[(name, context.json data or None)]``, the versions
    the job follows. A comp learns only when it runs whether it reads a
    check render (a re-comp resolves its imports then, Soren 2026-11-23):
    the worker's ``record_reads`` writes ``"published": false``, after the
    daily and the sync were queued. A missing ``context.json`` or one with
    no ``published`` is published, so versions written before the flag
    keep publishing (``pipe.paths.render.is_published``)."""
    from tumblepipe.pipe.hold_frames import _ordinal
    for name, context in contexts:
        if context is None or context.get('published', True) is not False:
            continue
        step = context.get('step_size', 1)
        if isinstance(step, int) and not isinstance(step, bool) and step > 1:
            why = f'every {_ordinal(step)} frame'
        else:
            why = 'reads a check render'
        return f'{name} {why}: a check comp, not published'
    return None


def read_unpublished_reason(context_paths: Iterable) -> Optional[str]:
    """:func:`unpublished_reason` of the ``context.json`` files at
    ``context_paths`` (a missing file is published)."""
    from tumblepipe.util.io import load_json_if_exists
    contexts = []
    for path in context_paths:
        path = Path(path)
        contexts.append((path.parent.as_posix(), load_json_if_exists(path)))
    return unpublished_reason(contexts)


###############################################################################
# The import's network
###############################################################################
@dataclass(frozen=True)
class FileRead:
    """One File COP an import_render builds: its node name, and the AOV of
    the channel it reads."""
    name: str
    channel: str
    aov: str


@dataclass(frozen=True)
class DivePlan:
    """The network inside an import_render: its File COPs in build order,
    the ``aovs`` cable's fields (each a File COP's name), the beauties laid
    over each other for ``rgba`` (File COP names, back to front), and the
    ``beauties`` cable's fields (each beauty named after its channel, in the
    same order)."""
    files: tuple
    fields: tuple
    beauties: tuple
    beauty_fields: tuple = ()

    def aov_of(self, name: str) -> str:
        return next(read.aov for read in self.files if read.name == name)

    def cable_fields(self, cable: str) -> tuple:
        """The fields of one of the AOV cables (:data:`AOV_CABLES`): those
        of :attr:`fields` whose AOV rides it (:func:`aov_cable`), in order.
        The three together are :attr:`fields`, each field in exactly one."""
        return tuple(f for f in self.fields if aov_cable(self.aov_of(f)) == cable)

    @property
    def signatures(self) -> dict[str, str]:
        """Each cable field's COP type (a Cable Unpack ``fieldtype`` token)."""
        return {field: aov_signature(self.aov_of(field)) for field in self.fields}

    @property
    def beauty_signatures(self) -> dict[str, str]:
        """Each ``beauties`` field's COP type: a beauty's."""
        return {field: aov_signature(BEAUTY) for field in self.beauty_fields}


def dive_plan(aovs_by_channel: Mapping[str, Iterable[str]], all_channels: bool,
              extras: bool = True) -> DivePlan:
    """The import's network for what it reads (``{channel: AOV labels}``,
    in the shot's channel order).

    One channel: a File COP per AOV named after it, the cable every one but
    the beauty, ``rgba`` the beauty. All channels: names by
    :func:`layer_field`, the cable every channel's AOVs
    (:func:`all_channels_fields`), ``rgba`` the beauties over each other.
    Either way the ``beauties`` cable carries each channel's beauty, named
    after the channel. Without ``extras`` (a 1.0 node, which has no extra
    output) the AOVs of the :data:`EXTRAS` cable are not read at all.
    """
    if not extras:
        aovs_by_channel = {
            channel: [label for label in labels
                      if label == BEAUTY or aov_cable(label) != EXTRAS]
            for channel, labels in aovs_by_channel.items()
        }
    files = []
    beauties = []
    beauty_fields = []
    for channel, labels in aovs_by_channel.items():
        for aov in file_fields(labels):
            name = layer_field(channel, aov) if all_channels else aov
            files.append(FileRead(name, channel, aov))
            if aov == BEAUTY:
                beauties.append(name)
                beauty_fields.append(channel)
    if all_channels:
        fields = all_channels_fields({
            channel: cable_fields(labels) for channel, labels in aovs_by_channel.items()
        })
    else:
        labels = next(iter(aovs_by_channel.values()), [])
        fields = cable_fields(labels)
    return DivePlan(tuple(files), tuple(fields), tuple(beauties), tuple(beauty_fields))


def follow_fields(old: Sequence[str], new: Sequence[str],
                  rehome: Sequence[str] = ()) -> list[Optional[tuple[bool, int]]]:
    """Where a wire on each of a Cable Unpack's ``old`` outputs goes when its
    fields become ``new``: ``(False, i)`` the same field at its new place,
    ``(True, i)`` the same-named output of the ``rehome`` unpack when ``new``
    lost it (a beauty that moved from ``aovs`` to ``beauties``), None when
    the field is gone. A Cable Unpack's outputs are by position, so without
    this a wire stays on whatever field takes its slot."""
    return [
        None if target is None else (target[0] is not None, target[1])
        for target in follow_fields_among(old, new, [rehome])
    ]


def follow_fields_among(old: Sequence[str], new: Sequence[str],
                        rehomes: Sequence[Sequence[str]] = ()
                        ) -> list[Optional[tuple[Optional[int], int]]]:
    """:func:`follow_fields` over several other unpacks: ``(None, i)`` the
    same field at its new place on this unpack, ``(k, i)`` output ``i`` of
    ``rehomes[k]``, the first that has the field (an AOV that moved from
    ``aovs`` to ``lpes`` or ``mattes``, #67), None when no unpack has it."""
    targets: list[Optional[tuple[Optional[int], int]]] = []
    for field in old:
        if field in new:
            targets.append((None, list(new).index(field)))
            continue
        home = next((k for k, fields in enumerate(rehomes) if field in fields), None)
        targets.append(None if home is None else (home, list(rehomes[home]).index(field)))
    return targets


def mix_unpack_fields(groups: Iterable[str], cable_beauty: bool) -> list[str]:
    """The fields a light mix unpacks from its cable: the beauty first when
    the cable carries it (an empty beauty input), then the graded groups."""
    return ([BEAUTY] if cable_beauty else []) + list(groups)


###############################################################################
# Light mix rows
###############################################################################
def sync_rows(rows: Iterable[str], groups: Iterable[str]) -> list[str]:
    """The light mix's rows after Sync Rows: the existing rows untouched and
    in order, then a row for each group that has none."""
    rows = list(rows)
    return rows + [group for group in dict.fromkeys(groups) if group not in rows]


def mixed_groups(rows: Iterable[str], fields: Iterable[str]) -> list[str]:
    """The groups the mix grades on their own: those with a row that the
    cable carries. A group without a row rides in Rest (beauty minus the
    graded groups), so it mixes at unity like a row at its defaults."""
    present = set(light_groups(fields))
    return [row for row in rows if row in present]


###############################################################################
# Submit: the freeze, the record, the unread channels
###############################################################################
@dataclass(frozen=True)
class ImportState:
    """One import_render as a submit sees it: its settings and what they
    resolve to now."""
    node_path: str
    channel: str
    department: str     # the Department parm: best, or a department
    version: str        # the Version parm: latest, or a v####
    resolved_department: str
    resolved_version: str

    @property
    def pinned(self) -> bool:
        return self.version != LATEST


def freeze_plan(imports: Iterable[ImportState]) -> dict[str, dict]:
    """What the farm sets every import to: ``{node path: {channel,
    department, version}}``.

    An import on ``latest`` is pinned to what it resolved in the session; a
    pinned one keeps its pin. Every import in ``imports`` is in the plan, and
    nothing else.
    """
    plan = dict()
    for state in imports:
        if state.pinned:
            department, version = state.department, state.version
        else:
            department, version = state.resolved_department, state.resolved_version
        plan[state.node_path] = dict(
            channel = state.channel,
            department = department,
            version = version,
        )
    return plan


# An all-channels import is frozen per channel: its plan keys are
# ``<node path>#<channel>``.
PLAN_KEY_SEPARATOR = '#'


def plan_key(node_path: str, channel: str, all_channels: bool) -> str:
    """The freeze plan's key for one channel an import reads."""
    if not all_channels: return node_path
    return f'{node_path}{PLAN_KEY_SEPARATOR}{channel}'


def split_plan_key(key: str) -> tuple[str, Optional[str]]:
    """``(node path, channel)`` of a plan key; the channel is None for a
    one-channel import's key (its channel is in the pin)."""
    if PLAN_KEY_SEPARATOR not in key: return key, None
    node_path, channel = key.rsplit(PLAN_KEY_SEPARATOR, 1)
    return node_path, channel


def format_pins(pins: Mapping[str, tuple[str, str]]) -> str:
    """An all-channels import's per-channel pins as its ``pins`` parm holds
    them: ``channel:department:version`` separated by spaces."""
    return ' '.join(
        f'{channel}:{department}:{version}'
        for channel, (department, version) in pins.items()
    )


def parse_pins(text: str) -> dict[str, tuple[str, str]]:
    """The inverse of :func:`format_pins`; malformed entries are skipped."""
    pins: dict = dict()
    for entry in text.split():
        parts = entry.split(':')
        if len(parts) != 3 or not all(parts): continue
        pins[parts[0]] = (parts[1], parts[2])
    return pins


def pins_by_node(plan: Mapping[str, Mapping]) -> dict[str, dict[str, tuple[str, str]]]:
    """A freeze plan per import node: ``{node path: {channel: (department,
    version)}}`` (an all-channels import has one entry per channel)."""
    nodes: dict = dict()
    for key, pin in plan.items():
        node_path, _channel = split_plan_key(key)
        nodes.setdefault(node_path, dict())[pin['channel']] = (pin['department'], pin['version'])
    return nodes


def build_key(shot_uri, reads: Mapping[str, tuple[str, str]]) -> str:
    """What an import_render's File COPs were built for: its shot, and per
    channel the department and version read. A change in any of them, the
    channel included, rebuilds them (two channels can both resolve to
    ``denoise v0001``, so the version alone does not tell them apart)."""
    return ' '.join([str(shot_uri)] + [
        f'{channel}:{department}:{version}'
        for channel, (department, version) in reads.items()
    ])


def build_key_channels(key: str) -> list[str]:
    """The channels a build key read, in order."""
    return [entry.split(':', 1)[0] for entry in key.split()[1:]]


def provenance(plan: Mapping[str, Mapping]) -> dict[str, dict]:
    """A composite version's record of what it read: ``{channel:
    {department, version}}``. Two imports of one channel must agree."""
    reads: dict = dict()
    for node_path, pin in plan.items():
        entry = dict(department = pin['department'], version = pin['version'])
        previous = reads.setdefault(pin['channel'], entry)
        if previous != entry:
            raise CompError(
                f'channel "{pin["channel"]}" is read at two versions '
                f'({previous["department"]} {previous["version"]} and '
                f'{entry["department"]} {entry["version"]}, the second by '
                f'{node_path}); a composite version records one per channel'
            )
    return reads


###############################################################################
# The frames the reads cover (#43)
###############################################################################
def covered_range(ranges: Iterable[Optional[tuple[int, int]]]) -> Optional[tuple[int, int]]:
    """The frames every read covers: the intersection of their rendered
    ``(first, last)`` ranges. None when there is no read, one's range is
    unknown, or they share no frame."""
    first, last, any_read = None, None, False
    for frame_range in ranges:
        if frame_range is None:
            return None
        any_read = True
        first = frame_range[0] if first is None else max(first, frame_range[0])
        last = frame_range[1] if last is None else min(last, frame_range[1])
    if not any_read or first > last:
        return None
    return first, last


def holding_covered_range(
    reads: Iterable[tuple[Optional[tuple[int, int]], bool]],
    ) -> Optional[tuple[int, int]]:
    """What a comp of ``reads`` covers on From Config, each read a
    ``(range, holds)`` pair, ``holds`` whether its import holds missing
    frames (``pipe.hold_frames.holds``: a check render on Automatic, any
    read on Hold). A holding read shows its first frame before its range
    and its last after it, so it does not narrow the comp (Soren,
    2026-11-24: a one-frame check render beside a 185-frame render comped
    one frame): :func:`covered_range` of the reads that do not hold.

    When every read holds, the span of their known ranges, the earliest
    first frame to the latest last (Soren, 2026-11-24: Antoine's CHARAr
    was a Step 2 check render too, and the intersection of 1001-1001 and
    1001-1185 still comped one frame): each read shows a frame on all of
    it, its own or one held. None when no read's range is known."""
    reads = list(reads)
    narrowing = [frame_range for frame_range, holds in reads if not holds]
    if narrowing:
        return covered_range(narrowing)
    known = [frame_range for frame_range, _ in reads if frame_range is not None]
    if not known:
        return None
    return min(first for first, _ in known), max(last for _, last in known)


def read_holds(reads: Iterable[tuple[str, bool]]) -> dict[str, bool]:
    """``{channel: holds}`` of ``(channel, holds)`` pairs, one per import
    reading the channel: a channel holds only when every import reading it
    does (one that leaves the frames empty shows them empty)."""
    holding: dict[str, bool] = dict()
    for channel, holds in reads:
        holding[channel] = holding.get(channel, True) and holds
    return holding


def read_ranges(renders: Mapping, reads: Mapping[str, Mapping]) -> dict[str, Optional[tuple[int, int]]]:
    """``{channel: (first, last)}`` each read covers, from its frame
    stack's ``context.json`` (None where it is gone or has none).

    ``reads`` is a composite version's record (:func:`provenance`):
    ``{channel: {department, version}}``; ``renders`` the shot's renders
    on disk (``RenderContext.renders``)."""
    ranges: dict[str, Optional[tuple[int, int]]] = dict()
    for channel, read in reads.items():
        frame_range = None
        try:
            layer = renders[read['department']].layers[channel][read['version']]
            block = layer.get_frame_range()
            if block is not None:
                frame_range = (block.first_frame, block.last_frame)
        except (KeyError, AttributeError, OSError, ValueError):
            pass
        ranges[channel] = frame_range
    return ranges


def best_reads(renders: Mapping, render_department_names: Iterable[str],
               channels: Iterable[str]) -> dict[str, dict]:
    """What a default import (``best``, ``latest``) of each channel reads
    now, as :func:`provenance` records it; a channel with nothing to read
    is left out (one with only a check render is not: ``latest`` reads
    it)."""
    return {
        channel: dict(department = resolved.department, version = resolved.version)
        for channel, resolved in _best_resolved(renders, render_department_names, channels).items()
    }


def _best_resolved(renders: Mapping, render_department_names: Iterable[str],
                   channels: Iterable[str]) -> dict[str, Resolved]:
    departments = list(render_department_names)
    resolved: dict[str, Resolved] = dict()
    for channel in channels:
        try:
            resolved[channel] = resolve_render(renders, departments, channel)
        except CompError:
            continue
    return resolved


def rendered_range(renders: Mapping, render_department_names: Iterable[str],
                   channels: Iterable[str]) -> Optional[tuple[int, int]]:
    """The frames every channel's best render covers now (what a re-comp's
    default imports read: :func:`best_reads`), None when none is rendered or
    a range is unknown. ``renders`` as :func:`resolve_render` takes them.
    Filesystem only: Farm Submit's status scan reads it on a worker thread,
    the farm's submit when it comps the renders on disk.

    A check render does not narrow it (:func:`holding_covered_range`), as
    export_comp's From Config: Farm Submit never opens the workfile, so it
    takes the imports on Automatic, where a check render holds its first
    and last frame past its range (an import on Empty leaves those black,
    which the worker's log says, ``missing_note``)."""
    from tumblepipe.pipe.hold_frames import AUTO, holds
    resolved = _best_resolved(renders, render_department_names, channels)
    ranges = read_ranges(renders, {
        channel: dict(department = read.department, version = read.version)
        for channel, read in resolved.items()
    })
    return holding_covered_range(
        (ranges[channel], holds(AUTO, read.check_step)) for channel, read in resolved.items()
    )


def uncovered_frames(frames: Iterable[int], ranges: Mapping[str, Optional[tuple[int, int]]]) -> dict[str, list[int]]:
    """``{channel: frames}`` of ``frames`` outside each read's rendered
    range: the frames a comp would fill with empty (transparent black)
    input there. A read of unknown range covers nothing it can vouch for,
    so it is left out rather than reported."""
    frames = list(frames)
    uncovered: dict[str, list[int]] = dict()
    for channel, frame_range in ranges.items():
        if frame_range is None:
            continue
        missing = [f for f in frames if not frame_range[0] <= f <= frame_range[1]]
        if missing:
            uncovered[channel] = missing
    return uncovered


def uncovered_message(uncovered: Mapping[str, list[int]], ranges: Mapping) -> list[str]:
    """One line per channel: how many frames had no render, and what it covers."""
    return [
        f'{len(missing)} frames had no render in channel "{channel}" '
        f'(it covers {ranges[channel][0]}-{ranges[channel][1]})'
        for channel, missing in uncovered.items()
    ]


def unread_channels(shot_channels: Iterable[str], read_channels: Iterable[str]) -> list[str]:
    """The shot's channels no import reads, in the shot's channel order."""
    read = set(read_channels)
    return [channel for channel in shot_channels if channel not in read]


def unread_warning(unread: Iterable[str]) -> str:
    """export_comp's warning for channels no import reads; empty for none."""
    unread = list(unread)
    if not unread: return ''
    names = ', '.join(f'`{name}`' for name in unread)
    noun = 'channel' if len(unread) == 1 else 'channels'
    verb = 'is' if len(unread) == 1 else 'are'
    return f'shot {noun} {names} {verb} not read by this comp'


###############################################################################
# The composite version a submit writes
###############################################################################
def next_version_name(existing: Iterable[str]) -> str:
    """The version after every one in ``existing`` (version folder names of
    the merged layer and of every layer the submit writes), so a submit
    never reuses a version on disk."""
    codes = [
        naming.get_version_code(name) for name in existing
        if naming.is_valid_version_name(name)
    ]
    return naming.get_version_name(max(codes, default=0) + 1)


def output_folder(layer: str) -> str:
    """The render layer folder an export_comp's Layer writes: the merged comp
    into the department's cross-channel folder, a channel into its own."""
    return MERGED_FOLDER if layer == MERGED_LAYER else layer


def layer_output_path(shot_uri, layer: str, version_name: str, purpose: str = 'render'):
    """Where an export_comp's Layer writes its frames (a ``####`` pattern):
    the merged comp in the version's cross-channel folder, frames directly
    in the version folder like the render chain's slapcomp; a channel as
    that channel's beauty AOV."""
    from tumblepipe.pipe.paths import get_aov_frame_path, get_frame_path
    if layer == MERGED_LAYER:
        return get_frame_path(
            shot_uri, COMPOSITE_DEPARTMENT, MERGED_FOLDER, version_name,
            '####', 'exr', purpose
        )
    return get_aov_frame_path(
        shot_uri, COMPOSITE_DEPARTMENT, layer, version_name,
        'beauty', '####', 'exr', purpose
    )


def layer_context_dir(output_path, layer: str):
    """The version folder holding a written layer's ``context.json``, from
    its frame pattern (:func:`layer_output_path`): the merged comp's frames
    sit in the version folder, a channel's in its ``beauty`` folder."""
    from pathlib import Path
    output_path = Path(output_path)
    return output_path.parent if layer == MERGED_LAYER else output_path.parent.parent


def next_composite_version(shot_uri, layers: Iterable[str], purpose: str = 'render') -> str:
    """The composite version a submit writes: after every version in the
    merged folder and in each written layer's folder, so none is reused."""
    from tumblepipe.api import api
    from tumblepipe.util.uri import Uri
    folders = {MERGED_FOLDER} | {output_folder(layer) for layer in layers}
    root = api.storage.resolve(
        Uri.parse(f'{purpose}:/render') / shot_uri.segments / COMPOSITE_DEPARTMENT
    )
    existing = []
    for folder in sorted(folders):
        layer_dir = root / folder
        if not layer_dir.is_dir(): continue
        existing += [path.name for path in layer_dir.iterdir() if path.is_dir()]
    return next_version_name(existing)


###############################################################################
# The template
###############################################################################
# Network-editor units; a position is a node's lower-left corner, y grows up.
#
# COPs is a horizontal stream (Remy, 2026-11-11, #66): the template reads
# left to right, inputs on the left, the export on the right. One row per
# channel, top to bottom in channel order, each an import_render then its
# light_mix; a free column after the mixes for grades; then the over chain,
# one column per over, each on its front channel's row, so every wire runs
# rightwards; then the export_comp on the last over's row. A Multi gives
# each member its own block of rows, members top to bottom (#66), and only
# the first member carries the sticky notes (#65).
#
# A COP node is a tile with its image preview, 1.77 wide and 1.68 to 1.98
# tall (hou.Node.size() in 22.0.429: export_comp, light_mix, import_render
# and blend), with its type and name drawn above it. NODE_FOOTPRINT is that
# tile plus its label: no two may overlap, nor a note a node (Remy,
# 2026-11-10: rows 1.2 to 1.5 apart stacked every import on its light mix).
NODE_FOOTPRINT = (1.8, 2.7)
ROW = 3.0                   # between channel rows
COLUMN_STRIDE = 4.0         # between stages along the stream
NOTE_SIZE = (3.2, 0.6)
NOTE_LIFT = 0.2             # a note's gap above the node (label) it labels
NOTE_BAND = NOTE_LIFT + NOTE_SIZE[1] + NOTE_LIFT    # above the first row
IMPORT_COLUMN = 0
MIX_COLUMN = 1
GRADE_COLUMN = 2            # left free: a channel's grade goes here
OVER_COLUMN = 3             # the first over; each later over one further
MEMBER_GAP = 1.5            # between a Multi's member blocks


@dataclass(frozen=True)
class NoteSpec:
    text: str
    position: tuple[float, float]
    size: tuple[float, float]


@dataclass(frozen=True)
class Layout:
    """Where the template puts each node (``name -> (x, y)``), how the over
    chain is wired (``(over node, back input, front input)``, back to front),
    and its sticky notes. Everything lies inside the block ``x .. x + width``
    by ``y - height .. y``, ``(x, y)`` its top-left corner."""
    positions: dict
    overs: list
    notes: list
    imports: dict           # import node name -> channel
    mixes: dict             # light_mix node name -> import node name
    export: str
    width: float
    height: float = 0.0


def template_layout(channels: list[str], suffix: str = '', x: float = 0.0,
                    y: float = 0.0, notes: bool = True) -> Layout:
    """The composite template's layout for one shot's channels, its block's
    top-left corner at ``(x, y)``.

    One row per channel in the shot's channel order: ``import_render`` then
    ``light_mix``. An over chain stacks them back to front, so the last
    channel is on top (the render chain's slapcomp stacks the same way), into
    one ``export_comp``. ``notes`` False leaves the sticky notes out (a
    Multi's later members).

    A project's own template from before #66 calls this with ``x`` alone
    and steps each member ``width + MEMBER_GAP`` along x: its members then
    sit side by side, still clear of each other.
    """
    node_width, node_height = NODE_FOOTPRINT
    first_row_y = y - NOTE_BAND - node_height

    def row_y(row: int) -> float:
        return first_row_y - row * ROW

    def column_x(column: int) -> float:
        return x + column * COLUMN_STRIDE

    positions: dict = dict()
    imports: dict = dict()
    mixes: dict = dict()
    overs: list = []
    for index, channel in enumerate(channels):
        import_name = f'import_{channel}{suffix}'
        mix_name = f'light_mix_{channel}{suffix}'
        positions[import_name] = (column_x(IMPORT_COLUMN), row_y(index))
        positions[mix_name] = (column_x(MIX_COLUMN), row_y(index))
        imports[import_name] = channel
        mixes[mix_name] = import_name
    mix_names = list(mixes)
    back = mix_names[0] if mix_names else None
    for index, front in enumerate(mix_names[1:]):
        over_name = f'over_{channels[index + 1]}{suffix}'
        positions[over_name] = (column_x(OVER_COLUMN + index), row_y(index + 1))
        overs.append((over_name, back, front))
        back = over_name
    rows = max(len(channels), 1)
    export_name = f'export_comp{suffix}'
    export_column = OVER_COLUMN + len(overs)    # one past the last over
    export_position = (column_x(export_column), row_y(rows - 1))
    positions[export_name] = export_position
    width = export_column * COLUMN_STRIDE + max(node_width, NOTE_SIZE[0])
    height = NOTE_BAND + node_height + (rows - 1) * ROW
    note_y = y - NOTE_LIFT - NOTE_SIZE[1]
    note_specs = [
        NoteSpec("Use mattes: Cable Unpack the import's mattes output",
                 (column_x(IMPORT_COLUMN), note_y), NOTE_SIZE),
        NoteSpec('Grade a channel here, after its light mix',
                 (column_x(GRADE_COLUMN), note_y), NOTE_SIZE),
        NoteSpec('Whole-frame work here, before the export',
                 (export_position[0], export_position[1] + node_height + NOTE_LIFT),
                 NOTE_SIZE),
    ] if notes else []
    return Layout(
        positions = positions,
        overs = overs,
        notes = note_specs,
        imports = imports,
        mixes = mixes,
        export = export_name,
        width = width,
        height = height,
    )


def multi_layout(members: Sequence[tuple[str, Sequence[str]]],
                 x: float = 0.0, y: float = 0.0) -> list[Layout]:
    """A Multi's comp: each member's block (``(suffix, channels)``, in
    member order) in its own rows, members top to bottom, ``MEMBER_GAP``
    apart. Only the first member's block has the sticky notes: they say the
    same thing for every member (#65)."""
    layouts = []
    for index, (suffix, channels) in enumerate(members):
        layout = template_layout(list(channels), suffix, x, y, notes = index == 0)
        layouts.append(layout)
        y -= layout.height + MEMBER_GAP
    return layouts


###############################################################################
# A comp of the render it is submitted with (#80)
###############################################################################
# A th::export_comp outside the composite template (a slap comp in the
# render scene: Slap Comp Import -> Block Begin -> ... -> export_comp) has no
# th::import_render. It comps the render of the same Farm Submit: the comp
# job waits for that render and its Slap Comp Import nodes read its frames
# (Soren, 2026-11-12: no fallback to the last published render). It
# publishes as a composite version, like any comp.

# Karma's name for the beauty, which Houdini's own slap comp setup reads;
# TumblePipe renders it as ``beauty`` (#81).
SLAP_BEAUTY_ALIASES = ('C',)


@dataclass(frozen=True)
class SceneComp:
    """What a workfile's comp of one shot without a th::import_render
    submits: its export_comps (``{node_path, layer}``) and the AOVs its
    Slap Comp Import nodes read, as named on them."""
    exports: tuple
    aovs: tuple = ()

    def settings(self, path: str = '') -> dict:
        """The ``comp_scene`` entry of a Farm Submit row: ``path`` is the
        workfile copy submitted with it (empty until it is saved)."""
        entry = dict(
            exports = [dict(export) for export in self.exports],
            aovs = list(self.aovs),
        )
        if path:
            entry['path'] = path
        return entry


def slap_aov(aov_name: str) -> str:
    """The rendered AOV a Slap Comp Import AOV reads: Karma's ``C`` is
    TumblePipe's ``beauty``; any other name as it is."""
    name = aov_name.strip()
    return BEAUTY if name in SLAP_BEAUTY_ALIASES else name


def slap_frame_token(frame_range) -> str:
    """The frame in a scene comp's File COP file name (#80): ``$F4``, or,
    for a render on a Step above 1 (a check render), the nearest frame it
    rendered (``pipe.hold_frames.hold_expression``), so a comp on frames
    the render lacks holds them as th::import_render does (Soren,
    2026-11-23). ``frame_range`` is the render's recorded range (a
    ``FrameRange`` or None, not known)."""
    from tumblepipe.pipe.hold_frames import hold_expression
    step = getattr(frame_range, 'step_size', 1) if frame_range is not None else 1
    if step <= 1:
        return '$F4'
    held = hold_expression(frame_range.first_frame, frame_range.last_frame, step)
    return f'`padzero(4, {held})`'


def slap_reads(aov_names: Iterable[str], channels: Sequence[str]) -> dict[str, list[str]]:
    """``{AOV as named: [channels, back to front]}``: which of the render's
    channels each Slap Comp Import AOV reads.

    The viewport a slap comp was built on renders the whole shot; the farm
    renders it channel by channel (``channels``, in the render's order).
    One channel: every AOV is read from it. Several: the beauty is the
    channels laid over each other, back to front (as the render chain's
    slapcomp stacks them); any other AOV has no alpha to lay it by and is
    refused. Raises :class:`CompError`, saying what to change.
    """
    channels = list(channels)
    if not channels:
        raise CompError('the comp reads the render of its submit, and it renders no channel')
    reads: dict[str, list[str]] = dict()
    for aov_name in aov_names:
        aov = slap_aov(aov_name)
        if not aov:
            raise CompError('a Slap Comp Import names no AOV: name the AOV it reads')
        if len(channels) > 1 and aov != BEAUTY:
            raise CompError(
                f'Slap Comp Import AOV "{aov_name}" is read from a render of '
                f'{len(channels)} channels ({", ".join(channels)}): only the '
                f'{BEAUTY} is laid over across channels. Render one channel '
                'for this comp, or comp the renders with th::import_render'
            )
        reads[aov_name] = list(channels)
    return reads


def scene_comp_problems(render: bool, full_range: bool, channels: Sequence[str],
                        aovs: Iterable[str] = ()) -> list[str]:
    """What stops a Farm Submit row comping a workfile's comp without a
    th::import_render (:class:`SceneComp`): it needs the shot's full render
    in the same submit, of channels its Slap Comp Imports can read
    (:func:`slap_reads`). Empty when nothing does."""
    if not render:
        return [
            'the comp has no th::import_render, so it reads the render of '
            'the same submit, and none is ticked: tick the shot\'s Render '
            'beside its Comp'
        ]
    if not full_range:
        return [
            'the comp has no th::import_render and reads the render of the '
            'same submit, which renders only its check frames: choose a '
            'render mode with the full range'
        ]
    try:
        slap_reads(aovs, channels)
    except CompError as error:
        return [str(error)]
    return []


def no_render_message(shot_uri) -> str:
    """export_comp's Submit of a shot no th::import_render reads: it has
    nothing to comp on its own (#80)."""
    return (
        f'no th::import_render reads {shot_uri}, so this comp reads the '
        'render of the same Farm Submit, and its own Submit renders '
        'nothing. Open Farm Submit from this workfile and tick the shot\'s '
        'Render and Comp: the comp waits for that render and comps its frames.'
    )


#: export_comp's note when no th::import_render reads its shot.
SCENE_COMP_NOTE = (
    'no th::import_render: this comp reads the render of its Farm Submit '
    '(tick the shot\'s Render and Comp)'
)


def render_reads(renders: Iterable[Mapping]) -> dict[str, dict]:
    """A comp of its submit's render, recorded as :func:`provenance` records
    a comp's reads: ``{channel: {department, version}}``. ``renders`` is
    the composite config's ``renders`` (``[{channel, department,
    version}]``, in the render's channel order)."""
    return {
        render['channel']: dict(department = render['department'], version = render['version'])
        for render in renders
    }


###############################################################################
# Copy to edit
###############################################################################
def merged_comp_layer(layers: Mapping) -> Optional[str]:
    """The version of the composite department's merged comp that Copy to
    edit syncs, None when the department has none complete. ``layers`` is
    the composite ``Render``'s layers (``{layer: {version: Layer}}``)."""
    versions = layers.get(MERGED_FOLDER) or {}
    for version_name in sorted(versions, key=naming.get_version_code, reverse=True):
        if versions[version_name].is_complete():
            return version_name
    return None


###############################################################################
# Render it again (#68)
###############################################################################
@dataclass(frozen=True)
class RenderSeed:
    """What Farm Submit opens on from an import's **Render** button: the
    shot's Render cell ticked on the Shots tab, the import's channel checked
    (``*``: every channel) and, when the import names a concrete
    department, that department as the render cut (Up to), which is also
    the department the render is written under, so the import reads it."""
    uris: tuple
    names: tuple[str, ...]
    context: str
    department: Optional[str]
    channels: tuple[str, ...]


def render_seed(shot_uri, channel: str, department: str) -> RenderSeed:
    """:class:`RenderSeed` for an import of ``shot_uri`` set to ``channel``
    and ``department``. ``best`` (or blank) pins no cut: each shot's own
    ``submission.render.department`` decides, as from the browser. A blank
    channel checks the menu's usual seed."""
    department = (department or '').strip()
    channel = (channel or '').strip()
    return RenderSeed(
        uris=(shot_uri,),
        names=(shot_uri.segments[-1],),
        context='shots',
        department=None if department in ('', BEST) else department,
        channels=(channel,) if channel else (),
    )
