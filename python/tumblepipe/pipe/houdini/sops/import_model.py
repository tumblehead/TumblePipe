"""th::import_model: an asset's model (or blendshape) layer, in SOPs.

The HDA wraps a th::import_layer at ``lopnet/import_layer``. Its own Entity,
Department and Version parms drive that inner node through channel
references; the inner node does the import, and this wrapper mirrors its
labels and bypass state onto the outer SOP so the artist sees why nothing
loaded.

This logic used to be the HDA's 109-line PythonModule, with its OnCreated
script carrying the rest. Every caller — the parm callbacks, the asset
browser drop, the scene-open import refresh — goes through ``ImportModel``.
"""

import hou

import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.lops.import_layer import ImportLayer
from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path

# The embedded th::import_layer, relative to the HDA.
INNER_IMPORT = 'lopnet/import_layer'


class ImportModel(ns.Node):

    def inner(self) -> ImportLayer:
        return ImportLayer(self.native().node(INNER_IMPORT))

    def _update_labels(self):
        """The outer labels show what the inner import resolved."""
        inner = self.inner()
        self.parm('entity_label').set(inner.parm('entity_label').eval())
        self.parm('version_label').set(inner.parm('version_label').eval())

    def execute(self):
        """Import: run the inner import_layer, then mirror its state."""
        native = self.native()
        inner = self.inner()
        inner.execute()
        self._update_labels()

        # The inner node bypasses itself with a reason when nothing is
        # staged (or nothing is selected); show the same on the SOP card.
        inner_native = inner.native()
        if inner_native.isBypassed():
            ns.set_node_comment(native, inner_native.comment())
            native.bypass(True)
        else:
            # The mirrored comment carries the inner node's 'Bypassed:'
            # prefix, so an artist's own bypass survives a re-import.
            ns.clear_auto_bypass(native)

    def open_location(self):
        self.inner().open_location()

    def pick_entity(self):
        """Entity button: pick, then import. The inner node's 'entity' is a
        channel reference to the outer parm, so its picker sets both."""
        if self.inner().pick_entity('Select Asset', contexts=('assets',)):
            self.execute()

    def variant_prim_path(self) -> str:
        """The prim the inner Set Variant LOP picks the model variant on:
        the imported asset's root. Empty while nothing is imported (an
        unresolved 'from_context' bypasses the inner import)."""
        entity_uri = self.inner().get_entity_uri()
        if entity_uri is None:
            return ''
        return uri_to_prim_path(entity_uri)


def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    # Link the inner import_layer's department to the SOP's Department parm
    # so the dropdown drives the import. A plain .set() here would clobber
    # the channel reference baked into the HDA and silently unlink them (the
    # bug where changing the dropdown did nothing).
    raw_node.node(INNER_IMPORT).parm('department').setExpression(
        'chs("../../department")', language=hou.exprLanguage.Hscript
    )


def variant_prim_path(set_variant_node) -> str:
    """The inner Set Variant LOP's primpattern1 expression
    (``lopnet/set_variant``, two levels below the HDA)."""
    return ImportModel(set_variant_node.parent().parent()).variant_prim_path()


def execute(kwargs):
    ImportModel(kwargs['node']).execute()


def open_location(kwargs):
    ImportModel(kwargs['node']).open_location()


def select(kwargs):
    ImportModel(kwargs['node']).pick_entity()
