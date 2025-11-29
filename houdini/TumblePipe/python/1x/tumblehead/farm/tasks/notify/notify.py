from tempfile import TemporaryDirectory
from io import BytesIO, TextIOWrapper
from dataclasses import dataclass
# PyOpenColorIO not needed anymore - using iconvert instead
from pathlib import Path
import datetime as dt
import discord
import logging
# OpenEXR and Imath not needed anymore - using iconvert instead
import sys
import os

# Add tumblehead python packages path
tumblehead_packages_path = Path(__file__).parent.parent.parent.parent.parent
if tumblehead_packages_path not in sys.path:
    sys.path.append(str(tumblehead_packages_path))

from tumblehead.api import (
    path_str,
    to_wsl_path,
    to_windows_path,
    default_client
)
from tumblehead.util.uri import Uri
from tumblehead.apps import mp4, wsl
from tumblehead.apps.houdini import IConvert
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
    discord_config_path = api.storage.resolve(Uri.parse_unsafe('config:/discord_info.json'))
    discord_config = load_json(discord_config_path)
    if discord_config is None:
        return _error(f'Discord config not found: {path_str(discord_config_path)}')

    # Find the user to notify
    user_id = discord_config['users'].get(user_name.lower())
    if user_id is None:
        return _error(f'User not found in discord config: {user_name}')

    # Find the channel to post to
    channel_id = discord_config['channels'].get(channel_name.lower())
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
        # Parse message into parts if it follows the pattern: "entity - department - version"
        message_parts = [part.strip() for part in message.split(' - ')]

        if len(message_parts) == 3:
            entity, department, version = message_parts
            formatted_message = (
                f'**Submitted by:** {user.mention}\n'
                f'**Entity:** {entity}\n'
                f'**Department:** {department}\n'
                f'**Version:** {version}'
            )
        else:
            formatted_message = (
                f'**Submitted by:** {user.mention}\n'
                f'**Message:** {message}'
            )

        await channel.send(
            content = formatted_message,
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
        # Parse message into parts if it follows the pattern: "entity - department - version"
        message_parts = [part.strip() for part in message.split(' - ')]

        if len(message_parts) == 3:
            entity, department, version = message_parts
            formatted_message = (
                f'**Submitted by:** {user.mention}\n'
                f'**Entity:** {entity}\n'
                f'**Department:** {department}\n'
                f'**Version:** {version}'
            )
        else:
            formatted_message = (
                f'**Submitted by:** {user.mention}\n'
                f'**Message:** {message}'
            )

        await channel.send(
            content = formatted_message
        )
    
    return _post(
        user_name,
        channel_name,
        _post_message
    )

MAX_MP4_SIZE = 10485760  # 10 MB

def _post_mp4(
    user_name: str,
    channel_name: str,
    message: str,
    mp4_path: Path
    ) -> int:

    def _scale_factor(mp4_file_size: int) -> int:
        result = 0
        while mp4_file_size > MAX_MP4_SIZE:
            mp4_file_size /= 2
            result += 1
        return result

    def _fix_size(
        temp_path: Path,
        message: str,
        mp4_path: Path
        ) -> Path:
        mp4_file_size = to_wsl_path(mp4_path).stat().st_size
        size_scale_factor = _scale_factor(mp4_file_size)
        if size_scale_factor == 0: return mp4_path, message
        temp_mp4_path = temp_path / mp4_path.name
        mp4.scale(
            input_mp4_path = to_wsl_path(mp4_path),
            output_mp4_path = to_wsl_path(temp_mp4_path),
            scale_factor = -size_scale_factor
        )
        return temp_mp4_path, (
            f'{message} - '
            f'Scaled down by {2**size_scale_factor}x - '
            f'Original path: {path_str(mp4_path)}'
        )

    # Check if the mp4 exists
    if not to_wsl_path(mp4_path).exists():
        return _error(f'MP4 not found: {path_str(mp4_path)}')
    
    # Fix the size if the mp4 is too large
    root_temp_path = to_wsl_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)

        # Resolve the mp4 size
        notify_mp4_path, notify_message = _fix_size(
            temp_path,
            message,
            mp4_path
        )

        # Check that the mp4 is not too large
        notify_mp4_file_size = to_wsl_path(notify_mp4_path).stat().st_size
        if notify_mp4_file_size > MAX_MP4_SIZE:
            return _error(
                f'MP4 is too large: {path_str(notify_mp4_path)} '
                f'({notify_mp4_file_size} bytes > {MAX_MP4_SIZE} bytes)'
            )

        # Open the mp4 file
        with to_wsl_path(notify_mp4_path).open('rb') as file:
            return _post_file(
                user_name,
                channel_name,
                notify_message,
                file,
                notify_mp4_path.name
            )

