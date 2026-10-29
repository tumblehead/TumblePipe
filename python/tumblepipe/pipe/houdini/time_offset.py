"""The time offset a th:: node moves its shot by, read one way on every node.

In a Multi with a shared timeline (``config.multi_timeline``) a member shot
sits on other frames than its own. ``th::import_shot`` moves the shot's
layers forward by the offset, and ``th::export_layer`` cooks the moved
frames and writes them back on the shot's own. A node with a 'Time Offset'
menu mixes in ``TimeOffsetNode``; the menu's tokens are the
``TimeOffsetSource`` values:

- ``from_multi`` (the default): the node's entity's offset on the open
  Multi's shared timeline; 0 in any other workfile, in a Multi without a
  shared timeline, and for an entity the Multi has not placed;
- ``none``: 0, the shot on its own frames, even in a shared-timeline Multi;
- ``manual``: the node's own 'time_offset' parm.

The default keeps every workfile saved before the parm existed as it was:
only a Multi with shared timeline switched on moves anything.
"""

from tumblepipe.pipe.houdini import multi_timeline


class TimeOffsetSource:
    FROM_MULTI = 'from_multi'
    NONE = 'none'
    MANUAL = 'manual'


class TimeOffsetNode:
    """Mixin for a th:: node with a time offset. Needs ``parm()``,
    ``get_entity_uri()`` and ``_error()`` from the node class it is mixed
    into."""

    TIME_OFFSET_SOURCE_PARM = 'time_offset_mode'
    TIME_OFFSET_PARM = 'time_offset'

    def get_time_offset_source(self) -> str:
        parm = self.parm(self.TIME_OFFSET_SOURCE_PARM)
        # A node from an HDA build without the parm: as if from_multi.
        if parm is None:
            return TimeOffsetSource.FROM_MULTI
        return parm.eval()

    def get_time_offset(self) -> int:
        source = self.get_time_offset_source()
        match source:
            case TimeOffsetSource.NONE:
                return 0
            case TimeOffsetSource.MANUAL:
                return int(self.parm(self.TIME_OFFSET_PARM).eval())
            case TimeOffsetSource.FROM_MULTI:
                entity_uri = self.get_entity_uri()
                if entity_uri is None:
                    return 0
                return multi_timeline.scene_time_offset(entity_uri)
            case _:
                raise self._error(f"unknown time offset source '{source}'")
