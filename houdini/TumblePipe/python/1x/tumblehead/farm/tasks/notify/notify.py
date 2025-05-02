from io import BytesIO, TextIOWrapper
from dataclasses import dataclass
import PyOpenColorIO as OCIO
from pathlib import Path
from PIL import Image
import datetime as dt
import numpy as np
import discord
import logging
import OpenEXR
import Imath
import sys

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    path_str,
    to_wsl_path,
    default_client
)
from tumblehead.util.io import load_json

api = default_client()

def _error(msg):
    logging.error(msg)
    return 1

def _get_frame_path(framestack_path, frame_index):
    frame_name = str(frame_index).zfill(4)
    return (
        framestack_path.parent /
        framestack_path.name.replace('####', frame_name)
    )

class Mode:
    Notify = 'notify'
    Full = 'full'
    Partial = 'partial'

@dataclass
class Command: pass

@dataclass
class CommandNotify(Command): pass

@dataclass
class CommandFull(Command):
    video_path: Path

@dataclass
class CommandPartial(Command):
    frame_path: Path
    first_frame: int
    middle_frame: int
    last_frame: int

def _post(
    user_name: str,
    channel_name: str,
    callback
    ) -> int:

    # Load discord config
    discord_config_path = api.storage.resolve('config:/discord_info.json')
    discord_config = load_json(discord_config_path)
    if discord_config is None:
        return _error(f'Discord config not found: {path_str(discord_config_path)}')

    # Find the user to notify
    user_id = discord_config['users'].get(user_name)
    if user_id is None:
        return _error(f'User not found in discord config: {user_name}')

    # Find the channel to post to
    channel_id = discord_config['channels'].get(channel_name)
    if channel_id is None:
        return _error(f'Channel not found in discord config: {channel_name}')

    # Post mp4 to discord and ping user
    intents = discord.Intents.default()
    client = discord.Client(intents = intents)

    @client.event
    async def on_ready():
        channel = client.get_channel(channel_id)
        if channel is None: return
        user = await client.fetch_user(user_id)
        if user is None: return
        await callback(channel, user)
        await client.close()
    
    try:

        # Run the client
        client.run(discord_config['token'])

        # Done
        print('Success')
        return 0
    
    except Exception as e:
        return _error(f'Failed to post mp4 to discord: {e}')

def _post_file(
    user_name: str,
    channel_name: str,
    message: str,
    file: TextIOWrapper,
    name: str
    ) -> int:

    async def _post_file(channel, user):
        await channel.send(
            content = (
                f'Submitted by '
                f'{user.mention} - '
                f'{message}'
            ),
            file = discord.File(file, name)
        )

    return _post(
        user_name,
        channel_name,
        _post_file
    )

def _post_message(
    user_name: str,
    channel_name: str,
    message: str
    ) -> int:

    async def _post_message(channel, user):
        await channel.send(
            content = (
                f'Submitted by '
                f'{user.mention} - '
                f'{message}'
            )
        )
    
    return _post(
        user_name,
        channel_name,
        _post_message
    )

def _post_mp4(
    user_name: str,
    channel_name: str,
    message: str,
    mp4_path: Path
    ) -> int:

    # Check if the mp4 exists
    if not to_wsl_path(mp4_path).exists():
        return _error(f'MP4 not found: {path_str(mp4_path)}')
    
    # Check if the mp4 is too large
    if mp4_path.stat().st_size > 10485760:
        return _post_message(
            user_name,
            channel_name,
            f'{message} - {path_str(mp4_path)}'
        )

    # Open the mp4 file
    with to_wsl_path(mp4_path).open('rb') as file:
        return _post_file(
            user_name,
            channel_name,
            message,
            file,
            mp4_path.name
        )

def _post_image(
    user_name: str,
    channel_name: str,
    message: str,
    image: Image
    ) -> int:

    # Load image data into byte stream
    image_stream = BytesIO()
    image.save(image_stream, format = 'JPEG')
    image_stream.seek(0)

    # Post the image to discord
    timestamp = dt.datetime.now().strftime('%Y%m%d%H%M%S')
    image_name = (
        f'{user_name}_'
        f'{channel_name}_'
        f'{timestamp}'
        '.jpg'
    )
    return _post_file(
        user_name,
        channel_name,
        message,
        image_stream,
        image_name
    )

