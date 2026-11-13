"""Shots' review media (their *takes*), and starting the editor on shots
or an edit.

The editor, Cinedex (``src/cinedex/``, shipped as ``bin/<platform>/cinedex``), finds
a shot's takes itself, in Rust (``th_pipe::discovery``); this module is the
specification it ports, which ``tests/test_pipe_parity.py`` holds it to. A
shot's takes are its playblasts and its renders, each by department in
pipeline order and version oldest first: every playblast, and every render
version whose frames are all there, as an EXR sequence (its beauty AOV,
else its first), for each render layer (the slapcomp last). The player
steps through a department's versions and between departments in that
order. A shot opens on the latest playblast of the most downstream
department that has one, else on its last render.

A shot's render layers are its channels (``config.channels.list_channels``:
'default' first, then the others, bottom to top). Where they were made
apart, a department also gets *stack* takes, the layers on top of each
other, each labelled ``"<dept> <version> layers"``, right after its plain
takes of that kind: a playblast version with layer movies
(``paths.review.list_layer_playblast_movies``) stacks the ones it has; a
render version with at least two channel layers that play (not the
slapcomp, which comps them already) stacks those that share the bottom
one's frame range. The player shows a stack's layers as rows over its
track and can hide each. A shot never opens on a stack while it has a plain
take: every stack is of a version that has one.

Nothing here writes to the project. ``play_shots`` starts the editor on
shots, end to end in an untitled edit (Magnus's ask: pick shots, play them
back to back, Resolve untouched), and ``open_edit`` on an edit; the
editor's output goes to ``PLAYER_LOG``.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import sys
import tempfile

from tumblepipe import naming
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import list_channels
from tumblepipe.config.department import list_departments
from tumblepipe.config.timeline import BlockRange, get_frame_range, require_fps

from tumblepipe.pipe.paths.review import (
    get_playblast_path,
    list_layer_playblast_movies,
    list_playblast_versions,
)
from tumblepipe.pipe.paths.render import AOV, Layer, get_render_context

# The AOV a render take shows when it has one.
BEAUTY = 'beauty'
# The render layer that comps the others, ordered after them.
SLAPCOMP = 'slapcomp'
# Digits in a rendered frame's number (``$F4``).
FRAME_PADDING = 4
# Render versions checked at once: each check lists a folder, which is slow
# over the network one at a time.
CHECKS = 16

@dataclass(frozen=True)
class Movie:
    """A playblast."""
    path: Path

@dataclass(frozen=True)
class Frames:
    """A numbered frame sequence: frame ``n`` is
    ``directory / f'{prefix}{n:0{padding}d}{suffix}'``, for ``n`` from
    ``first`` to ``last`` by ``step``."""
    directory: Path
    prefix: str
    suffix: str
    padding: int
    first: int
    last: int
    step: int

    def path(self, number: int) -> Path:
        return self.directory / f'{self.prefix}{number:0{self.padding}d}{self.suffix}'

@dataclass(frozen=True)
class Exr:
    """A render: one AOV of one render layer."""
    layer: str
    frames: Frames

@dataclass(frozen=True)
class Stack:
    """Render layers on top of each other, bottom first, by name: all
    movies (a layered playblast) or all renders of one frame range."""
    layers: tuple[tuple[str, Movie | Exr], ...]

# What a stack take's label ends in.
STACK_LABEL = 'layers'

def _kind_of(media: Movie | Exr | Stack) -> str:
    if isinstance(media, Stack): return _kind_of(media.layers[0][1])
    return 'movie' if isinstance(media, Movie) else 'exr'

@dataclass(frozen=True)
class Take:
    label: str
    department: str
    version: str
    media: Movie | Exr | Stack

    @property
    def kind(self) -> str:
        return _kind_of(self.media)

    @property
    def is_stack(self) -> bool:
        return isinstance(self.media, Stack)

@dataclass(frozen=True)
class Shot:
    name: str
    sequence: str
    fps: int
    # The cut, in frames: how long the shot stands in the sequence while its
    # take cannot play.
    length: int
    takes: list[Take]
    take: int

###############################################################################
# Takes
###############################################################################
def list_playblast_takes(entity_uri: Uri) -> list[Take]:
    """Every playblast of the shot: departments in pipeline order, versions
    ascending, each department's layered versions (as stacks of their layer
    movies, in channel order) after its plain ones."""
    channels = list_channels(entity_uri)
    takes = []
    for department in list_departments('shots'):
        versions = list_playblast_versions(entity_uri, department.name)
        stacks = []
        for version_name in versions:
            takes.append(Take(
                label = f'{department.name} {version_name}',
                department = department.name,
                version = version_name,
                media = Movie(get_playblast_path(entity_uri, department.name, version_name))
            ))
            movies = list_layer_playblast_movies(entity_uri, department.name, version_name, channels)
            if not movies: continue
            stacks.append(Take(
                label = f'{department.name} {version_name} {STACK_LABEL}',
                department = department.name,
                version = version_name,
                media = Stack(tuple((layer_name, Movie(path)) for layer_name, path in movies.items()))
            ))
        takes += stacks
    return takes

def _shown_frames(layer: Layer) -> tuple[AOV, str | None]:
    """What a render version's take shows, and the AOV's name: its beauty
    AOV, else its first, else (a layer without AOVs) the layer's own frames."""
    if layer.aovs:
        aov_name = BEAUTY if BEAUTY in layer.aovs else sorted(layer.aovs)[0]
        return layer.aovs[aov_name], aov_name
    own = AOV(path = layer.path, label = layer.label, name = layer.name, suffix = layer.suffix)
    return own, None

