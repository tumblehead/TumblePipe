"""Post a farm job's result to a project Discord channel: a message, a
render's first/middle/last frames as one panorama, or a review mp4."""

from tempfile import TemporaryDirectory
from io import BytesIO, TextIOWrapper
from dataclasses import dataclass
from pathlib import Path
import datetime as dt
import discord
import sys
import os

from tumblepipe.api import (
    path_str,
    api
)
from tumblepipe.util.uri import Uri
from tumblepipe.apps import mp4, houdini
from tumblepipe.apps.houdini import IConvert
from tumblepipe.config.discord import (
    is_configured as is_discord_configured,
    get_token as get_discord_token,
    get_user_discord_id,
    get_channel_id as get_discord_channel_id,
    list_channels as list_discord_channels
)
from tumblepipe.config.timeline import BlockRange
from tumblepipe.farm import worker
from tumblepipe.farm.env import ocio_value, print_env
from tumblepipe.farm.frames import frame_file
from tumblepipe.farm.specs import notify as notify_spec
from tumblepipe.farm.worker import error

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

    # A project with no discord setup at all has nothing to post to. That is
    # not a failure of the work this task trails -- say so and leave the farm
    # job green, rather than reddening a render that went fine.
    if not is_discord_configured():
        print(
            'Skipping discord notification: this project has no discord '
            'configuration (set config:/discord token and channels to '
            f'enable it). Would have posted to #{channel_name}.'
        )
        return 0

    # Get discord token
    token = get_discord_token()
    if token is None:
        return error('Discord token not found in config')

    # Find the user to notify (optional - continue without mention if not found)
    user_id = get_user_discord_id(user_name)
    if user_id is None:
        print(f'Warning: User not found in discord config: {user_name} (will post without mention)')

    # Find the channel to post to. The project does have discord configured,
    # so an unknown channel name is a real misconfiguration -- name the ones
    # it does know, so the fix is obvious from the farm log.
    channel_id = get_discord_channel_id(channel_name)
    if channel_id is None:
        known_channels = ', '.join(sorted(list_discord_channels()))
        return error(
            f'Channel not found in discord config: {channel_name} '
            f'(configured channels: {known_channels})'
        )

    # Post mp4 to discord and ping user
    intents = discord.Intents.default()
    client = discord.Client(intents = intents)

    # discord.py logs an exception raised in an event handler and keeps the
    # client running, so a failed post would leave client.run() blocked until
    # the task timed out (and an unknown channel returned early the same
    # way). Close the client whatever happens and re-raise once it is down.
    failures = []

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(channel_id)
            if channel is None:
                raise RuntimeError(
                    f'Discord channel {channel_name} ({channel_id}) is not '
                    'visible to the bot'
                )
            # Fetch user only if we have a user_id
            user = None
            if user_id is not None:
                user = await client.fetch_user(user_id)
            await callback(channel, user, user_name)
        except Exception as failure:
            failures.append(failure)
        finally:
            await client.close()

    client.run(token)
    if failures:
        raise failures[0]
    print('Success')
    return 0

