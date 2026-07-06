from pathlib import Path

import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.config.variants import list_variants
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini import render_stage
from tumblepipe.pipe.paths import (
    get_workfile_context
)


def _clear_dive(dive_node):
    for node in dive_node.children():
        if node.name() == 'output': continue
        node.destroy()

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

class RenderDebug(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_shot_uris(self) -> list[Uri]:
        return api.config.list_entity_uris(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )

    def list_variant_names(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return []
        return list_variants(shot_uri)
    
    def get_shot_uri(self) -> Uri | None:
        shot_uris = self.list_shot_uris()
        if len(shot_uris) == 0: return None
        shot_uri_raw = self.parm('shot').eval()
        if len(shot_uri_raw) == 0: return shot_uris[0]
        shot_uri = Uri.parse_unsafe(shot_uri_raw)
        if shot_uri not in shot_uris: return None
        return shot_uri

    def get_variant_name(self):
        variant_names = self.list_variant_names()
        if len(variant_names) == 0: return None
        variant_name = self.parm('variant').eval()
        if len(variant_name) == 0: return variant_names[0]
        if variant_name not in variant_names: return None
        return variant_name
    
    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if shot_uri not in shot_uris: return
        self.parm('shot').set(str(shot_uri))

    def set_variant_name(self, variant_name):
        variant_names = self.list_variant_names()
        if variant_name not in variant_names: return
        self.parm('variant').set(variant_name)

    def execute(self):

        # Nodes
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        _clear_dive(dive_node)

        # Parameters
        shot_uri = self.get_shot_uri()
        if shot_uri is None:
            ns.set_node_comment(context, "Bypassed: No shot selected")
            context.bypass(True)
            return
        context.bypass(False)
        variant_name = self.get_variant_name() or 'default'

        # Build the same graph the farm's stage task exports for rendering
        last_node = render_stage.build_render_stage_graph(
            dive_node,
            shot_uri,
            variant_name,
            name_prefix = ''
        )

        # Connect output node
        _connect(last_node, output_node)

        # Layout nodes
        dive_node.layoutChildren()

def create(scene, name):
    return ns.create_node(scene, name, RenderDebug, 'render_debug')

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('render_debug', 'Lop')
    if raw_node_type != node_type: return
    node = RenderDebug(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return

    # Set the default values from context
    node.set_shot_uri(context.entity_uri)

def execute():
    raw_node = hou.pwd()
    node = RenderDebug(raw_node)
    node.execute()