def _playable_range(layer: Layer) -> BlockRange | None:
    """The frame range of a render version that can play: it records one
    and every frame of what it shows is there. None for a render still
    running, or one that stopped short."""
    frame_range = layer.get_frame_range()
    if frame_range is None: return None
    aov, _ = _shown_frames(layer)
    return frame_range if aov.is_complete(frame_range) else None

def _layer_order(layer_name: str) -> tuple[bool, str]:
    """Render layers in order: the channels by name, then the slapcomp."""
    return (layer_name == SLAPCOMP, layer_name)

def _frames(aov: AOV, frame_range: BlockRange) -> Frames:
    """``aov``'s frames over ``frame_range``, as ``AOV.get_aov_frame_path``
    names them."""
    return Frames(
        directory = aov.path,
        prefix = f'{aov.name}.',
        suffix = f'.{aov.suffix}',
        padding = FRAME_PADDING,
        first = frame_range.first_frame,
        last = frame_range.last_frame,
        step = frame_range.step_size
    )

def _render_stacks(department_takes: list[Take], channels: list[str]) -> list[Take]:
    """A department's render stacks, versions oldest first: for each
    version, its takes of the shot's channels (not the slapcomp), in channel
    order, that share the bottom one's frame range, when there are two."""
    by_version: dict[str, dict[str, Exr]] = {}
    for take in department_takes:
        by_version.setdefault(take.version, {})[take.media.layer] = take.media
    stacks = []
    for version_name in sorted(by_version, key = naming.get_version_code):
        found = by_version[version_name]
        layers = [(name, found[name]) for name in channels if name != SLAPCOMP and name in found]
        if not layers: continue
        bottom = layers[0][1].frames
        span = (bottom.first, bottom.last, bottom.step)
        layers = [
            (name, exr) for name, exr in layers
            if (exr.frames.first, exr.frames.last, exr.frames.step) == span
        ]
        if len(layers) < 2: continue
        department_name = department_takes[0].department
        stacks.append(Take(
            label = f'{department_name} {version_name} {STACK_LABEL}',
            department = department_name,
            version = version_name,
            media = Stack(tuple(layers))
        ))
    return stacks

def list_render_takes(entity_uri: Uri) -> list[Take]:
    """Every render of the shot that can play (see ``_playable_range``):
    render departments in pipeline order, then layers (the slapcomp last),
    then versions oldest first; each department's stacks (see
    ``_render_stacks``) after its plain takes."""
    takes = _plain_render_takes(entity_uri)
    channels = list_channels(entity_uri)
    ordered = []
    for department_name in dict.fromkeys(take.department for take in takes):
        department_takes = [take for take in takes if take.department == department_name]
        ordered += department_takes + _render_stacks(department_takes, channels)
    return ordered

