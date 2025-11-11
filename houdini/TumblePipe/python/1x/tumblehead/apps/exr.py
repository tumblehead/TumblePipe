from tempfile import TemporaryDirectory
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import datetime as dt
import shutil
import sys
import os

from tumblehead.api import (
    path_str,
    to_wsl_path,
    to_windows_path
)
from tumblehead.apps import app, houdini
from tumblehead.apps import wsl

def _run(args, **kwargs):
    if sys.platform == 'win32': return wsl.run(args, **kwargs)
    return app.run(args, **kwargs)

def _call(args, **kwargs):
    if sys.platform == 'win32': return wsl.call(args, **kwargs)
    return app.call(args, **kwargs)

@dataclass(frozen=True)
class ImageInfo:
    name: str
    date: dt.datetime
    width: int
    height: int
    channels: list[str]
    aspect_ratio: float
    compression: str
    compression_level: int

def get_image_info(input_path: Path) -> Optional[list[ImageInfo]]:

    # Check if input path is an EXR file
    if input_path.suffix.lower() != '.exr': return None

    # Helpers
    def _split(delim: str, raw: str) -> list[str]:
        return list(filter(
            lambda part: len(part) > 0,
            map(
                lambda part: part.strip(),
                raw.split(delim)
            )
        ))

    # Parse image info
    def _parse_image_info(raw_info: str) -> ImageInfo:

        # Parse channel names
        def _parse_channel_names(channel_parts: list[str]) -> list[str]:
            return [
                line.split('>', 1)[1].split('<', 1)[0]
                for line in channel_parts
                if line.startswith('<channelname>')
                and line.endswith('</channelname>')
            ]
        
        # Parse key value pairs
        def _is_entry(raw: str) -> bool:
            if not raw.startswith('<'): return False
            if not raw.endswith('>'): return False
            return True

        def _is_attrib(raw: str) -> bool:
            if not raw.startswith('<attrib name='): return False
            if not raw.endswith('</attrib>'): return False
            return True
        
        def _parse_attrib(raw: str) -> tuple[str, str]:
            name = raw.split('name="', 1)[1].split('"', 1)[0]
            value = raw.split('>', 1)[1].split('<', 1)[0]
            return name, value
        
        def _parse_property(raw: str) -> tuple[str, str]:
            name = raw.split('<', 1)[1].split('>', 1)[0]
            value = raw.split('>', 1)[1].split('<', 1)[0]
            return name, value
        
        def _parse_key_value(raw: str) -> tuple[str, str]:
            if _is_attrib(raw): return _parse_attrib(raw)
            return _parse_property(raw)

        # Split the raw info
        parts = _split('\n', raw_info)

        # Parse the channel names
        begin_index = parts.index('<channelnames>')
        end_index = parts.index('</channelnames>')
        channel_parts = parts[begin_index:end_index]
        del parts[begin_index:end_index]
        channel_names = _parse_channel_names(channel_parts)

        # Parse the image info
        info = dict()
        for part in parts:
            if not _is_entry(part): continue
            key, value = _parse_key_value(part)
            match key:
                case 'oiio:subimagename':
                    info['name'] = value.lower()
                case 'DateTime':
                    info['date'] = dt.datetime.strptime(
                        value, '%Y:%m:%d %H:%M:%S'
                    )
                case 'width':
                    info['width'] = int(value)
                case 'height':
                    info['height'] = int(value)
                case 'PixelAspectRatio':
                    info['aspect_ratio'] = float(value)
                case 'compression':
                    info['compression'] = value
                case 'openexr:dwaCompressionLevel':
                    info['compression_level'] = int(value)
                case _: pass

        # Done
        return ImageInfo(
            name = info.get('name'),
            date = info.get('date'),
            width = info.get('width'),
            height = info.get('height'),
            channels = channel_names,
            aspect_ratio = info.get('aspect_ratio'),
            compression = info.get('compression'),
            compression_level = info.get('compression_level')
        )

    # Query the image info
    raw_infos = _split('</ImageSpec>', _call([
        'oiiotool', '--info:format=xml', '-v', '-a',
        path_str(to_wsl_path(input_path)),
    ]))

    # Parse the image info
    return [
        _parse_image_info(raw_info)
        for raw_info in raw_infos
    ]

