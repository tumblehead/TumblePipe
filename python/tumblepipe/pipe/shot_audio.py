"""A shot's sound from the edit (Remy's RFE, 2026-11-22), without ``hou``.

"Load Audio from Edit" (the TumblePipe shelf) mixes the edit's audio tracks
over the shot's frames into a WAV and sets it as the scene's audio, so the
animator hears the dialogue the cut times the shot against.

- **Which edit**: the project's master edit (``pipe.edits.master_edit``),
  its newest document as Import Edit to Timeline reads it
  (``pipe.edits.document_path``): the newest working version, else the
  latest published one. Both are versioned files that never change, so a
  WAV names the version it was mixed from and is the same mix again.
- **Which tracks**: all of the edit's audio tracks, or those picked, asked
  for by place (``A1`` for the first; a place wins over a track named
  ``A1``, see ``th_pipe::shot_audio::select_tracks``). The shots' own
  playblast sound is left out: it is what the scene plays already.
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
  ``<tracks>`` is ``all`` or the places picked (``a1-a3``). Derived media,
  made again on demand: a re-run overwrites it.
- **What it was made of**: next to the WAV, ``<wav>.json``
  (:func:`sidecar_path`, :func:`sidecar_json`): the edit version mixed,
  the shot and its frames, and the tracks, by place and by name with the
  names of all the edit's audio tracks then, so the mix can be made again
  from a newer version with the same tracks.
- **In the scene**: the WAV's first sample is the shot's first frame, so
  the Audio Panel plays the file from that frame with no offset
  (:func:`audio_settings`). At another fps than the edit's, it drifts: the
  load warns (:func:`fps_warning`).
- **Kept up to date** (Soren, 2026-11-22): when a shot workfile opens in a
  GUI session playing such a WAV and the master edit has a newer version
  than the one it was mixed from (the same pick as above), the mix is
  made again from it with the same tracks, followed by name
  (:func:`follow_tracks`), and the scene plays the new file
  (:func:`plan_refresh`, :func:`refresh_shot_audio`; the scene side is
  ``pipe.houdini.shot_audio.refresh_on_open``). A scene playing any other
  sound is left alone. A track that is gone, or whose name now fits more
  or fewer tracks than it did, keeps the old sound and says why.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tumblepipe import naming
from tumblepipe.util.uri import Uri

log = logging.getLogger(__name__)

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
    """What ``cinedex --shot-audio`` is given for ``edit``: the edit and
    that very version, ``@vNNNN`` for a published one and
    ``@vNNNN_working`` for a working one (:func:`version_folder`). Never the
    edit by name alone, which Cinedex reads as its newest working version:
    one saved between choosing the tracks (by place, in ``edit``) and the
    mix would be mixed with those places pointing at other tracks."""
    return f'entity:/edits/{edit.name}@{version_folder(edit)}'


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


def raw_track_names(document: Mapping) -> list[str]:
    """The names of an edit document's audio tracks, in order, surrounding
    space stripped: '' for a track without one."""
    tracks = document.get('audio') if isinstance(document, Mapping) else None
    if not isinstance(tracks, list):
        return []
    names = []
    for track in tracks:
        name = track.get('name') if isinstance(track, Mapping) else None
        names.append(name.strip() if isinstance(name, str) else '')
    return names


def track_label(names: Sequence[str], place: int) -> str:
    """How the editor labels the track at ``place`` (1 the first) of tracks
    named ``names``: its name, ``A<place>`` without one."""
    name = names[place - 1].strip() if 0 < place <= len(names) else ''
    return name or f'A{place}'


def audio_track_names(document: Mapping) -> list[str]:
    """The labels of an edit document's audio tracks, in order: each one's
    name, ``A<n>`` for one without (as the editor shows it)."""
    names = raw_track_names(document)
    return [track_label(names, place) for place in range(1, len(names) + 1)]


def place_words(places: Iterable[int]) -> list[str]:
    """What asks Cinedex for the tracks at ``places`` (1 the first): ``A1``,
    ``A3``, ..., once each, in order."""
    return [f'A{place}' for place in sorted(set(places))]


# ── Keeping a scene's sound up to date (no hou) ─────────────────────────


#: The ``kind`` of a mix's sidecar.
SIDECAR_KIND = 'tumblepipe.shot_audio'
#: The sidecar's format: a reader takes only the formats it knows.
SIDECAR_FORMAT = 1


def sidecar_path(wav: str | Path) -> Path:
    """``<wav>.json``: what the mix at ``wav`` was made of."""
    wav = Path(wav)
    return wav.with_name(f'{wav.name}.json')


@dataclass(frozen=True)
class MixRecord:
    """What a mix was made of, so it can be made again from a newer edit
    version: the edit document ``edit``, the shot (its URI), its frames,
    and the tracks: ``places`` (1 for the first; empty: every track) of the
    edit's audio tracks then, named ``edit_tracks`` in order ('' for a
    track without a name; None when that is not known). ``recorded``:
    read from a sidecar, not made out from the WAV's path."""
    edit: EditVersion
    shot: str
    places: tuple[int, ...] = ()
    edit_tracks: tuple[str, ...] | None = None
    first: int | None = None
    last: int | None = None
    recorded: bool = True

    @property
    def all_tracks(self) -> bool:
        return not self.places


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def sidecar_json(record: MixRecord, fps: int | None = None) -> dict:
    """The sidecar of the mix ``record`` describes (at ``fps``).
    ``tracks`` (the labels of the tracks mixed, every one's for ``all``) is
    for a person reading it: :func:`read_sidecar` goes by ``places`` and
    ``edit_tracks``."""
    names = record.edit_tracks
    if names is None:
        tracks = None if record.all_tracks else place_words(record.places)
    else:
        places = record.places or range(1, len(names) + 1)
        tracks = [track_label(names, place) for place in places]
    return {
        'kind': SIDECAR_KIND,
        'format': SIDECAR_FORMAT,
        'edit': record.edit.name,
        'version': record.edit.version,
        'published': record.edit.published,
        'shot': record.shot,
        'first': record.first,
        'last': record.last,
        'fps': fps,
        'all': record.all_tracks,
        'places': list(record.places),
        'tracks': tracks,
        'edit_tracks': None if names is None else list(names),
    }