def _plain_render_takes(entity_uri: Uri) -> list[Take]:
    context = get_render_context(entity_uri)
    versions = [
        (department_name, layer_name, layers[version_name])
        for department_name, render in context.renders.items()
        for layer_name, layers in sorted(render.layers.items(), key = lambda item: _layer_order(item[0]))
        for version_name in sorted(layers, key = naming.get_version_code)
    ]
    with ThreadPoolExecutor(CHECKS) as pool:
        ranges = list(pool.map(lambda version: _playable_range(version[2]), versions))
    takes = []
    for (department_name, layer_name, layer), frame_range in zip(versions, ranges):
        if frame_range is None: continue
        aov, aov_name = _shown_frames(layer)
        label = f'{department_name} {layer.version} {layer_name}'
        if aov_name is not None: label = f'{label} {aov_name}'
        takes.append(Take(
            label = label,
            department = department_name,
            version = layer.version,
            media = Exr(layer_name, _frames(aov, frame_range))
        ))
    return takes

def _written_at(take: Take) -> float:
    try:
        return take.media.path.stat().st_mtime
    except OSError:
        return float('-inf')

def default_take(takes: list[Take]) -> int:
    """The take a shot opens on: the playblast written last, whichever
    department it was cut at (a tie goes to the more downstream one), else
    the last plain take (the latest render of the most downstream department
    and layer), else (only stacks) the last take.

    Not the most downstream department's latest: a farm playblast cut at
    animation after one cut at render would never open (wifi_retina). Never
    a stack while there is a plain take: a stack is a closer look at a
    version, not what a shot is."""
    plain = [index for index, take in enumerate(takes) if not take.is_stack]
    movies = [index for index in plain if takes[index].kind == 'movie']
    if movies: return max(movies, key = lambda index: (_written_at(takes[index]), index))
    if plain: return plain[-1]
    return max(len(takes) - 1, 0)

def sequence_of(entity_uri: Uri) -> str:
    """The sequence a shot is in: its path between ``shots`` and its name."""
    return '/'.join(entity_uri.segments[1:-1])

def get_shot(entity_uri: Uri) -> Shot:
    """The shot and its takes (possibly none). Raises when the shot has no
    fps or frame range configured: the player cannot place it."""
    frame_range = get_frame_range(entity_uri)
    if frame_range is None:
        raise ValueError(f'No frame range configured for {entity_uri}')
    takes = list_playblast_takes(entity_uri) + list_render_takes(entity_uri)
    return Shot(
        name = entity_uri.segments[-1],
        sequence = sequence_of(entity_uri),
        fps = require_fps(entity_uri),
        length = len(frame_range.play_range()),
        takes = takes,
        take = default_take(takes)
    )

def project_ocio() -> str:
    """The project's OCIO config: ``$OCIO``, as the package sets it."""
    ocio = os.environ.get('OCIO', '').strip()
    if not ocio:
        raise ValueError('$OCIO is not set: the editor shows renders through the project\'s OCIO config')
    if not Path(ocio).is_file():
        raise FileNotFoundError(f'The OCIO config $OCIO names is not there: {ocio}')
    return ocio

###############################################################################
# The player
###############################################################################
def _platform() -> str:
    if sys.platform == 'win32': return 'windows-x86_64'
    if sys.platform == 'darwin': return 'macos-aarch64'
    if sys.platform == 'linux': return 'linux-x86_64'
    raise OSError(f'The editor is not built for {sys.platform}')

#: The variables that name a dev build of the editor, first set wins.
PLAYER_OVERRIDES = ('TH_CINEDEX', 'TH_PLAYER')

