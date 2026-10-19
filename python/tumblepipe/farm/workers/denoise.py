"""Denoise a render's published per-AOV EXRs with Houdini's `idenoise`.

This used to launch hython and build a COPs network (`file` x3 -> `denoiseai`
-> `rop_image`) whose only real work was an Intel OIDN call. hython checks out
an Engine license, falling back to Core/FX -- so image post-processing could
take a seat an artist needed, and the graph cooked once per AOV per frame.

`idenoise` is Houdini's CLI front-end onto the same OIDN and consumes no
license token, so the whole job runs here in plain python. Per frame:
merge the per-AOV EXRs into one multi-plane file (idenoise reads its
normal/albedo guides as planes *inside* the input), denoise every AOV in one
call, then split the planes back out and write them to their published paths
with the compression each AOV's render input had -- the render department's
nodes choose it, the denoise keeps it.

Mattes (`objid_*`, `holdout_*`) and distance ramps (`ramp_*`) are not
denoised: OIDN softens a matte's hard edges and smears a ramp, and neither is
noisy to begin with. They are published to the denoise output as byte copies
of their render input -- same pixels, same compression, same ACEScg stamp --
and never enter the combined file, so they cannot change how the other AOVs
are denoised.

The beauty is rendered RGBA. OIDN denoises its RGB; its alpha is the render's
own coverage (the part that honours holdouts, which the old separate `alpha`
AOV did not), so it is carried around the denoise untouched and published
back onto the denoised RGB.
"""
from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import sys
import os

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.util.io import store_json
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.uri import Uri
from tumblepipe.apps.app import CommandFailed
from tumblepipe.apps.houdini import IDenoise
from tumblepipe.apps.deadline import log_progress
from tumblepipe.apps import exr
from tumblepipe.farm import worker
from tumblepipe.farm.env import print_env
from tumblepipe.farm.frames import frame_file, frame_of
from tumblepipe.farm.specs import denoise as denoise_spec
from tumblepipe.farm.worker import ensure_dir, error, headline
from tumblepipe.pipe.aovs import is_matte_aov, is_ramp_aov

# The guide planes OIDN uses to preserve detail. idenoise resolves them by
# plane name inside the merged frame, and they are denoised in their own right
# as well (they are published AOVs like any other).
GUIDE_AOV_NAMES = ('normal', 'albedo')

# idenoise's response to a --normal/--albedo/--aovs name it cannot resolve.
# For a *guide* this is not an error to idenoise: it warns, denoises unguided,
# and exits 0. Treated as a failure here -- see _run_idenoise.
MISSING_AOV_WARNING = "can't find specified AOV"

def _should_pass_through_aov(aov_name):
    """AOVs published unchanged: the mattes and the distance ramps."""
    if is_matte_aov(aov_name): return True
    if is_ramp_aov(aov_name): return True
    return False

def _should_denoise_aov(aov_name):
    """Denoise all AOVs except variance/MSE passes and the pass-through ones."""
    name = aov_name.lower()
    if name.endswith('_mse'): return False
    if _should_pass_through_aov(aov_name): return False
    return True

def _is_critical_aov(aov_name):
    """AOVs the downstream slapcomp cannot proceed without."""
    name = aov_name.lower()
    if name == 'beauty': return True
    if name.startswith('objid_'): return True
    if name.startswith('holdout_'): return True
    return False

def _run_idenoise(idenoise, input_path, output_path, aov_names, force_cpu) -> bool:
    """Whether idenoise denoised ``aov_names`` guided, as asked."""
    args = [
        path_str(input_path),
        path_str(output_path),
        '--aovs', *aov_names,
        '--normal', 'normal',
        '--albedo', 'albedo'
    ]
    if force_cpu:
        args.append('--oidn-cpu')
    try:
        output = idenoise.call(args)
    except CommandFailed:
        return False
    if MISSING_AOV_WARNING in output:
        # Only a *guide* reaches here with exit 0 (a missing target AOV already
        # exits 1). Denoising unguided still produces a plausible-looking frame,
        # just a worse one, so fail loudly rather than publish it silently.
        print('  ERROR: idenoise could not resolve a requested AOV/guide plane')
        return False
    return True