def read_sidecar(data) -> MixRecord | None:
    """The :class:`MixRecord` a sidecar holds; None for anything that is not
    one this reader knows (another kind or format, a missing or wrong
    field), so a damaged sidecar is as good as none."""
    if not isinstance(data, Mapping):
        return None
    if data.get('kind') != SIDECAR_KIND or not _is_int(data.get('format')) or data['format'] != SIDECAR_FORMAT:
        return None
    name, version, published, shot = (data.get(k) for k in ('edit', 'version', 'published', 'shot'))
    if not (isinstance(name, str) and name and isinstance(version, str)
            and naming.is_valid_version_name(version) and isinstance(published, bool)
            and isinstance(shot, str) and shot):
        return None
    names = data.get('edit_tracks')
    if names is not None and not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
        return None
    first, last = data.get('first'), data.get('last')
    if any(v is not None and not _is_int(v) for v in (first, last)):
        return None
    every = data.get('all')
    places = data.get('places')
    if not isinstance(every, bool) or not isinstance(places, list) or not all(_is_int(p) for p in places):
        return None
    if every:
        places = []
    elif not places or len(set(places)) != len(places) or min(places) < 1:
        return None
    elif names is not None and max(places) > len(names):
        return None
    return MixRecord(
        EditVersion(name, version, published), shot, tuple(sorted(places)),
        None if names is None else tuple(names), first, last, True,
    )


@dataclass(frozen=True)
class EditWav:
    """A scene's audio file that is a mix of :func:`audio_uri`'s shape for
    the scene's shot: of ``edit``, its tracks ``key`` (``all``, ``a1-a3``)."""
    edit: EditVersion
    key: str


