
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.lops import import_asset as lop_import_asset

class ImportAsset(ns.Node):
    """SOP-side wrapper around th::Sop/import_asset::1.0.

    The SOP's Selection/Settings parms (entity, channel, version,
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

def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    node = ImportAsset(raw_node)
    # 'entity' keeps its 'from_context' default, mirroring the LOP's
    # on_created. Seeding it with the first asset in the project pinned every
    # fresh node to an arbitrary asset — whichever sorted first.
    # Prime the embedded node's label parms — the SOP's label defaults
    # mirror them via chs(), and they stay empty until an execute
    # otherwise.
    node.embedded()._update_labels()

def execute(kwargs):
    raw_node = kwargs['node']
    node = ImportAsset(raw_node)
    node.execute()

def select(kwargs):
    """Entity button: pick the asset, then import it."""
    node = ImportAsset(kwargs['node'])
    # The embedded node's 'entity' channel-references this one's.
    if node.embedded().pick_entity('Select Asset'):
        node.execute()
