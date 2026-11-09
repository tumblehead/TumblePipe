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

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from tumblepipe import naming
from tumblepipe.pipe.aovs import is_ramp_aov

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

# The AOV every channel's comp needs: the RGBA beauty, whose A is the
# channel's alpha.
BEAUTY = 'beauty'
LIGHT_GROUP_PREFIX = 'beauty_'


class CompError(Exception):
    """A comp cannot be made from what is on disk; the message says why."""


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
    return name in (BEAUTY, 'albedo', 'normal', 'depth', 'uv', 'position')


def aov_signature(aov_name: str) -> str:
    """The COP data type an AOV is read as (a File COP's ``type#`` token).

    The beauty is RGBA (its A is the channel's alpha); light groups, albedo,
    normals, depth and position RGB; uv a vector2; mattes, holdouts and
    ramps one channel.
    """
    name = aov_name.lower()
    if name == BEAUTY: return 'vector4'
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


def cable_fields(aov_labels: Iterable[str]) -> list[str]:
    """The fields of import_render's ``aovs`` cable for a version's AOVs:
    the included ones, the beauty first, then by name. Unique."""
    included = {label for label in aov_labels if aov_included(label)}
    rest = sorted(label for label in included if label != BEAUTY)
    return ([BEAUTY] if BEAUTY in included else []) + rest


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
        return cable_fields(self.aovs)

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
            aovs = {
                label: aov for label, aov in layer.aovs.items()
                if aov_included(label)
            },
        )
    where = ' or '.join(reversed(candidates)) or 'any render department'
    raise CompError(f'channel "{channel}" has no complete render in {where} yet')


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
# Network-editor units. One column per channel; a Multi repeats the set per
# member, MEMBER_STRIDE further along x.
COLUMN_STRIDE = 4.0
IMPORT_Y = 3.0
MIX_Y = 1.5
OVER_Y = 0.0
OVER_STEP_Y = -1.2
EXPORT_GAP_Y = -2.0
MEMBER_GAP = 4.0


@dataclass(frozen=True)
class NoteSpec:
    text: str
    position: tuple[float, float]
    size: tuple[float, float]


@dataclass(frozen=True)
class Layout:
    """Where the template puts each node (``name -> (x, y)``), how the over
    chain is wired (``(over node, back input, front input)``, back to front),
    and its sticky notes."""
    positions: dict
    overs: list
    notes: list
    imports: dict           # import node name -> channel
    mixes: dict             # light_mix node name -> import node name
    export: str
    width: float


def template_layout(channels: list[str], suffix: str = '', x: float = 0.0) -> Layout:
    """The composite template's layout for one shot's channels.

    One column per channel in the shot's channel order: ``import_render``
    over ``light_mix``. An over chain stacks them back to front, so the last
    channel is on top (the render chain's slapcomp stacks the same way), into
    one ``export_comp``.
    """
    positions: dict = dict()
    imports: dict = dict()
    mixes: dict = dict()
    overs: list = []
    for index, channel in enumerate(channels):
        column_x = x + index * COLUMN_STRIDE
        import_name = f'import_{channel}{suffix}'
        mix_name = f'light_mix_{channel}{suffix}'
        positions[import_name] = (column_x, IMPORT_Y)
        positions[mix_name] = (column_x, MIX_Y)
        imports[import_name] = channel
        mixes[mix_name] = import_name
    mix_names = list(mixes)
    back = mix_names[0] if mix_names else None
    chain_x = x + max(len(channels) - 1, 0) * COLUMN_STRIDE
    for index, front in enumerate(mix_names[1:]):
        over_name = f'over_{channels[index + 1]}{suffix}'
        positions[over_name] = (chain_x, OVER_Y + index * OVER_STEP_Y)
        overs.append((over_name, back, front))
        back = over_name
    export_name = f'export_comp{suffix}'
    last_y = OVER_Y + max(len(overs) - 1, 0) * OVER_STEP_Y if overs else MIX_Y
    positions[export_name] = (chain_x, last_y + EXPORT_GAP_Y)
    width = max(len(channels), 1) * COLUMN_STRIDE
    notes = [
        NoteSpec('Grade a channel here, after its light mix',
                 (x - 3.6, MIX_Y - 0.9), (3.2, 0.6)),
        NoteSpec("Use mattes: Cable Unpack the import's aovs output",
                 (x - 3.6, IMPORT_Y - 0.3), (3.2, 0.6)),
        NoteSpec('Whole-frame work here, above the export',
                 (chain_x - 3.6, positions[export_name][1] + 0.5), (3.2, 0.6)),
    ]
    return Layout(
        positions = positions,
        overs = overs,
        notes = notes,
        imports = imports,
        mixes = mixes,
        export = export_name,
        width = width,
    )


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
