"""COP wrapper for th::import_lop_camera.

Brings a shot's render camera into COPs, so comp works against the real
camera — focal length, aperture, clipping — instead of a hand-copied one.

The HDA is three stages:

    lopnet/import_camera   th::import_shot, composing the shot's staged stage
    objnet/lopimportcam    lifts /cameras/render_camera into an OBJ camera
    cameraimport           feeds that camera to COPs

The embedded import_shot loads no payloads: a camera is a light prim, and
comp has no use for the shot's geometry, so composing it would be a large
bill for nothing.

Entity/department/version are channel-referenced down into the embedded
import_shot, so this wrapper only has to own the selection UI. Like every
entity-aware th:: HDA the entity parm defaults to 'from_context', resolving
the shot from the workfile the node lives in.
"""


import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode

# The pipeline's render camera. Shot templates author it here (the layout
# template's camera LOP, the animation template's lopimportcam), and playblast
# and render read it from here.
CAMERA_PRIM_PATH = '/cameras/render_camera'

# The embedded th::import_shot, relative to the HDA.
IMPORT_NODE = 'lopnet/import_camera'


class ImportLopCamera(EntityNode):

    # A render camera only ever comes from a shot, so 'from_context' inside an
    # asset workfile resolves to nothing rather than to the asset.
    ENTITY_CONTEXTS = ('shots',)
    DEPARTMENT_CONTEXT = 'shots'

    def __init__(self, native):
        super().__init__(native)


    def list_department_names(self) -> list[str]:
        # Every shot department, not just the publishable ones: the camera is
        # imported *from* whichever department authored it, and 'from_context'
        # in a comp workfile resolves to 'composite', which is not publishable.
        return ['from_context'] + [
            d.name for d in self.scoped_departments('shots')
        ]

    def embedded(self):
        """The th::import_shot that composes the shot's stage."""
        return self.native().node(IMPORT_NODE)

    def _update_labels(self):
        self.parm('entity_label').set(self._entity_label())

    def execute(self):
        """Import button — drive the embedded import_shot.

        Its entity/channel/department/version are channel references to this
        node's parms, so there is nothing to copy down; it just has to run.
        """
        from tumblepipe.pipe.houdini.lops import import_shot

        self._update_labels()
        inner = self.embedded()
        if inner is None:
            raise self._error(
                f'the HDA has no embedded import_shot at {IMPORT_NODE}'
            )
        import_shot.ImportShot(inner).execute()


def create(scene, name):
    return ns.create_node(scene, name, ImportLopCamera, 'import_lop_camera', 'Cop')


def on_created(raw_node):
    # 'entity' keeps its 'from_context' default — see the module docstring.
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    ImportLopCamera(raw_node)._update_labels()


def execute(kwargs):
    ImportLopCamera(kwargs['node']).execute()


def select(kwargs):
    """Entity button."""
    ImportLopCamera(kwargs['node']).pick_entity('Select Shot')
