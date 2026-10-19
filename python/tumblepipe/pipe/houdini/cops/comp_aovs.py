"""The AOV half of th::build_comp: which rendered AOVs a comp reads, and the
COP networks that read them.

Per channel the comp holds one subnet per included AOV (a File COP reading
the AOV's frames, then a resample for proxy scales), an `alpha` subnet that
splits the RGBA beauty's A, and a `grade` subnet that grades and sums the
LPE AOVs into the channel's RGBA. ``build_comp`` decides what to build and
when; this module builds it.
"""

import hou

from tumblepipe.api import path_str
from tumblepipe.apps import exr
from tumblepipe.pipe.aovs import is_ramp_aov
from tumblepipe.util.nested import contains_in, set_in


class Source:
    """Where the comp reads its frames: the renders, or their proxies
    (cops/comp_proxy.py makes them)."""
    Render = 'render'
    Proxy = 'proxy'


class Resolution:
    Full = 'full'
    Half = 'half'
    Quarter = 'quarter'


def resolution_scale(resolution):
    """How many render pixels one proxy pixel spans at ``resolution``: the
    proxy export's pixel scale, and the comp's resample scale back up."""
    match resolution:
        case Resolution.Full: return 1.0
        case Resolution.Half: return 2.0
        case Resolution.Quarter: return 4.0
        case _: raise ValueError(f'unknown proxy resolution {resolution!r}')


class AOVType:
    Mask = 'mask'
    LPE = 'lpe'
    Util = 'util'
    Mono = 'mono'


# A channel's alpha subnet in the comp, and the suffix of its File COP
# (`<channel>_alpha`): the RGBA beauty read again, its A split off for the
# grade subnet's last input.
ALPHA_SUBNET = 'alpha'


def ensure_node(parent, node_type, name):
    """``parent``'s child ``name``, created as ``node_type`` if missing."""
    node = parent.node(name)
    if node is not None: return node
    return parent.createNode(node_type, name)


def connect(output_node, output_index, input_node, input_index):
    input_node.setInput(input_index, output_node, output_index)


def merge_channel_aovs(sources) -> dict:
    """Merge ``{channel: {aov name: AOV}}`` dicts into ``{channel: {label: AOV}}``.

    ``sources`` is in priority order: the first one to provide a channel's
    AOV label wins.
    """
    aovs = dict()
    for channel_aovs_by_channel in sources:
        for channel_name, channel_aovs in channel_aovs_by_channel.items():
            for aov in channel_aovs.values():
                if contains_in(aovs, (channel_name, aov.label)): continue
                set_in(aovs, (channel_name, aov.label), aov)
    return aovs


def aov_included(aov_name):
    """Whether the comp reads an AOV at all."""
    name = aov_name.lower()
    if name.startswith('beauty_'): return True
    if name.startswith('objid_'): return True
    if name.startswith('holdout_'): return True
    if is_ramp_aov(name): return True
    return name in ('beauty', 'albedo', 'normal', 'depth', 'uv', 'position')


def aov_output_type(aov_name):
    """The File COP 'type1' an AOV is read as."""
    name = aov_name.lower()
    if name.startswith('beauty_'): return 2
    if name.startswith('objid_'): return 0
    if name.startswith('holdout_'): return 0
    if is_ramp_aov(name): return 0
    if name in ('beauty', 'albedo', 'normal', 'depth', 'position'): return 2
    if name == 'uv': return 1
    raise ValueError(f'the comp reads no AOV named {aov_name!r}')


def aov_type(label):
    """The ``AOVType`` of an AOV label.

    LPEs are the beauty and its ``beauty_*`` splits, masks the ``objid_*``
    mattes, mono the holdouts and distance ramps; everything else is a
    utility pass.
    """
    if label == 'beauty' or label.startswith('beauty_'): return AOVType.LPE
    if label.startswith('objid_'): return AOVType.Mask
    if label.startswith('holdout_') or is_ramp_aov(label): return AOVType.Mono
    return AOVType.Util


