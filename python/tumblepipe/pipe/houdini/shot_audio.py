"""The shot's sound from the edit on the scene's Audio Panel (Remy's RFE,
2026-11-22).

``pipe.shot_audio`` mixes the master edit's audio tracks over the shot's
frames into a WAV; here it is set as the scene's audio: the file, played
in scrub (timeline) mode from the shot's first frame. Houdini saves the
Audio Panel in the hip, so the sound stays with the workfile; run it again
after the cut changes.
"""

from __future__ import annotations

import hou

from tumblepipe.pipe import shot_audio as logic
from tumblepipe.pipe.shot_audio import AudioSettings

_TITLE = 'Load Audio from Edit'


def apply_settings(settings: AudioSettings) -> None:
    """Set the Audio Panel to ``settings``: its file, from its frame, at its
    offset, playing as the playbar plays and scrubs.

    ``hou.audio`` exists only with a UI; without one (hython) the same is
    the ``audiopanel`` command (:func:`logic.audiopanel_command`), which
    the hip saves just the same."""
    audio = getattr(hou, 'audio', None)
    if audio is None:
        _out, err = hou.hscript(logic.audiopanel_command(settings))
        if err.strip():
            raise RuntimeError(f'The Audio Panel was not set: {err.strip()}')
        return
    audio.useAudioFile()
    audio.setAudioFileName(settings.path)
    audio.setAudioFrame(settings.frame)
    audio.setAudioOffset(settings.offset)
    audio.useTimeLineMode()


def scene_fps() -> int:
    return int(round(hou.fps()))


def load(shot, tracks=(), client=None) -> str:
    """Mix ``tracks`` (none: all) of the master edit over ``shot`` and set
    it as the scene's audio; a summary (the fps warning appended when the
    scene plays at another rate than the edit)."""
    made = logic.export_shot_audio(shot, tracks, client=client)
    settings = logic.audio_settings(made.path, made.summary)
    apply_settings(settings)
    summary = made.summary
    kind = 'published' if made.edit.published else 'working'
    track_list = ', '.join(summary.get('tracks') or []) or 'none'
    clips = summary.get('clips', 0)
    lines = [
        f'The sound of {shot} from edit {made.edit.name} ({kind} {made.edit.version}) plays from frame {settings.frame}.',
        f'Tracks: {track_list}; {clips} clip{"" if clips == 1 else "s"} sound over frames '
        f'{summary.get("first")}-{summary.get("last")}.',
        f'File: {made.path}',
    ]
    if not clips:
        lines.append('Nothing sounds there: the file is silent.')
    warning = logic.fps_warning(int(summary.get('fps') or scene_fps()), scene_fps())
    text = '\n'.join(lines)
    return f'{text}\n\n{warning}' if warning else text


def _tell(summary: str) -> None:
    warned = '\n\n' in summary
    if hou.isUIAvailable():
        hou.ui.displayMessage(
            summary, title=_TITLE,
            severity=hou.severityType.Warning if warned else hou.severityType.Message,
        )
    else:
        print(summary)


def load_audio_dialog() -> None:
    """The shelf tool: in a shot's workfile, pick the master edit's audio
    tracks (all preselected) and load their mix as the scene's audio."""
    from tumblepipe.pipe.houdini.scene_context import scene_entity_uri
    from tumblepipe.pipe.houdini.ui.report import reporting

    def _run() -> None:
        shot = scene_entity_uri(contexts=('shots',))
        if shot is None:
            hou.ui.displayMessage(
                "This scene is not a shot's workfile: open one to load its sound from the edit.",
                title=_TITLE,
            )
            return
        edit = logic.master_version()
        names = logic.audio_track_names(logic.document_of(edit))
        if not names:
            hou.ui.displayMessage(
                f'The master edit {edit.name} ({edit.version}) has no audio tracks.',
                title=_TITLE,
            )
            return
        picked = hou.ui.selectFromList(
            names, default_choices=tuple(range(len(names))), exclusive=False,
            title=_TITLE,
            message=f'Mix which audio tracks of {edit.name} ({edit.version}) over {shot}?',
            column_header='Audio track', clear_on_cancel=True,
        )
        if not picked:
            return
        # Every track is the "all" mix: the same file whatever they are named.
        tracks = () if len(picked) == len(names) else tuple(f'A{i + 1}' for i in picked)
        _tell(load(shot, tracks))

    reporting(_TITLE, _run)()
