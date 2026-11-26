"""Cooking a layer through a LOP node's export ROP: th::export_layer's
and th::layer_split's one path.

Both HDAs carry the same ROP parms (``export_f1``/``export_f2``/``export_f3``,
``export_lopoutput``, ``export_execute``); :class:`LayerWriter` drives them.
"""

import logging
import shutil

from tumblepipe.api import path_str
from tumblepipe.config.multi_timeline import shift_block_range
from tumblepipe.pipe.houdini import sim_start
from tumblepipe.pipe.houdini.export_checks import ExportLayerError, validate_export_files
from tumblepipe.pipe.usd.retime import shift_directory
from tumblepipe.pipe.usd.stitch import (
    CHUNK_MODES,
    CLIPS,
    calculate_chunks,
    clip_cook_ranges,
    flatten_sidecar_directories,
    stitch_usd_clips,
    stitch_usd_directories,
)
from tumblepipe.pipe.usd.chunk_plan import (
    DEFAULT_RENDER_BATCH,
    auto_chunks,
    budget_bytes,
    frame_bytes,
)
from tumblepipe.util.progress import report_progress

logger = logging.getLogger(__name__)


def _gigabytes(size: int) -> str:
    return f"{size / 2 ** 30:.2f} GB"


class LayerWriter:
    """A node that cooks its stage into a layer file through its export ROP."""

    def cook_layer(self, temp_path, layer_file_name, render_range, time_offset: int = 0, batch_size: int = 0):
        """Cook ``render_range`` into ``temp_path/layer_file_name``.

        * On 1s whatever the render step: a published stage cache carries
          every frame, so a render can sample any of them (and a later
          switch to 1s); batched chunks would also come out off a step>1
          grid. The farm exporter has always dropped the step.
        * TumbleRig sims (Motion Effects, Collisions, Wobl...) export From
          Start Frame, the same frames whatever was played before
          (``sim_start``), live as on the farm.
        * On a Multi's shared timeline (``time_offset``) the frames are
          cooked where the shot sits there, then written back on the shot's
          own frames.
        * ``batch_size`` > 0 cooks chunks of that many frames and joins them
          (Batch Mode: value clips in ``clips/``, or one stitched file).
        """
        with sim_start.forced():
            self._write_layer(
                temp_path, layer_file_name,
                shift_block_range(render_range, time_offset), batch_size,
            )
        if time_offset != 0:
            report_progress(f"moving frames back by {time_offset}")
            shift_directory(temp_path, -time_offset)

    # th::export_layer's menu: how chunks are joined. Missing on
    # th::layer_split (and on nodes saved before #108): value clips.
    BATCH_MODE_PARM = 'batch_mode'

    def get_batch_mode(self) -> str:
        parm = self.parm(self.BATCH_MODE_PARM)
        mode = parm.evalAsString() if parm is not None else CLIPS
        return mode if mode in CHUNK_MODES else CLIPS

    def _plan_chunks(self, render_range, batch_size) -> list[tuple[int, int]]:
        """The chunks to cook ``render_range`` in: ``batch_size`` frames
        each when set, else sized from the stage (``chunk_plan``): one
        whole-range chunk while its samples fit the budget."""
        first, last = render_range.first_frame, render_range.last_frame
        if batch_size > 0:
            return calculate_chunks(first, last, batch_size)
        per_frame = self._frame_bytes()
        render_batch = self._render_batch_size()
        budget = budget_bytes()
        chunks = auto_chunks(first, last, per_frame, render_batch, budget)
        frames = last - first + 1
        if len(chunks) > 1:
            message = (
                f"{frames} frames of about {_gigabytes(per_frame * frames)} of "
                f"samples: exporting {len(chunks)} clips of up to "
                f"{chunks[0][1] - chunks[0][0] + 1} frames"
            )
            logger.info(message)
            report_progress(message)
        else:
            logger.info(
                "%d frames of about %s of samples: one file",
                frames, _gigabytes(per_frame * frames),
            )
        return chunks

    def _frame_bytes(self) -> int:
        """One frame's raw samples on the stage the export ROP writes; 0
        (one file, as before) when it cannot be read."""
        try:
            import hou
            rop = self.native().node('export')
            source = rop.inputs()[0] if rop is not None and rop.inputs() else None
            stage = source.stage() if source is not None else self.native().stage()
            if stage is None:
                return 0
            return frame_bytes(stage, hou.frame())
        except Exception as error:  # an estimate: never fail the export over it
            logger.warning("Could not size the export from its stage: %s", error)
            return 0

    def _render_batch_size(self) -> int:
        """The entity's farm render batch (Farm Submit's Batch default)."""
        try:
            from tumblepipe.api import api
            properties = api.config.get_properties(self.require_entity_uri()) or {}
            value = (properties.get('farm') or {}).get('batch_size')
        except Exception as error:
            logger.warning("Could not read the render batch size: %s", error)
            value = None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return DEFAULT_RENDER_BATCH
        return value

    def _write_layer(self, temp_path, layer_file_name, render_range, batch_size):
        """Export the stage into ``temp_path/layer_file_name``: in one go, or
        in chunks of frames joined as value clips (or, with Batch Mode set
        to Stitched, into one file)."""
        self.parm('export_f3').set(1)
        chunks = self._plan_chunks(render_range, batch_size)
        if batch_size == 0 and len(chunks) == 1:
            report_progress(
                f"cooking frames {render_range.first_frame}-{render_range.last_frame}"
            )
            self._export_frames(
                temp_path, layer_file_name,
                render_range.first_frame, render_range.last_frame,
                f"standard export (frames {render_range.first_frame}-"
                f"{render_range.last_frame})",
            )
            return

        mode = self.get_batch_mode()
        cook_ranges = clip_cook_ranges(chunks) if mode == CLIPS else chunks
        chunks_dir = temp_path / 'chunks'
        chunks_dir.mkdir()
        chunk_dirs = []
        for (chunk_start, chunk_end), (cook_start, cook_end) in zip(
            chunks, cook_ranges, strict=True
        ):
            chunk_name = f"{chunk_start:04d}-{chunk_end:04d}"
            report_progress(
                f"cooking frames {chunk_start}-{chunk_end} of {render_range.last_frame}"
            )
            chunk_dir = chunks_dir / chunk_name
            chunk_dir.mkdir()
            self._export_frames(
                chunk_dir, layer_file_name, cook_start, cook_end,
                f"batch export chunk {chunk_name}",
            )
            chunk_dirs.append(chunk_dir)

        report_progress(
            f"writing {len(chunk_dirs)} chunks as clips" if mode == CLIPS
            else f"stitching {len(chunk_dirs)} chunks"
        )
        try:
            if mode == CLIPS:
                stitch_usd_clips(chunk_dirs, chunks, layer_file_name, temp_path)
            else:
                stitch_usd_directories(chunk_dirs, layer_file_name, temp_path)
        except Exception as error:
            raise ExportLayerError(
                f"Failed to join {len(chunk_dirs)} USD chunks into "
                f"{temp_path / layer_file_name}: {error}"
            ) from error
        shutil.rmtree(chunks_dir)

    def _export_frames(self, out_dir, layer_file_name, first_frame, last_frame, description):
        """Run the export ROP for ``first_frame``..``last_frame`` into
        ``out_dir/layer_file_name``, flattening its sidecar folders."""
        # COP textures land in 'stage.usd.textures/' beside it; the flatten
        # below moves them up and repoints the layers at them
        temp_export_name = 'stage.usd'
        self.parm('export_f1').deleteAllKeyframes()
        self.parm('export_f2').deleteAllKeyframes()
        self.parm('export_f1').set(first_frame)
        self.parm('export_f2').set(last_frame)
        self.parm('export_lopoutput').set(path_str(out_dir / temp_export_name))
        self.parm('export_execute').pressButton()
        validate_export_files(out_dir, temp_export_name, description)
        (out_dir / temp_export_name).rename(out_dir / layer_file_name)
        flatten_sidecar_directories(out_dir)