def aov_types(aovs) -> dict:
    """``{channel: {label: AOVType}}`` for ``{channel: {label: AOV}}``."""
    types = dict()
    for channel_name, channel_aovs in aovs.items():
        for aov in channel_aovs.values():
            set_in(types, (channel_name, aov.label), aov_type(aov.label))
    return types


# (frame path, mtime) -> channel names. A build reads each AOV's header
# several times (render checks, plane name), on a network share.
_CHANNEL_NAMES_CACHE = dict()


def aov_channel_names(aov):
    """Channel names of a rendered AOV, from its first frame's EXR header.

    Every frame of a render shares one layout, so one header is enough. None
    when it can't be read. Cached per file and modification time.
    """
    render_range = aov.get_frame_range()
    if render_range is None: return None
    frame_path = aov.get_aov_frame_path(str(render_range.first_frame).zfill(4))
    if frame_path is None: return None
    try:
        mtime = frame_path.stat().st_mtime_ns
    except OSError:
        return None
    key = (path_str(frame_path), mtime)
    if key not in _CHANNEL_NAMES_CACHE:
        _CHANNEL_NAMES_CACHE[key] = exr.read_channel_names(frame_path) or None
    return _CHANNEL_NAMES_CACHE[key]


def aov_plane_name(aov):
    """The plane name a File COP finds this AOV's channels under."""
    return exr.cop_plane_name(aov_channel_names(aov), aov.label)


def beauty_alpha_problems(beauty_channel_names):
    """What stops the comp taking each channel's alpha from its beauty.

    ``beauty_channel_names`` maps a channel name to its beauty's EXR channel
    names, None when the header could not be read. The comp's alpha is the
    beauty's A, so a beauty without one -- an RGB beauty, rendered before
    beauty went RGBA -- cannot be comped. Empty when nothing stops it.
    """
    problems = []
    for channel_name, channel_names in beauty_channel_names.items():
        if channel_names is None:
            problems.append(
                f'the beauty of channel "{channel_name}" could not be read'
            )
            continue
        if exr.alpha_channel_index(channel_names) is None:
            problems.append(
                f'the beauty of channel "{channel_name}" has no alpha '
                'channel (an RGB beauty; re-render it)'
            )
    return problems


def matte_channel_problems(matte_channel_names):
    """What stops the comp importing each `objid_*` matte as one mask.

    ``matte_channel_names`` maps ``(channel name, AOV label)`` to the matte's
    EXR channel names, None when the header could not be read. A matte is
    one channel (th::mattes); a three-channel puzzle matte, rendered by the
    th::puzzlemattes node th::mattes replaced, cannot be comped. Empty when
    nothing stops it.
    """
    problems = []
    for (channel_name, aov_label), channel_names in matte_channel_names.items():
        if channel_names is None:
            problems.append(
                f'the {aov_label} matte of channel "{channel_name}" could '
                'not be read'
            )
            continue
        if len(channel_names) != 1:
            problems.append(
                f'the {aov_label} matte of channel "{channel_name}" has '
                f'{len(channel_names)} channels, not one (a puzzle matte; '
                're-render it with th::mattes)'
            )
    return problems


def render_problems(aov_context):
    """What in ``aov_context``'s rendered files stops the comp: beauties
    without an alpha and mattes that are not one channel."""
    beauties = {
        channel_name: aov_channel_names(channel_aovs['beauty'])
        for channel_name, channel_aovs in aov_context.items()
        if 'beauty' in channel_aovs
    }
    mattes = {
        (channel_name, aov.label): aov_channel_names(aov)
        for channel_name, channel_aovs in aov_context.items()
        for aov in channel_aovs.values()
        if aov.label.lower().startswith('objid_')
    }
    return beauty_alpha_problems(beauties) + matte_channel_problems(mattes)


