"""The shot's sound from the edit on the scene's Audio Panel (Remy's RFE,
2026-11-22).

``pipe.shot_audio`` mixes the master edit's audio tracks over the shot's
frames into a WAV; here it is set as the scene's audio: the file, played
in scrub (timeline) mode from the shot's first frame. Houdini saves the
Audio Panel in the hip, so the sound stays with the workfile.

It is kept up to date (Soren, 2026-11-22): when a shot's workfile opens in
a GUI session (``asset_browser.load_hook``) and plays such a mix, and the
master edit has a newer version than the one it was mixed from, the mix is
made again in the background with the same tracks, followed by name, and
the scene plays the new file (:func:`refresh_on_open`). Scenes playing any
other sound are left alone.
"""

from __future__ import annotations

import logging
import re
import threading

import hou

from tumblepipe.pipe import shot_audio as logic
from tumblepipe.pipe.shot_audio import AudioPanel, AudioSettings, EditVersion

log = logging.getLogger(__name__)

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


def scene_audio_panel() -> AudioPanel:
    """What the scene's Audio Panel plays: what ``audiopanel`` prints
    (``hou.audio`` has no getters), else, when that names no audio file
    (hython keeps no panel and prints nothing), what the loaded hip saved
    it as (:func:`logic.hip_audio_text`)."""
    out, _err = hou.hscript('audiopanel')
    if re.search(r'(?:^|\s)-a(?:\s|$)', out):
        return logic.parse_audio_panel(out)
    hip = hou.hipFile.path()
    if not hip or hou.hipFile.isNewFile():
        return AudioPanel()
    return logic.parse_audio_panel(logic.hip_audio_text(hip))


def scene_fps() -> int:
    return int(round(hou.fps()))


def load(shot, places=(), client=None, edit: EditVersion | None = None, edit_tracks=None) -> str:
    """Mix the tracks at ``places`` (1 the first; none: all) of the master
    edit (``edit``, else its newest document) over ``shot`` and set it as
    the scene's audio; a summary (the fps warning appended when the scene
    plays at another rate than the edit). ``edit_tracks``: the names of
    ``edit``'s audio tracks, recorded with the mix."""
    made = logic.export_shot_audio(shot, places, edit, client=client, edit_tracks=edit_tracks)
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
        'When this workfile opens and the master edit has a newer version, '
        'it is mixed again from it with the same tracks.',
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
        document = logic.document_of(edit)
        names = logic.audio_track_names(document)
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
        # Every track is the "all" mix: the same file whatever they are
        # named, and a refresh mixes every track the newer version has.
        places = () if len(picked) == len(names) else tuple(i + 1 for i in picked)
        _tell(load(shot, places, edit=edit, edit_tracks=logic.raw_track_names(document)))

    reporting(_TITLE, _run)()


# ── Kept up to date when the workfile opens ─────────────────────────────


def _say(text: str, warned: bool) -> None:
    """Tell the artist ``text``: on the console, and in the status bar."""
    print(text)
    if hou.isUIAvailable():
        hou.ui.setStatusMessage(
            ' '.join(text.split()),
            severity=hou.severityType.Warning if warned else hou.severityType.Message,
        )


def _in_background(work, then) -> None:
    """``work()`` on a worker thread (no ``hou`` there), then
    ``then(result)`` on Houdini's main thread. Upkeep: a failure is logged
    and said, never put up as an error dialog."""
    from tumblepipe.pipe.houdini.ui.report import run_on_main_thread

    def _run() -> None:
        try:
            result = work()
        except Exception as exc:
            log.warning('Keeping the edit audio up to date failed', exc_info=True)
            run_on_main_thread(_say, f'Edit audio: not refreshed: {exc}', True)
            return
        run_on_main_thread(then, result)

    threading.Thread(target=_run, name='tumblepipe edit audio refresh', daemon=True).start()


def _apply_refresh(done: logic.Refreshed | None, hip: str, played: str) -> None:
    """On the main thread: set the scene's audio to the mix ``done`` made,
    and say what happened. When the artist opened another scene, or gave
    this one other sound, while it was made, it is not set."""
    if done is None:
        return
    if done.made is None:
        _say(done.text, done.warned)
        return
    if hou.hipFile.path() != hip or scene_audio_panel().path != played:
        _say(
            f'Edit audio: the scene changed while its new sound was made, so it was not set: '
            f'{done.made.path}', True,
        )
        return
    apply_settings(logic.audio_settings(done.made.path, done.made.summary))
    warning = logic.fps_warning(int(done.made.summary.get('fps') or scene_fps()), scene_fps())
    _say(f'{done.text} {warning}' if warning else done.text, done.warned or bool(warning))


def refresh_on_open(run=None) -> None:
    """On a GUI open of a shot's workfile that plays an edit's mix over the
    shot (``asset_browser.load_hook``): make it again from the master
    edit's newer version, if it has one, and play that
    (:func:`logic.refresh_shot_audio`). Any other scene, or sound, is left
    alone, with nothing read beyond the Audio Panel.

    The open is not held up: the edit is read and mixed off the main
    thread, and the scene's audio set back on it. ``run(work, then)`` does
    that (tests run it in line)."""
    from tumblepipe.pipe.houdini.scene_context import scene_entity_uri

    shot = scene_entity_uri(contexts=('shots',))
    if shot is None:
        return
    panel = scene_audio_panel()
    if not panel.plays_file or logic.edit_wav(panel.path, shot) is None:
        return
    hip = hou.hipFile.path()
    played = panel.path
    (run or _in_background)(
        lambda: logic.refresh_shot_audio(played, shot),
        lambda done: _apply_refresh(done, hip, played),
    )
