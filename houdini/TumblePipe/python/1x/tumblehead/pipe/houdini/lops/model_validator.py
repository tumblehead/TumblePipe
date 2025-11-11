from tumblehead.api import default_client
import tumblehead.pipe.houdini.nodes as ns

api = default_client()

class ModelValidator(ns.Node):
    def __init__(self, native):
        super().__init__(native)

def create(scene, name):
    node_type = ns.find_node_type('model_validator', 'Lop')
    assert node_type is not None, 'Could not find model_validator node type'
    native = scene.node(name)
    if native is not None: return ModelValidator(native)
    return ModelValidator(scene.createNode(node_type.name(), name))