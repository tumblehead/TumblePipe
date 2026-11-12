"""An edit laid on Houdini's timeline (#61): what to show, without ``hou``.

"Import Edit to Timeline" (the TumblePipe shelf) and an edit card's "Show on
Houdini Timeline" (the asset browser) read an edit and show its cut on the
playbar, to check the cut's ranges against the shots. Read-only: nothing is
written to the project.

- The playbar (global and playback range) spans the edit: its frame 0 (edit
  positions count from 0, ``designs/edit-entity.md`` §2.2) sits at the
  edit's start frame, ``frame_start`` as the edit entity inherits it from
  the project (1001 by default), and its last frame is the end of its last
  clip, video or audio (``th_edit``'s ``Edit::length``).
- One bookmark per video clip, named after the clip's shot
  (``SQ010/SH030``) or, for a media clip, its library item; slugs are
  skipped and gaps have no clip. A bookmark spans the clip's cut-in to
  cut-out on the edit's timeline. Clips on different tracks may overlap
  (a dissolve), and so may their bookmarks.
- An edit at another fps than the scene's is converted to the scene's
  rate, with a warning: positions scale by ``scene / edit`` and round to
  the nearest frame, every clip keeping at least one frame.

The bookmarks are tagged with :data:`EDIT_KEY` (Houdini bookmark metadata)
naming the edit, so a re-run replaces the ones it made and never touches
the artist's own. ``pipe.houdini.edit_timeline`` applies a
:class:`TimelinePlan` to the session; this module is pure, but for
:func:`read_edit`, which reads the edit's newest document from disk.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: The bookmark metadata key naming the edit a pipeline bookmark shows.
#: Bookmarks without it are the artist's (or a Multi's,
#: ``pipe.houdini.multi_timeline``) and are never touched.
EDIT_KEY = 'tumblepipe_edit'

#: Where an edit starts when nothing configures ``frame_start``.
DEFAULT_START_FRAME = 1001


@dataclass(frozen=True)
class BookmarkSpec:
    """One clip on the playbar: ``name`` over frames ``start``-``end``
    (inclusive, scene frames). ``comment`` says where it came from."""
    name: str
    start: int
    end: int
    comment: str = ''


@dataclass(frozen=True)
class TimelinePlan:
    """What the playbar shows for an edit: frames ``first``-``last``
    (inclusive) and a bookmark per clip, in edit order. ``warning`` is
    non-empty when the edit's fps was converted."""
    first: int
    last: int
    bookmarks: tuple[BookmarkSpec, ...]
    warning: str = ''


def _frame(value) -> int | None:
    """An edit position: a non-negative integer (a bool is not one)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def shot_label(shot: str) -> str:
    """``SQ010/SH030`` for ``entity:/shots/SQ010/SH030``; anything else
    as it is."""
    prefix = 'entity:/shots/'
    if shot.startswith(prefix):
        rest = shot[len(prefix):].strip('/')
        if rest:
            return rest
    return shot


def clip_label(source) -> str | None:
    """The bookmark name for a clip's ``source``: its shot (``SQ/SH``), its
    media item, None for a slug or anything not shaped like a source."""
    if not isinstance(source, Mapping):
        return None
    kind = source.get('kind')
    if kind == 'shot':
        shot = source.get('shot')
        return shot_label(shot) if isinstance(shot, str) and shot else None
    if kind == 'media':
        item = source.get('item')
        return item if isinstance(item, str) and item else None
    return None


def scale(frames: int, edit_fps: int, scene_fps: int) -> int:
    """``frames`` at ``edit_fps`` as frames at ``scene_fps``, to the nearest
    frame (halves up). Identity when the rates match."""
    if edit_fps == scene_fps:
        return frames
    return (2 * frames * scene_fps + edit_fps) // (2 * edit_fps)


def _clips(document: Mapping, key: str):
    """``(track index, clip)`` for every clip of ``document[key]`` shaped
    like one (an ``at`` and a positive ``duration``)."""
    tracks = document.get(key)
    if not isinstance(tracks, list):
        return
    for track_index, track in enumerate(tracks):
        clips = track.get('clips') if isinstance(track, Mapping) else None
        if not isinstance(clips, list):
            continue
        for clip in clips:
            if not isinstance(clip, Mapping):
                continue
            at = _frame(clip.get('at'))
            duration = _frame(clip.get('duration'))
            if at is None or not duration:
                continue
            yield track_index, at, duration, clip


def edit_length(document: Mapping) -> int:
    """The edit's length in its own frames: the end of its last clip, video
    or audio; 0 for an edit with none."""
    ends = [
        at + duration
        for key in ('video', 'audio')
        for _track, at, duration, _clip in _clips(document, key)
    ]
    return max(ends, default=0)


def document_fps(document: Mapping) -> int:
    """The edit's fps; ValueError when it has none that reads."""
    fps = document.get('fps') if isinstance(document, Mapping) else None
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise ValueError('The edit has no fps')
    return fps