def _pass_through_frame(
    frame_index: int,
    input_paths: dict[str, Path],
    output_paths: dict[str, Path]
    ) -> tuple[dict[str, Path], set[str]]:
    """Publish the pass-through AOVs of one frame unchanged.

    A byte copy, not an oiiotool re-encode: re-encoding a DWA-compressed
    input would quantise it a second time, and the copy keeps the input's
    compression and its ACEScg stamp (applied when the render was split into
    per-AOV files, see exr.split_subimages) as they are -- 1- and 3-channel
    mattes alike. Same ``(written, failed)`` contract as ``_denoise_frame``.
    """
    written = dict()
    failed = set()
    for aov_name, aov_path in input_paths.items():
        frame_path = frame_file(aov_path, frame_index)
        if not frame_path.exists():
            print(f'  WARNING: Source file missing: {frame_path}')
            failed.add(aov_name)
            continue
        output_frame_path = frame_file(output_paths[aov_name], frame_index)
        ensure_dir(output_frame_path.parent)
        try:
            shutil.copyfile(frame_path, output_frame_path)
        except OSError as failure:
            print(f'  ERROR: Failed to publish AOV {aov_name}: {failure}')
            failed.add(aov_name)
            continue
        written[aov_name] = output_frame_path
    return written, failed

def _denoise_frame(
    idenoise,
    temp_path: Path,
    frame_index: int,
    input_paths: dict[str, Path],
    output_paths: dict[str, Path],
    channel_counts: dict[str, int],
    compressions: dict[str, str],
    force_cpu: bool,
    alpha_channels: dict[str, int] = None
    ) -> tuple[dict[str, Path], set[str]]:
    """Denoise every target AOV of one frame.

    ``alpha_channels`` names the AOVs with an alpha channel (the RGBA beauty)
    and its index in their render input: that channel is published as is,
    after the denoised RGB.

    Returns ``(written, failed)``: the AOVs published for this frame, and the
    AOVs that could not be. A failed AOV does not sink the frame -- only the
    critical ones fail the task, back in ``main``.
    """
    frame_temp_path = temp_path / str(frame_index).zfill(4)
    frame_temp_path.mkdir(parents = True, exist_ok = True)
    failed = set()

    # Drop AOVs with no source file for this frame
    frame_input_paths = dict()
    for aov_name, aov_path in input_paths.items():
        frame_path = frame_file(aov_path, frame_index)
        if not frame_path.exists():
            print(f'  WARNING: Source file missing: {frame_path}')
            failed.add(aov_name)
            continue
        frame_input_paths[aov_name] = frame_path

    # Without the guides there is nothing to denoise against
    for guide_name in GUIDE_AOV_NAMES:
        if guide_name in frame_input_paths: continue
        print(f'  ERROR: Guide AOV {guide_name} missing for frame {frame_index}')
        return dict(), set(input_paths.keys())

    # Merge the per-AOV files into the one multi-plane file idenoise reads
    combined_path = frame_temp_path / 'combined.exr'
    try:
        included = exr.combine_aovs(frame_input_paths, channel_counts, combined_path)
    except CommandFailed:
        included = None
    if included is None:
        print(f'  ERROR: Failed to combine AOVs for frame {frame_index}')
        return dict(), set(input_paths.keys())
    failed |= set(frame_input_paths.keys()) - set(included)
    for guide_name in GUIDE_AOV_NAMES:
        if guide_name in included: continue
        print(f'  ERROR: Guide AOV {guide_name} dropped from frame {frame_index}')
        return dict(), set(input_paths.keys())

    # One call for the whole frame; on failure, retry AOV by AOV so a single
    # bad AOV costs only itself instead of the frame.
    denoised_path = frame_temp_path / 'denoised.exr'
    source_paths = dict()
    if _run_idenoise(idenoise, combined_path, denoised_path, included, force_cpu):
        source_paths = {aov_name: denoised_path for aov_name in included}
    else:
        print(f'  Batch denoise failed for frame {frame_index}, isolating per AOV')
        for aov_name in included:
            aov_denoised_path = frame_temp_path / f'{aov_name}_denoised.exr'
            if not _run_idenoise(
                idenoise, combined_path, aov_denoised_path, [aov_name], force_cpu
                ):
                print(f'  ERROR: Failed to denoise AOV {aov_name}')
                failed.add(aov_name)
                continue
            source_paths[aov_name] = aov_denoised_path

    # Split the denoised planes back out to their published paths
    alpha_channels = alpha_channels or dict()
    written = dict()
    for aov_name, source_path in source_paths.items():
        extracted_path = frame_temp_path / f'{aov_name}.exr'
        alpha = None
        if aov_name in alpha_channels:
            alpha = (frame_input_paths[aov_name], alpha_channels[aov_name])
        try:
            exr.extract_aov(source_path, aov_name, extracted_path, alpha)
        except CommandFailed:
            print(f'  ERROR: Failed to extract AOV {aov_name}')
            failed.add(aov_name)
            continue
        output_frame_path = frame_file(output_paths[aov_name], frame_index)
        ensure_dir(output_frame_path.parent)
        # Keep the compression the render wrote this AOV with: the render
        # department's nodes choose it (lossless for mattes and depth), and
        # the denoise must not override that.
        try:
            exr.encode(
                extracted_path, output_frame_path,
                compressions[aov_name]
            )
        except CommandFailed:
            print(f'  ERROR: Failed to encode AOV {aov_name}')
            failed.add(aov_name)
            continue
        if not output_frame_path.exists():
            print(f'  ERROR: Frame not written: {output_frame_path}')
            failed.add(aov_name)
            continue
        written[aov_name] = output_frame_path
    return written, failed

