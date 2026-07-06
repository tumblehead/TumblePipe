import hou

from tumblepipe.api import api
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.lops import import_asset as lop_import_asset


class ImportAsset(ns.Node):
    """SOP-side wrapper around th::Sop/import_asset::1.0.

    The SOP's Selection/Settings parms (entity, variant, version,
    departments, include_layerbreak) channel-feed the embedded LOP
    import_asset at ``lopnet/import_asset``; a display-flagged lopimport
    pulls the composed geometry back out. Everything non-trivial —
    resolving the staged file, department exclusion, metadata — is the
    embedded node's job, so this wrapper just drives its wrapper.
    """

    def __init__(self, native):
        super().__init__(native)

    def embedded(self) -> lop_import_asset.ImportAsset:
        return lop_import_asset.ImportAsset(
            self.native().node('lopnet/import_asset')
        )

    def get_entity_uri(self):
        return self.embedded().get_entity_uri()

    def execute(self):
        return self.embedded().execute()


def create(scene, name):
    return ns.create_node(
        scene, name, ImportAsset, 'import_asset', 'Sop',
        force_valid_node_name=True,
    )

def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):
    set_style(raw_node)
    node = ImportAsset(raw_node)
    # Default the selection to the first available asset, mirroring the
    # LOP's on_created. Set on the SOP parm — the embedded node's entity
    # channels up to it.
    asset_uris = node.embedded().list_asset_uris()
    if asset_uris:
        node.parm('entity').set(str(asset_uris[0]))
    # Prime the embedded node's label parms — the SOP's label defaults
    # mirror them via chs(), and they stay empty until an execute
    # otherwise.
    node.embedded()._update_labels()

def execute():
    raw_node = hou.pwd()
    node = ImportAsset(raw_node)
    node.execute()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportAsset(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=False,
        current_selection=node.parm('entity').eval(),
        title="Select Asset",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node.execute()
