"""The frame range a th:: node acts over, read one way on every node.

A node with a 'Frame Range' menu mixes in ``FrameRangeNode``. The menu's
tokens are the ``FrameRangeSource`` values; a node offers the subset that
makes sense for it (an export never offers 'single_frame'), but a token
means the same thing on every node that offers it:

- ``single_frame``: the current frame, no roll;
- ``playback_range``: the playbar's range, its rolls from the global range;
- ``from_context``: the addressed entity's authored range from the database,
  rolls included — the node must be an ``EntityNode``, and an entity with no
  range authored raises;
- ``from_settings``: the node's own 'frame_settings' (first, last and, when
  the tuple has three components, step) and 'roll_settings' parms.

``get_frame_range()`` always returns one ``FrameRange`` (step included) or
raises; it never returns None or a ``(range, step)`` pair.
"""

import hou

from tumblepipe.config.timeline import FrameRange, get_frame_range
from tumblepipe.pipe.houdini import playbar


class FrameRangeSource:
    SINGLE_FRAME = 'single_frame'
    PLAYBACK_RANGE = 'playback_range'
    ENTITY = 'from_context'
    SETTINGS = 'from_settings'


class FrameRangeNode:
    """Mixin for a th:: node with a frame range. Needs ``parm()``,
    ``native()`` and ``_error()`` from the node class it is mixed into."""

    # The source menu parm; None for a node that only has settings.
    FRAME_RANGE_PARM: str | None = 'frame_range'
    FRAME_SETTINGS_PARM: str = 'frame_settings'
    # The roll parm; None for a node without rolls.
    ROLL_SETTINGS_PARM: str | None = 'roll_settings'

    def get_frame_range_source(self) -> str:
        if self.FRAME_RANGE_PARM is None:
            return FrameRangeSource.SETTINGS
        return self.parm(self.FRAME_RANGE_PARM).eval()

    def get_frame_range(self) -> FrameRange:
        source = self.get_frame_range_source()
        match source:
            case FrameRangeSource.SINGLE_FRAME:
                frame = round(hou.frame())
                return FrameRange(frame, frame, 0, 0)
            case FrameRangeSource.PLAYBACK_RANGE:
                return playbar.get_frame_range()
            case FrameRangeSource.ENTITY:
                entity_uri = self.require_entity_uri()
                frame_range = get_frame_range(entity_uri)
                if frame_range is None:
                    raise self._error(
                        f'{entity_uri} has no frame range authored in the '
                        "database; author one, or use 'From Settings'"
                    )
                return frame_range
            case FrameRangeSource.SETTINGS:
                return self._settings_frame_range()
            case _:
                raise self._error(f"unknown frame range source '{source}'")

    def _settings_frame_range(self) -> FrameRange:
        native = self.native()
        frame_settings = native.parmTuple(self.FRAME_SETTINGS_PARM).eval()
        start_roll, end_roll = (
            (0, 0) if self.ROLL_SETTINGS_PARM is None
            else native.parmTuple(self.ROLL_SETTINGS_PARM).eval()
        )
        # A First/Last/Step tuple carries the step; First/Last steps by one.
        step = frame_settings[2] if len(frame_settings) == 3 else 1
        return FrameRange(
            frame_settings[0], frame_settings[1], start_roll, end_roll, step
        )