def main(
    render_range: BlockRange,
    receipt_path: Path,
    input_paths: dict[str, Path],
    output_paths: dict[str, Path],
    force_cpu: bool
    ) -> int:

    # Check that OCIO has been set. idenoise needs no colour config, but an
    # unset OCIO means the farm env was not built properly -- fail fast.
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Print environment variables for debugging
    print_env()

    # Find missing output receipts
    receipt_paths = [
        frame_file(receipt_path, frame_index)
        for frame_index in render_range
    ]
    missing_receipt_paths = [
        output_receipt_path
        for output_receipt_path in receipt_paths
        if not output_receipt_path.exists()
    ]
    if len(missing_receipt_paths) == 0:
        print('Output receipts already exist')
        return 0
    missing_frames = [
        frame_of(missing_receipt_path)
        for missing_receipt_path in missing_receipt_paths
    ]

    # Check if enough aovs are available
    for guide_name in GUIDE_AOV_NAMES:
        if guide_name in input_paths: continue
        return error(f'{guide_name.capitalize()} AOV not found')

    # Find the AOVs to publish: denoised, or passed through unchanged
    target_aov_paths = {
        aov_name: aov_path
        for aov_name, aov_path in input_paths.items()
        if _should_denoise_aov(aov_name)
        or _should_pass_through_aov(aov_name)
    }

    # Filter to only AOVs that have source files on disk
    # (some AOVs may be in config but weren't rendered)
    available_aov_paths = dict()
    probe_frame_paths = dict()
    for aov_name, aov_path in target_aov_paths.items():
        test_frame_path = frame_file(aov_path, render_range.first_frame)
        if test_frame_path.exists():
            available_aov_paths[aov_name] = aov_path
            probe_frame_paths[aov_name] = test_frame_path
        else:
            print(f'WARNING: Skipping AOV {aov_name} - source files not found')
    target_aov_paths = available_aov_paths

    # Check that target AOVs have somewhere to go
    for aov_name in target_aov_paths.keys():
        if aov_name in output_paths: continue
        return error(f'No output path given for {aov_name}')

    # Set the pass-through AOVs aside: they never reach idenoise
    passthrough_aov_paths = {
        aov_name: aov_path
        for aov_name, aov_path in target_aov_paths.items()
        if _should_pass_through_aov(aov_name)
    }
    target_aov_paths = {
        aov_name: aov_path
        for aov_name, aov_path in target_aov_paths.items()
        if aov_name not in passthrough_aov_paths
    }
    probe_frame_paths = {
        aov_name: probe_frame_path
        for aov_name, probe_frame_path in probe_frame_paths.items()
        if aov_name in target_aov_paths
    }

    # The channel layout is a property of the render, not of a frame, so probe
    # one real frame once instead of once per AOV per frame. The paths above are
    # frame *patterns*; the probe needs the concrete frame checked for existence.
    channel_counts = exr.get_channel_counts(probe_frame_paths)
    compressions = exr.get_compressions(probe_frame_paths)
    alpha_channels = exr.get_alpha_channels(probe_frame_paths)

    idenoise = IDenoise()
    failed_aovs = set()
    output_frame_paths = {
        frame_index: dict()
        for frame_index in render_range
    }

    # Denoise the input frames
    headline('Denoising')
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        for frame_index in log_progress(missing_frames):
            print(f'Denoising frame {frame_index}')
            written, failed = _denoise_frame(
                idenoise,
                temp_path,
                frame_index,
                target_aov_paths,
                output_paths,
                channel_counts,
                compressions,
                force_cpu,
                alpha_channels
            )
            passed, passed_failed = _pass_through_frame(
                frame_index,
                passthrough_aov_paths,
                output_paths
            )
            output_frame_paths[frame_index] = {**written, **passed}
            failed_aovs |= failed | passed_failed

    # Report failed AOVs
    if failed_aovs:
        print(
            f'\nWARNING: Failed to denoise {len(failed_aovs)} AOV(s): '
            f'{", ".join(sorted(failed_aovs))}'
        )

    # Check for critical AOV failures (beauty + mattes are required downstream)
    failed_critical = set(filter(_is_critical_aov, failed_aovs))
    if failed_critical:
        return error(
            'Critical AOV(s) failed to denoise: '
            f'{", ".join(sorted(failed_critical))}'
        )

    # Create the frame receipts
    headline('Creating frame receipts')
    for frame_index, aov_paths in output_frame_paths.items():
        # Filter out failed AOVs from receipt
        successful_aov_paths = {
            aov_name: aov_path
            for aov_name, aov_path in aov_paths.items()
            if aov_name not in failed_aovs
        }
        if len(successful_aov_paths) == 0: continue
        current_receipt_path = frame_file(receipt_path, frame_index)
        print(f'Creating receipt: {current_receipt_path}')
        store_json(current_receipt_path, {
            aov_name: path_str(output_aov_path)
            for aov_name, output_aov_path in successful_aov_paths.items()
        })

    # Check that the missing receipts were generated
    for current_receipt_path in missing_receipt_paths:
        if current_receipt_path.exists(): continue
        return error(f'Output receipt not found: {current_receipt_path}')

    # Done
    headline('Done')
    print('Success')
    return 0

def from_config(config: dict, render_range: BlockRange) -> int:
    """Denoise ``render_range`` as the job's context.json describes."""
    return main(
        render_range,
        Path(config['receipt_path']),
        {
            aov_name: Path(aov_path)
            for aov_name, aov_path in config['input_paths'].items()
        },
        {
            aov_name: Path(aov_path)
            for aov_name, aov_path in config['output_paths'].items()
        },
        config['force_cpu']
    )

if __name__ == '__main__':
    sys.exit(worker.run(denoise_spec.is_valid_context, from_config))

