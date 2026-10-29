"""Shots' review media as a playlist for the sequence player.

The player (``src/player/``, shipped as ``bin/<platform>/th_player``) plays an
OTIO timeline: one video track, a clip per shot. Each clip opens on one take
(its media reference) and lists every take it can switch to under its
``tumblepipe`` metadata; ``src/player/playlist`` reads it. A shot's takes
are its playblasts and its renders, each by department in pipeline order
and version oldest first: every playblast, and every render version whose
frames are all there, as an EXR sequence (its beauty AOV, else its first),
for each render layer (the slapcomp last). The player steps through a
department's versions and between departments in that order. A shot opens
on the latest playblast of the most downstream department that has one,
else on its last render.

Nothing here writes to the project. ``write_playlist`` writes one file;
``play_shots`` writes it to a temp file and starts the player on it, which
is how Magnus's ask (pick shots, play them back to back, Resolve untouched)
is met.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

from tumblepipe import naming
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import list_departments
from tumblepipe.config.timeline import BlockRange, get_fps

from tumblepipe.pipe.paths.review import (
    get_playblast_path,
    list_playblast_versions,
)
from tumblepipe.pipe.paths.render import AOV, Layer, get_render_context

# The metadata key the player reads (th_playlist).
METADATA_KEY = 'tumblepipe'
# The AOV a render take shows when it has one.
BEAUTY = 'beauty'
# The render layer that comps the others, ordered after them.
SLAPCOMP = 'slapcomp'
# Render versions checked at once: each check lists a folder, which is slow
# over the network one at a time.
CHECKS = 16

@dataclass(frozen=True)
class Take:
    label: str
    department: str
    version: str
    kind: str  # 'movie' or 'exr'
    path: Path
    # A render's layer; None for a playblast.
    layer: Optional[str] = None

@dataclass(frozen=True)
class Shot:
    name: str
    entity: str
    takes: list[Take]
    take: int
    fps: Optional[int]

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
                kind = 'movie',
                path = get_playblast_path(entity_uri, department.name, version_name)
            ))
    return takes

def _shown_frames(layer: Layer) -> tuple[AOV, Optional[str]]:
    """What a render version's take shows, and the AOV's name: its beauty
    AOV, else its first, else (a layer without AOVs) the layer's own frames."""
    if layer.aovs:
        aov_name = BEAUTY if BEAUTY in layer.aovs else sorted(layer.aovs)[0]
        return layer.aovs[aov_name], aov_name
    own = AOV(path = layer.path, label = layer.label, name = layer.name, suffix = layer.suffix)
    return own, None

def _playable_range(layer: Layer) -> Optional[BlockRange]:
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

def list_render_takes(entity_uri: Uri) -> list[Take]:
    """Every render of the shot that can play (see ``_playable_range``), as
    the first frame of what it shows: render departments in pipeline order,
    then layers (the slapcomp last), then versions oldest first."""
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
            kind = 'exr',
            path = aov.get_aov_frame_path(f'{frame_range.first_frame:04d}'),
            layer = layer_name
        ))
    return takes

def default_take(takes: list[Take]) -> int:
    """The take a shot opens on: the last playblast (the latest of the most
    downstream department), else the last take (the latest render of the
    most downstream department and layer)."""
    movies = [index for index, take in enumerate(takes) if take.kind == 'movie']
    return movies[-1] if movies else max(len(takes) - 1, 0)

def get_shot(entity_uri: Uri) -> Shot:
    takes = list_playblast_takes(entity_uri) + list_render_takes(entity_uri)
    return Shot(
        name = entity_uri.segments[-1],
        entity = str(entity_uri),
        takes = takes,
        take = default_take(takes),
        fps = get_fps(entity_uri)
    )

###############################################################################
# OTIO
###############################################################################
def _path_text(path: Path) -> str:
    return str(path).replace('\\', '/')

def _media_reference(take: Take) -> dict:
    return {
        'OTIO_SCHEMA': 'ExternalReference.1',
        'name': take.label,
        'metadata': {},
        'target_url': _path_text(take.path),
        'available_range': None
    }

