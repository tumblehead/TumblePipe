from tempfile import TemporaryDirectory
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import datetime as dt
import shutil
import os

from tumblepipe.api import (
    path_str,
    local_path,
    to_windows_path
)
from tumblepipe.apps import houdini

# Native OpenImageIO oiiotool (Houdini's hoiiotool), lazily cached. The farm runs
# in native Windows python now, so oiiotool runs directly — no WSL bridge — and
# its path args are native (local_path), not /mnt forms.
_OIIOTOOL = None
def _oiiotool():
    global _OIIOTOOL
    if _OIIOTOOL is None:
        _OIIOTOOL = houdini.OIIOTool()
    return _OIIOTOOL

def _run(command, **kwargs):
    # command[0] is the 'oiiotool' program name; the native wrapper supplies the
    # real hoiiotool executable, so drop it.
    return _oiiotool().run(command[1:], **kwargs)

def _call(command, **kwargs):
    return _oiiotool().call(command[1:], **kwargs)

# ACEScg stamp for every EXR the pipeline publishes. Two attributes, because
# consumers disagree on which to trust: RV/Nuke key off the EXR-spec
# `chromaticities` (absent means Rec.709 primaries BY SPEC, so an unstamped
# ACEScg render displays as Rec709), while OIIO-based tools read
# `oiio:ColorSpace`. husk stamps only the latter (`lin_ap1`), never
# chromaticities, and oiiotool channel ops (--ch/--chappend) reset it to
# "Raw" -- so the publish steps stamp both explicitly.
# Values are ACEScg's AP1 primaries (R,G,B xy) + D60 white point.
ACESCG_ATTRIB_ARGS = [
    '--attrib:type=float[8]', 'chromaticities',
    '0.713,0.293,0.165,0.830,0.128,0.044,0.32168,0.33767',
    '--attrib', 'oiio:ColorSpace', 'ACEScg',
]

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
        path_str(local_path(input_path)),
    ]))

    # Parse the image info
    return [
        _parse_image_info(raw_info)
        for raw_info in raw_infos
    ]

def _rename_channel(image_name: str, channel: str) -> str:
    # Map common channel suffixes to new names
    for suffix in ('R', 'G', 'B', 'A'):
        if channel == suffix or channel.endswith(f'.{suffix}'):
            return f'{image_name}.{suffix}'
    # For other channels, use the subimage name as prefix
    channel_suffix = channel.split('.', 1)[-1] if '.' in channel else channel
    return f'{image_name}.{channel_suffix}'

def _stage_subimage(temp_path, temp_image_path, image_info, index_name):
    # Returns the file to copy to the output location; channels are renamed to
    # match the subimage name first when any of them don't already carry it.
    # CONTRACT for consumers of the split per-AOV files: channels are named
    # `<aov>.R/G/B/...`, never plain R,G,B — select channels positionally
    # (oiiotool `--ch 0,1,2`), not by name. oiiotool's by-name `--ch` silently
    # zero-fills channels it can't find, producing solid-black output (this
    # blacked out every denoise-off slapcomp until v1.23.4).
    image_name = image_info.name
    channels = image_info.channels or []
    needs_renaming = any(
        not channel.lower().startswith(image_name.lower())
        for channel in channels
    )
    if not needs_renaming: return temp_image_path

    temp_renamed_path = temp_path / f'renamed_{index_name}.exr'
    _run([
        'oiiotool',
        path_str(local_path(temp_image_path)),
        '--chnames', ','.join(
            _rename_channel(image_name, channel)
            for channel in channels
        ),
        '-o', path_str(local_path(temp_renamed_path))
    ])
    return temp_renamed_path

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

        # Split subimages. `-a` applies the ACEScg stamp to every subimage
        # while the file is still one multi-part image, so each split AOV
        # carries it (the conditional rename below preserves attributes).
        temp_output_path = temp_path / '%04d.exr'
        _run([
            'oiiotool',
            path_str(local_path(input_path)),
            '-a', *ACESCG_ATTRIB_ARGS,
            '-sisplit', '-o:all=1',
            path_str(local_path(temp_output_path))
        ])

        # Copy each subimage to the output path, renaming channels when needed
        output_image_paths = dict()
        for index, image_info in enumerate(image_infos, 1):
            image_name = image_info.name
            index_name = str(index).zfill(4)
            temp_image_path = temp_output_path.with_name(f'{index_name}.exr')
            output_image_path = output_path / image_name / input_path.name
            output_image_path.parent.mkdir(parents=True, exist_ok=True)
            source_path = _stage_subimage(
                temp_path, temp_image_path, image_info, index_name
            )
            shutil.copyfile(source_path, output_image_path)
            output_image_paths[image_name] = output_image_path

        # Done
        return output_image_paths

