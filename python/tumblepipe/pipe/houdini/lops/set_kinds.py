import hou

import tumblepipe.pipe.houdini.nodes as ns

def _clear_dive(dive_node, output_node):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than output
    for node in dive_node.children():
        if node.name() == output_node.name(): continue
        node.destroy()

class SetKinds(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def get_prim_path(self) -> str | None:
        prim_path = self.parm('prim_path').eval()
        if len(prim_path) == 0: return None
        return prim_path

    def set_prim_path(self, prim_path: str):
        self.parm('prim_path').set(prim_path)

    def execute(self):

        def _prim_kind_type(index: int) -> tuple[str, str]:
            if index == 0: return ('assembly', 'UsdGeomXform')
            if index == 1: return ('component', 'UsdGeomXform')
            return ('', 'UsdGeomScope')
        
        # Nodes
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')

        # Clear existing configure nodes
        _clear_dive(dive_node, output_node)

        # Get parameters
        prim_path = self.get_prim_path()
        if prim_path is None: return

        # Parse prim path into segments
        prev_node = dive_node.indirectInputs()[0]
        segments = [s for s in prim_path.split('/') if s]
        if len(segments) == 0:
            output_node.setInput(0, prev_node)
            return

        # Create configure nodes for each level
        for index in range(len(segments)):

            # Get configure parameters
            level_path = '/'.join([''] + segments[:index + 1])
            prim_kind, prim_type = _prim_kind_type(index)

            # Create configure primitive node
            config_node = dive_node.createNode(
                'configureprimitive',
                f'level_{index + 1}'
            )
            config_node.parm('primpattern').set(level_path)
            config_node.parm('setkind').set(1)
            config_node.parm('kind').set(prim_kind)
            config_node.parm('settype').set(1)
            config_node.parm('type').set(prim_type)

            # Connect to previous node
            config_node.setInput(0, prev_node)
            prev_node = config_node
        
        # Add the last configure primitive node
        last_path = '/'.join([''] + segments + ['*'])
        config_node = dive_node.createNode(
            'configureprimitive',
            f'level_{len(segments)}'
        )
        config_node.parm('primpattern').set(last_path)
        config_node.parm('setkind').set(1)
        config_node.parm('kind').set('')
        config_node.parm('settype').set(1)
        config_node.parm('type').set('UsdGeomScope')
        config_node.setInput(0, prev_node)
        prev_node = config_node

        # Connect last node to output
        output_node.setInput(0, prev_node)

        # Layout nodes
        dive_node.layoutChildren()

def create(scene, name):
    return ns.create_node(scene, name, SetKinds, 'set_kinds')

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = SetKinds(raw_node)
    node.execute()