def find_player() -> Path:
    """The editor: ``$TH_CINEDEX`` when set (a dev build), else
    ``$TH_PLAYER`` (its name before Cinedex), else the package's
    ``bin/<platform>/cinedex``. Raises when it is not there."""
    exe = 'cinedex.exe' if sys.platform == 'win32' else 'cinedex'
    overrides = (os.environ.get(name, '').strip() for name in PLAYER_OVERRIDES)
    override = next((value for value in overrides if value), '')
    if override:
        player = Path(override)
    else:
        pipeline_path = os.environ.get('TH_PIPELINE_PATH', '').strip()
        if not pipeline_path:
            raise ValueError('$TH_PIPELINE_PATH is not set: cannot find the sequence player')
        player = Path(pipeline_path) / 'bin' / _platform() / exe
    if not player.is_file():
        raise FileNotFoundError(f'The sequence player is not installed: no {player}')
    return player

#: What the last editor started printed (each start replaces it).
PLAYER_LOG = Path(tempfile.gettempdir()) / 'cinedex.log'

def editor_arguments(player: Path, project: str, ocio: str, entity_uris: list[Uri]) -> list[str]:
    """The command line that opens ``entity_uris`` in the editor, in order,
    as an untitled edit of the project at ``project``."""
    if not entity_uris:
        raise ValueError('Nothing to play: no shot')
    return [str(player), '--project', project, '--ocio', ocio, '--shots', *map(str, entity_uris)]

def edit_arguments(player: Path, project: str, ocio: str, edit_uri: Uri, version: str | None = None) -> list[str]:
    """The command line that opens the edit ``edit_uri`` of the project at
    ``project`` in the editor: its newest working version, or with
    ``version`` that published version, read-only."""
    if edit_uri.purpose != 'entity' or len(edit_uri.segments) != 2 or edit_uri.segments[0] != 'edits':
        raise ValueError(f'{edit_uri} is not an edit')
    if version is not None and not naming.is_valid_version_name(version):
        raise ValueError(f'{version!r} is not a version')
    target = str(edit_uri) if version is None else f'{edit_uri}@{version}'
    return [str(player), '--project', project, '--ocio', ocio, '--edit', target]

def render_arguments(player: Path, project: str, ocio: str, edit_uri: Uri, version: str, out_dir: Path) -> list[str]:
    """The command line that renders published version ``version`` of the
    edit ``edit_uri`` headless into ``out_dir``: ``edit.NNNN.png`` from 0,
    ``edit.wav`` when it has sound, and ``render.json`` describing them
    (Render Edit, ``farm/workers/edit_render.py``)."""
    if edit_uri.purpose != 'entity' or len(edit_uri.segments) != 2 or edit_uri.segments[0] != 'edits':
        raise ValueError(f'{edit_uri} is not an edit')
    if not naming.is_valid_version_name(version):
        raise ValueError(f'{version!r} is not a version')
    return [
        str(player), '--project', project, '--ocio', ocio,
        '--render', f'{edit_uri}@{version}', '--out', str(out_dir),
    ]

def frames_arguments(player: Path, ocio: str, folder: Path) -> list[str]:
    """The command line that opens the preview frames of the local farm
    batch in ``folder`` (``farm.local.previews.batch_previews``), or the
    frames in any other folder, as stills (#75). No project: only the OCIO
    config they are shown through."""
    return [str(player), '--ocio', ocio, '--frames', str(folder)]

def open_preview_frames(folder: Path) -> None:
    """Start the editor on the preview frames of the local farm batch in
    ``folder`` (the Farm Manager's "Open in player"). Raises when there is
    no player or no OCIO config."""
    player = find_player()
    _start(player, frames_arguments(player, project_ocio(), Path(folder)))

def _current_project() -> str:
    project = os.environ.get('TH_PROJECT_PATH', '').strip()
    if not project:
        raise ValueError('$TH_PROJECT_PATH is not set: which project to open is unknown')
    return project

def play_shots(entity_uris: list[Uri]) -> None:
    """Start the editor on ``entity_uris``, end to end in an untitled edit of
    the current project (``$TH_PROJECT_PATH``). The editor finds each shot's
    takes; one with nothing made yet shows as an empty clip saying so.
    Raises when there is no player, no OCIO config, no project or no shot."""
    player = find_player()
    _start(player, editor_arguments(player, _current_project(), project_ocio(), entity_uris))