def _clip(shot: Shot) -> dict:
    take = shot.takes[shot.take]
    return {
        'OTIO_SCHEMA': 'Clip.2',
        'name': shot.name,
        'metadata': {METADATA_KEY: {
            'entity': shot.entity,
            'take': shot.take,
            # The rate EXR takes play at; movies keep their own.
            'fps': shot.fps,
            'takes': [
                {
                    'label': t.label,
                    'department': t.department,
                    'version': t.version,
                    'kind': t.kind,
                    'layer': t.layer,
                    'path': _path_text(t.path)
                }
                for t in shot.takes
            ]
        }},
        'source_range': None,
        'media_reference': _media_reference(take),
        'markers': [],
        'effects': [],
        'enabled': True
    }

def playlist_document(
    shots: list[Shot],
    name: str = '',
    ocio: Optional[str] = None
    ) -> dict:
    """The OTIO timeline of ``shots``, in order. A shot with no takes has
    nothing to play and is left out."""
    timeline_metadata = {}
    if ocio is not None:
        timeline_metadata[METADATA_KEY] = {'ocio': ocio.replace('\\', '/')}
    return {
        'OTIO_SCHEMA': 'Timeline.1',
        'name': name,
        'metadata': timeline_metadata,
        'global_start_time': None,
        'tracks': {
            'OTIO_SCHEMA': 'Stack.1',
            'name': 'tracks',
            'metadata': {},
            'source_range': None,
            'markers': [],
            'effects': [],
            'enabled': True,
            'children': [{
                'OTIO_SCHEMA': 'Track.1',
                'name': 'Shots',
                'kind': 'Video',
                'metadata': {},
                'source_range': None,
                'markers': [],
                'effects': [],
                'enabled': True,
                'children': [_clip(shot) for shot in shots if shot.takes]
            }]
        }
    }

def project_ocio() -> Optional[str]:
    """The project's OCIO config: ``$OCIO`` as the package sets it, else
    ``_config/ocio/tumblehead.ocio``, None when neither exists."""
    env_ocio = os.environ.get('OCIO', '').strip()
    if env_ocio and Path(env_ocio).is_file(): return env_ocio
    config_path = os.environ.get('TH_CONFIG_PATH', '').strip()
    if not config_path: return None
    path = Path(config_path) / 'ocio' / 'tumblehead.ocio'
    return str(path) if path.is_file() else None

def write_playlist(
    path: Path,
    entity_uris: list[Uri],
    name: str = ''
    ) -> list[Shot]:
    """Write the playlist of ``entity_uris`` to ``path``; the shots in it."""
    shots = [get_shot(uri) for uri in entity_uris]
    document = playlist_document(shots, name, project_ocio())
    path.write_text(json.dumps(document, indent = 4), encoding = 'utf-8')
    return [shot for shot in shots if shot.takes]

###############################################################################
# The player
###############################################################################
def _platform() -> str:
    if sys.platform == 'win32': return 'windows-x86_64'
    if sys.platform == 'darwin': return 'macos-aarch64'
    return 'linux-x86_64'

def find_player() -> Optional[Path]:
    """The player: ``$TH_PLAYER``, else the package's
    ``bin/<platform>/th_player``, else a dev build under ``src/player``."""
    exe = 'th_player.exe' if sys.platform == 'win32' else 'th_player'
    candidates = []
    if os.environ.get('TH_PLAYER'):
        candidates.append(Path(os.environ['TH_PLAYER']))
    pipeline_path = os.environ.get('TH_PIPELINE_PATH', '').strip()
    if pipeline_path:
        root = Path(pipeline_path)
        candidates.append(root / 'bin' / _platform() / exe)
        candidates.append(root / 'src' / 'player' / 'target' / 'release' / exe)
    for candidate in candidates:
        if candidate.is_file(): return candidate
    return None

def play_shots(entity_uris: list[Uri], name: str = '') -> subprocess.Popen:
    """Start the player on the shots' playlist. Raises when there is no
    player, or none of the shots has anything to play."""
    player = find_player()
    if player is None:
        raise FileNotFoundError(
            'The sequence player is not installed (no bin/'
            f'{_platform()}/th_player in the TumblePipe package)'
        )
    handle, temp_path = tempfile.mkstemp(prefix = 'tumblepipe-playlist-', suffix = '.otio')
    os.close(handle)
    shots = write_playlist(Path(temp_path), entity_uris, name)
    if not shots:
        raise ValueError('None of the shots has a playblast or a render to play')
    return subprocess.Popen([str(player), temp_path], cwd = str(player.parent))