def plan(document: Mapping, start_frame: int, scene_fps: int, name: str = '') -> TimelinePlan:
    """What the playbar shows for edit ``document`` (a working or published
    edit's JSON) starting at ``start_frame`` in a scene at ``scene_fps``.
    ValueError for a document that is not an edit or has no clips."""
    if not isinstance(document, Mapping):
        raise ValueError('Not an edit document')
    if isinstance(scene_fps, bool) or not isinstance(scene_fps, int) or scene_fps <= 0:
        raise ValueError(f'The scene fps {scene_fps!r} is not a rate')
    edit_fps = document_fps(document)
    length = edit_length(document)
    title = f'Edit {name}' if name else 'The edit'
    if length == 0:
        raise ValueError(f'{title} has no clips')

    def at_scene(frames: int) -> int:
        return start_frame + scale(frames, edit_fps, scene_fps)

    bookmarks = []
    for track_index, at, duration, clip in sorted(
        _clips(document, 'video'), key=lambda item: (item[1], item[0])
    ):
        label = clip_label(clip.get('source'))
        if label is None:
            continue
        start = at_scene(at)
        end = max(start, at_scene(at + duration) - 1)
        source_in = _frame(clip.get('source_in'))
        source = (
            f', source {source_in}-{source_in + duration - 1}'
            if source_in is not None else ''
        )
        track = document['video'][track_index]
        track_name = track.get('name') if isinstance(track.get('name'), str) else ''
        comment = (
            f'{title}: {track_name or f"V{track_index + 1}"}, '
            f'edit frames {at}-{at + duration - 1}{source}'
        )
        bookmarks.append(BookmarkSpec(label, start, end, comment))
    warning = ''
    if edit_fps != scene_fps:
        warning = (
            f'{title} is cut at {edit_fps} fps and the scene plays at '
            f'{scene_fps} fps: its frames were converted to {scene_fps} fps '
            f'and rounded to the nearest frame.'
        )
    # A clip converted to a lower rate keeps one frame, so the last one
    # may reach a frame past the scaled length: the playbar covers it.
    last = max([start_frame, at_scene(length) - 1] + [b.end for b in bookmarks])
    return TimelinePlan(start_frame, last, tuple(bookmarks), warning)


def is_ours(metadata) -> bool:
    """Whether a bookmark with ``metadata`` (its :data:`EDIT_KEY` value)
    is one an edit import made."""
    return isinstance(metadata, str) and bool(metadata)


# ── Reading an edit (disk, no hou) ─────────────────────────────────────


@dataclass(frozen=True)
class EditDocument:
    """An edit's newest document: ``version`` (``vNNNN``), whether it is
    ``published``, its JSON, and the frame its frame 0 sits at."""
    name: str
    version: str
    published: bool
    document: Mapping
    start_frame: int


def start_frame_of(properties) -> int:
    """``frame_start`` from an edit entity's properties (inherited from the
    project), else :data:`DEFAULT_START_FRAME`."""
    value = properties.get('frame_start') if isinstance(properties, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_START_FRAME
    return value


def read_edit(name: str, client=None) -> EditDocument:
    """Edit ``name``'s newest document (``pipe.edits.document_path``: its
    newest working version, else its latest published one) and its start
    frame. ValueError when it has none or it does not read."""
    from tumblepipe.pipe import edits

    client = edits._client(client)
    path = edits.document_path(name, client)
    if path is None:
        raise ValueError(f'Edit {name} has no saved or published version yet')
    try:
        document = json.loads(Path(path).read_bytes().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f'Edit {name}: {path} does not read: {exc}') from exc
    if not isinstance(document, Mapping):
        raise ValueError(f'Edit {name}: {path} is not an edit document')
    # A published version's document sits in its version folder.
    version = Path(path).stem[len(f'{name}_{edits.DEPARTMENT}_'):]
    published = Path(path).parent.name == version
    properties = client.config.get_properties(edits.edit_uri(name)) or {}
    return EditDocument(name, version, published, document, start_frame_of(properties))