def open_edit(edit_uri: Uri, version: str | None = None) -> None:
    """Start the editor on the edit ``edit_uri`` of the current project
    (``$TH_PROJECT_PATH``): its newest working version (an edit never saved
    opens empty), or with ``version`` that published version, read-only.
    Raises when there is no player, no OCIO config or no project."""
    player = find_player()
    _start(player, edit_arguments(player, _current_project(), project_ocio(), edit_uri, version))

def project_arguments(player: Path, project: str, ocio: str) -> list[str]:
    """The command line that opens the editor on ``project`` with no edit
    named: an untitled edit of every shot (what ``editor_app`` starts)."""
    return [str(player), '--project', project, '--ocio', ocio]

def timeline_arguments(
    player: Path, project: str, ocio: str, shots: list[Uri], master: str | None,
) -> list[str]:
    """The command line of the asset browser's Open Timeline: the ``shots``
    when any are given (an untitled edit of them, as Play), else the master
    edit ``master`` (its name), else every shot of the project."""
    if shots:
        return editor_arguments(player, project, ocio, shots)
    if master is not None:
        return edit_arguments(player, project, ocio, Uri.parse(f'entity:/edits/{master}'))
    return project_arguments(player, project, ocio)

def open_timeline(shots: list[Uri]) -> None:
    """Start the editor as Open Timeline does (``timeline_arguments``) on
    the current project (``$TH_PROJECT_PATH``). Raises when there is no
    player, no OCIO config or no project."""
    player = find_player()
    ocio = project_ocio()
    project = _current_project()
    master = None
    if not shots:
        from tumblepipe.pipe.edits import master_edit
        master = master_edit()
    _start(player, timeline_arguments(player, project, ocio, shots, master))

def view_arguments(player: Path, project: str, ocio: str, edit_uri: Uri, shot: str, frame: int | None) -> list[str]:
    """The command line that opens the edit ``edit_uri`` showing ``shot``
    as Live view does: its clip looping with the shots around it, the
    playhead on shot frame ``frame``."""
    view = ['--view', shot] + ([] if frame is None else ['--at', str(int(frame))])
    return edit_arguments(player, project, ocio, edit_uri) + view

def view_on_master(shot: str, frame: int | None) -> None:
    """Start the editor on the current project's master edit showing
    ``shot`` (Live view, when no open editor is on it). Raises when the
    project has no master edit, or there is no player, OCIO config or
    project."""
    from tumblepipe.pipe.edits import master_edit
    master = master_edit()
    if master is None:
        raise ValueError('the project has no master edit: tick Master edit on one in the editor to use Live view')
    player = find_player()
    edit_uri = Uri.parse(f'entity:/edits/{master}')
    _start(player, view_arguments(player, _current_project(), project_ocio(), edit_uri, shot, frame))

def _start(player: Path, arguments: list[str]) -> None:
    """Start the editor with ``arguments``, its output to ``PLAYER_LOG``."""
    # The editor outlives Houdini, so it must not hold Houdini's output: when
    # that is a pipe (TumbleTrove Desktop reads it), whoever reads it would
    # wait on the editor too. Its own log keeps what it prints, a panic
    # included; errors it shows in its window. Cinedex is a console
    # program (``--render`` prints to it), and Houdini has no console, so on
    # Windows it would get a new, empty console window of its own beside the
    # editor's (QA 2026-11-10): ask for none.
    with PLAYER_LOG.open('wb') as log:
        subprocess.Popen(
            arguments,
            stdin = subprocess.DEVNULL,
            stdout = log,
            stderr = log,
            cwd = str(player.parent),
            **_no_console()
        )

def _no_console() -> dict:
    """Popen's arguments that start a console program without a console
    window: ``CREATE_NO_WINDOW`` on Windows, nothing elsewhere."""
    if sys.platform != 'win32': return {}
    return {'creationflags': subprocess.CREATE_NO_WINDOW}
