"""Exporting a frame range in batches and putting the batches back together.

A long animated export is written as several frame chunks, each a layer of
its own (with any sidecar layers and textures beside it), then joined in
one of two ways:

* **clips** (:func:`stitch_usd_clips`, #108): each chunk's time samples stay
  a file of their own in ``clips/`` beside the published layer, which holds
  everything else (prims, topology, arcs, defaults: the chunks' "topology")
  and names the chunks as USD value clips. Nothing ever holds the whole
  range in memory, and a render loads only the chunks its frames fall in.
  Each chunk carries the next chunk's first frame too, so motion blur at a
  chunk's last frame interpolates to the next frame as in one file.
* **stitch** (:func:`stitch_usd_directories`): the time samples of every
  chunk merged into one layer per file, as before #108. The whole range is
  in memory at once and the file is as big as an unbatched export.

The interactive exporter (``pipe.houdini.lops.export_layer``) and the farm's
turntable exporter (``farm.workers.export_houdini``, stitch only) both go
through here.
"""

import os
from pathlib import Path
import re
import shutil

# The files a flatten repoints: every USD layer an export writes
USD_SUFFIXES = ('.usd', '.usda', '.usdc')

# How a chunked export is joined (th::export_layer's ``batch_mode``)
CLIPS = 'clips'
STITCH = 'stitch'
CHUNK_MODES = (CLIPS, STITCH)

# The folder beside a clipped layer that holds its chunks and manifest
CLIPS_DIR = 'clips'


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
    directory beside the exported USD, and the layer names each texture
    there (``@./stage.usd.textures/img/tex/OUT.1.exr@``). Move that content
    up next to the USD, drop the empty directory, and point every USD file
    under ``export_path`` at where its textures went, so (for a batched
    export) the stitch carries the textures through beside the main layer.

    The paths used to stay as they were, so a published lookdev layer named
    textures in a folder that no longer existed and every COP texture broke
    after export (Remy, 2026-11-10).
    """
    sidecars = [
        item for item in export_path.iterdir()
        if item.is_dir() and item.name.endswith('.usd.textures')
    ]
    if not sidecars: return
    _repoint_sidecar_paths(export_path, sidecars)
    for item in sidecars:
        _merge_into(item, export_path)


def _merge_into(source: Path, target: Path) -> None:
    """Move everything in ``source`` into ``target`` and remove ``source``.
    Folders merge (two sidecars both holding ``img/`` keep both sets of
    textures; the first used to be deleted); a file replaces its namesake."""
    for item in source.iterdir():
        dest = target / item.name
        if item.is_dir() and dest.is_dir():
            _merge_into(item, dest)
            continue
        if dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists():
            dest.unlink()
        item.rename(dest)
    source.rmdir()



def flattened_asset_path(asset_path: str, layer_dir: Path, export_path: Path,
                         sidecar_names) -> str:
    """Where a layer's ``asset_path`` points once the sidecar folders named
    ``sidecar_names`` (directly under ``export_path``) are flattened into
    ``export_path``; ``asset_path`` itself when it points anywhere else.

    Pure path arithmetic: nothing is read from disk, so a UDIM or frame
    token (``<UDIM>``, ``$F4``) passes through as text.
    """
    if not asset_path or ':' in asset_path.split('/', 1)[0]:
        return asset_path  # empty, op:/, a URI, or a drive letter
    if os.path.isabs(asset_path): return asset_path
    target = os.path.normpath(os.path.join(str(layer_dir), asset_path))
    root = os.path.normpath(str(export_path))
    for name in sidecar_names:
        sidecar = os.path.join(root, name)
        if target == sidecar or not target.startswith(sidecar + os.sep):
            continue
        moved = os.path.join(root, os.path.relpath(target, sidecar))
        relative = os.path.relpath(moved, str(layer_dir)).replace(os.sep, '/')
        return relative if relative.startswith('../') else './' + relative
    return asset_path


def _repoint_sidecar_paths(export_path: Path, sidecars: list[Path]) -> None:
    """Rewrite the asset paths of every USD file under ``export_path`` that
    point into one of ``sidecars`` to where the flatten moves them."""
    from pxr import Sdf, UsdUtils
    names = [item.name for item in sidecars]
    for layer_path in sorted(export_path.rglob('*')):
        if layer_path.suffix not in USD_SUFFIXES or not layer_path.is_file():
            continue
        layer = Sdf.Layer.FindOrOpen(str(layer_path))
        if layer is None: continue
        changed = []

        def repoint(path, layer_dir=layer_path.parent):
            new = flattened_asset_path(path, layer_dir, export_path, names)
            if new != path: changed.append(path)
            return new

        UsdUtils.ModifyAssetPaths(layer, repoint)
        if changed:
            layer.Save()


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

    # 1. Stitch main USD files
    main_files = [d / main_filename for d in chunk_dirs]
    output_main = output_dir / main_filename
    stitch_usd_files(main_files, output_main)

    _merge_sidecars(chunk_dirs, Path(main_filename), output_dir)


def _merge_sidecars(chunk_dirs: list[Path], main_rel: Path, output_dir: Path) -> None:
    """Everything in the chunk directories but their main layer, joined
    into ``output_dir``: sidecar USD layers stitched, other files copied."""
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


# ── value clips (#108) ─────────────────────────────────────────────────────


def clip_cook_ranges(chunks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The frames each chunk of a clipped export cooks: its own and the next
    chunk's first frame (the last chunk only its own).

    A clip is active from its first frame up to the next clip's, so a time
    between a chunk's last frame and the next chunk's first (a motion blur
    shutter, a subframe) is read from this chunk. USD 0.26 interpolates to
    the next clip's first sample by itself, but from this chunk's last
    sample, wherever that is: a chunk that holds one value (written as a
    default, sampled once at its first frame) would ramp across the whole
    chunk. With the next frame in it, the chunk has what one file has.
    """
    return [
        (start, chunks[index + 1][0] if index + 1 < len(chunks) else end)
        for index, (start, end) in enumerate(chunks)
    ]


