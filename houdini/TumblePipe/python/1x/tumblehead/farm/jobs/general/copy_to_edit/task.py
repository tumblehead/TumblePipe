from pathlib import Path
from tqdm import tqdm
import logging
import shutil
import sys
import os

logging.basicConfig(
    level = logging.DEBUG,
    format = '%(message)s',
    stream = sys.stdout
)

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import default_client
from tumblehead.pipe.paths import (
    ShotEntity,
    get_latest_aov_frame_path
)

api = default_client()

def _error(msg):
    print(f'Error: {msg}')
    return 1

def main(
    entity: ShotEntity,
    render_layer_name: str,
    target_path: Path,
    ):

    # Paths
    frame_path = get_latest_aov_frame_path(
        entity,
        'render',
        render_layer_name,
        'beauty',
        '*'
    )
    if frame_path is None:
        return _error(f'No frame found for render layer {render_layer_name}')
    output_path = (
        target_path /
        f'{entity.sequence_name}_{entity.shot_name}' /
        f'{entity.sequence_name}_{entity.shot_name}.*.exr'
    )
    
    # Copy the render layer color aov to the target path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    input_frame_paths = list(frame_path.parent.glob(frame_path.name))
    for input_frame_path in tqdm(input_frame_paths, desc='Copying frames'):
        frame_name = input_frame_path.stem.rsplit('.', 1)[-1]
        output_frame_path = output_path.with_name(output_path.name.replace('*', frame_name))
        shutil.copyfile(input_frame_path, output_frame_path)

    # Done
    return 0

def _get_target_path():
    raw_path = os.environ.get('TH_EDIT_PATH')
    if raw_path is None: return None
    return Path(raw_path)

def _input_valid_sequence_name():
    sequence_name = None
    while sequence_name is None:
        sequence_name = input('Enter a sequence code: ')
        if not api.naming.is_valid_sequence_name(sequence_name):
            print(f'Invalid sequence code "{sequence_name}"')
            sequence_name = None
    return sequence_name

def _input_valid_shot_name(sequence_name):
    shot_name = None
    while shot_name is None:
        shot_name = input('Enter a shot code: ')
        if not api.naming.is_valid_shot_name(shot_name):
            print(f'Invalid shot code "{shot_name}"')
            shot_name = None
    return shot_name

def _input_valid_render_layer_name(sequence_name, shot_name):
    render_layer_names = api.config.list_render_layer_names(sequence_name, shot_name)
    render_layer_name = None
    while render_layer_name is None:
        render_layer_name = input('Enter a render layer name: ')
        if render_layer_name not in render_layer_names:
            print(f'Invalid render layer name "{render_layer_name}"')
            render_layer_name = None
    return render_layer_name

def cli():

    # Get the target path
    target_path = _get_target_path()
    if target_path is None:
        return _error('Environment variable TH_EDIT_PATH is not set')

    # Get the parameters
    sequence_name = _input_valid_sequence_name()
    shot_name = _input_valid_shot_name(sequence_name)

    # Get the entity
    entity = ShotEntity(
        sequence_name,
        shot_name,
        'light'
    )

    # Get the render layer name
    render_layer_name = _input_valid_render_layer_name(sequence_name, shot_name)

    # Run the main function
    return main(
        entity,
        render_layer_name,
        target_path
    )

if __name__ == '__main__':
    sys.exit(cli())