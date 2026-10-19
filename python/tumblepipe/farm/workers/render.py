"""Render a stage with husk (Karma XPU), split the AOVs and publish them."""

from tempfile import TemporaryDirectory
from pathlib import Path
import shutil
import math
import json
import sys
import os

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.util.io import store_json
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import Husk, ITileStitch
from tumblepipe.apps import exr
from tumblepipe.farm import worker
from tumblepipe.farm.env import get_base_env, ocio_value, print_env, job_data_dir
from tumblepipe.farm.frames import frame_file, with_frame_token, HOUDINI_FRAME_TOKEN
from tumblepipe.farm.specs import render as render_spec
from tumblepipe.farm.worker import ensure_dir, error, headline

# Required AOVs that must be present - missing these fails the render
# Other AOVs (utility passes like depth, position) are optional
REQUIRED_AOVS = {'beauty', 'normal', 'albedo'}


def main(
    tile_count: int,
    render_range: BlockRange,
    input_path: Path,
    receipt_path: Path,
    output_paths: dict[str, Path]
    ) -> int:

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Output receipt paths
    receipt_paths = [
        frame_file(receipt_path, frame_index)
        for frame_index in render_range
    ]

    # Find missing output receipts
    missing_receipt_paths = [
        output_receipt_path
        for output_receipt_path in receipt_paths
        if not output_receipt_path.exists()
    ]

    # Check if all receipts already exist
    if len(missing_receipt_paths) == 0:
        print('Output receipts already exist')
        return 0

    # Get apps ready
    husk = Husk()
    itilestitch = ITileStitch()

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Temp paths
        temp_frame_path = temp_path / 'render' / 'render.####.exr'

        # Get and print environment for debugging
        env = get_base_env()
        print_env()

        # Render with husk and Karama XPU
        headline('Rendering tiles')
        x_tiles = y_tiles = int(math.sqrt(tile_count))
        temp_frame_path.parent.mkdir(parents=True, exist_ok=True)

        # Build base husk arguments
        # --resolver-context is required for custom ArResolver plugins to work with husk
        base_args = [
            '--resolver-context', path_str(input_path),
            '--make-output-path',
            '--no-mplay',
            '--check-licenses', 'Karma Renderer',
            '--threads', '-1',
            '--engine', 'xpu',
            '--frame', str(render_range.first_frame),
            '--frame-count', str(len(render_range))
        ]

        # Render tiles
        if tile_count == 1:
            # Single tile - render directly without tile flags
            husk.run(
                input_path,
                base_args + [
                    '--output', path_str(with_frame_token(temp_frame_path, HOUDINI_FRAME_TOKEN))
                ],
                env=env
            )
        else:
            # Multiple tiles - render with tile flags
            for tile_index in range(tile_count):
                husk.run(
                    input_path,
                    base_args + [
                        '--tile-count', str(x_tiles), str(y_tiles),
                        '--tile-index', str(tile_index),
                        '--tile-suffix', '.%04d',
                        '--output', path_str(with_frame_token(temp_frame_path, HOUDINI_FRAME_TOKEN))
                    ],
                    env=env
                )

            # Stitch the tiles together to create the frames
            headline('Stitching tiles')
            for frame_index in render_range:
                frame_path = frame_file(temp_frame_path, frame_index)
                tile_paths = [
                    temp_frame_path.parent / f'render.{frame_index:04d}.{tile_index:04d}.exr'
                    for tile_index in range(tile_count)
                ]
                print(f'Stitching tiles for frame {frame_index}: {frame_path}')
                itilestitch.run(
                    [ path_str(frame_path) ] +
                    [ path_str(tile_path) for tile_path in tile_paths ],
                    env = dict(OCIO=ocio_value())
                )

        # Check that the frames were generated
        for frame_index in render_range:
            current_frame_path = frame_file(temp_frame_path, frame_index)
            if current_frame_path.exists(): continue
            return error(f'Frame not generated: {current_frame_path}')

        # Split AOVs into separate stacks
        headline('Splitting AOVs')
        print(f'Expected AOV names from config: {list(output_paths.keys())}')
        framestack_aov_paths = dict()
        for frame_index in render_range:

            # Split the frame
            current_frame_path = frame_file(temp_frame_path, frame_index)
            print(f'Splitting frame: {current_frame_path}')
            split_frame_paths = exr.split_subimages(
                current_frame_path, temp_path
            )

            # Log found AOV names
            print(f'Found AOV names in EXR: {list(split_frame_paths.keys())}')

            # Store the paths
            aov_paths = dict()
            for aov_name, temp_aov_path in split_frame_paths.items():
                output_aov_path = output_paths.get(aov_name)
                if output_aov_path is None: continue
                aov_paths[aov_name] = (
                    temp_aov_path,
                    frame_file(output_aov_path, frame_index)
                )
            framestack_aov_paths[frame_index] = aov_paths

        # Validate that all expected AOVs were rendered
        expected_aovs = set(output_paths.keys())
        rendered_aovs = set()
        for aov_paths in framestack_aov_paths.values():
            rendered_aovs.update(aov_paths.keys())
        missing_aovs = expected_aovs - rendered_aovs

        if missing_aovs:
            # Check for potential name mismatches (case differences, etc.)
            exr_aovs_lower = {name.lower(): name for name in split_frame_paths.keys()}
            potential_matches = []
            for missing in missing_aovs:
                if missing.lower() in exr_aovs_lower:
                    potential_matches.append(f"'{missing}' -> '{exr_aovs_lower[missing.lower()]}'")

            # Separate required vs optional missing AOVs
            missing_required = {aov for aov in missing_aovs if aov.lower() in REQUIRED_AOVS}
            missing_optional = missing_aovs - missing_required

            if missing_optional:
                print(f'WARNING: Missing optional AOVs in rendered output: {sorted(missing_optional)}')
                print('  These utility passes were not produced by the renderer')

            if missing_required:
                print(f'ERROR: Missing required AOVs in rendered output: {sorted(missing_required)}')
                print(f'  Expected AOVs: {sorted(expected_aovs)}')
                print(f'  Rendered AOVs: {sorted(rendered_aovs)}')
                print(f'  EXR AOVs: {sorted(split_frame_paths.keys())}')
                if potential_matches:
                    print(f'  Potential name mismatches: {potential_matches}')
                return error(f'Render did not produce required AOVs. Missing: {missing_required}')

        # Copy frames to network
        headline('Copying files to network')
        for aov_paths in framestack_aov_paths.values():
            for temp_aov_path, output_aov_path in aov_paths.values():
                print(f'Copying file: {output_aov_path}')
                ensure_dir(output_aov_path.parent)
                shutil.copyfile(temp_aov_path, output_aov_path)

        # Verify all frames were copied successfully
        headline('Verifying copied frames')
        for frame_index, aov_paths in framestack_aov_paths.items():
            for aov_name, (_, output_aov_path) in aov_paths.items():
                if not output_aov_path.exists():
                    return error(f'Frame not copied: {output_aov_path}')
                print(f'Verified: {output_aov_path}')

        # Create the output receipts
        headline('Creating output receipts')
        for frame_index, aov_paths in framestack_aov_paths.items():
            current_receipt_path = frame_file(receipt_path, frame_index)
            print(f'Creating receipt: {current_receipt_path}')
            store_json(current_receipt_path, {
                aov_name: path_str(output_aov_path)
                for aov_name, (_, output_aov_path) in aov_paths.items()
            })

    # Check if output receipts were generated
    for receipt_path in receipt_paths:
        if receipt_path.exists(): continue
        return error(f'Output receipt not found: {receipt_path}')

    # Done
    print('Success')
    return 0

def from_config(config: dict, render_range: BlockRange) -> int:
    """Render ``render_range`` as the job's context.json describes."""
    headline('Config')
    print(json.dumps(config, indent=4))

    tile_count = config['tile_count']
    if tile_count not in render_spec.TILE_COUNTS:
        return error(f'Invalid tile count: {tile_count}')

    # The stage is bundled into the job data dir and referenced relatively;
    # resolve it against that dir, since the HPM task runs with CWD = the hpm
    # manifest dir, not the data dir.
    input_path = job_data_dir() / config['input_path']
    if not input_path.exists():
        return error(f'Input path not found: {input_path}')

    # Print input USD content for debugging (only for ASCII .usda files)
    if input_path.suffix == '.usda':
        headline('Input USD')
        print(input_path.read_text())

    return main(
        tile_count,
        render_range,
        input_path,
        Path(config['receipt_path']),
        {
            aov_name: Path(aov_path)
            for aov_name, aov_path in config['output_paths'].items()
        }
    )

if __name__ == '__main__':
    sys.exit(worker.run(render_spec.is_valid_context, from_config))
