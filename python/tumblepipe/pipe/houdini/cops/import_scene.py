"""th::import_scene: a shot's staged scene, as geometry and a camera, in COPs.

Copernicus can rasterize anything: this node hands a comp the shot itself.
Its two outputs wire straight into a Rasterize Geometry:

    geometry   the staged stage's prims (Primitives), unpacked to polygons
    camera     the shot's render camera (/cameras/render_camera)

Inside, an embedded ``th::import_shot`` composes the shot's staged stage
**with payloads** (the geometry is the point), a SOP Import lifts the prims
out through a LOP Import and Unpack USD, and a Camera Import reads the camera
through a ``lopimportcam``. Geometry is Animated by default, so it follows
the frame like the renders do.

Entity, Channel, Department and Version are channel-referenced into the
embedded import_shot, which does the importing: the node joins scene
imports' refresh through it (``scene_imports.REFRESHED_BY_INNER_NODE``).
Like every entity-aware th:: HDA the entity defaults to 'from_context'.
"""

import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode

TYPE_NAME = 'import_scene'

# The embedded th::import_shot, relative to the HDA.
IMPORT_NODE = 'lopnet/import_shot'


class ImportScene(EntityNode):

    ENTITY_CONTEXTS = ('shots',)
    DEPARTMENT_CONTEXT = 'shots'

    def embedded(self):
        """The th::import_shot that composes the shot's stage."""
        return self.native().node(IMPORT_NODE)

    def _update_labels(self):
        self.parm('entity_label').set(self._entity_label())
        inner = self.embedded()
        if inner is not None and inner.parm('version_label') is not None:
            self.parm('version_label').set(inner.parm('version_label').eval())

    def execute(self):
        """Import: run the embedded import_shot (its selection follows this
        node's parms, so there is nothing to copy down)."""
        from tumblepipe.pipe.houdini.lops import import_shot
        inner = self.embedded()
        if inner is None:
            raise self._error(f'the HDA has no embedded import_shot at {IMPORT_NODE}')
        import_shot.ImportShot(inner).execute()
        self._update_labels()


def find_scenes() -> list[ImportScene]:
    return [ImportScene(native) for native in ns.list_by_node_type(TYPE_NAME, 'Cop')]


def create(scene, name):
    return ns.create_node(scene, name, ImportScene, TYPE_NAME, 'Cop')


def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    node = ImportScene(raw_node)
    # Resolve the shot at once when there is one; a node dropped outside a
    # shot workfile says so on its labels instead of failing the creation.
    try:
        node.execute()
    except Exception:
        node._update_labels()


def execute(kwargs):
    node = ImportScene(kwargs['node'])
    with ns.node_errors_as_message('Import Scene'):
        node.execute()


def select_entity(kwargs):
    node = ImportScene(kwargs['node'])
    node.pick_entity('Select Shot', contexts=('shots',))
    with ns.node_errors_as_message('Import Scene'):
        node.execute()