_EDIT_WAV = re.compile(r'(?:^|/)edits/(?P<edit>[^/]+)/audio/(?P<folder>[^/]+)/(?P<stem>[^/]+)\.wav$')
_KEY = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_PLACE = re.compile(r'a([1-9][0-9]*)')


def edit_wav(path: str, shot: Uri) -> EditWav | None:
    """``path`` read as a mix of an edit's sound over ``shot``
    (``.../edits/<edit>/audio/<vNNNN | vNNNN_working>/<SEQ>_<SHOT>_<tracks>.wav``,
    either slash); None for any other file, another shot's mix among them.
    Read from the path's end, so a project seen through another drive or
    share still counts."""
    if not isinstance(path, str) or not path:
        return None
    found = _EDIT_WAV.search(path.replace('\\', '/'))
    if found is None or shot.purpose != 'entity' or len(shot.segments) < 2 or shot.segments[0] != 'shots':
        return None
    folder = found['folder']
    version, working = (folder[:-len('_working')], True) if folder.endswith('_working') else (folder, False)
    if not naming.is_valid_version_name(version):
        return None
    prefix = '_'.join(shot.segments[1:]) + '_'
    stem = found['stem']
    key = stem[len(prefix):]
    if not stem.startswith(prefix) or not _KEY.fullmatch(key):
        return None
    return EditWav(EditVersion(found['edit'], version, not working), key)


def record_from_wav(wav: EditWav, shot: Uri) -> MixRecord | None:
    """What a mix with no sidecar (made before they were written) was made
    of, from its path: its edit version, and its tracks (``all``, or the
    places ``a1-a3`` names; their names are not known). None when the
    tracks were not asked for by place (not a Load Audio from Edit mix)."""
    if wav.key == ALL_TRACKS:
        return MixRecord(wav.edit, str(shot), (), None, recorded=False)
    places = []
    for word in wav.key.split('-'):
        found = _PLACE.fullmatch(word)
        if found is None:
            return None
        places.append(int(found[1]))
    return MixRecord(wav.edit, str(shot), tuple(sorted(set(places))), None, recorded=False)


def _same(name: str) -> str:
    """A track name as names are matched: surrounding space and case aside."""
    return name.strip().casefold()


def follow_tracks(then: Sequence[str], places: Sequence[int], now: Sequence[str]) -> tuple[int, ...] | str:
    """The places in ``now`` (a newer version's audio track names, in order)
    of the tracks at ``places`` (1 the first) of ``then`` (the names when
    they were mixed), followed by name: the track named so, wherever it
    went. Several tracks of one name (or several without one) are told
    apart by their order among themselves, so the second ``Dialog`` stays
    the second. Why they cannot be followed (a str) when a track's name is
    gone, or fits more or fewer tracks than it did (which one is meant is
    no longer clear): the sound is not made again with other tracks than
    were picked."""
    chosen, gone, unclear = [], [], []
    for place in sorted(set(places)):
        if not 0 < place <= len(then):
            return f'the tracks it was mixed from are not known (A{place} is past its {len(then)})'
        key = _same(then[place - 1])
        were = [p for p in range(1, len(then) + 1) if _same(then[p - 1]) == key]
        are = [p for p in range(1, len(now) + 1) if _same(now[p - 1]) == key]
        name = then[place - 1].strip()
        if not are:
            gone.append(name or 'without a name')
        elif len(are) != len(were):
            unclear.append((f'named {name}' if name else 'without a name', len(were), len(are)))
        else:
            chosen.append(are[were.index(place)])
    if gone:
        have = ', '.join(track_label(now, p) for p in range(1, len(now) + 1)) or 'none'
        which = ', '.join(dict.fromkeys(gone))
        return f'it has no audio track {which} any more (it has: {have})'
    if unclear:
        which, were, are = unclear[0]
        return f'it has {are} audio tracks {which} where it had {were}: which to hear is not clear'
    return tuple(sorted(chosen))


