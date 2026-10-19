
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

class NodeError(Exception):
    """A th:: node's parms do not resolve to something the node can act
    on. The message names the node, and says how to fix it."""


class Node:
    def __init__(self, native):
        self._path = native.path()
        self._session_id = native.sessionId()

    def _error(self, message: str) -> NodeError:
        return NodeError(f'{self.path()}: {message}')

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
# Network building
##############################################################################

def connect_next(source, target):
    """Wire ``source`` into ``target``'s next input after its last connected one."""
    target.setInput(len(target.inputs()), source)

def clear_network(network, output_node):
    """Disconnect ``output_node`` and destroy every other child of ``network``.

    The import/build nodes rebuild their internal network on every run; the
    output node is the one thing that stays.
    """
    for connection in output_node.inputConnections():
        output_node.setInput(connection.inputIndex(), None)
    for child in network.children():
        if child.name() == output_node.name(): continue
        child.destroy()

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

# Every bypass an import node sets on itself comes with a comment starting
# with this, which is how a later run tells its own bypass from the artist's.
AUTO_BYPASS_PREFIX = 'Bypassed:'

def is_auto_bypass(bypassed: bool, comment: str) -> bool:
    """True if a bypass was set by the node's own execute(), not the artist."""
    return bypassed and comment.startswith(AUTO_BYPASS_PREFIX)

def clear_auto_bypass(native):
    """Undo a bypass the node set on itself in an earlier run.

    A bypass the artist set is left alone. Import nodes re-execute on every
    scene open (asset_browser.load_hook), and clearing the flag
    unconditionally re-enabled every import an artist had switched off, so
    assets they had disabled came back into the shot on the next export.
    """
    if is_auto_bypass(native.isBypassed(), native.comment()):
        native.bypass(False)

##############################################################################
# Node creation / styling
##############################################################################

def create_node(scene, name, node_cls, type_name, context = 'Lop', **create_kwargs):
    """Create-or-wrap the ``th::<type_name>`` node named ``name`` under ``scene``.

    Returns an existing child wrapped in ``node_cls`` if one already exists, else
    creates it from the latest version of the node type. Extra keyword arguments
    (e.g. ``force_valid_node_name=True``) are forwarded to ``createNode``.
    """
    node_type = find_node_type(type_name, context)
    assert node_type is not None, f'Could not find {type_name} node type'
    native = scene.node(name)
    if native is not None: return node_cls(native)
    return node_cls(scene.createNode(node_type.name(), name, **create_kwargs))

def set_node_style(raw_node, shape = SHAPE_NODE_DEFAULT, color = COLOR_NODE_DEFAULT):
    """Apply the standard th:: node styling: node color + node shape."""
    raw_node.setColor(color)
    raw_node.setUserData('nodeshape', shape)

def on_created(raw_node, shape = SHAPE_NODE_DEFAULT):
    """The OnCreated script of a th:: HDA whose only setup is its styling.

    HDAs with more to do on creation call their module's own on_created.
    """
    set_node_style(raw_node, shape)

##############################################################################
# LOP layer save paths
##############################################################################

# H22 flipped the sopcreate/sopimport 'Layer Save Path' default to ON with
# $HIP/usd/$OS.usd. A save-path'ed layer is written NEXT TO THE WORKFILE by
# the export ROP (savestyle=flattenimplicitlayers) instead of flattening into
# the published layer, which then composes empty on every other machine.
_LAYER_SAVE_PATH_PARMS = (
    ('enable_savepath', 'savepath'),  # sopcreate / sopimport / sopmodify
)

def disable_layer_save_path(node) -> None:
    """Turn off a LOP node's layer save path so its layer flattens on export."""
    for enable_name, path_name in _LAYER_SAVE_PATH_PARMS:
        enable_parm = node.parm(enable_name)
        if enable_parm is None:
            continue
        enable_parm.set(False)
        path_parm = node.parm(path_name)
        if path_parm is not None:
            path_parm.set('')
