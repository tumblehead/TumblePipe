import hou # type: ignore

#region composite
def composite(nodes: list[hou.Node], mode: str):
    if len(nodes) == 0: return
    layout_nodes = list(nodes)
    network = nodes[0].parent()
    blend_node = network.createNode('blend', f"{nodes[0]}_{mode}_{nodes[1]}")
    blend_node.setInput(0, nodes[0])
    blend_node.setInput(1, nodes[1])
    blend_node.parm("mode").set(mode)
    layout_nodes.append(blend_node)
    network.layoutChildren(layout_nodes)

#region render cop
def render_cop(nodes: list[hou.Node]):
    ROP_OFFSET = hou.Vector2(3, 0)
    for node in nodes:
        network = node.parent()
        rop_node = network.createNode("rop_image", f"render_{node.name()}")
        rop_node.parm("coppath").set(node.path())
        rop_node.parm("copoutput").set(f"$HIP/render/{node.name()}.$F4.exr")
        rop_node.setPosition(node.position() + ROP_OFFSET)

#region cop_type_convert
def type_convert(nodes: list[hou.Node], totype: str) -> None:
    if len(nodes) == 0: return
    network = nodes[0].node("..")
    totype = totype.lower()
    for node in nodes:
        layout_nodes = []
        layout_nodes.append(node)
        for i, datatype in enumerate(node.outputDataTypes()):
            if datatype == totype: continue
            if datatype == "unknown": continue
            datatype = datatype.lower()
            if totype == "mono":
                convert_node_name = f"{totype}"
                convert_node = network.createNode(convert_node_name, f"{datatype}_to_{convert_node_name}")
            else:
                convert_node_name = f"{datatype}to{totype}"
                convert_node = network.createNode(convert_node_name, f"{datatype}_to_{totype}")
            convert_node.setInput(0, node, i)
            layout_nodes.append(convert_node)
        network.layoutChildren(layout_nodes)