def shot_label(shot: Uri | str) -> str:
    """``SQ010/SH020`` for ``entity:/shots/SQ010/SH020``."""
    uri = Uri.parse(shot) if isinstance(shot, str) else shot
    return '/'.join(uri.segments[1:]) or str(uri)


def version_label(edit: EditVersion) -> str:
    """``published v0003``, ``working v0007``."""
    return f'{"published" if edit.published else "working"} {edit.version}'


def is_newer(candidate: EditVersion, mixed: EditVersion) -> bool:
    """Whether ``candidate`` (what :func:`pick_version` picks now) is a
    newer document of the edit than ``mixed``: a later version of the same
    kind (working and published are numbered apart), or a working version
    where the mix was of a published one (saved after it: the pick takes
    working first). A published one where the mix was of a working one is
    not (the working versions are gone, not superseded)."""
    if candidate.name != mixed.name or candidate == mixed:
        return False
    if candidate.published == mixed.published:
        return naming.get_version_code(candidate.version) > naming.get_version_code(mixed.version)
    return not candidate.published


@dataclass(frozen=True)
class RefreshPlan:
    """What to do with a scene playing the mix ``record`` describes: make
    it again from ``edit`` with the tracks at ``places`` (empty: every
    track) of its ``edit_tracks``, or (``edit`` None) keep the sound. ``note``
    is what to tell the artist besides ('' nothing)."""
    record: MixRecord | None
    edit: EditVersion | None = None
    places: tuple[int, ...] = ()
    edit_tracks: tuple[str, ...] = ()
    note: str = ''

    @property
    def remix(self) -> bool:
        return self.edit is not None

    def track_labels(self) -> list[str]:
        """The labels of the tracks the new mix has."""
        places = self.places or range(1, len(self.edit_tracks) + 1)
        return [track_label(self.edit_tracks, place) for place in places]


def plan_refresh(
    audio_path: str, shot: Uri, sidecar, master: str | None,
    working: Sequence[str], published: Sequence[str],
    names_of: Callable[[EditVersion], Sequence[str]],
) -> RefreshPlan | None:
    """What becomes of the sound of ``shot``'s scene, which plays
    ``audio_path``, when it opens. None, nothing at all, when that is not
    a mix of an edit's sound over this shot (:func:`edit_wav`): a scene
    playing other sound, or none, is left alone.

    The mix is what its sidecar (``sidecar``, the JSON read, None for none)
    says, else what its path says (:func:`record_from_wav`). It is made
    again when the master edit (``master``, with its ``working`` and
    ``published`` versions, oldest first) is the edit it came from and has
    a newer document (:func:`pick_version`, :func:`is_newer`): every track
    again for an ``all`` mix, else the same tracks followed by name
    (:func:`follow_tracks`) from the names the mix had (an old mix's from
    the document it came from, read by ``names_of``; when that is gone too,
    by place, with a note). ``names_of(edit)`` is the audio track names of
    that document (:func:`raw_track_names`), raising ValueError when it does
    not read. Anything else keeps the sound, with a note saying why unless
    it is simply up to date."""
    wav = edit_wav(audio_path, shot)
    if wav is None:
        return None
    record = read_sidecar(sidecar) if sidecar is not None else None
    if record is not None and record.shot != str(shot):
        record = None
    if record is None:
        record = record_from_wav(wav, shot)
    file_name = audio_path.replace('\\', '/').rsplit('/', 1)[-1]
    if record is None:
        return RefreshPlan(None, note=(
            f'Edit audio: {shot_label(shot)} plays {file_name}, whose tracks were not picked by '
            f'Load Audio from Edit: it is not refreshed.'
        ))
    mixed = record.edit
    keeps = f'{shot_label(shot)} keeps its sound from {mixed.name} ({version_label(mixed)})'
    if master is None:
        return RefreshPlan(record, note=f'Edit audio: the project has no master edit now: {keeps}.')
    if master != mixed.name:
        return RefreshPlan(record, note=(
            f'Edit audio: the master edit is now {master}: {keeps}. '
            f"Load Audio from Edit (TumblePipe shelf) to hear {master}'s."
        ))
    picked = pick_version(master, working, published)
    if picked is None or picked == mixed:
        return RefreshPlan(record)
    if not is_newer(picked, mixed):
        return RefreshPlan(record, note=(
            f'Edit audio: the master edit\'s newest version ({version_label(picked)}) is not newer '
            f'than the one the sound was mixed from: {keeps}.'
        ))
    newer = f'{master} {version_label(picked)}'
    again = 'Load Audio from Edit to pick its tracks again.'
    try:
        now = tuple(names_of(picked))
    except ValueError as exc:
        return RefreshPlan(record, note=f'Edit audio: {newer} does not read ({exc}): {keeps}.')
    if not now:
        return RefreshPlan(record, note=f'Edit audio: {newer} has no audio tracks: {keeps}.')
    if record.all_tracks:
        return RefreshPlan(record, picked, (), now)
    then = record.edit_tracks
    if then is None:
        try:
            then = tuple(names_of(mixed))
        except ValueError:
            then = None
    if then is None:
        missing = [place for place in record.places if place > len(now)]
        if missing:
            return RefreshPlan(record, note=(
                f'Edit audio: {newer} has {len(now)} audio track{"" if len(now) == 1 else "s"}, '
                f'no {", ".join(place_words(missing))}: {keeps}. {again}'
            ))
        return RefreshPlan(record, picked, record.places, now, note=(
            f'The names of the tracks it had were not recorded, so '
            f'{", ".join(place_words(record.places))} were taken by place: '
            f'Load Audio from Edit once to have them followed by name.'
        ))
    followed = follow_tracks(then, record.places, now)
    if isinstance(followed, str):
        return RefreshPlan(record, note=f'Edit audio: {newer}: {followed}. {keeps}. {again}')
    return RefreshPlan(record, picked, followed, now)