def clip_set_name(layer_file_name: str) -> str:
    """The clip set a clipped layer authors, named after the layer so two
    clipped layers composed on one prim (an anim and an fx publish) keep
    their own: ``clips`` is a dictionary, composed key by key."""
    return 'th_' + re.sub(r'[^A-Za-z0-9_]', '_', Path(layer_file_name).stem)


def clip_file_name(layer_file_name: str, start: int, end: int) -> str:
    """The chunk of ``layer_file_name`` holding frames ``start``..``end``."""
    stem = Path(layer_file_name).stem
    return f'{stem}.{start:04d}-{end:04d}.usd'


def manifest_file_name(layer_file_name: str) -> str:
    """The clip manifest of ``layer_file_name``: every attribute the clips
    sample. Authored, so a stage never opens every clip to make one."""
    return f'{Path(layer_file_name).stem}.manifest.usd'


def rebased_asset_path(asset_path: str) -> str:
    """``asset_path`` as a layer one folder down (``clips/``) must name the
    same file: anchored relative paths gain a ``../``; any other path (absolute,
    a search path, a URI) means the same from anywhere."""
    if asset_path.startswith('./'):
        return '../' + asset_path[2:]
    if asset_path.startswith('../'):
        return '../' + asset_path
    return asset_path


def _open_anonymous(path: Path):
    """``path`` opened outside the layer registry, so dropping the handle
    closes the file (Windows will not delete a chunk a layer still maps)."""
    from pxr import Sdf
    layer = Sdf.Layer.OpenAsAnonymous(str(path))
    if layer is None:
        raise UsdStitchError(f"Failed to open {path}")
    return layer


