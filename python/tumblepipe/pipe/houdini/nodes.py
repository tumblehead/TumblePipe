
import hou


##############################################################################
# Node Type helpers
##############################################################################

def find_node_type(type_name, context):
    def _version_code(version_name):
        if '.' not in version_name: return int(version_name)
        return int(version_name.split('.', 1)[0])
    categories = hou.nodeTypeCategories()
    if context not in categories: return None
    node_types = dict()
    for node_type in categories[context].nodeTypes().values():
        node_type_name = node_type.name()
        if not node_type_name.lower().startswith(f'th::{type_name}::'): continue
        node_types[node_type_name] = node_type
    if len(node_types) == 0: return None
    node_type_names = list(node_types.keys())
    node_type_names.sort(key = lambda node_type_name: _version_code(node_type_name.rsplit('::', 1)[-1]))
    return node_types[node_type_names[-1]]

def _type_category(node):
    parent = node.parent()
    if parent is None: return None
    return parent.childTypeCategory()

def list_node_types(context):
    categories = hou.nodeTypeCategories()
    if context not in categories: return []
    return [
        node_type.name()
        for node_type in categories[context].nodeTypes().values()
        if node_type.name().lower().startswith('th::')
    ]

def list_by_node_type(type_name, context):
    def _valid_node(node):
        type_category = _type_category(node)
        if type_category is None: return False
        if type_category.name() != context: return False
        return node.type().name().lower().startswith(f'th::{type_name}::')
    return list(filter(_valid_node, hou.node('/').allSubChildren()))

##############################################################################
# Base Node class
##############################################################################

class Node:
    def __init__(self, native):
        self._path = native.path()
        self._session_id = native.sessionId()

    def native(self):
        node = hou.nodeBySessionId(self._session_id)
        if node is not None: return node
        return hou.node(self._path)

    def is_valid(self):
        return self.native() is not None
    
    # Hierarchy
    
    def parm(self, name):
        return self.native().parm(name)
    
    def node(self, name):
        result = self.native().node(name)
        if result is None: return None
        return Node(result)
    
    def path(self):
        return self._path
    
    def children(self):
        return [Node(child) for child in self.native().children()]
    
    def editableStage(self):
        return self.native().editableStage()
    
    def uneditableStage(self):
        return self.native().uneditableStage()
    
    # Types

    def type(self):
        return self.native().type()
    
    # Inputs and outputs

    def inputs(self):
        return tuple(Node(input) for input in self.native().inputs())
    
    def input(self, index):
        result = self.native().input(index)
        if result is None: return None
        return Node(result)

    def outputs(self):
        return tuple(Node(output) for output in self.native().outputs())
    
    def inputConnections(self):
        native = self.native()
        return native.inputConnections()
    
    def outputConnections(self):
        return self.native().outputConnections()
    
    def setInput(self, in_port, node, out_port = 0):
        _node = (
            node.native()
            if issubclass(type(node), Node)
            else node
        )
        self.native().setInput(in_port, _node, out_port)
    
    # Layout

    def setDisplayFlag(self, flag):
        self.native().setDisplayFlag(flag)
    
    def layoutChildren(self):
        self.native().layoutChildren()

##############################################################################
# Styling
##############################################################################

# Colors
COLOR_NODE_DEFAULT = hou.Color((0.631373, 0.870588, 1.0))
COLOR_NODE_BLACK = hou.Color((0.0, 0.0, 0.0))

# Shapes
SHAPE_NODE_IMPORT = 'bulge_down'
SHAPE_NODE_EXPORT = 'bulge'
SHAPE_NODE_DIVE = 'cigar'
SHAPE_NODE_ARCHIVE = 'oval'
SHAPE_NODE_DEFAULT = 'rect'

def set_node_comment(native, message: str):
    """Set node comment and ensure it's visible."""
    native.setComment(message)
    native.setGenericFlag(hou.nodeFlag.DisplayComment, True)