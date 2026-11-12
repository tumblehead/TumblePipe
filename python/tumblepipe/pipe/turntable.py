"""An asset's farm turntable: the frames, names and places of a Farm Submit asset render.

Farm Submit renders an asset without opening its lookdev workfile (#53). It
does so through ``th::lookdev_studio`` itself, cooked headless on the farm:
the collapse job snapshots the asset's staged build, builds a small workfile
that sublayers the snapshot under a studio (``pipe.houdini.turntable_workfile``),
exports that studio as the export job exports a studio's own Submit Render To
Farm (``farm/workers/export_houdini.py``), and submits the same render over
it. One studio, one look: the
camera framing (``createFramedCameraToBounds`` on the asset's bounds), the
lights scaled and swapped per loop on a batch render, the backdrop, the
render vars and the mattes all come from the HDA, never from a copy of its
values that could drift.

What is pure is here, so the dialog, the submit and the workers read the same
answers: the studio's default turntable frames (its Turntable > Frame Range
and Farm tab defaults, pinned to the HDA's DialogScript by
``tests/test_turntable.py``), and the render layer, department and purpose a
turntable writes to (those of a studio's own farm render, so the Asset
Browser and the players find both alike).

No ``hou``, no ``pxr``, no config reads.
"""

from __future__ import annotations

from typing import NamedTuple

#: The studio's node type, and the node inside it the farm exports.
NODE_TYPE = 'th::lookdev_studio'
EXPORT_NODE = 'OUT/stage_OUT'
#: Where a farm turntable workfile has its studio (``pipe.houdini.turntable_workfile``).
WORKFILE_NETWORK = '/stage'
WORKFILE_NODE = 'TURNTABLE'

#: Turntable > Frame Range > Asset Turn Framerange, the studio's default:
#: the asset turns once, 1001 to 1101.
ASSET_TURN_RANGE = (1001, 1101)

#: Farm > Start/End: the farm renders five loop lengths from the loop start.
LOOP_COUNT = 5

#: Farm > Step size: every 10th frame of those.
FARM_STEP = 10

#: Where a studio's farm render lands: ``turntable:/render/<asset>/render/<layer>``
#: (``lookdev_studio.LookdevStudio.submit``).
RENDER_PURPOSE = 'turntable'
RENDER_DEPARTMENT = 'render'
#: The layer a studio's own farm render writes, which is the default channel's.
DEFAULT_LAYER = 'main'
#: The Discord channel the render posts to, as a studio's does.
RENDER_NOTIFY_CHANNEL = 'previews'


class TurntableFrames(NamedTuple):
    first: int
    last: int
    step: int


def light_turn_range(asset_first: int, asset_last: int) -> tuple[int, int]:
    """The lights' loop after the asset's: the studio's Light Turn Framerange.

    ``ch("asset_turn_framerangemax")+1`` to
    ``ch("asset_turn_framerangemin")+(max-min)*2+1``: as long again, starting
    the frame after the asset's turn ends (1102-1202 for 1001-1101).
    """
    return asset_last + 1, asset_first + (asset_last - asset_first) * 2 + 1


def loop_length(asset_first: int, asset_last: int) -> int:
    """Frames in one loop: the asset's turn and the lights' turn after it."""
    return light_turn_range(asset_first, asset_last)[1] - asset_first + 1


def farm_frames(
    asset_first: int = ASSET_TURN_RANGE[0],
    asset_last: int = ASSET_TURN_RANGE[1],
    loops: int = LOOP_COUNT,
    step: int = FARM_STEP,
) -> TurntableFrames:
    """What the farm renders of a turntable: the studio's Farm tab defaults.

    First is the loop start; last is ``loops`` loop lengths past it (the
    studio's ``first_frame + frame_count * 5``), so 1001-2011 for the default
    1001-1202 loop; every ``step``-th frame. On a batch render the studio
    swaps its light rig once per loop, so the five loops are five lightings.
    """
    return TurntableFrames(
        asset_first, asset_first + loop_length(asset_first, asset_last) * loops, step,
    )


def stepped_frames(frames: TurntableFrames) -> list[int]:
    """The frames a turntable render makes: first to last on its step."""
    return list(range(frames.first, frames.last + 1, frames.step))


def render_layer(channel: str) -> str:
    """The render layer an asset channel's turntable writes.

    The default channel's is ``main``, the layer a studio's own farm render
    has always written, so the two are versions of one turntable. Any other
    channel keeps its name, so two channels never share a version counter;
    a channel itself named ``main`` gets ``channel_main`` (a channel name of
    letters and digits never spells that).
    """
    if channel == 'default':
        return DEFAULT_LAYER
    if channel == DEFAULT_LAYER:
        return f'channel_{channel}'
    return channel


def folder_name(channel: str) -> str:
    """The folder a channel's turntable is exported into, and bundled from.

    It holds the asset snapshot beside the exported stage, so the stage's
    sublayer of it (written relative by the export) still resolves once the
    folder is copied into the render job's data dir.
    """
    return f'turntable_{channel}'


#: The snapshot and the exported stage inside :func:`folder_name`.
ASSET_FILE = 'asset.usda'
STAGE_FILE = 'stage.usd'


def asset_input_name(channel: str) -> str:
    """The asset snapshot of a channel's turntable, relative to the collapse output."""
    return f'{folder_name(channel)}/{ASSET_FILE}'


def stage_name(channel: str) -> str:
    """The exported turntable stage, relative to the collapse output."""
    return f'{folder_name(channel)}/{STAGE_FILE}'


def workfile_name(channel: str) -> str:
    """The turntable workfile the collapse job builds for a channel (not bundled)."""
    return f'turntable_{channel}.hip'


def export_node_path() -> str:
    """The node a farm turntable workfile's export task exports."""
    return f'{WORKFILE_NETWORK}/{WORKFILE_NODE}/{EXPORT_NODE}'


def render_settings_name(channel: str) -> str:
    return f'render_settings_{channel}.json'
