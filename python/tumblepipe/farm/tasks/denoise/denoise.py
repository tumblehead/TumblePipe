"""Denoise a render's published per-AOV EXRs with Houdini's `idenoise`.

This used to launch hython and build a COPs network (`file` x3 -> `denoiseai`
-> `rop_image`) whose only real work was an Intel OIDN call. hython checks out
an Engine license, falling back to Core/FX -- so image post-processing could
take a seat an artist needed, and the graph cooked once per AOV per frame.

`idenoise` is Houdini's CLI front-end onto the same OIDN and consumes no
license token, so the whole job runs here in plain python. Per frame:
merge the per-AOV EXRs into one multi-plane file (idenoise reads its
normal/albedo guides as planes *inside* the input), denoise every AOV in one
call, then split the planes back out and DWAB-compress them to their published
paths. See designs/denoise-without-hython.md.
"""
from tempfile import TemporaryDirectory
from pathlib import Path
import logging
import sys
import os

# Add tumblepipe python packages path
tumblepipe_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblepipe_packages_path not in sys.path:
    sys.path.append(str(tumblepipe_packages_path))

from tumblepipe.api import (
    path_str,
    local_path,
    api
)
from tumblepipe.util.io import (
    load_json,
    store_json
)
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import IDenoise
from tumblepipe.apps.deadline import log_progress
from tumblepipe.apps import exr
from tumblepipe.farm.tasks.env import print_env
from tumblepipe.farm.tasks.denoise import _spec

# The guide planes OIDN uses to preserve detail. idenoise resolves them by
# plane name inside the merged frame, and they are denoised in their own right
# as well (they are published AOVs like any other).
GUIDE_AOV_NAMES = ('normal', 'albedo')

# idenoise's response to a --normal/--albedo/--aovs name it cannot resolve.
# For a *guide* this is not an error to idenoise: it warns, denoises unguided,
# and exits 0. Treated as a failure here -- see _run_idenoise.
MISSING_AOV_WARNING = "can't find specified AOV"

def _headline(title):
    print(f' {title} '.center(80, '='))

def _error(msg):
    logging.error(msg)
    return 1

def _fix_frame_pattern(frame_path, frame_pattern):
    name, _, ext = frame_path.name.split('.')
    return (
        frame_path.parent /
        f'{name}.{frame_pattern}.{ext}'
    )

def _get_frame_path(frame_path, frame_index):
    frame_name = str(frame_index).zfill(4)
    return (
        frame_path.parent /
        frame_path.name.replace('*', frame_name)
    )

def _get_frame_index(frame_path):
    return int(frame_path.stem.rsplit('.', 1)[-1])

def _should_denoise_aov(aov_name):
    """Denoise all AOVs except variance/MSE passes."""
    name = aov_name.lower()
    if name.endswith('_mse'): return False
    return True

def _is_critical_aov(aov_name):
    """AOVs the downstream slapcomp cannot proceed without."""
    name = aov_name.lower()
    if name == 'beauty': return True
    if name.startswith('objid_'): return True
    if name.startswith('holdout_'): return True
    return False

def _run_idenoise(idenoise, input_path, output_path, aov_names, force_cpu) -> int:
    args = [
        path_str(local_path(input_path)),
        path_str(local_path(output_path)),
        '--aovs', *aov_names,
        '--normal', 'normal',
        '--albedo', 'albedo'
    ]
    if force_cpu:
        args.append('--oidn-cpu')
    exit_code, output = idenoise.call(args)
    if MISSING_AOV_WARNING in output:
        # Only a *guide* reaches here with exit 0 (a missing target AOV already
        # exits 1). Denoising unguided still produces a plausible-looking frame,
        # just a worse one, so fail loudly rather than publish it silently.
        print('  ERROR: idenoise could not resolve a requested AOV/guide plane')
        return 1
    return exit_code