def _post_image_bytes(
    user_name: str,
    channel_name: str,
    message: str,
    jpeg_bytes: bytes
    ) -> int:

    # Create byte stream from JPEG bytes (preserves color profile)
    image_stream = BytesIO(jpeg_bytes)
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




def _create_panorama_exr(
    image_paths: list[Path],
    output_path: Path,
    scale: float = 0.5
    ) -> int:
    """Create a panorama EXR from multiple EXR images using oiiotool."""
    
    # Build oiiotool command for horizontal mosaic
    command = ['oiiotool']
    
    # Add all input EXR paths
    for image_path in image_paths:
        command.append(path_str(to_wsl_path(image_path)))
    
    # Create horizontal mosaic (Nx1 where N is number of images)
    command.extend(['--mosaic', f'{len(image_paths)}x1'])
    
    # Apply scaling if requested
    if scale != 1.0:
        scale_percent = int(scale * 100)
        command.extend(['--resize', f'{scale_percent}%'])
    
    # Output path
    command.extend(['-o', path_str(to_wsl_path(output_path))])
    
    # Run oiiotool command
    return wsl.run(command)

def _exr_to_jpeg_bytes(path: Path) -> bytes:
    """Convert EXR to JPEG bytes using iconvert, preserving color profiles."""
    
    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )
    
    # Create temporary JPEG file
    root_temp_path = to_wsl_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        temp_jpeg_path = temp_path / 'temp.jpg'

        # Convert EXR to JPEG using iconvert (same as exr.to_jpeg)
        iconvert = IConvert()
        result = iconvert.run(
            [
                '--iscolorspace', 'acescg',
                '--ociodisplay', 'sRGB - Display', '',
                path_str(to_windows_path(path)),
                path_str(to_windows_path(temp_jpeg_path))
            ],
            env = dict(
                OCIO = path_str(to_windows_path(Path(os.environ['OCIO']))),
            )
        )
        
        # Check if conversion was successful
        if result != 0 or not temp_jpeg_path.exists():
            raise RuntimeError(f'Failed to convert EXR to JPEG: {path}')
        
        # Return the raw JPEG bytes to preserve color profile
        return temp_jpeg_path.read_bytes()

def _post_panorama(
    user_name: str,
    channel_name: str,
    message: str,
    image_paths: list[Path]
    ) -> int:

    # Create temporary directory for panorama EXR
    root_temp_path = to_wsl_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        panorama_exr_path = temp_path / 'panorama.exr'

        # Create panorama EXR using oiiotool
        result = _create_panorama_exr(image_paths, panorama_exr_path, scale=0.5)
        if result != 0:
            return _error(f'Failed to create panorama EXR: {result}')
        
        # Check that panorama was created
        if not to_wsl_path(panorama_exr_path).exists():
            return _error(f'Panorama EXR not created: {panorama_exr_path}')
        
        # Convert panorama EXR to JPEG bytes
        try:
            panorama_jpeg_bytes = _exr_to_jpeg_bytes(panorama_exr_path)
        except Exception as e:
            return _error(f'Failed to convert panorama to JPEG: {e}')

        # Post the panorama to discord
        return _post_image_bytes(
            user_name,
            channel_name,
            message,
            panorama_jpeg_bytes
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
    user_name = config['user_name'].lower()
    channel_name = config['channel_name'].lower()
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