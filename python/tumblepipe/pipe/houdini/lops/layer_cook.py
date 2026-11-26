"""Cooking a layer through a LOP node's export ROP: th::export_layer's
and th::layer_split's one path.

Both HDAs carry the same ROP parms (``export_f1``/``export_f2``/``export_f3``,
``export_lopoutput``, ``export_execute``); :class:`LayerWriter` drives them.
"""

import shutil

from tumblepipe.api import path_str
from tumblepipe.config.multi_timeline import shift_block_range
from tumblepipe.pipe.houdini import sim_start
from tumblepipe.pipe.houdini.export_checks import ExportLayerError, validate_export_files
from tumblepipe.pipe.usd.retime import shift_directory
from tumblepipe.pipe.usd.stitch import (
    calculate_chunks,
    flatten_sidecar_directories,
    stitch_usd_directories,
)
from tumblepipe.util.progress import report_progress


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
        * ``batch_size`` > 0 cooks chunks of that many frames and stitches them.
        """
        with sim_start.forced():
            self._write_layer(
                temp_path, layer_file_name,
                shift_block_range(render_range, time_offset), batch_size,
            )
        if time_offset != 0:
            report_progress(f"moving frames back by {time_offset}")
            shift_directory(temp_path, -time_offset)

    def _write_layer(self, temp_path, layer_file_name, render_range, batch_size):
        """Export the stage into ``temp_path/layer_file_name``: in one go, or
        (batch_size > 0) in chunks of frames stitched together."""
        self.parm('export_f3').set(1)
        if batch_size == 0:
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

        chunks_dir = temp_path / 'chunks'
        chunks_dir.mkdir()
        chunk_dirs = []
        for chunk_start, chunk_end in calculate_chunks(
            render_range.first_frame, render_range.last_frame, batch_size
        ):
            chunk_name = f"{chunk_start:04d}-{chunk_end:04d}"
            report_progress(
                f"cooking frames {chunk_start}-{chunk_end} of {render_range.last_frame}"
            )
            chunk_dir = chunks_dir / chunk_name
            chunk_dir.mkdir()
            self._export_frames(
                chunk_dir, layer_file_name, chunk_start, chunk_end,
                f"batch export chunk {chunk_name}",
            )
            chunk_dirs.append(chunk_dir)

        report_progress(f"stitching {len(chunk_dirs)} chunks")
        try:
            stitch_usd_directories(chunk_dirs, layer_file_name, temp_path)
        except Exception as error:
            raise ExportLayerError(
                f"Failed to stitch {len(chunk_dirs)} USD chunks into "
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
