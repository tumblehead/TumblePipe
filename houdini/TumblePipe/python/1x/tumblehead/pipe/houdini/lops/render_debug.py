from pathlib import Path

import hou

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.variants import list_variants
from tumblehead.config.department import list_departments
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini.lops import (
    build_shot,
    import_layer
)
from tumblehead.pipe.paths import (
    get_workfile_context
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/render_debug')

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
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return [entity.uri for entity in shot_entities]

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
        variant_name = self.get_variant_name()
        included_shot_departments = [d.name for d in list_departments('shots') if d.renderable]

        # Setup build shot
        shot_node = build_shot.create(dive_node, 'build_shot')
        shot_node.set_shot_uri(shot_uri)
        shot_node.set_exclude_shot_department_names([])
        shot_node.set_include_procedurals(True)
        shot_node.set_include_downstream_departments(True)
        shot_node.execute()
        prev_node = shot_node.native()

        # Prepare import variants
        variant_subnet = dive_node.createNode('subnet', 'import_variant')
        variant_subnet.node('output0').destroy()
        variant_subnet_input = variant_subnet.indirectInputs()[0]
        variant_subnet_output = variant_subnet.createNode('output', 'output')

        # Connect build shot to subnet
        _connect(prev_node, variant_subnet)
        prev_node = variant_subnet_input

        # Setup import layer
        for shot_department_name in included_shot_departments:
            layer_node = import_layer.create(variant_subnet, f'{shot_department_name}_import')
            layer_node.set_entity_uri(shot_uri)
            layer_node.set_department_name(shot_department_name)
            layer_node.set_variant_name(variant_name)
            layer_node.set_version_name('current')
            layer_node.execute()
            _connect(prev_node, layer_node.native())
            prev_node = layer_node.native()

        # Connect last node to subnet output
        _connect(prev_node, variant_subnet_output)
        prev_node = variant_subnet

        # Connect output node
        _connect(prev_node, output_node)

        # Layout nodes
        dive_node.layoutChildren()
        variant_subnet.layoutChildren()

def create(scene, name):
    node_type = ns.find_node_type('render_debug', 'Lop')
    assert node_type is not None, 'Could not find render_debug node type'
    native = scene.node(name)
    if native is not None: return RenderDebug(native)
    return RenderDebug(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

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