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

import hou

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

    def list_entity_uris(self) -> list[str]:
        return ['from_context'] + [str(uri) for uri in self.list_shot_uris()]

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
        entity_raw = self.parm('entity').eval()
        entity_uri = self.get_entity_uri()
        if entity_raw == 'from_context':
            self.parm('entity_label').set(
                f'from_context: {entity_uri}' if entity_uri else 'from_context: none'
            )
        else:
            self.parm('entity_label').set(str(entity_uri) if entity_uri else 'none')

    def execute(self):
        """Import button — drive the embedded import_shot.

        Its entity/variant/department/version are channel references to this
        node's parms, so there is nothing to copy down; it just has to run.
        """
        from tumblepipe.pipe.houdini.lops import import_shot

        self._update_labels()
        inner = self.embedded()
        if inner is None:
            return
        import_shot.ImportShot(inner).execute()


def create(scene, name):
    return ns.create_node(scene, name, ImportLopCamera, 'import_lop_camera', 'Cop')


def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)


def on_created(raw_node):
    # 'entity' keeps its 'from_context' default — see the module docstring.
    set_style(raw_node)
    ImportLopCamera(raw_node)._update_labels()


def execute():
    ImportLopCamera(hou.pwd()).execute()


def select():
    """Entity picker button callback."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog
    from tumblepipe.api import default_client

    raw_node = hou.pwd()
    node = ImportLopCamera(raw_node)

    dialog = EntitySelectorDialog(
        api=default_client(),
        entity_filter='shots',
        include_from_context=True,
        current_selection=raw_node.parm('entity').eval(),
        title="Select Shot",
        parent=hou.qt.mainWindow(),
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            raw_node.parm('entity').set(selected_uri)
            node._update_labels()