# Per AOV type: (subnet color, subnet output type, File COP colorspace).
_AOV_SUBNET_STYLE = {
    AOVType.LPE: ((1, 1, 0), 3, 0),
    AOVType.Mask: ((0, 1, 0), 1, 1),
    AOVType.Util: ((0, 0, 1), 4, 1),
    AOVType.Mono: ((0.5, 0.5, 0.5), 1, 1),
}


def build_aov_subnet(parent_node, channel_name, aov, aov_type):
    """One AOV's subnet: its File COP (``<channel>_<aov>``) into a resample
    into the subnet's single output."""
    color, output_type, colorspace = _AOV_SUBNET_STYLE[aov_type]
    render_range = aov.get_frame_range()

    aov_subnet = ensure_node(parent_node, 'subnet', aov.label)
    aov_subnet.parm('inputs').set(0)
    aov_subnet.parm('outputs').set(1)
    aov_subnet.parm('outputlabel1').set(aov.label)
    aov_subnet.parm('outputtype1').set(output_type)
    aov_subnet_outputs = aov_subnet.node('outputs')
    aov_subnet.setColor(hou.Color(color))
    aov_subnet.node('inputs').setColor(hou.Color((1, 1, 1)))
    aov_subnet_outputs.setColor(hou.Color((0, 0, 0)))

    aov_frame_path = aov.get_aov_frame_path('$F4')
    if aov_frame_path is None:
        raise ValueError(f'no frame path for the {aov.label} AOV')
    aov_import_node = ensure_node(aov_subnet, 'file', f'{channel_name}_{aov.label}')
    aov_import_node.parm('filename').set(path_str(aov_frame_path))
    aov_import_node.parm('videoframestart').deleteAllKeyframes()
    aov_import_node.parm('videoframestart').set(render_range.first_frame)
    hou.setFrame(render_range.first_frame)
    aov_import_node.parm('missingdata').set(1)
    aov_import_node.parm('missingcolora').set(0)
    aov_import_node.parm('colorspace').set(colorspace)
    aov_import_node.parm('aovs').set(1)
    aov_import_node.parm('aov1').set(aov_plane_name(aov))
    aov_import_node.parm('type1').set(aov_output_type(aov.label))

    aov_resample_node = ensure_node(aov_subnet, 'resample', 'resample')
    aov_resample_node.parm('scale').set(1)
    aov_resample_node.parm('filter').set('point')
    connect(aov_import_node, 0, aov_resample_node, 0)
    connect(aov_resample_node, 0, aov_subnet_outputs, 0)
    aov_subnet.layoutChildren()
    return aov_subnet


def configure_alpha_import(aov_subnet, aov_import_node, aov_resample_node, beauty):
    """Point a channel's `alpha` subnet at its RGBA beauty.

    The subnet outputs one mono `alpha` (the grade subnet's last input): it
    reads the beauty as RGBA and splits off A. Run on every build and
    update, so the alpha follows the channel's newest beauty.
    """
    render_range = beauty.get_frame_range()
    aov_frame_path = beauty.get_aov_frame_path('$F4')
    if aov_frame_path is None:
        raise ValueError(f'no frame path for the {beauty.label} AOV')
    aov_import_node.parm('filename').set(path_str(aov_frame_path))
    aov_import_node.parm('videoframestart').deleteAllKeyframes()
    if render_range is not None:
        aov_import_node.parm('videoframestart').set(render_range.first_frame)
    aov_import_node.parm('missingdata').set(1)
    aov_import_node.parm('missingcolora').set(0)
    aov_import_node.parm('colorspace').set(1)
    aov_import_node.parm('aovs').set(1)
    aov_import_node.parm('aov1').set(aov_plane_name(beauty))
    aov_import_node.parm('type1').set(3)  # RGBA
    aov_split_node = ensure_node(aov_subnet, 'channelsplit', 'split')
    connect(aov_resample_node, 0, aov_split_node, 0)
    connect(aov_split_node, 3, aov_subnet.node('outputs'), 0)
    aov_subnet.layoutChildren()