@dataclass(frozen=True)
class AudioPanel:
    """What the scene's Audio Panel plays, as ``audiopanel`` prints it:
    the audio file (``-a``), the source (``-m``: 0 CHOPs, 1 the file) and
    the mode (``-o d``: 0 off, 1 timeline, 2 time slice, 3 test); None for
    what was not printed."""
    path: str = ''
    source: int | None = None
    mode: int | None = None

    @property
    def plays_file(self) -> bool:
        """Whether the scene plays its audio file: there is one, the source
        is the file and the audio is not off."""
        return bool(self.path) and self.source != 0 and self.mode != 0


def _hscript_words(text: str) -> list[str]:
    """``text`` split as HScript splits a command line: at spaces, a
    ``'...'`` word taken as it is, a ``"..."`` one with ``\\"`` and ``\\\\``
    unescaped."""
    words, i, n = [], 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        word = []
        while i < n and not text[i].isspace():
            c = text[i]
            if c == "'":
                end = text.find("'", i + 1)
                end = n if end < 0 else end
                word.append(text[i + 1:end])
                i = end + 1
            elif c == '"':
                i += 1
                while i < n and text[i] != '"':
                    if text[i] == '\\' and i + 1 < n and text[i + 1] in '"\\':
                        i += 1
                    word.append(text[i])
                    i += 1
                i += 1
            else:
                word.append(c)
                i += 1
        words.append(''.join(word))
    return words


def _int_or_none(word: str) -> int | None:
    try:
        return int(float(word))
    except ValueError:
        return None


def parse_audio_panel(text: str) -> AudioPanel:
    """The Audio Panel ``text`` describes: what ``audiopanel`` prints with
    no arguments (a setting a line, ``audiopanel -a 'file.wav'``), or the
    lines a hip keeps it in (several settings a line, the first glued to
    the archive's header). The last word on each setting wins."""
    path, source, mode = '', None, None
    for line in text.splitlines():
        at = line.find('audiopanel ')
        if at < 0:
            continue
        words = _hscript_words(line[at + len('audiopanel '):])
        i = 0
        while i < len(words):
            flag = words[i]
            if flag in ('-a', '-m', '-f', '-O') and i + 1 < len(words):
                value = words[i + 1]
                if flag == '-a':
                    path = value
                elif flag == '-m':
                    source = _int_or_none(value)
                i += 2
            elif flag in ('-o', '-s', '-t', '-r') and i + 2 < len(words):
                if flag == '-o' and words[i + 1] == 'd':
                    mode = _int_or_none(words[i + 2])
                i += 3
            else:
                i += 1
    return AudioPanel(path, source, mode)