def _stitch_images(
    images: list[Image],
    scale: float = 1.0
    ) -> Image:

    # Compute dimensions
    width = 0
    height = 0
    for image in images:
        width += image.width
        height = max(height, image.height)

    # Create the panorama image
    panorama = Image.new('RGB', (width, height))
    position = 0
    for image in images:
        panorama.paste(image, (position, 0))
        position += image.width

    # Scale the panorama
    if scale != 1.0:
        panorama = panorama.resize((
            int(panorama.width * scale),
            int(panorama.height * scale))
        )

    # Return the result image
    return panorama

def _view_transform(data: np.ndarray) -> np.ndarray:

    def _display_processor(config, colorspace, display, view):

        def _display_view_transform(colorspace, display, view):
            transform = OCIO.DisplayViewTransform()
            transform.setSrc(colorspace)
            transform.setDisplay(display)
            transform.setView(view)
            return transform

        def _stack(*transforms):
            group = OCIO.GroupTransform()
            for transform in transforms:
                group.appendTransform(transform)
            return group
        
        return config.getProcessor(_stack(
            _display_view_transform(colorspace, display, view)
        )).getDefaultCPUProcessor()

    config = OCIO.Config.CreateFromEnv()
    processor = _display_processor(
        config,
        'ACEScg',
        'sRGB - Display',
        'ACES 1.0 - SDR Video'
    )
    processor.applyRGB(data)
    return data

def _open_exr(path: Path) -> Image:

    def _map_channel_name(channel_name):
        if '.' not in channel_name: return channel_name
        return channel_name.rsplit('.', 1)[1].upper()

    # Open the EXR file
    exr = OpenEXR.InputFile(path_str(to_wsl_path(path)))
    header = exr.header()

    # Get the image size
    data_window = header['dataWindow']
    size = (
        data_window.max.x - data_window.min.x + 1,
        data_window.max.y - data_window.min.y + 1
    )

    # Get the image data
    mapped_channels = {
        _map_channel_name(channel_name): channel_name
        for channel_name in header['channels']
    }
    channels = [
        mapped_channels[channel]
        for channel in list('RGBA')
        if channel in mapped_channels
    ]
    channel_descriptions = exr.channels(
        channels, Imath.PixelType(Imath.PixelType.FLOAT)
    )
    channel_data = (
        np.dstack([
            np.frombuffer(c, dtype = np.float32)
            for c in channel_descriptions
        ])
        .reshape((size[1], size[0], len(channels)))
    )

    # Clamp the data to [0, 1]
    channel_data = _view_transform(channel_data)
    channel_data = np.clip(channel_data, 0.0, 1.0)

    # Create the image
    image = Image.fromarray(
        (channel_data * 255).astype(np.uint8),
        'RGBA'[:channel_data.shape[2]]
    )
    return image

def _post_panorama(
    user_name: str,
    channel_name: str,
    message: str,
    image_paths: list[Path]
    ) -> int:

    # Stich and scale the images
    panorama = _stitch_images([
        _open_exr(image_path)
        for image_path in image_paths
    ], scale = 0.5)

    # Post the panorama to discord
    return _post_image(
        user_name,
        channel_name,
        message,
        panorama
    )

def _notify(
    user_name: str,
    channel_name: str,
    message: str
    ) -> int:

    # Post message to discord
    return _post_message(
        user_name,
        channel_name,
        message
    )

def _full(
    user_name: str,
    channel_name: str,
    message: str,
    video_path: Path
    ) -> int:

    # Check the video path
    if not to_wsl_path(video_path).exists():
        return _error(f'Video not found: {path_str(video_path)}')
    
    # Post video to discord
    return _post_mp4(
        user_name,
        channel_name,
        message,
        video_path
    )

