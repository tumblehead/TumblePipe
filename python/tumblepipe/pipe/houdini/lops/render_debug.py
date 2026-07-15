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

    def list_shot_uris(self) -> list[str]:
        uris = api.config.list_entity_uris(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return ['from_context'] + [str(uri) for uri in uris]

    def list_variant_names(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return []
        return list_variants(shot_uri)

    def get_shot_uri(self) -> Uri | None:
        shot_uri_raw = self.parm('shot').eval()
        if shot_uri_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None: return None
            return context.entity_uri
        # From settings
        shot_uris = self.list_shot_uris()
        if len(shot_uris) <= 1: return None  # Only 'from_context' means no real URIs
        if len(shot_uri_raw) == 0: return Uri.parse_unsafe(shot_uris[1])  # Skip 'from_context'
        if shot_uri_raw not in shot_uris: return None  # Compare strings
        return Uri.parse_unsafe(shot_uri_raw)

    def get_variant_name(self):
        variant_names = self.list_variant_names()
        if len(variant_names) == 0: return None
        variant_name = self.parm('variant').eval()
        if len(variant_name) == 0: return variant_names[0]
        if variant_name not in variant_names: return None
        return variant_name
    
    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if str(shot_uri) not in shot_uris: return  # Compare strings
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

    # Set node style. The 'shot' parm keeps its 'from_context' default —
    # baking the workfile's URI in here would pin the node to whichever
    # shot it was born in.
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = RenderDebug(raw_node)
    node.execute()