def _attribute_paths(layer) -> list:
    """Every attribute spec path in ``layer``, variants included."""
    from pxr import Sdf
    found = []

    def visit(path):
        if path.IsPropertyPath() and layer.GetAttributeAtPath(path) is not None:
            found.append(path)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return found


def _clip_sets_in(layer) -> list:
    """The prim paths in ``layer`` that author value clips."""
    from pxr import Sdf
    found = []

    def visit(path):
        if path.IsPrimPath() or path.IsPrimVariantSelectionPath():
            spec = layer.GetPrimAtPath(path)
            if spec is not None and spec.HasInfo('clips'):
                found.append(path)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return found


def value_clip_files(layer_path: Path) -> list[Path]:
    """The clip and manifest files the layer at ``layer_path`` names as
    value clips, resolved beside it; empty for a layer without clips."""
    layer = _open_anonymous(layer_path)
    base = Path(layer_path).parent
    files = []
    for prim_path in _clip_sets_in(layer):
        for clip_set in layer.GetPrimAtPath(prim_path).GetInfo('clips').values():
            names = [asset.path for asset in clip_set.get('assetPaths', [])]
            manifest = clip_set.get('manifestAssetPath')
            if manifest is not None and manifest.path:
                names.append(manifest.path)
            for name in names:
                if not name or ':' in name.split('/', 1)[0]:
                    continue  # a URI or a drive letter: not beside it
                path = Path(os.path.normpath(base / name))
                if path not in files:
                    files.append(path)
    return files


def is_clipped_layer(layer_path: Path) -> bool:
    """Whether the layer at ``layer_path`` names value clips."""
    return bool(value_clip_files(layer_path))


