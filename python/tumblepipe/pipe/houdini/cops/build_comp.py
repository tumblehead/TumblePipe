"""th::build_comp: a shot's comp, built from its finished renders.

The comp reads each channel's AOVs (or their proxy frames), grades the LPEs
into the channel's RGBA and lays the channels over each other. Building it
is this module; the AOV networks it builds are cops/comp_aovs.py, the
proxies cops/comp_proxy.py and the farm submission cops/comp_farm.py.
"""

import nodesearch
import hou

from tumblepipe.api import path_str, api
from tumblepipe.config.timeline import get_frame_range, get_fps
from tumblepipe.config.department import list_departments
from tumblepipe.config.channels import list_channels
from tumblepipe.util.nested import contains_in, get_in, set_in
from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini import playbar
from tumblepipe.pipe.houdini.cops import comp_aovs, comp_farm
from tumblepipe.pipe.houdini.cops.comp_aovs import (
    ALPHA_SUBNET,
    AOVType,
    Resolution,
    Source,
    connect,
    ensure_node,
)
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.farm_node import FarmNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode
from tumblepipe.pipe.paths import (
    get_render_context,
    get_proxy_aov,
    comp_input_problems,
)

# The Render Layer token that comps every channel of the shot.
ALL_CHANNELS = 'all'

def split_aov_node_name(name: str, channel_names) -> tuple[str, str] | None:
    """``(channel, AOV label)`` of an AOV import named ``<channel>_<label>``
    for one of ``channel_names``, None for any other node. Labels may hold
    underscores (``objid_hero``, ``beauty_key``), so the name is split after
    the longest channel name it starts with, not at its first underscore."""
    matches = [
        channel_name for channel_name in channel_names
        if name.startswith(f'{channel_name}_')
        and len(name) > len(channel_name) + 1
    ]
    if not matches: return None
    channel_name = max(matches, key = len)
    return channel_name, name[len(channel_name) + 1:]

def _report_unbuildable(shot_uri, render_department_names, problems):
    render_root = path_str(api.storage.resolve(
        Uri.parse('render:/render') / shot_uri.segments
    ))
    departments = ' or '.join(reversed(render_department_names))
    message = (
        f'Cannot build the comp for {shot_uri}: '
        + '; '.join(problems) + '.\n\n'
        'build_comp reads finished renders from\n'
        f'  {render_root}/<{departments}>/<channel>/v####/<aov>/\n'
        'with a context.json (the frame range) in each v#### folder. A '
        'version counts only when every frame in that range exists, and '
        'each channel needs an RGBA beauty AOV. Farm renders write this '
        'layout; see "Rendering without a farm" in the compositing docs.'
    )
    if hou.isUIAvailable():
        hou.ui.displayMessage(message, severity=hou.severityType.Warning)
        return
    raise RuntimeError(message)

def _get_connected_output(node, index):
    connections = node.outputConnections()
    if len(connections) <= index: return None
    return connections[index].outputNode()

