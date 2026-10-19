"""Exporting a frame range in batches and stitching the batches back into one.

A long animated export is written as several frame chunks, each a layer of
its own (with any sidecar layers and textures beside it), then stitched:
the time samples of every chunk merged into one layer per file. The
interactive exporter (``pipe.houdini.lops.export_layer``) and the farm's
(``farm.workers.export_houdini``) both go through here.
"""

from pathlib import Path
import shutil


class UsdStitchError(Exception):
    """Raised when USD stitching fails."""
    pass


def calculate_chunks(first_frame: int, last_frame: int, batch_size: int) -> list[tuple[int, int]]:
    """Split an inclusive frame range into contiguous chunks of batch_size.

    Batches are sized on integer frames (not the render step): the export
    always writes on 1s, so [first, last] is tiled into back-to-back
    [start, end] spans whose union is exactly the full range with no gaps.
    ``batch_size <= 0`` means no batching - a single whole-range chunk.

    Shared by the interactive (``pipe.houdini.lops.export_layer``) and farm
    (``farm.workers.export_houdini``) exporters so both slice identically.
    """
    if batch_size <= 0:
        return [(first_frame, last_frame)]
    chunks = []
    current = first_frame
    while current <= last_frame:
        chunk_end = min(current + batch_size - 1, last_frame)
        chunks.append((current, chunk_end))
        current = chunk_end + 1
    return chunks


def flatten_sidecar_directories(export_path: Path) -> None:
    """Flatten Houdini's ``{filename}.usd.textures`` sidecar directories.

    Houdini writes COP-generated textures into a ``{filename}.usd.textures/``
    directory beside the exported USD. Move that content up next to the USD
    and drop the empty directory so the published layer's relative texture
    references resolve and (for batched export) the stitch can carry the
    textures through to the output alongside the main layer.
    """
    for item in export_path.iterdir():
        if item.is_dir() and item.name.endswith('.usd.textures'):
            # Move all contents from sidecar dir to parent
            for sidecar_item in item.iterdir():
                target = export_path / sidecar_item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                sidecar_item.rename(target)
            # Remove empty sidecar directory
            item.rmdir()


def stitch_usd_files(input_files: list[Path], output_file: Path) -> None:
    """Stitch multiple USD files into one using USD Python API.

    Uses pxr.UsdUtils.StitchLayers which properly merges time samples
    from multiple files without spawning external processes.

    Args:
        input_files: List of USD files to stitch together (in order).
        output_file: Path for the combined output file.

    Raises:
        UsdStitchError: If stitching fails.
    """
    from pxr import Sdf, UsdUtils

    if not input_files:
        raise UsdStitchError("No input files provided for stitching")

    # Verify all input files exist
    for f in input_files:
        if not f.exists():
            raise UsdStitchError(f"Input file does not exist: {f}")

    # Open first file as the base layer
    result_layer = Sdf.Layer.FindOrOpen(str(input_files[0]))
    if result_layer is None:
        raise UsdStitchError(f"Failed to open first input file: {input_files[0]}")

    # Stitch remaining files into the result layer
    for input_file in input_files[1:]:
        weak_layer = Sdf.Layer.FindOrOpen(str(input_file))
        if weak_layer is None:
            raise UsdStitchError(f"Failed to open input file: {input_file}")

        # StitchLayers merges weak_layer INTO result_layer
        # Time samples are merged as a union (what we want)
        UsdUtils.StitchLayers(result_layer, weak_layer)

    # Export the stitched result
    result_layer.Export(str(output_file))

    if not output_file.exists():
        raise UsdStitchError(f"Failed to create output file: {output_file}")


def stitch_usd_directories(
    chunk_dirs: list[Path],
    main_filename: str,
    output_dir: Path
) -> None:
    """Stitch multiple chunk directories into one output directory.

    Each chunk directory should contain:
    - A main USD file (main_filename)
    - Optional sidecar directories with USD files

    This function stitches the main USD files and all sidecar USD files
    found across the chunk directories, and carries any non-USD sidecar
    content (e.g. flattened .usd.textures) through to the output,
    preserving the directory structure.

    Args:
        chunk_dirs: List of chunk directories to merge (in order).
        main_filename: Name of the main USD file in each chunk (e.g., "layer.usd").
        output_dir: Output directory for merged result.

    Raises:
        UsdStitchError: If stitching fails.
    """
    if not chunk_dirs:
        raise UsdStitchError("No chunk directories provided")

    output_dir.mkdir(parents=True, exist_ok=True)
    main_rel = Path(main_filename)

    # 1. Stitch main USD files
    main_files = [d / main_filename for d in chunk_dirs]
    output_main = output_dir / main_filename
    stitch_usd_files(main_files, output_main)

    # 2. Group every non-main sidecar file across all chunks by its path
    # relative to its chunk. USD files (.usd) are stitched; everything else
    # (textures, volumes, ...) is carried through so it is not silently
    # dropped. Skip only the top-level main file - a like-named file deeper
    # in a sidecar directory is a distinct layer and must not be skipped.
    usd_sidecars: dict[Path, list[Path]] = {}
    other_sidecars: dict[Path, list[Path]] = {}
    for chunk_dir in chunk_dirs:
        for path in chunk_dir.rglob("*"):
            if not path.is_file():
                continue
            rel_path = path.relative_to(chunk_dir)
            if rel_path == main_rel:
                continue  # top-level main file, already stitched
            bucket = usd_sidecars if path.suffix == ".usd" else other_sidecars
            bucket.setdefault(rel_path, []).append(path)

    # 3. Stitch each USD sidecar (union its time samples across chunks)
    for rel_path, files in usd_sidecars.items():
        output_sidecar = output_dir / rel_path
        output_sidecar.parent.mkdir(parents=True, exist_ok=True)
        if len(files) == 1:
            # Only one chunk has this file, just copy it
            shutil.copy(files[0], output_sidecar)
        else:
            # Multiple chunks have this file, stitch them
            stitch_usd_files(files, output_sidecar)

    # 4. Carry non-USD sidecars through. Frame-varying assets carry a frame
    # number in their name (distinct rel paths, all preserved);
    # frame-independent ones repeat identically per chunk, so last-wins is
    # a straight copy of the same bytes.
    for rel_path, files in other_sidecars.items():
        output_sidecar = output_dir / rel_path
        output_sidecar.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(files[-1], output_sidecar)
