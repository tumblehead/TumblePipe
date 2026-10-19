"""Composite a span of frames: cook the comp workfile's render node in hython
(``composite_houdini.py``) for the frames still missing their receipts."""

from tempfile import TemporaryDirectory
from pathlib import Path
import sys
import os

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.util.io import store_json
from tumblepipe.config.timeline import BlockRange
from tumblepipe.util.uri import Uri
from tumblepipe.apps.houdini import Hython
from tumblepipe.farm import worker
from tumblepipe.farm.env import get_hython_env, job_data_dir, print_env
from tumblepipe.farm.frames import frame_file, frame_of
from tumblepipe.farm.specs import composite as composite_spec
from tumblepipe.farm.worker import error

SCRIPT_PATH = Path(__file__).parent / 'composite_houdini.py'


def main(
    render_range: BlockRange,
    receipt_path: Path,
    input_path: Path,
    node_path: str,
    layer_names: list[str],
    output_paths: dict[str, Path],
    entity_json: dict
    ) -> int:

    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )

    # Print environment variables for debugging
    print_env()

    # Find the frames still missing their receipts
    missing_receipt_paths = [
        current_receipt_path
        for current_receipt_path in (
            frame_file(receipt_path, frame_index)
            for frame_index in render_range
        )
        if not current_receipt_path.exists()
    ]
    if len(missing_receipt_paths) == 0:
        print('Output receipts already exist')
        return 0
    missing_frames = list(map(frame_of, missing_receipt_paths))

    # Get hython ready
    hython = Hython()

    # Open a temporary directory
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Store the hython half's config
        config_path = temp_path / 'config.json'
        store_json(config_path, dict(
            entity = entity_json,
            first_frame = render_range.first_frame,
            last_frame = render_range.last_frame,
            frames = missing_frames,
            receipt_path = path_str(receipt_path),
            input_path = path_str(input_path),
            node_path = node_path,
            layer_names = layer_names,
            output_paths = {
                aov_name: path_str(output_path)
                for aov_name, output_path in output_paths.items()
            }
        ))

        # Run script in hython
        hython.run(
            SCRIPT_PATH,
            [path_str(config_path)],
            env = get_hython_env()
        )

    # Check if output receipts were generated
    for current_receipt_path in missing_receipt_paths:
        if current_receipt_path.exists(): continue
        return error(f'Output receipt not found: {current_receipt_path}')

    # Done
    print('Success')
    return 0


def from_config(config: dict, frame_span: BlockRange) -> int:
    """Composite the stepped frames of ``frame_span`` as the context describes.

    The span's bounds come from Deadline's per-chunk argv, the step from the
    config. Dropping the step made the receipts enumerate every in-between
    frame, so a stepped comp rendered and published a gapped step-1 sequence
    that never read as complete.
    """
    return main(
        BlockRange(
            frame_span.first_frame,
            frame_span.last_frame,
            config['step_size']
        ),
        Path(config['receipt_path']),
        # The workfile is bundled into the job data dir, addressed relative
        # to it (the task runs with CWD = the hpm manifest dir).
        job_data_dir() / config['input_path'],
        config['node_path'],
        config['layer_names'],
        {
            aov_name: Path(aov_path)
            for aov_name, aov_path in config['output_paths'].items()
        },
        config['entity']
    )


if __name__ == '__main__':
    sys.exit(worker.run(composite_spec.is_valid_context, from_config))