#: How much of a hip's end :func:`hip_audio_text` reads: its ``audiopanel``
#: lines are in the ``.application`` part, a few kilobytes from the end.
HIP_TAIL = 1 << 20


def hip_audio_text(hip: str | Path, tail: int = HIP_TAIL) -> str:
    """The ``audiopanel`` lines of the hip at ``hip``, from its last
    ``tail`` bytes ('' when it does not read): what the Audio Panel was
    saved as, for a session where ``audiopanel`` prints nothing."""
    try:
        with open(hip, 'rb') as file:
            file.seek(0, 2)
            size = file.tell()
            file.seek(max(0, size - tail))
            data = file.read()
    except OSError:
        return ''
    text = data.decode('utf-8', 'replace')
    return '\n'.join(line for line in text.splitlines() if 'audiopanel ' in line)


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


def mixed_version(asked: EditVersion, summary: Mapping) -> EditVersion:
    """The edit document Cinedex says it mixed (``summary``'s ``version``
    and ``published``), which must be ``asked``: the tracks were asked for
    by their places in it, so a mix of any other document (or one whose
    summary does not say) is not the mix asked for. ValueError then."""
    version, published = summary.get('version'), summary.get('published')
    if not (isinstance(version, str) and naming.is_valid_version_name(version) and isinstance(published, bool)):
        raise ValueError(f'Cinedex did not say which version of edit {asked.name} it mixed')
    mixed = EditVersion(asked.name, version, published)
    if mixed != asked:
        raise ValueError(
            f'Cinedex mixed edit {asked.name} {version_label(mixed)}, not {version_label(asked)}, '
            f'whose tracks were asked for'
        )
    return mixed


def write_sidecar(wav: Path, record: MixRecord, fps: int | None = None) -> None:
    """Write ``record`` next to the mix at ``wav`` (:func:`sidecar_path`)."""
    text = json.dumps(sidecar_json(record, fps), indent=2) + '\n'
    sidecar_path(wav).write_bytes(text.encode('utf-8'))