def stitch_usd_clips(
    chunk_dirs: list[Path],
    chunks: list[tuple[int, int]],
    main_filename: str,
    output_dir: Path,
) -> str:
    """Join chunk directories as value clips; the mode used (``clips``, or
    ``stitch`` when clips do not apply).

    ``chunk_dirs[i]`` holds ``main_filename`` exported over
    ``clip_cook_ranges(chunks)[i]`` (sidecars beside it, as
    :func:`stitch_usd_directories` takes them); ``chunks`` are the frames
    each clip is active for, back to back.

    Written to ``output_dir``:

    * ``main_filename``: the chunks' topology (``UsdUtils.StitchClipsTopology``:
      everything but time samples), the default of every attribute a chunk
      samples removed (a default in the anchoring layer would beat the clips),
      and on each root prim a clip set (:func:`clip_set_name`) naming the
      chunks. Start and end time codes are the whole range.
    * ``clips/<stem>.<first>-<last>.usd``: each chunk's time samples and
      nothing else; an attribute the chunk wrote as a default only (constant
      there, sampled in another chunk) gets that value as a sample at the
      chunk's first frame. Relative asset paths are rebased one folder down.
    * ``clips/<stem>.manifest.usd``: every sampled attribute, declared.
    * the sidecars, joined as :func:`stitch_usd_directories` joins them.

    ``interpolateMissingClipValues`` is on, so an attribute a chunk does not
    sample at all (a prim that is not there for a while) interpolates
    between the chunks that do, as the samples of one file would.

    Chunks that already author value clips, or that sample nothing, are
    stitched into one file instead (``stitch``).
    """
    from pxr import Gf, Sdf, UsdUtils, Vt

    if not chunk_dirs:
        raise UsdStitchError("No chunk directories provided")
    if len(chunk_dirs) != len(chunks):
        raise UsdStitchError(
            f"{len(chunk_dirs)} chunk directories for {len(chunks)} chunks"
        )
    main_files = [d / main_filename for d in chunk_dirs]
    for path in main_files:
        if not path.exists():
            raise UsdStitchError(f"Input file does not exist: {path}")

    # 1. What the chunks sample, by namespace path (a variant's attribute
    # is its prim's to a clip): one chunk open at a time.
    sampled: dict = {}
    for path in main_files:
        layer = _open_anonymous(path)
        if _clip_sets_in(layer):
            del layer
            stitch_usd_directories(chunk_dirs, main_filename, output_dir)
            return STITCH
        for attr_path in _attribute_paths(layer):
            if layer.GetNumTimeSamplesForPath(attr_path) == 0:
                continue
            key = attr_path.StripAllVariantSelections()
            if key in sampled:
                continue
            spec = layer.GetAttributeAtPath(attr_path)
            sampled[key] = (spec.typeName, spec.variability, spec.custom)
        del layer
    if not sampled:
        stitch_usd_directories(chunk_dirs, main_filename, output_dir)
        return STITCH

    output_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = output_dir / CLIPS_DIR
    clips_dir.mkdir(exist_ok=True)

    # 2. The topology, in the published layer
    root_path = output_dir / main_filename
    if root_path.exists():
        root_path.unlink()
    root = Sdf.Layer.CreateNew(str(root_path))
    if root is None:
        raise UsdStitchError(f"Failed to create {root_path}")
    UsdUtils.StitchClipsTopology(root, [str(path) for path in main_files])
    for attr_path in _attribute_paths(root):
        if attr_path.StripAllVariantSelections() in sampled:
            spec = root.GetAttributeAtPath(attr_path)
            if spec.HasDefaultValue():
                spec.ClearDefaultValue()

    # 3. Each chunk's samples, a clip of its own
    clip_names = []
    for (start, end), path in zip(chunks, main_files, strict=True):
        name = clip_file_name(main_filename, start, end)
        _write_clip(_open_anonymous(path), clips_dir / name, sampled, start)
        clip_names.append(name)

    # 4. The manifest
    manifest_name = manifest_file_name(main_filename)
    manifest_path = clips_dir / manifest_name
    if manifest_path.exists():
        manifest_path.unlink()
    manifest = Sdf.Layer.CreateNew(str(manifest_path))
    for key in sorted(sampled):
        type_name, variability, custom = sampled[key]
        prim = Sdf.CreatePrimInLayer(manifest, key.GetPrimPath())
        Sdf.AttributeSpec(prim, key.name, type_name, variability, custom)
    if not manifest.Save():
        raise UsdStitchError(f"Failed to save {manifest_path}")

    # 5. The clip sets, one per root prim with samples under it
    first, last = chunks[0][0], chunks[-1][1]
    times = [Gf.Vec2d(first, first)]
    if last != first:
        times.append(Gf.Vec2d(last, last))
    set_name = clip_set_name(main_filename)
    for root_prim in sorted({key.GetPrefixes()[0] for key in sampled}):
        spec = root.GetPrimAtPath(root_prim) or Sdf.CreatePrimInLayer(root, root_prim)
        spec.SetInfo('clips', {set_name: {
            'assetPaths': Sdf.AssetPathArray([
                f'./{CLIPS_DIR}/{name}' for name in clip_names
            ]),
            'manifestAssetPath': Sdf.AssetPath(f'./{CLIPS_DIR}/{manifest_name}'),
            'primPath': str(root_prim),
            'active': Vt.Vec2dArray([
                Gf.Vec2d(start, index) for index, (start, _end) in enumerate(chunks)
            ]),
            'times': Vt.Vec2dArray(times),
            'interpolateMissingClipValues': True,
        }})
    root.startTimeCode = first
    root.endTimeCode = last
    if not root.Save():
        raise UsdStitchError(f"Failed to save {root_path}")
    del root

    _merge_sidecars(chunk_dirs, Path(main_filename), output_dir)
    return CLIPS


