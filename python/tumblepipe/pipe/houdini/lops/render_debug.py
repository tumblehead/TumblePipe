
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini import render_stage
from tumblepipe.pipe.houdini.entity_node import EntityNode

class RenderDebug(EntityNode):

    # The render stage of one shot. The parm is spelled 'shot' in saved
    # scenes; a Multi's workfile resolves no shot.
    ENTITY_PARM = 'shot'
    ENTITY_CONTEXTS = ('shots',)

    def execute(self):

        # Nodes
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        ns.clear_network(dive_node, output_node)

        # Parameters
        shot_uri = self.get_entity_uri()
        if shot_uri is None:
            ns.set_node_comment(context, "Bypassed: No shot selected")
            context.bypass(True)
            return
        ns.clear_auto_bypass(context)
        channel_name = self.get_channel_name()

        # Build the same graph the farm's stage task exports for rendering
        last_node = render_stage.build_render_stage_graph(
            dive_node,
            shot_uri,
            channel_name,
            name_prefix = ''
        )
        ns.connect_next(last_node, output_node)
        dive_node.layoutChildren()

def create(scene, name):
    return ns.create_node(scene, name, RenderDebug, 'render_debug')

def execute(kwargs):
    raw_node = kwargs['node']
    node = RenderDebug(raw_node)
    node.execute()