def build_alpha(parent_node, channel_name, beauty):
    """The channel's `alpha` subnet: one mono output, the A of the RGBA
    ``beauty``."""

    # Create aov subnet
    aov_subnet = ensure_node(parent_node, 'subnet', ALPHA_SUBNET)
    aov_subnet.parm('inputs').set(0)
    aov_subnet.parm('outputs').set(1)
    aov_subnet.parm('outputlabel1').set('alpha')
    aov_subnet.parm('outputtype1').set(1)
    aov_subnet.setColor(hou.Color((0.5, 0.5, 0.5)))
    aov_subnet.node('inputs').setColor(hou.Color((1, 1, 1)))
    aov_subnet.node('outputs').setColor(hou.Color((0, 0, 0)))

    # Import + resample, named like every other AOV import so Update
    # finds them
    aov_import_node = ensure_node(
        aov_subnet, 'file', f'{channel_name}_{ALPHA_SUBNET}'
    )
    beauty_render_range = beauty.get_frame_range()
    if beauty_render_range is not None:
        hou.setFrame(beauty_render_range.first_frame)
    aov_resample_node = ensure_node(aov_subnet, 'resample', 'resample')
    aov_resample_node.parm('scale').set(1)
    aov_resample_node.parm('filter').set('point')
    connect(aov_import_node, 0, aov_resample_node, 0)
    configure_alpha_import(
        aov_subnet, aov_import_node, aov_resample_node, beauty
    )
    return aov_subnet