def read_sidecar_file(wav: str | Path):
    """The JSON next to the mix at ``wav``; None when there is none or it
    does not read (as good as none)."""
    try:
        return json.loads(sidecar_path(wav).read_bytes().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def export_shot_audio(
    shot: Uri, places: Sequence[int] = (), edit: EditVersion | None = None,
    client=None, timeout: float = 600.0, edit_tracks: Sequence[str] | None = None,
) -> ShotAudio:
    """Mix the tracks at ``places`` (1 the first; none: all) of ``edit``
    (None: the master edit's, :func:`master_version`) over ``shot``'s
    frames into its :func:`audio_uri`, with Cinedex, and write what it was
    made of next to it (:func:`write_sidecar`; ``edit_tracks``, the names
    of ``edit``'s audio tracks, saves reading its document again). Cinedex
    is asked for exactly ``edit`` (:func:`edit_target`), and a mix it says
    is of another version is refused, its WAV and sidecar removed
    (:func:`mixed_version`). Raises when there is no player, no project,
    the shot is not in the edit, a track is not there, or the mix fails
    (with what Cinedex said). A sidecar that cannot be written is logged:
    the mix stands."""
    from tumblepipe.pipe import review_playlist
    from tumblepipe.pipe.edits import _client
    client = _client(client)
    edit = edit or master_version(client)
    places = tuple(sorted(set(places)))
    words = place_words(places)
    out = Path(client.storage.resolve(audio_uri(edit, shot, words)))
    player = review_playlist.find_player()
    project = review_playlist._current_project()
    arguments = shot_audio_arguments(player, project, edit, shot, out, words, shot_frames(shot, client))
    done = subprocess.run(
        arguments, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
        cwd=str(player.parent), **review_playlist._no_console(),
    )
    stdout = done.stdout.decode('utf-8', 'replace')
    stderr = done.stderr.decode('utf-8', 'replace').strip()
    if done.returncode != 0:
        said = stderr.removeprefix('cinedex: ') or stdout.strip() or f'exit code {done.returncode}'
        raise ValueError(f'The sound of {shot} from edit {edit.name} {edit.version} was not made: {said}')
    try:
        summary = read_summary(stdout)
        mixed = mixed_version(edit, summary)
    except ValueError as exc:
        # Not the mix asked for: no WAV under this version's name, and no
        # sidecar recording tracks that were not the ones heard.
        for stale in (out, sidecar_path(out)):
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                log.warning('%s is not the mix asked for and was not removed', stale, exc_info=True)
        raise ValueError(f'The sound of {shot} from edit {edit.name} {edit.version} was not made: {exc}') from exc
    names = tuple(edit_tracks) if edit_tracks is not None else None
    if names is None:
        try:
            names = tuple(raw_track_names(document_of(mixed, client)))
        except ValueError:
            names = None
    first, last = summary.get('first'), summary.get('last')
    record = MixRecord(
        mixed, str(shot), places, names,
        first if _is_int(first) else None, last if _is_int(last) else None,
    )
    fps = summary.get('fps')
    try:
        write_sidecar(out, record, fps if _is_int(fps) else None)
    except OSError:
        log.warning('What the mix %s was made of was not written: it will not be kept up to date', out, exc_info=True)
    return ShotAudio(out, mixed, summary)


@dataclass(frozen=True)
class Refreshed:
    """What keeping a scene's sound up to date did: the new mix (None: the
    scene keeps its sound), the mix the scene played, and what to tell the
    artist (``warned``: something kept it from being refreshed, or needs a
    look)."""
    made: ShotAudio | None
    was: EditVersion | None
    text: str
    warned: bool


def refresh_shot_audio(audio_path: str, shot: Uri, client=None) -> Refreshed | None:
    """Keep the sound of ``shot``'s scene, which plays ``audio_path``, up to
    date (:func:`plan_refresh`): make it again from the master edit's newer
    version when there is one. None when there is nothing to do or say (not
    an edit's mix, or up to date). Disk and Cinedex, no ``hou``: run it off
    Houdini's main thread, and set the scene's audio to ``made`` on it."""
    from tumblepipe.pipe import edits
    client = edits._client(client)
    master = edits.master_edit(client)
    working = edits.working_versions(master, client) if master else []
    published = edits.published_versions(master, client) if master else []

    def names_of(edit: EditVersion) -> list[str]:
        return raw_track_names(document_of(edit, client))

    plan = plan_refresh(
        audio_path, shot, read_sidecar_file(audio_path), master, working, published, names_of,
    )
    if plan is None:
        return None
    was = plan.record.edit if plan.record is not None else None
    if not plan.remix:
        return Refreshed(None, was, plan.note, True) if plan.note else None
    edit = plan.edit
    try:
        made = export_shot_audio(shot, plan.places, edit, client, edit_tracks=plan.edit_tracks)
    except Exception as exc:  # whatever stopped it, the scene keeps its sound and says why
        log.warning('Refreshing the edit audio of %s failed', shot, exc_info=True)
        return Refreshed(None, was, (
            f'Edit audio: the sound of {shot_label(shot)} was not made again from {edit.name} '
            f'({version_label(edit)}): {exc}. It keeps its sound from {version_label(was)}.'
        ), True)
    tracks = ', '.join(plan.track_labels())
    text = (
        f'Edit audio: {shot_label(shot)} now plays {edit.name} ({version_label(made.edit)}; '
        f'it played {version_label(was)}), tracks {tracks}.'
    )
    return Refreshed(made, was, f'{text} {plan.note}' if plan.note else text, bool(plan.note))