def _post_file(
    user_name: str,
    channel_name: str,
    message: str,
    file: TextIOWrapper,
    name: str
    ) -> int:

    async def _post_file(channel, user, fallback_name):
        # Parse message into parts if it follows the pattern: "entity - department - version"
        message_parts = [part.strip() for part in message.split(' - ')]
        # Use mention if user is available, otherwise use fallback name
        submitted_by = user.mention if user else fallback_name

        if len(message_parts) == 3:
            entity, department, version = message_parts
            formatted_message = (
                f'**Submitted by:** {submitted_by}\n'
                f'**Entity:** {entity}\n'
                f'**Department:** {department}\n'
                f'**Version:** {version}'
            )
        else:
            formatted_message = (
                f'**Submitted by:** {submitted_by}\n'
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

    async def _post_message(channel, user, fallback_name):
        # Parse message into parts if it follows the pattern: "entity - department - version"
        message_parts = [part.strip() for part in message.split(' - ')]
        # Use mention if user is available, otherwise use fallback name
        submitted_by = user.mention if user else fallback_name

        if len(message_parts) == 3:
            entity, department, version = message_parts
            formatted_message = (
                f'**Submitted by:** {submitted_by}\n'
                f'**Entity:** {entity}\n'
                f'**Department:** {department}\n'
                f'**Version:** {version}'
            )
        else:
            formatted_message = (
                f'**Submitted by:** {submitted_by}\n'
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
        mp4_file_size = mp4_path.stat().st_size
        size_scale_factor = _scale_factor(mp4_file_size)
        if size_scale_factor == 0: return mp4_path, message
        temp_mp4_path = temp_path / mp4_path.name
        mp4.scale(
            input_mp4_path = mp4_path,
            output_mp4_path = temp_mp4_path,
            scale_factor = -size_scale_factor
        )
        return temp_mp4_path, (
            f'{message} - '
            f'Scaled down by {2**size_scale_factor}x - '
            f'Original path: {path_str(mp4_path)}'
        )

    # Check if the mp4 exists
    if not mp4_path.exists():
        return error(f'MP4 not found: {path_str(mp4_path)}')
    
    # Fix the size if the mp4 is too large
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
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
        notify_mp4_file_size = notify_mp4_path.stat().st_size
        if notify_mp4_file_size > MAX_MP4_SIZE:
            return error(
                f'MP4 is too large: {path_str(notify_mp4_path)} '
                f'({notify_mp4_file_size} bytes > {MAX_MP4_SIZE} bytes)'
            )

        # Open the mp4 file
        with notify_mp4_path.open('rb') as file:
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
    ) -> None:
    """Create a panorama EXR from multiple EXR images using oiiotool."""
    
    # Build oiiotool command for horizontal mosaic (native paths; the wrapper
    # supplies the hoiiotool executable, so no 'oiiotool' program token here).
    command = []

    # Add all input EXR paths
    for image_path in image_paths:
        command.append(path_str(image_path))

    # Create horizontal mosaic (Nx1 where N is number of images)
    command.extend(['--mosaic', f'{len(image_paths)}x1'])

    # Apply scaling if requested
    if scale != 1.0:
        scale_percent = int(scale * 100)
        command.extend(['--resize', f'{scale_percent}%'])

    # Output path
    command.extend(['-o', path_str(output_path)])

    # Run oiiotool natively
    houdini.OIIOTool().run(command)

def _exr_to_jpeg_bytes(path: Path) -> bytes:
    """Convert EXR to JPEG bytes using iconvert, preserving color profiles."""
    
    # Check that OCIO has been set
    assert os.environ.get('OCIO') is not None, (
        'OCIO environment variable not set. '
        'Please set it to the OCIO config file.'
    )
    
    # Create temporary JPEG file
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        temp_jpeg_path = temp_path / 'temp.jpg'

        # Convert EXR to JPEG using iconvert (same as exr.to_jpeg)
        iconvert = IConvert()
        iconvert.run(
            [
                '--iscolorspace', 'acescg',
                '--ociodisplay', 'sRGB - Display', '',
                path_str(path),
                path_str(temp_jpeg_path)
            ],
            env = dict(OCIO=ocio_value())
        )

        # Check if conversion was successful
        if not temp_jpeg_path.exists():
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
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
        temp_path = Path(temp_dir)
        panorama_exr_path = temp_path / 'panorama.exr'

        # Create panorama EXR using oiiotool
        _create_panorama_exr(image_paths, panorama_exr_path, scale=0.5)

        # Check that panorama was created
        if not panorama_exr_path.exists():
            return error(f'Panorama EXR not created: {panorama_exr_path}')
        
        # Convert panorama EXR to JPEG bytes
        panorama_jpeg_bytes = _exr_to_jpeg_bytes(panorama_exr_path)

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
    if not video_path.exists():
        return error(f'Video not found: {path_str(video_path)}')
    
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
    first_frame_path = frame_file(frame_path, first_frame)
    middle_frame_path = frame_file(frame_path, middle_frame)
    last_frame_path = frame_file(frame_path, last_frame)
    if not first_frame_path.exists():
        return error(f'First frame not found: {path_str(first_frame_path)}')
    if not middle_frame_path.exists():
        return error(f'Middle frame not found: {path_str(middle_frame_path)}')
    if not last_frame_path.exists():
        return error(f'Last frame not found: {path_str(last_frame_path)}')
    
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
    # Print environment variables for debugging
    print_env()

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
        case _: raise ValueError(f'Unknown command: {command}')

def _command(command: dict) -> Command:
    match command['mode']:
        case Mode.Notify:
            return CommandNotify()
        case Mode.Full:
            return CommandFull(video_path = Path(command['video_path']))
        case Mode.Partial:
            return CommandPartial(
                Path(command['frame_path']),
                command['first_frame'],
                command['middle_frame'],
                command['last_frame']
            )
    raise ValueError(f'Unknown notify mode: {command["mode"]}')

def from_config(config: dict, _frame_span: BlockRange) -> int:
    """Post as the job's context.json describes."""
    return main(
        config['user_name'].lower(),
        config['channel_name'].lower(),
        config['message'],
        _command(config['command'])
    )

if __name__ == '__main__':
    sys.exit(worker.run(notify_spec.is_valid_context, from_config))