def _partial(
    user_name: str,
    channel_name: str,
    message: str,
    frame_path: Path,
    first_frame: int,
    middle_frame: int,
    last_frame: int
    ) -> int:
    
    # Check the frame numbers
    first_frame_path = _get_frame_path(frame_path, first_frame)
    middle_frame_path = _get_frame_path(frame_path, middle_frame)
    last_frame_path = _get_frame_path(frame_path, last_frame)
    if not to_wsl_path(first_frame_path).exists():
        return _error(f'First frame not found: {path_str(first_frame_path)}')
    if not to_wsl_path(middle_frame_path).exists():
        return _error(f'Middle frame not found: {path_str(middle_frame_path)}')
    if not to_wsl_path(last_frame_path).exists():
        return _error(f'Last frame not found: {path_str(last_frame_path)}')
    
    # Post panorama to discord
    image_paths = [first_frame_path, middle_frame_path, last_frame_path]
    return _post_panorama(
        user_name,
        channel_name,
        message,
        image_paths
    )

def main(
    user_name: str,
    channel_name: str,
    message: str,
    command: Command
    ) -> int:
    match command:
        case CommandNotify():
            return _notify(
                user_name,
                channel_name,
                message
            )
        case CommandFull(video_path):
            return _full(
                user_name,
                channel_name,
                message,
                video_path
            )
        case CommandPartial(
            frame_path,
            first_frame,
            middle_frame,
            last_frame
            ):
            return _partial(
                user_name,
                channel_name,
                message,
                frame_path,
                first_frame,
                middle_frame,
                last_frame
            )
        case _: assert False, f'Unknown command: {command}'

"""
config = {
    'user_name': 'user',
    'channel_name': 'channel',
    'message': 'message',
    'command': {
        'mode': 'notify'
    } | {
        'mode': 'partial',
        'frame_path': 'path/to/frame.####.exr'
        'first_frame': 1,
        'middle_frame': 50,
        'last_frame': 100
    } | {
        'mode': 'full',
        'video_path': 'path/to/mp4.mp4'
    }
}
"""

def _is_valid_config(config):

    def _is_valid_command(command):
        if not isinstance(command, dict): return False
        if 'mode' not in command: return False
        match command['mode']:
            case 'notify': return True
            case 'partial':
                if 'frame_path' not in command: return False
                if not isinstance(command['frame_path'], str): return False
                if 'first_frame' not in command: return False
                if not isinstance(command['first_frame'], int): return False
                if 'middle_frame' not in command: return False
                if not isinstance(command['middle_frame'], int): return False
                if 'last_frame' not in command: return False
                if not isinstance(command['last_frame'], int): return False
            case 'full':
                if 'video_path' not in command: return False
                if not isinstance(command['video_path'], str): return False
            case _: return False
        return True
    
    if not isinstance(config, dict): return False
    if 'user_name' not in config: return False
    if not isinstance(config['user_name'], str): return False
    if 'channel_name' not in config: return False
    if not isinstance(config['channel_name'], str): return False
    if 'message' not in config: return False
    if not isinstance(config['message'], str): return False
    if 'command' not in config: return False
    if not _is_valid_command(config['command']): return False
    return True

def cli():

    # Define CLI
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
    if not _is_valid_config(config):
        return _error(f'Invalid config file: {config_path}')

    # Parse command
    def _parse_command(command):
        
        # Parse mode
        match command['mode']:
            case Mode.Notify:
                return CommandNotify()

            case Mode.Full:

                # Return command
                return CommandFull(
                    video_path = Path(command['video_path'])
                )
            
            case Mode.Partial:

                # Get the frame path
                frame_path = Path(command['frame_path'])
                
                # Check frame numbers
                first_frame = command['first_frame']
                middle_frame = command['middle_frame']
                last_frame = command['last_frame']
                if first_frame < 0:
                    return _error(f'Invalid first frame: {first_frame}')
                if middle_frame < 0 and middle_frame < first_frame:
                    return _error(f'Invalid middle frame: {middle_frame}')
                if last_frame < 0 and last_frame < middle_frame:
                    return _error(f'Invalid last frame: {last_frame}')

                # Return command
                return CommandPartial(
                    frame_path,
                    first_frame,
                    middle_frame,
                    last_frame
                )
            
    # Parameters
    user_name = config['user_name']
    channel_name = config['channel_name']
    message = config['message']
    command = _parse_command(config['command'])
    
    # Run main
    return main(
        user_name,
        channel_name,
        message,
        command
    )

if __name__ == '__main__':
    logging.basicConfig(
        level = logging.DEBUG,
        format = '%(message)s',
        stream = sys.stdout
    )
    sys.exit(cli())