def split_subimages(input_path, output_path):
    
    # Check if input path is an EXR file
    if input_path.suffix.lower() != '.exr': return None

    # Check if output path is a directory
    if not output_path.is_dir(): return None
    
    # Get image infos
    image_infos = get_image_info(input_path)
    if len(image_infos) == 0: return None

    # Split subimages locally
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Split subimages
        temp_output_path = temp_path / '%04d.exr'
        _run([
            'oiiotool',
            path_str(to_wsl_path(input_path)),
            '-sisplit', '-o:all=1',
            path_str(to_wsl_path(temp_output_path))
        ])

        # Process and copy subimages to the output path with channel renaming
        output_image_paths = dict()
        for index, image_info in enumerate(image_infos, 1):
            image_name = image_info.name
            index_name = str(index).zfill(4)
            temp_image_path = temp_output_path.with_name(f'{index_name}.exr')
            output_image_path = output_path / image_name / input_path.name
            output_image_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Check if channels need renaming to match subimage name
            needs_renaming = False
            if image_info.channels:
                # Check if any channels don't start with the image name
                for channel in image_info.channels:
                    if not channel.lower().startswith(image_name.lower()):
                        needs_renaming = True
                        break
            
            if needs_renaming and image_info.channels:
                # Generate new channel names based on subimage name
                new_channel_names = []
                for channel in image_info.channels:
                    # Map common channel suffixes to new names
                    if channel.endswith('.R') or channel == 'R':
                        new_channel_names.append(f'{image_name}.R')
                    elif channel.endswith('.G') or channel == 'G':
                        new_channel_names.append(f'{image_name}.G')
                    elif channel.endswith('.B') or channel == 'B':
                        new_channel_names.append(f'{image_name}.B')
                    elif channel.endswith('.A') or channel == 'A':
                        new_channel_names.append(f'{image_name}.A')
                    else:
                        # For other channels, use the subimage name as prefix
                        channel_suffix = channel.split('.', 1)[-1] if '.' in channel else channel
                        new_channel_names.append(f'{image_name}.{channel_suffix}')
                
                # Create temp renamed file
                temp_renamed_path = temp_path / f'renamed_{index_name}.exr'
                channel_rename_list = ','.join(new_channel_names)
                
                # Rename channels using oiiotool
                _run([
                    'oiiotool',
                    path_str(to_wsl_path(temp_image_path)),
                    '--chnames', channel_rename_list,
                    '-o', path_str(to_wsl_path(temp_renamed_path))
                ])
                
                # Copy the renamed file to output
                shutil.copyfile(temp_renamed_path, output_image_path)
            else:
                # No renaming needed, copy directly
                shutil.copyfile(temp_image_path, output_image_path)
            
            output_image_paths[image_name] = output_image_path

        # Done
        return output_image_paths

def dwab_encode(input_path, output_path):

    # Check if input path is an EXR file
    if input_path.suffix.lower() != '.exr': return None

    # DWAB compress
    return _run([
        'oiiotool', path_str(to_wsl_path(input_path)),
        '--compression', 'dwab:45',
        '-o', path_str(to_wsl_path(output_path))
    ])

def to_jpeg(input_path, output_path):

    # Check if input path is an EXR file
    if input_path.suffix.lower() != '.exr': return None

    # Check if output path is a JPEG file
    if output_path.suffix.lower() != '.jpg': return None

    # Convert to JPEG
    # Note: Using --ociodisplay with empty string for view uses the OCIO config's default view
    iconvert = houdini.IConvert()
    return iconvert.run(
        [
            '--iscolorspace', 'acescg',
            '--ociodisplay', 'sRGB - Display', '',
            path_str(to_windows_path(input_path)),
            path_str(to_windows_path(output_path))
        ],
        env = dict(
            OCIO = path_str(to_windows_path(Path(os.environ['OCIO']))),
        )
    )