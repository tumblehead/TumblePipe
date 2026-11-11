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
from tumblepipe.pipe.aovs import is_cryptomatte_aov, is_ramp_aov

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
    """A channel has no complete render in the departments asked (yet): an
    all-channels import leaves such a channel out instead of failing."""


###############################################################################
# AOVs
###############################################################################
def aov_included(aov_name: str) -> bool:
    """Whether a comp reads an AOV at all (it rides import_render's cable)."""
    name = aov_name.lower()
    if name.startswith(LIGHT_GROUP_PREFIX): return True
    if name.startswith('objid_'): return True
    if name.startswith('holdout_'): return True
    if is_ramp_aov(name): return True
    if is_cryptomatte_aov(name): return True
    return name in (BEAUTY, 'albedo', 'normal', 'depth', 'uv', 'position')


def aov_signature(aov_name: str) -> str:
    """The COP data type an AOV is read as (a File COP's ``type#`` token).

    The beauty is RGBA (its A is the channel's alpha); light groups, albedo,
    normals, depth and position RGB; uv a vector2; mattes, holdouts and
    ramps one channel; a cryptomatte's rank planes RGBA (two id/coverage
    pairs each, see :func:`expand_cryptomattes`).
    """
    name = aov_name.lower()
    if name == BEAUTY: return 'vector4'
    if is_cryptomatte_aov(name): return 'vector4'
    if name.startswith(LIGHT_GROUP_PREFIX): return 'vector'
    if name.startswith('objid_'): return 'float'
    if name.startswith('holdout_'): return 'float'
    if is_ramp_aov(name): return 'float'
    if name in ('albedo', 'normal', 'depth', 'position'): return 'vector'
    if name == 'uv': return 'vector2'
    raise ValueError(f'the comp reads no AOV named {aov_name!r}')


def is_colour_aov(aov_name: str) -> bool:
    """Whether an AOV is colour (the beauty and its light groups), read in
    the scene's colour space; everything else is data, read raw."""
    name = aov_name.lower()
    return name == BEAUTY or name.startswith(LIGHT_GROUP_PREFIX)


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
        return f'{self.department} {self.version}, {len(self.fields)} AOVs'


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


def _newest(renders: Mapping, department: str, channel: str) -> Optional[Newest]:
    versions = list_versions(renders, department, channel)
    if not versions: return None
    layer = renders[department].layers[channel][versions[0]]
    return layer_newest(department, versions[0], layer, channel)


def _fresh_candidates(renders: Mapping, candidates: list[str], channel: str) -> list[str]:
    """``candidates`` (highest first) without the departments whose newest
    version a lower department's supersedes."""
    newest = [n for n in (_newest(renders, d, channel) for d in candidates) if n]
    fresh = set(best_departments(newest))
    return [d for d in candidates if d in fresh or d not in {n.department for n in newest}]


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
    readable department with a complete version) or one readable
    department; ``version`` is ``latest`` (its newest complete version) or a
    ``v####`` of it, which must be complete. A pin needs a concrete
    department, so ``best`` with a pin is refused.
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
    if department == BEST:
        candidates = _fresh_candidates(renders, candidates, channel)
    for candidate in candidates:
        versions = list_versions(renders, candidate, channel)
        if version == LATEST:
            if not versions: continue
            version_name = versions[0]
        else:
            if version not in versions:
                raise CompError(
                    f'{candidate} {version} of channel "{channel}" is not a '
                    'complete render'
                )
            version_name = version
        layer = renders[candidate].layers[channel][version_name]
        return Resolved(
            channel = channel,
            department = candidate,
            version = version_name,
            aovs = expand_cryptomattes({
                label: aov for label, aov in layer.aovs.items()
                if aov_included(label)
            }),
        )
    where = ' or '.join(reversed(candidates)) or 'any render department'
    raise NotRendered(f'channel "{channel}" has no complete render in {where} yet')


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

    @property
    def signatures(self) -> dict[str, str]:
        """Each cable field's COP type (a Cable Unpack ``fieldtype`` token)."""
        return {field: aov_signature(self.aov_of(field)) for field in self.fields}

    @property
    def beauty_signatures(self) -> dict[str, str]:
        """Each ``beauties`` field's COP type: a beauty's."""
        return {field: aov_signature(BEAUTY) for field in self.beauty_fields}


def dive_plan(aovs_by_channel: Mapping[str, Iterable[str]], all_channels: bool) -> DivePlan:
    """The import's network for what it reads (``{channel: AOV labels}``,
    in the shot's channel order).

    One channel: a File COP per AOV named after it, the cable every one but
    the beauty, ``rgba`` the beauty. All channels: names by
    :func:`layer_field`, the cable every channel's AOVs
    (:func:`all_channels_fields`), ``rgba`` the beauties over each other.
    Either way the ``beauties`` cable carries each channel's beauty, named
    after the channel.
    """
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
    targets: list[Optional[tuple[bool, int]]] = []
    for field in old:
        if field in new:
            targets.append((False, list(new).index(field)))
        elif field in rehome:
            targets.append((True, list(rehome).index(field)))
        else:
            targets.append(None)
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
    now, as :func:`provenance` records it; a channel with no complete
    render is left out."""
    departments = list(render_department_names)
    reads: dict[str, dict] = dict()
    for channel in channels:
        try:
            resolved = resolve_render(renders, departments, channel)
        except CompError:
            continue
        reads[channel] = dict(department = resolved.department, version = resolved.version)
    return reads


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
        NoteSpec("Use mattes: Cable Unpack the import's aovs output",
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