def _denoise_frame(
    idenoise,
    temp_path: Path,
    frame_index: int,
    input_paths: dict[str, Path],
    output_paths: dict[str, Path],
    channel_counts: dict[str, int],
    force_cpu: bool
    ) -> tuple[dict[str, Path], set[str]]:
    """Denoise every target AOV of one frame.

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
        frame_path = _get_frame_path(aov_path, frame_index)
        if not local_path(frame_path).exists():
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
    included = exr.combine_aovs(frame_input_paths, channel_counts, combined_path)
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
    if _run_idenoise(idenoise, combined_path, denoised_path, included, force_cpu) == 0:
        source_paths = {aov_name: denoised_path for aov_name in included}
    else:
        print(f'  Batch denoise failed for frame {frame_index}, isolating per AOV')
        for aov_name in included:
            aov_denoised_path = frame_temp_path / f'{aov_name}_denoised.exr'
            if _run_idenoise(
                idenoise, combined_path, aov_denoised_path, [aov_name], force_cpu
                ) != 0:
                print(f'  ERROR: Failed to denoise AOV {aov_name}')
                failed.add(aov_name)
                continue
            source_paths[aov_name] = aov_denoised_path

    # Split the denoised planes back out to their published paths
    written = dict()
    for aov_name, source_path in source_paths.items():
        extracted_path = frame_temp_path / f'{aov_name}.exr'
        if exr.extract_aov(source_path, aov_name, extracted_path) != 0:
            print(f'  ERROR: Failed to extract AOV {aov_name}')
            failed.add(aov_name)
            continue
        output_frame_path = _get_frame_path(output_paths[aov_name], frame_index)
        local_path(output_frame_path).parent.mkdir(parents = True, exist_ok = True)
        exr.dwab_encode(extracted_path, output_frame_path)
        if not local_path(output_frame_path).exists():
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
        _get_frame_path(receipt_path, frame_index)
        for frame_index in render_range
    ]
    missing_receipt_paths = [
        output_receipt_path
        for output_receipt_path in receipt_paths
        if not local_path(output_receipt_path).exists()
    ]
    if len(missing_receipt_paths) == 0:
        print('Output receipts already exist')
        return 0
    missing_frames = [
        _get_frame_index(missing_receipt_path)
        for missing_receipt_path in missing_receipt_paths
    ]

    # Check if enough aovs are available
    for guide_name in GUIDE_AOV_NAMES:
        if guide_name in input_paths: continue
        return _error(f'{guide_name.capitalize()} AOV not found')

    # Find the AOVs to denoise
    target_aov_paths = {
        aov_name: aov_path
        for aov_name, aov_path in input_paths.items()
        if _should_denoise_aov(aov_name)
    }

    # Filter to only AOVs that have source files on disk
    # (some AOVs may be in config but weren't rendered)
    available_aov_paths = dict()
    probe_frame_paths = dict()
    for aov_name, aov_path in target_aov_paths.items():
        test_frame_path = _get_frame_path(aov_path, render_range.first_frame)
        if local_path(test_frame_path).exists():
            available_aov_paths[aov_name] = aov_path
            probe_frame_paths[aov_name] = test_frame_path
        else:
            print(f'WARNING: Skipping AOV {aov_name} - source files not found')
    target_aov_paths = available_aov_paths

    # Check that target AOVs have somewhere to go
    for aov_name in target_aov_paths.keys():
        if aov_name in output_paths: continue
        return _error(f'No output path given for {aov_name}')

    # The channel layout is a property of the render, not of a frame, so probe
    # one real frame once instead of once per AOV per frame. The paths above are
    # frame *patterns*; the probe needs the concrete frame checked for existence.
    channel_counts = exr.get_channel_counts(probe_frame_paths)

    idenoise = IDenoise()
    failed_aovs = set()
    output_frame_paths = {
        frame_index: dict()
        for frame_index in render_range
    }

    # Denoise the input frames
    _headline('Denoising')
    root_temp_path = local_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
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
                force_cpu
            )
            output_frame_paths[frame_index] = written
            failed_aovs |= failed

    # Report failed AOVs
    if failed_aovs:
        print(
            f'\nWARNING: Failed to denoise {len(failed_aovs)} AOV(s): '
            f'{", ".join(sorted(failed_aovs))}'
        )

    # Check for critical AOV failures (beauty + mattes are required downstream)
    failed_critical = set(filter(_is_critical_aov, failed_aovs))
    if failed_critical:
        return _error(
            'Critical AOV(s) failed to denoise: '
            f'{", ".join(sorted(failed_critical))}'
        )

    # Create the frame receipts
    _headline('Creating frame receipts')
    for frame_index, aov_paths in output_frame_paths.items():
        # Filter out failed AOVs from receipt
        successful_aov_paths = {
            aov_name: aov_path
            for aov_name, aov_path in aov_paths.items()
            if aov_name not in failed_aovs
        }
        if len(successful_aov_paths) == 0: continue
        current_receipt_path = _get_frame_path(receipt_path, frame_index)
        print(f'Creating receipt: {current_receipt_path}')
        store_json(current_receipt_path, {
            aov_name: path_str(output_aov_path)
            for aov_name, output_aov_path in successful_aov_paths.items()
        })

    # Check that the missing receipts were generated
    for current_receipt_path in missing_receipt_paths:
        if local_path(current_receipt_path).exists(): continue
        return _error(f'Output receipt not found: {current_receipt_path}')

    # Done
    _headline('Done')
    print('Success')
    return 0

def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', type=str)
    parser.add_argument('first_frame', type=int)
    parser.add_argument('last_frame', type=int)
    args = parser.parse_args()

    # Load config data
    config_path = Path(args.config_path)
    config = load_json(config_path)
    if config is None:
        return _error(f'Config file not found: {config_path}')
    if not _spec.is_valid_context(config):
        return _error(f'Invalid config file: {config_path}')

    # Check render range
    first_frame = args.first_frame
    last_frame = args.last_frame
    if first_frame > last_frame:
        return _error('Invalid render range')
    render_range = BlockRange(first_frame, last_frame)

    # Get the receipt path
    receipt_path = _fix_frame_pattern(Path(config['receipt_path']), '*')

    # Get the input paths
    input_paths = {
        aov_name: _fix_frame_pattern(Path(aov_path), '*')
        for aov_name, aov_path in config['input_paths'].items()
    }

    # Get the output paths
    output_paths = {
        aov_name: _fix_frame_pattern(Path(aov_path), '*')
        for aov_name, aov_path in config['output_paths'].items()
    }

    # Run the main function
    return main(
        render_range,
        receipt_path,
        input_paths,
        output_paths,
        config.get('force_cpu', False)
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())
