"""The session's timeline: playbar range, fps and the cook update mode."""

from tumblepipe.config.timeline import BlockRange, FrameRange

import hou

def get_frame_range() -> FrameRange:
    first_frame, last_frame = hou.playbar.frameRange()
    start_frame, end_frame = hou.playbar.playbackRange()
    start_roll = round(first_frame - start_frame)
    end_roll = round(end_frame - last_frame)
    return FrameRange(
        round(start_frame),
        round(end_frame),
        start_roll,
        end_roll
    )


def set_frame_range(frame_range: FrameRange, preserve_current_frame: bool = False):
    """Apply a shot frame range to the playbar.

    By default the playhead snaps to the shot's start frame. Pass
    preserve_current_frame=True to leave the playhead alone when it already
    sits inside the new range (rolls included) and only snap it to the start
    when the current frame falls outside — this keeps a re-import from yanking
    the artist back to frame one every time.
    """
    range_start = frame_range.start_frame - frame_range.start_roll
    range_end = frame_range.end_frame + frame_range.end_roll
    hou.playbar.setFrameRange(range_start, range_end)
    hou.playbar.setPlaybackRange(
        frame_range.start_frame,
        frame_range.end_frame
    )
    if preserve_current_frame and range_start <= hou.frame() <= range_end:
        return
    hou.setFrame(
        frame_range.start_frame
    )


def set_block_range(block_range: BlockRange):
    hou.playbar.setFrameRange(
        block_range.first_frame,
        block_range.last_frame
    )
    hou.playbar.setPlaybackRange(
        block_range.first_frame,
        block_range.last_frame
    )
    hou.setFrame(
        block_range.first_frame
    )


def set_fps(fps: int):
    """Set Houdini session FPS.

    Only affects playback speed - keeps the frame range, frame count, and
    keyframes exactly where they are.

    preserve_frame_start=True is load-bearing: hou.setFps defaults it to
    False, which holds the start *time* (seconds) constant and therefore
    SHIFTS the start frame by the fps ratio. Paired with
    modify_frame_count=False (which keeps the frame *count* fixed) that
    produces the classic "start/end frames wrong but the number of frames
    is right" symptom whenever the session fps differs from the shot fps
    (e.g. opening a 25fps shot in a fresh 24fps session). preserve_frame_start
    pins the start frame, and with modify_frame_count=False the end frame is
    pinned too, so the range survives the fps change untouched.
    """
    hou.setFps(
        fps,
        modify_frame_count=False,
        preserve_keyframes=True,
        preserve_frame_start=True,
    )


class _UpdateModeContext:
    def __init__(self, mode):
        self.mode = mode
        self.previous_mode = None
        self.has_ui = False

    def __enter__(self):
        self.has_ui = hou.isUIAvailable()
        if not self.has_ui: return
        self.previous_mode = hou.updateModeSetting()
        hou.setUpdateMode(self.mode)

    def __exit__(self, *args):
        if not self.has_ui: return
        hou.setUpdateMode(self.previous_mode)
        self.has_ui = False


def update_mode(mode):
    return _UpdateModeContext(mode)
