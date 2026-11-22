"""A shot's sound from the edit (Remy's RFE, 2026-11-22), without ``hou``.

"Load Audio from Edit" (the TumblePipe shelf) mixes the edit's audio tracks
over the shot's frames into a WAV and sets it as the scene's audio, so the
animator hears the dialogue the cut times the shot against.

- **Which edit**: the project's master edit (``pipe.edits.master_edit``),
  its newest document as Import Edit to Timeline reads it
  (``pipe.edits.document_path``): the newest working version, else the
  latest published one. Both are versioned files that never change, so a
  WAV names the version it was mixed from and is the same mix again.
- **Which tracks**: all of the edit's audio tracks, or those named (by
  name, or ``A1`` for the first). The shots' own playblast sound is left
  out: it is what the scene plays already.
- **Which frames**: the shot's range, roll included (what its playbar
  covers), mapped through its clip in the edit: shot frame ``f`` is edit
  frame ``at + f - source_in``. Past the clip the sound is the edit's
  around the cut; before the edit's start, silence. The mixing is Cinedex's
  (``cinedex --shot-audio``, the mix Render Edit makes), which decodes
  every format the editor plays; the frame maths is
  ``th_pipe::shot_audio``.
- **Where**: next to the edit's rendered movies,
  ``render:/edits/<edit>/audio/<version>/<SEQ>_<SHOT>_<tracks>.wav``
  (:func:`audio_uri`): ``<version>`` is ``vNNNN`` for a published version
  and ``vNNNN_working`` for a working one (they are numbered apart);
  ``<tracks>`` is ``all`` or the tracks asked for. Derived media, made
  again on demand: a re-run overwrites it.
- **In the scene**: the WAV's first sample is the shot's first frame, so
  the Audio Panel plays the file from that frame with no offset
  (:func:`audio_settings`). At another fps than the edit's, it drifts: the
  load warns (:func:`fps_warning`).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tumblepipe import naming
from tumblepipe.util.uri import Uri

#: The ``<tracks>`` part of a mix of every audio track.
ALL_TRACKS = 'all'


@dataclass(frozen=True)
class EditVersion:
    """The edit document a mix is made from: ``version`` of edit ``name``,
    ``published`` or working."""
    name: str
    version: str
    published: bool


@dataclass(frozen=True)
class AudioSettings:
    """What the Audio Panel is set to: play ``path`` from scene frame
    ``frame``, ``offset`` seconds into it."""
    path: str
    frame: int
    offset: float = 0.0


def pick_version(name: str, working: Sequence[str], published: Sequence[str]) -> EditVersion | None:
    """The document of edit ``name`` a mix is made from, given its working
    and published versions (each oldest first): the newest working one,
    else the latest published one, as ``pipe.edits.document_path`` picks
    it; None when it has neither."""
    if working:
        return EditVersion(name, working[-1], False)
    if published:
        return EditVersion(name, published[-1], True)
    return None


def edit_target(edit: EditVersion) -> str:
    """What ``cinedex --shot-audio`` is given for ``edit``: the published
    version by its ``@vNNNN``, a working one by the edit (Cinedex opens its
    newest working version, the one :func:`pick_version` picked)."""
    uri = f'entity:/edits/{edit.name}'
    return f'{uri}@{edit.version}' if edit.published else uri


def _word(name: str) -> str:
    """A track name as a file name part: letters and digits, the rest
    dropped."""
    return re.sub(r'[^A-Za-z0-9]+', '', name)


def tracks_key(tracks: Sequence[str]) -> str:
    """The ``<tracks>`` part of a mix's file name: :data:`ALL_TRACKS` for
    every track (no names), else the names asked for as file name words,
    sorted, case folded and once each, joined by ``-``. Two asks for the
    same tracks are the same file."""
    words = sorted({_word(t).lower() for t in tracks if _word(t)})
    return '-'.join(words) if words else ALL_TRACKS


def version_folder(edit: EditVersion) -> str:
    """``vNNNN`` for a published version, ``vNNNN_working`` for a working
    one."""
    if not naming.is_valid_version_name(edit.version):
        raise ValueError(f'{edit.version!r} is not a version')
    return edit.version if edit.published else f'{edit.version}_working'


def audio_uri(edit: EditVersion, shot: Uri, tracks: Sequence[str]) -> Uri:
    """``render:/edits/<edit>/audio/<version>/<SEQ>_<SHOT>_<tracks>.wav``,
    the mix of ``tracks`` (none: all) of ``edit`` over ``shot``."""
    if shot.purpose != 'entity' or len(shot.segments) < 2 or shot.segments[0] != 'shots':
        raise ValueError(f'{shot} is not a shot')
    stem = '_'.join([*shot.segments[1:], tracks_key(tracks)])
    return Uri.parse('render:/edits') / edit.name / 'audio' / version_folder(edit) / f'{stem}.wav'


def shot_audio_arguments(
    player: Path, project: str, edit: EditVersion, shot: Uri, out: Path,
    tracks: Sequence[str] = (), frames: tuple[int, int] | None = None,
) -> list[str]:
    """The command line that mixes ``tracks`` (none: all) of ``edit`` over
    ``shot``'s ``frames`` (first and last; None: Cinedex reads the shot's
    range) into ``out``."""
    arguments = [
        str(player), '--project', project,
        '--shot-audio', edit_target(edit), '--shot', str(shot), '--out', str(out),
    ]
    names = [t.strip() for t in tracks if t.strip()]
    if any(',' in t for t in names):
        raise ValueError(f'A track name with a comma cannot be asked for by name: ask for it by place (A1, A2, ...): {names}')
    if names:
        arguments += ['--tracks', ','.join(names)]
    if frames is not None:
        first, last = frames
        if last < first:
            raise ValueError(f'The frames {first}-{last} are no range')
        arguments += ['--first', str(first), '--last', str(last)]
    return arguments


def read_summary(stdout: str) -> dict:
    """The summary ``cinedex --shot-audio`` printed: its last line that is
    a JSON object. ValueError when there is none."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            summary = json.loads(line)
        except ValueError:
            continue
        if isinstance(summary, Mapping):
            return dict(summary)
    raise ValueError('Cinedex printed no summary of the mix')