def update_grade_subnet(grade_subnet, channel_name, lpe_names, aov_nodes):
    """(Re)build a channel's grade subnet over its LPE AOVs: a brightness
    and tint per LPE, promoted as spare parms, summed into one RGBA with
    the alpha subnet's A."""

    lpe_subnet_inputs = grade_subnet.node('inputs')
    lpe_subnet_outputs = grade_subnet.node('outputs')

    # Set up inputs - include all LPEs (including beauty) plus alpha (the
    # RGBA beauty's A)
    num_lpes = len(lpe_names)
    grade_subnet.parm('inputs').set(num_lpes + 1)
    for lpe_index, lpe_name in enumerate(lpe_names):
        grade_subnet.parm(f'inputlabel{lpe_index + 1}').set(lpe_name)
        grade_subnet.parm(f'inputtype{lpe_index + 1}').set(3)
    grade_subnet.parm(f'inputlabel{num_lpes + 1}').set('alpha')
    grade_subnet.parm(f'inputtype{num_lpes + 1}').set(1)

    # Set up outputs - one rgba output plus individual graded outputs for each LPE
    grade_subnet.parm('outputs').set(num_lpes + 1)
    grade_subnet.parm('outputlabel1').set('rgba')
    grade_subnet.parm('outputtype1').set(4)
    for lpe_index, lpe_name in enumerate(lpe_names):
        grade_subnet.parm(f'outputlabel{lpe_index + 2}').set(lpe_name)
        grade_subnet.parm(f'outputtype{lpe_index + 2}').set(3)

    # Colors
    grade_subnet.setColor(hou.Color((1, 0, 1)))
    lpe_subnet_inputs.setColor(hou.Color((1, 1, 1)))
    lpe_subnet_outputs.setColor(hou.Color((0, 0, 0)))

    # Prepare spare parameters - preserve existing ones, but exclude LPE parameters that we're about to recreate
    parm_group = hou.ParmTemplateGroup()
    spare_folder_old = hou.FolderParmTemplate(
        'old_parms', 'Old Parameters'
    )

    # Create set of parameter names we're about to add to avoid conflicts
    new_parm_names = set()
    for lpe_name in lpe_names:
        new_parm_names.add(f'{lpe_name}_brightness')
        new_parm_names.add(f'{lpe_name}_tint')

    # Only preserve old parameters that don't conflict with new ones
    for old_parm in grade_subnet.parmTemplateGroup().entries():
        if old_parm.name() not in new_parm_names:
            spare_folder_old.addParmTemplate(old_parm)
    spare_folder_old.hide(True)
    parm_group.append(spare_folder_old)

    # Connect LPE import nodes to grade subnet inputs
    for lpe_index, lpe_name in enumerate(lpe_names):
        lpe_import_node = aov_nodes[AOVType.LPE][lpe_name]
        connect(lpe_import_node, 0, grade_subnet, lpe_index)

    # Create or update grade nodes per LPE
    grade_nodes = dict()
    layer_output_node = None
    for lpe_index, lpe_name in enumerate(lpe_names):

        # Create color correct node
        lpe_grade_node = ensure_node(
            grade_subnet, 'bright',
            f'{channel_name}_{lpe_name}_grade'
        )
        grade_nodes[lpe_name] = lpe_grade_node
        connect(lpe_subnet_inputs, lpe_index, lpe_grade_node, 0)

        # Add spare brightness parameter
        spare_brightness_parm = (
            lpe_grade_node
            .parmTuple('bright')
            .parmTemplate()
        )
        spare_brightness_parm.setName(f'{lpe_name}_brightness')
        spare_brightness_parm.setLabel(f'{lpe_name} brightness')
        parm_group.append(spare_brightness_parm)

        # Add spare color parameter
        spare_color_parm = (
            lpe_grade_node
            .parmTuple('brighttint')
            .parmTemplate()
        )
        spare_color_parm.setName(f'{lpe_name}_tint')
        spare_color_parm.setLabel(f'{lpe_name} tint')
        parm_group.append(spare_color_parm)

        # Merge LPEs
        if layer_output_node is None:
            layer_output_node = (lpe_name, lpe_grade_node)
        else:
            prev_lpe_name, prev_output_node = layer_output_node
            add_node = ensure_node(
                grade_subnet, 'blend',
                f'{channel_name}_{prev_lpe_name}_{lpe_name}_add'
            )
            add_node.parm('mode').set('add')
            connect(prev_output_node, 0, add_node, 0)
            connect(lpe_grade_node, 0, add_node, 1)
            layer_output_node = (lpe_name, add_node)

    # Set the spare parameters on the grade subnet
    grade_subnet.setParmTemplateGroup(parm_group)

    # Hook up the new spare parameters
    for lpe_name in lpe_names:
        grade_node = grade_nodes[lpe_name]

        # Brightness - use relative path from child to parent parameter
        grade_node.parm('bright').setExpression(
            f'ch("../{lpe_name}_brightness")'
        )

        # Tint - use relative path from child to parent parameter
        grade_node.parm('brighttintr').setExpression(
            f'ch("../{lpe_name}_tintr")'
        )
        grade_node.parm('brighttintg').setExpression(
            f'ch("../{lpe_name}_tintg")'
        )
        grade_node.parm('brighttintb').setExpression(
            f'ch("../{lpe_name}_tintb")'
        )

    # Convert grade output to rgba
    grade_rgba_node = ensure_node(grade_subnet, 'rgbtorgba', 'rgba')
    final_output_node = None if layer_output_node is None else layer_output_node[1]
    if final_output_node is None:
        connect(lpe_subnet_inputs, num_lpes, grade_rgba_node, 0)
    else:
        connect(final_output_node, 0, grade_rgba_node, 0)
    connect(lpe_subnet_inputs, num_lpes, grade_rgba_node, 1)
    connect(grade_rgba_node, 0, lpe_subnet_outputs, 0)

    # Connect individual graded LPE outputs
    for lpe_index, lpe_name in enumerate(lpe_names):
        grade_node = grade_nodes[lpe_name]
        connect(grade_node, 0, lpe_subnet_outputs, lpe_index + 1)

    # Layout grade subnet nodes
    grade_subnet.layoutChildren()
