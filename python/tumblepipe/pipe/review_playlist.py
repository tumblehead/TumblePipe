"""Shots' review media as a playlist for the sequence player.

The player (``src/player/``, shipped as ``bin/<platform>/th_player``) reads a
TumblePipe playlist on its standard input: the OCIO config its renders show
through, and the shots in order, each with its sequence, rate, cut length
and every take it can switch to; ``src/player/playlist`` (``th_playlist``)
is the reader and the schema, and refuses anything it does not know. A
shot's takes are its playblasts and its renders, each by department in
pipeline order and version oldest first: every playblast, and every render
version whose frames are all there, as an EXR sequence (its beauty AOV,
else its first), for each render layer (the slapcomp last). The player
steps through a department's versions and between departments in that
order. A shot opens on the latest playblast of the most downstream
department that has one, else on its last render.

Nothing here writes to the project. ``play_shots`` hands the playlist to
the player and starts it, which is how Magnus's ask (pick shots, play them
back to back, Resolve untouched) is met; the player's output goes to
``PLAYER_LOG``.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

from tumblepipe import naming
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import list_departments
from tumblepipe.config.timeline import BlockRange, get_frame_range, require_fps

from tumblepipe.pipe.paths.review import (
    get_playblast_path,
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
class Take:
    label: str
    department: str
    version: str
    media: Movie | Exr

    @property
    def kind(self) -> str:
        return 'movie' if isinstance(self.media, Movie) else 'exr'

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
    ascending."""
    takes = []
    for department in list_departments('shots'):
        for version_name in list_playblast_versions(entity_uri, department.name):
            takes.append(Take(
                label = f'{department.name} {version_name}',
                department = department.name,
                version = version_name,
                media = Movie(get_playblast_path(entity_uri, department.name, version_name))
            ))
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

def list_render_takes(entity_uri: Uri) -> list[Take]:
    """Every render of the shot that can play (see ``_playable_range``):
    render departments in pipeline order, then layers (the slapcomp last),
    then versions oldest first."""
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
    the last take (the latest render of the most downstream department and
    layer).

    Not the most downstream department's latest: a farm playblast cut at
    animation after one cut at render would never open (wifi_retina)."""
    movies = [index for index, take in enumerate(takes) if take.kind == 'movie']
    if not movies: return max(len(takes) - 1, 0)
    return max(movies, key = lambda index: (_written_at(takes[index]), index))

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

###############################################################################
# The playlist
###############################################################################
def _path_text(path: Path) -> str:
    return str(path).replace('\\', '/')

def _media(media: Movie | Exr) -> dict:
    if isinstance(media, Movie):
        return {'kind': 'movie', 'path': _path_text(media.path)}
    frames = media.frames
    return {
        'kind': 'exr',
        'layer': media.layer,
        'frames': {
            'directory': _path_text(frames.directory),
            'prefix': frames.prefix,
            'suffix': frames.suffix,
            'padding': frames.padding,
            'first': frames.first,
            'last': frames.last,
            'step': frames.step
        }
    }

def _shot(shot: Shot) -> dict:
    if not shot.takes:
        raise ValueError(f'Shot {shot.name} has nothing to play')
    return {
        'name': shot.name,
        'sequence': shot.sequence,
        'fps': shot.fps,
        'length': shot.length,
        'take': shot.take,
        'takes': [
            {
                'label': take.label,
                'department': take.department,
                'version': take.version,
                'media': _media(take.media)
            }
            for take in shot.takes
        ]
    }

def playlist_document(shots: list[Shot], name: str, ocio: str) -> dict:
    """The playlist of ``shots``, in order, shown through the OCIO config
    ``ocio``. Every shot must have a take."""
    if not shots:
        raise ValueError('A playlist needs a shot')
    return {
        'name': name,
        'ocio': ocio.replace('\\', '/'),
        'shots': [_shot(shot) for shot in shots]
    }

def project_ocio() -> str:
    """The project's OCIO config: ``$OCIO``, as the package sets it."""
    ocio = os.environ.get('OCIO', '').strip()
    if not ocio:
        raise ValueError('$OCIO is not set: the player shows renders through the project\'s OCIO config')
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
    raise OSError(f'The sequence player is not built for {sys.platform}')

def find_player() -> Path:
    """The player: ``$TH_PLAYER`` when set (a dev build), else the package's
    ``bin/<platform>/th_player``. Raises when it is not there."""
    exe = 'th_player.exe' if sys.platform == 'win32' else 'th_player'
    override = os.environ.get('TH_PLAYER', '').strip()
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

#: What the last player started printed (each start replaces it).
PLAYER_LOG = Path(tempfile.gettempdir()) / 'th_player.log'

def play_shots(entity_uris: list[Uri], name: str) -> list[str]:
    """Start the player on the shots that have something to play, in order;
    the names of those that have not, which are left out. Raises when there
    is no player, no OCIO config, or nothing to play."""
    player = find_player()
    ocio = project_ocio()
    shots = [get_shot(uri) for uri in entity_uris]
    playable = [shot for shot in shots if shot.takes]
    left_out = [shot.name for shot in shots if not shot.takes]
    if not playable:
        raise ValueError(f'Nothing to play: {", ".join(left_out)} have no playblast or render')
    document = json.dumps(playlist_document(playable, name, ocio))
    # The player outlives Houdini, so it must not hold Houdini's output: when
    # that is a pipe (TumbleTrove Desktop reads it), whoever reads it would
    # wait on the player too. Its own log keeps what it prints, a panic
    # included; errors it shows in its window.
    with PLAYER_LOG.open('wb') as log:
        process = subprocess.Popen(
            [str(player)],
            stdin = subprocess.PIPE,
            stdout = log,
            stderr = log,
            cwd = str(player.parent)
        )
    # The player reads the playlist to its end before it opens a window.
    with process.stdin as stdin:
        stdin.write(document.encode('utf-8'))
    return left_out