def audio_settings(path: str | Path, summary: Mapping) -> AudioSettings:
    """The Audio Panel's settings for the WAV at ``path`` that ``summary``
    describes: its first sample is the shot frame ``summary['first']``,
    so it plays from that frame, no offset."""
    first = summary.get('first')
    if isinstance(first, bool) or not isinstance(first, int):
        raise ValueError(f'The mix summary names no first frame: {summary!r}')
    return AudioSettings(str(path), first, 0.0)


def audiopanel_command(settings: AudioSettings) -> str:
    """The HScript ``audiopanel`` command that sets the Audio Panel to
    ``settings`` (for a session without ``hou.audio``, which needs a UI):
    an audio file, from its frame, at its offset, in timeline mode. The
    path goes forward-slashed and in single quotes, so a ``$`` in it is not
    expanded; one with a single quote is refused (HScript has no escape
    for it there)."""
    path = str(settings.path).replace('\\', '/')
    if "'" in path:
        raise ValueError(f'An audio file path with a single quote cannot be set: {path}')
    return (
        f"audiopanel -m 1 -a '{path}' -f {settings.frame} "
        f'-O {settings.offset!r} -o d 1'
    )


def fps_warning(edit_fps: int, scene_fps: int) -> str:
    """Why the sound will drift, '' when it will not: the edit is cut at
    ``edit_fps`` and the scene plays at ``scene_fps``."""
    if edit_fps == scene_fps:
        return ''
    return (
        f'The edit is cut at {edit_fps} fps and the scene plays at {scene_fps} fps: '
        f'the sound starts on the shot\'s first frame but drifts from the picture '
        f'after it. Set the scene to {edit_fps} fps to keep them together.'
    )