class BuildComp(FrameRangeNode, FarmNode, EntityNode):

    # A comp is built for one shot — its channels, frame range and AOV paths
    # all come from it. The parm is spelled 'shot' in saved scenes; a Multi's
    # workfile resolves no shot, so the Shot parm names the member there.
    ENTITY_PARM = 'shot'
    ENTITY_CONTEXTS = ('shots',)

    def list_render_department_names(self):
        # build_comp uses ALL render departments (no renderable filter)
        return [d.name for d in list_departments('render')]

    def list_render_layer_names(self):
        """The Render Layer menu: every channel of the shot, or all of them."""
        return [ALL_CHANNELS] + self.list_channel_names()

    def get_channel_names(self) -> list[str]:
        """The channels the comp renders: the Render Layer parm's channel,
        or every channel of the shot for 'all'."""
        render_layer = self.parm('render_layer').eval()
        if render_layer == ALL_CHANNELS:
            return list_channels(self.require_entity_uri())
        if render_layer not in self.list_channel_names():
            raise self._error(
                f"render layer '{render_layer}' is not a channel of "
                f'{self.require_entity_uri()}'
            )
        return [render_layer]

    def get_render_department_name(self):
        department_name = self.parm('render_department').eval()
        department_names = self.list_render_department_names()
        if department_name not in department_names:
            raise self._error(
                f"render department '{department_name}' is not one of "
                f"{', '.join(department_names) or 'none'}"
            )
        return department_name

    def _render_department_names_up_to_selected(self) -> list[str]:
        """The render departments to read, in order, up to the selected one:
        a later one's AOVs win over an earlier one's."""
        department_names = self.list_render_department_names()
        index = department_names.index(self.get_render_department_name())
        return department_names[:index + 1]

    def get_source_name(self):
        return self.parm('source').eval()

    def get_proxy_resolution(self):
        source_name = self.get_source_name()
        if source_name == Source.Render: return Resolution.Full
        return self.parm('proxy_resolution').eval()

    def get_step_size(self):
        return self.parm('stepsize').eval()

    def get_submit_partial(self):
        return self.parm('submit_partial').eval()

    def get_submit_full(self):
        return self.parm('submit_full').eval()

    def get_partial_frames(self):
        return (
            self.parm('specific_framesx').eval(),
            self.parm('specific_framesy').eval(),
            self.parm('specific_framesz').eval()
        )

    def _update_labels(self):
        self.parm('shot_label').set(self._entity_label())

    def _initialize(self):
        """Refresh the labels. The 'shot' parm keeps its 'from_context'
        default even when the context can't be read: an unresolved
        'from_context' visibly comps nothing."""
        self._update_labels()

    def set_render_department_name(self, render_department_name):
        department_names = self.list_render_department_names()
        if render_department_name not in department_names:
            raise self._error(
                f"render department '{render_department_name}' is not one of "
                f"{', '.join(department_names)}"
            )
        self.parm('render_department').set(render_department_name)

    def set_source_name(self, source_name):
        self.parm('source').set(source_name)

    def set_proxy_resolution(self, resolution_name):
        self.parm('proxy_resolution').set(resolution_name)

    def update(self):
        
        # Nodes
        context = self.native()
        proxy_node = context.node('proxy')
        tops_node = proxy_node.node('tops')

        # Parameters
        source_name = self.get_source_name()

        # Build if not already built
        if not self.parm('built').eval():
            self._build()
            if not self.parm('built').eval(): return

        # Check if source is render
        if source_name == Source.Render: return self.update_imports()
        
        # Set tops target, in order to display the progress
        context.parm('targettopnetwork').set('proxy/tops/merge')

        # Generate proxy frames; the network's last step (comp_proxy.
        # read_proxies) then points the imports at them
        tops_node.parm('dirtybutton').pressButton()
        tops_node.parm('cookbutton').pressButton()

    def execute(self):
        self.update()
    
    def _resolve_aovs(
        self,
        source_name,
        shot_uri,
        resolution_name,
        render_department_names
        ):
        """``({channel: {label: AOV}}, {channel: {label: AOVType}})`` of the
        shot's newest complete renders from ``source_name``.

        Both sources resolve the renders; a Proxy source then reads each
        resolved AOV's ``resolution_name`` proxy instead, so it shows the
        very versions a Render source would.
        """
        aov_context = get_render_context(
            shot_uri,
            purpose = Source.Render
        ).list_latest_complete_aovs()
        # The latest render department wins.
        aovs = comp_aovs.merge_channel_aovs(
            aov_context[name]
            for name in reversed(render_department_names)
            if name in aov_context
        )
        match source_name:
            case Source.Render:
                pass
            case Source.Proxy:
                aovs = {
                    channel_name: {
                        label: get_proxy_aov(aov, resolution_name)
                        for label, aov in channel_aovs.items()
                    }
                    for channel_name, channel_aovs in aovs.items()
                }
            case _:
                raise self._error(f"unknown comp source '{source_name}'")
        return aovs, comp_aovs.aov_types(aovs)

    def resolve_render_aovs(self):
        """``{channel: {label: AOV}}`` of the shot's newest complete renders,
        departments up to the selected one -- what the comp reads, or proxies."""
        aovs, _ = self._resolve_aovs(
            Source.Render,
            self.require_entity_uri(),
            Resolution.Full,
            self._render_department_names_up_to_selected()
        )
        return aovs
    
    def update_imports(self):
        """Point every import at the newest complete AOVs of the Source
        (the proxies once comp_proxy has made them)."""

        # Nodes
        context = self.native()
        dive_node = context.node('dive')

        # Parameters
        shot_uri = self.require_entity_uri()
        source_name = self.get_source_name()
        resolution_name = self.get_proxy_resolution()
        scale = comp_aovs.resolution_scale(resolution_name)
        channel_names = list_channels(shot_uri)
        render_department_names = self._render_department_names_up_to_selected()

        # Get render data
        aov_context, aov_types = self._resolve_aovs(
            source_name,
            shot_uri,
            resolution_name,
            render_department_names
        )

        # Refuse to update onto renders the comp cannot read (an RGB beauty,
        # a puzzle matte)
        problems = comp_aovs.render_problems(aov_context)
        if problems:
            _report_unbuildable(shot_uri, render_department_names, problems)
            return

        # Find all file nodes
        aov_import_nodes = dict()
        file_matcher = nodesearch.NodeType('file', 'Cop')
        for aov_import_node in file_matcher.nodes(dive_node, recursive = True):

            # Get aov context
            names = split_aov_node_name(aov_import_node.name(), channel_names)
            if names is None: continue
            channel_name, aov_name = names

            # Get the resample node
            aov_resample_node = _get_connected_output(aov_import_node, 0)
            if aov_resample_node is None: continue
            if aov_resample_node.type().name() != 'resample': continue

            # Store aov import node
            set_in(aov_import_nodes, (channel_name, aov_name), (aov_import_node, aov_resample_node))
        
        # Update file node paths, planes and resample scales
        for channel_name, aov_nodes in aov_import_nodes.items():
            for aov_name, (aov_import_node, aov_resample_node) in aov_nodes.items():
                if aov_name == ALPHA_SUBNET:
                    # The alpha follows the channel's newest beauty
                    beauty = get_in(aov_context, (channel_name, 'beauty'))
                    if beauty is None: continue
                    comp_aovs.configure_alpha_import(
                        aov_import_node.parent(), aov_import_node,
                        aov_resample_node, beauty
                    )
                    aov_resample_node.parm('scale').set(scale)
                    continue
                aov = get_in(aov_context, (channel_name, aov_name))
                if aov is None: continue
                aov_frame_path = aov.get_aov_frame_path('$F4')
                if aov_frame_path is None: continue
                aov_import_node.parm('filename').set(path_str(aov_frame_path))
                # The new version may name its channels differently (a raw
                # render's `beauty.R` vs the denoise publish's bare `R`)
                aov_import_node.parm('aov1').set(comp_aovs.aov_plane_name(aov))
                aov_render_range = aov.get_frame_range()
                if aov_render_range is not None:
                    aov_import_node.parm('videoframestart').deleteAllKeyframes()
                    aov_import_node.parm('videoframestart').set(aov_render_range.first_frame)
                aov_resample_node.parm('scale').set(scale)

        # Find new aovs that do not have a file node
        for channel_name, aovs in aov_context.items():
            for aov in aovs.values():
                if contains_in(aov_import_nodes, (channel_name, aov.label)): continue

                # Check if aov is included in comp
                if not comp_aovs.aov_included(aov.label): continue

                # Create aov import node
                channel_subnet = dive_node.node(channel_name)
                if channel_subnet is None: continue
                aov_subnet = comp_aovs.build_aov_subnet(
                    channel_subnet, channel_name, aov,
                    get_in(aov_types, (channel_name, aov.label)),
                )
                
                # Store import node
                if channel_name not in aov_import_nodes: aov_import_nodes[channel_name] = dict()
                aov_import_nodes[channel_name][aov.label] = (aov_subnet.node('outputs'), aov_subnet.node('inputs'))

        # Update grade subnets for render layers with new LPEs
        for channel_name, aovs in aov_context.items():
            channel_subnet = dive_node.node(channel_name)
            if channel_subnet is None: continue
            
            grade_subnet = channel_subnet.node('grade')
            if grade_subnet is None: continue
            
            # Check if we have new LPEs for this render layer
            layer_aov_types = get_in(aov_types, (channel_name,))
            if layer_aov_types is None: continue
            
            # Collect all LPE names (including beauty and new LPEs)
            aov_nodes_by_type = {
                AOVType.LPE: dict(),
                AOVType.Mask: dict(), 
                AOVType.Util: dict(),
                AOVType.Mono: dict()
            }
            
            # Group aovs by type
            for aov in aovs.values():
                if not comp_aovs.aov_included(aov.label): continue
                aov_type = layer_aov_types.get(aov.label)
                if aov_type is None: continue
                
                # Find the aov subnet node
                aov_subnet = channel_subnet.node(aov.label)
                if aov_subnet is None: continue
                
                aov_nodes_by_type[aov_type][aov.label] = aov_subnet
            
            # Get all LPE names including beauty
            lpe_names = list(aov_nodes_by_type[AOVType.LPE].keys())
            if lpe_names:
                # Update the grade subnet with current LPEs
                comp_aovs.update_grade_subnet(
                    grade_subnet,
                    channel_name,
                    lpe_names,
                    aov_nodes_by_type
                )

                # Connect alpha to grade subnet; every built channel has one
                alpha_node = channel_subnet.node(ALPHA_SUBNET)
                if alpha_node is None:
                    raise RuntimeError(
                        f'{channel_subnet.path()} has no {ALPHA_SUBNET} '
                        'subnet: the comp was built before the alpha came '
                        'from the RGBA beauty. Rebuild it.'
                    )
                connect(alpha_node, 0, grade_subnet, len(lpe_names))

    def _build(self):

        # Parameters
        shot_uri = self.require_entity_uri()
        channel_names = list_channels(shot_uri)
        frame_range = get_frame_range(shot_uri)
        if frame_range is None:
            raise self._error(f'{shot_uri} has no frame range authored')
        render_department_names = self._render_department_names_up_to_selected()

        # Nodes
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')

        # Update dive node outputs
        dive_node.parm('outputs').set(len(channel_names) + 1)
        for index, channel_name in enumerate(channel_names):
            dive_node.parm(f'outputlabel{index + 2}').set(channel_name)
            dive_node.parm(f'outputtype{index + 2}').set(4)

        # Get render data
        aov_context, aov_types = self._resolve_aovs(
            Source.Render,
            shot_uri,
            Resolution.Full,
            render_department_names
        )

        # Refuse to build from renders that cannot make a comp. Building empty
        # used to set the built flag anyway, and Update never rebuilds, so the
        # node stayed empty with no word of why.
        problems = comp_input_problems(
            {
                channel_name: [aov.label for aov in aovs.values()]
                for channel_name, aovs in aov_context.items()
            },
            channel_names,
        ) + comp_aovs.render_problems(aov_context)
        if problems:
            _report_unbuildable(shot_uri, render_department_names, problems)
            return

        # Set FPS before the range so the range can't be shifted by a later
        # fps change (see playbar.set_fps; consistent with import_shot).
        fps = get_fps(shot_uri)
        if fps is not None:
            playbar.set_fps(fps)
        playbar.set_frame_range(frame_range)

        # Build render layer comps
        layer_nodes = dict()
        for channel_name in channel_names:

            # Get render layer aovs
            aovs = get_in(aov_context, (channel_name,))
            if aovs is None: continue

            # All names
            aov_names = list(aovs.keys())
            assert 'beauty' in aov_names, (
                'Missing beauty aov in '
                f'{channel_name}'
            )

            # Prepare render layer subnet
            channel_subnet = ensure_node(dive_node, 'subnet', channel_name)
            channel_subnet.parm('inputs').set(0)
            channel_subnet.parm('outputs').set(1)
            channel_subnet.parm('outputlabel1').set('rgba')
            channel_subnet.parm('outputtype1').set(4)
            channel_subnet_inputs = channel_subnet.node('inputs')
            channel_subnet_outputs = channel_subnet.node('outputs')
            channel_subnet.setColor(hou.Color((1, 0, 1)))
            channel_subnet_inputs.setColor(hou.Color((1, 1, 1)))
            channel_subnet_outputs.setColor(hou.Color((0, 0, 0)))

            # Import layer aovs
            aov_nodes = {
                AOVType.LPE: dict(),
                AOVType.Mask: dict(),
                AOVType.Util: dict(),
                AOVType.Mono: dict()
            }
            for aov in aovs.values():

                # Check if aov is included in comp
                if not comp_aovs.aov_included(aov.label): continue

                # Build aov import node
                aov_type = get_in(aov_types, (channel_name, aov.label))
                aov_nodes[aov_type][aov.label] = comp_aovs.build_aov_subnet(
                    channel_subnet, channel_name, aov, aov_type
                )

            # The channel's alpha: the RGBA beauty's A
            channel_alpha_node = comp_aovs.build_alpha(
                channel_subnet, channel_name, aovs['beauty']
            )

            # Prepare grade subnet
            assert 'beauty' in aov_nodes[AOVType.LPE], (
                'Missing beauty aov in '
                f'{channel_name}'
            )
            
            # Include all LPE names including beauty
            lpe_names = list(aov_nodes[AOVType.LPE].keys())
            grade_subnet = ensure_node(channel_subnet, 'subnet', 'grade')
            
            comp_aovs.update_grade_subnet(
                grade_subnet,
                channel_name,
                lpe_names,
                aov_nodes
            )
            
            # Create the render layer subnet output node
            connect(channel_alpha_node, 0, grade_subnet, len(lpe_names))
            connect(grade_subnet, 0, channel_subnet_outputs, 0)
            
            # Layout render layer subnet nodes
            channel_subnet.layoutChildren()
            
            # Store layer output node
            layer_nodes[channel_name] = (channel_subnet, aov_nodes)
        
        # Merge render layer comps
        comp_output_node = None
        for index, channel_name in enumerate(channel_names):

            # Get render layer nodes
            if channel_name not in layer_nodes: continue
            layer_subnet, _ = layer_nodes[channel_name]

            # Connect render layer to output
            connect(layer_subnet, 0, output_node, index + 1)

            # Merge render layers
            if comp_output_node is None:
                comp_output_node = (channel_name, layer_subnet)
            else:

                # Merge render layers
                prev_channel_name, prev_output_node = comp_output_node
                over_node = ensure_node(
                    dive_node,
                    'blend',
                    f'{prev_channel_name}_{channel_name}_over'
                )
                over_node.parm('mode').set('over')
                connect(prev_output_node, 0, over_node, 0)
                connect(layer_subnet, 0, over_node, 1)
                comp_output_node = (channel_name, over_node)
        
        # Set output node
        if comp_output_node is not None:
            _, final_output_node = comp_output_node
            comp_rgb_node = ensure_node(dive_node, 'rgbatorgb', 'rgb')
            connect(final_output_node, 0, comp_rgb_node, 0)
            connect(comp_rgb_node, 0, output_node, 0)
        
        # Layout nodes
        dive_node.layoutChildren()

        # Set built flag
        self.parm('built').set(1)
    
    def stop(self):

        # Nodes
        context = self.native()

        # Get task node to cancel
        task_name = context.parm('targettopnetwork').eval().split('/', 1)[0]
        task_node = context.node(task_name)
        tops_node = task_node.node('tops')

        # Generate proxy frames
        tops_node.parm('cancelbutton').pressButton()
        tops_node.parm('dirtybutton').pressButton()
    
    def preview(self):
        
        # Nodes
        context = self.native()
        preview_node = context.node('preview')

        # Trigger the preview
        preview_node.parm('renderpreview').pressButton()
    
def create(scene, name):
    return ns.create_node(scene, name, BuildComp, 'build_comp', 'Cop')

def on_created(raw_node):
    node = BuildComp(raw_node)
    node._initialize()

def update(kwargs):
    raw_node = kwargs['node']
    node = BuildComp(raw_node)
    node.update()

def stop(kwargs):
    raw_node = kwargs['node']
    node = BuildComp(raw_node)
    node.stop()

def preview(kwargs):
    raw_node = kwargs['node']
    node = BuildComp(raw_node)
    node.preview()


def submit(kwargs):
    from tumblepipe.pipe.houdini.ui import farm_submit
    node = BuildComp(kwargs['node'])
    farm_submit.submit(
        'the composite', 'Submit Composite', lambda: comp_farm.submit(node),
    )