def get_channel_counts(input_paths: dict[str, Path]) -> dict[str, int]:
    """Channel count of each per-AOV EXR, keyed by AOV name.

    Takes CONCRETE frame paths, not the `*`/`####` frame patterns the task
    configs carry -- oiiotool reads a file, not a glob. Split per-AOV files hold
    exactly one subimage, and the layout is constant for a whole render, so
    callers probe one frame ONCE per task and reuse the result across frames
    rather than paying an oiiotool probe per AOV per frame.
    """
    result = dict()
    for aov_name, aov_path in input_paths.items():
        image_infos = get_image_info(local_path(aov_path))
        if not image_infos: continue
        channels = image_infos[0].channels or []
        if len(channels) == 0: continue
        result[aov_name] = len(channels)
    return result

def _channel_selector(channel_count: int) -> Optional[str]:
    """oiiotool `--ch` selection normalizing an AOV to 3 channels.

    The COPs path this replaces fed every AOV through `rop_image` as RGB, so a
    1-channel AOV (`alpha.Z`) was broadcast to 3 and each channel then denoised
    independently. Reproduce that shape here, before the denoise, so the result
    matches: doing it afterwards would denoise alpha as a single channel and
    give different values.
    """
    if channel_count >= 3: return '0,1,2'
    if channel_count == 1: return '0,0,0'
    return None

def combine_aovs(
    input_paths: dict[str, Path],
    channel_counts: dict[str, int],
    output_path: Path
    ) -> Optional[list[str]]:
    """Merge per-AOV EXRs into the single multi-plane EXR idenoise needs.

    idenoise resolves its `--normal`/`--albedo` guides as planes *inside* one
    file, but the render publishes one EXR per AOV, so they have to be merged
    first. Each AOV is normalized to 3 channels named `<aov>.R/G/B`, which both
    reproduces the old COPs shape and makes every plane's name unambiguous --
    raw render channels are inconsistent (`normal.x/y/z`, lowercase
    `albedo.r/g/b`) and a plane idenoise can't resolve degrades silently.

    Returns the AOV names actually included (an AOV whose channel count can't be
    normalized is warned about and dropped), or None if oiiotool failed.
    """
    included = []
    command = ['oiiotool']
    for aov_name, aov_path in input_paths.items():
        channel_count = channel_counts.get(aov_name)
        if channel_count is None:
            print(f'WARNING: Skipping AOV {aov_name} - could not read channels')
            continue
        selector = _channel_selector(channel_count)
        if selector is None:
            print(
                f'WARNING: Skipping AOV {aov_name} - '
                f'cannot normalize {channel_count} channels to RGB'
            )
            continue
        command += [
            path_str(local_path(aov_path)),
            '--ch', selector,
            '--chnames', f'{aov_name}.R,{aov_name}.G,{aov_name}.B'
        ]
        if len(included) > 0:
            command += ['--chappend']
        included.append(aov_name)

    if len(included) == 0: return None
    command += ['-o', path_str(local_path(output_path))]
    if _run(command) != 0: return None
    if not local_path(output_path).exists(): return None
    return included

def extract_aov(input_path: Path, aov_name: str, output_path: Path) -> int:
    """Pull one denoised plane out of an idenoise result.

    idenoise keeps its input's channel names, so the plane comes back as
    `<aov>.R/G/B`; the published per-AOV files have always carried plain
    `R,G,B` (the subimage name is what identifies the AOV). Rename to match --
    consumers index positionally, so the names are cosmetic, but the shape is
    not something to change by accident.
    """
    return _run([
        'oiiotool',
        path_str(local_path(input_path)),
        '--subimage', aov_name,
        '--chnames', 'R,G,B',
        '-o', path_str(local_path(output_path))
    ])

def dwab_encode(input_path, output_path):

    # Check if input path is an EXR file
    if input_path.suffix.lower() != '.exr': return None

    # DWAB compress. This is the publish step of the denoise chain, whose
    # channel shuffles dropped the colorspace metadata -- re-stamp it here.
    return _run([
        'oiiotool', path_str(local_path(input_path)),
        *ACESCG_ATTRIB_ARGS,
        '--compression', 'dwab:45',
        '-o', path_str(local_path(output_path))
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