def _write_clip(source, clip_path: Path, sampled: dict, start: int) -> None:
    """The time samples of ``source``'s ``sampled`` attributes, saved as a
    clip at ``clip_path``; an attribute with a default and no samples gets
    the default as a sample at ``start``."""
    from pxr import Sdf, UsdUtils

    if clip_path.exists():
        clip_path.unlink()
    clip = Sdf.Layer.CreateNew(str(clip_path))
    if clip is None:
        raise UsdStitchError(f"Failed to create {clip_path}")
    for attr_path in _attribute_paths(source):
        key = attr_path.StripAllVariantSelections()
        if key not in sampled or clip.GetAttributeAtPath(key) is not None:
            continue
        Sdf.CreatePrimInLayer(clip, key.GetPrimPath())
        if not Sdf.CopySpec(source, attr_path, clip, key):
            raise UsdStitchError(f"Failed to copy {attr_path} into {clip_path}")
        spec = clip.GetAttributeAtPath(key)
        if clip.GetNumTimeSamplesForPath(key) == 0:
            if not spec.HasDefaultValue():
                spec.connectionPathList.ClearEdits()
                continue
            clip.SetTimeSample(key, start, spec.default)
        spec.ClearDefaultValue()
        spec.connectionPathList.ClearEdits()
    for field in ('timeCodesPerSecond', 'framesPerSecond'):
        if source.pseudoRoot.HasInfo(field):
            clip.pseudoRoot.SetInfo(field, source.pseudoRoot.GetInfo(field))
    if source.HasStartTimeCode():
        clip.startTimeCode = source.startTimeCode
    if source.HasEndTimeCode():
        clip.endTimeCode = source.endTimeCode
    UsdUtils.ModifyAssetPaths(clip, rebased_asset_path)
    if not clip.Save():
        raise UsdStitchError(f"Failed to save {clip_path}")


def rename_layer_file(directory: Path, old_name: str, new_name: str) -> None:
    """Rename the exported layer ``directory/old_name`` to ``new_name``;
    a clipped one's clips, manifest and clip set follow the new name."""
    from pxr import Sdf, Vt

    old_path = Path(directory) / old_name
    new_path = Path(directory) / new_name
    if old_name == new_name:
        return
    if not is_clipped_layer(old_path):
        old_path.rename(new_path)
        return
    layer = _open_anonymous(old_path)
    old_set = clip_set_name(old_name)
    renames: dict[str, str] = {}
    for prim_path in _clip_sets_in(layer):
        spec = layer.GetPrimAtPath(prim_path)
        clips = dict(spec.GetInfo('clips'))
        if old_set not in clips:
            continue
        clip_set = dict(clips.pop(old_set))
        assets = []
        for asset in clip_set['assetPaths']:
            prefix, _, name = asset.path.rpartition('/')
            renamed = _renamed_clip(name, old_name, new_name)
            renames[name] = renamed
            assets.append(f'{prefix}/{renamed}' if prefix else renamed)
        clip_set['assetPaths'] = Sdf.AssetPathArray(assets)
        manifest = clip_set.get('manifestAssetPath')
        if manifest is not None:
            prefix, _, name = manifest.path.rpartition('/')
            renamed = manifest_file_name(new_name)
            renames[name] = renamed
            clip_set['manifestAssetPath'] = Sdf.AssetPath(
                f'{prefix}/{renamed}' if prefix else renamed
            )
        for key in ('active', 'times'):
            if key in clip_set:
                clip_set[key] = Vt.Vec2dArray(list(clip_set[key]))
        clips[clip_set_name(new_name)] = clip_set
        spec.SetInfo('clips', clips)
    clips_dir = Path(directory) / CLIPS_DIR
    for old, new in renames.items():
        if (clips_dir / old).exists():
            (clips_dir / old).rename(clips_dir / new)
    if not layer.Export(str(new_path)):
        raise UsdStitchError(f"Failed to save {new_path}")
    del layer
    old_path.unlink()


def _renamed_clip(clip_name: str, old_name: str, new_name: str) -> str:
    """``clip_name`` (a chunk of ``old_name``) as a chunk of ``new_name``."""
    old_stem, new_stem = Path(old_name).stem, Path(new_name).stem
    if clip_name.startswith(old_stem + '.'):
        return new_stem + clip_name[len(old_stem):]
    return clip_name