def audio_track_names(document: Mapping) -> list[str]:
    """The labels of an edit document's audio tracks, in order: each one's
    name, ``A<n>`` for one without (as the editor shows it)."""
    tracks = document.get('audio') if isinstance(document, Mapping) else None
    if not isinstance(tracks, list):
        return []
    names = []
    for index, track in enumerate(tracks):
        name = track.get('name') if isinstance(track, Mapping) else None
        names.append(name.strip() if isinstance(name, str) and name.strip() else f'A{index + 1}')
    return names


# ── Making the mix (disk and Cinedex, no hou) ───────────────────────────


@dataclass(frozen=True)
class ShotAudio:
    """A mix made: where it is, from which edit document, and Cinedex's
    summary of it."""
    path: Path
    edit: EditVersion
    summary: dict


def master_version(client=None) -> EditVersion:
    """The master edit's document a mix is made from (:func:`pick_version`).
    ValueError when the project has no master edit or it has no version."""
    from tumblepipe.pipe import edits
    name = edits.master_edit(client)
    if name is None:
        raise ValueError(
            'The project has no master edit: tick Master edit on one in the '
            'editor, the cut whose sound the shots take.'
        )
    picked = pick_version(name, edits.working_versions(name, client), edits.published_versions(name, client))
    if picked is None:
        raise ValueError(f'The master edit {name} has no saved or published version yet')
    return picked


def document_of(edit: EditVersion, client=None) -> dict:
    """The JSON of ``edit``'s document."""
    from tumblepipe.pipe import edits
    folder = (edits.export_dir(edit.name, client) / edit.version) if edit.published else edits.working_dir(edit.name, client)
    path = folder / edits.working_file_name(edit.name, edit.version)
    try:
        document = json.loads(path.read_bytes().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f'Edit {edit.name}: {path} does not read: {exc}') from exc
    if not isinstance(document, dict):
        raise ValueError(f'Edit {edit.name}: {path} is not an edit document')
    return document


def shot_frames(shot: Uri, client=None) -> tuple[int, int] | None:
    """``shot``'s frames, roll included; None when it has no range."""
    from tumblepipe.config.timeline import get_frame_range_in
    from tumblepipe.pipe.edits import _client
    frame_range = get_frame_range_in(_client(client).config, shot)
    if frame_range is None:
        return None
    full = frame_range.full_range()
    return full.first_frame, full.last_frame


def export_shot_audio(
    shot: Uri, tracks: Sequence[str] = (), edit: EditVersion | None = None,
    client=None, timeout: float = 600.0,
) -> ShotAudio:
    """Mix ``tracks`` (none: all) of ``edit`` (None: the master edit's,
    :func:`master_version`) over ``shot``'s frames into its
    :func:`audio_uri`, with Cinedex. Raises when there is no player, no
    project, the shot is not in the edit, a track is not there, or the mix
    fails (with what Cinedex said)."""
    from tumblepipe.pipe import review_playlist
    from tumblepipe.pipe.edits import _client
    client = _client(client)
    edit = edit or master_version(client)
    out = Path(client.storage.resolve(audio_uri(edit, shot, tracks)))
    player = review_playlist.find_player()
    project = review_playlist._current_project()
    arguments = shot_audio_arguments(player, project, edit, shot, out, tracks, shot_frames(shot, client))
    done = subprocess.run(
        arguments, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
        cwd=str(player.parent), **review_playlist._no_console(),
    )
    stdout = done.stdout.decode('utf-8', 'replace')
    stderr = done.stderr.decode('utf-8', 'replace').strip()
    if done.returncode != 0:
        said = stderr.removeprefix('cinedex: ') or stdout.strip() or f'exit code {done.returncode}'
        raise ValueError(f'The sound of {shot} from edit {edit.name} {edit.version} was not made: {said}')
    return ShotAudio(out, edit, read_summary(stdout))
