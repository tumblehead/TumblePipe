"""The asset-authoring LOPs: th::create_asset, create_asset_model and
create_asset_lookdev.

All three bind the asset they author through the standard 'entity' /
'entity_label' pair (default 'from_context'), and derive the root prim the
internals author under from it — or, with Use Entity off, take the artist's
'primpath'. The model and lookdev nodes also hold a USD variant per row of
their 'variants' multiparm: each row owns one output node inside the HDA's
editable variant network.

This logic used to live, copy-pasted, in each HDA's PythonModule. The
PythonModules now only expose ``effective_primpath`` for the hidden parm's
``pythonexprs`` expression, which saved scenes store verbatim.
"""

import hou

from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path


class CreateAsset(EntityNode):
    """th::create_asset: authors one asset's root prim."""

    ENTITY_CONTEXTS = ('assets',)

    def _update_labels(self):
        self.parm('entity_label').set(self._entity_label())

    def effective_primpath(self) -> str:
        """The root prim path the internal LOPs author under.

        With Use Entity on, the asset's prim path; an unresolved
        'from_context' or an unknown asset raises, which surfaces as an
        error on the hidden parm's expression rather than authoring under
        a path the artist did not pick. With it off, the 'primpath' parm.
        """
        if not self.parm('use_entity').eval():
            return self.parm('primpath').eval()
        return uri_to_prim_path(self.require_entity_uri())


class VariantAsset(CreateAsset):
    """A create_asset node with a USD variant per 'variants' row.

    Row ``i`` (1-based) owns the output node ``OUT_<variant_name{i}>`` with
    outputidx ``i - 1`` inside ``VARIANT_NETWORK``, the only editable
    network: the HDA fetches each variant straight from there, so its
    parent stays locked and "U" from the dive target jumps back out.
    """

    VARIANT_NETWORK: str
    # A child of VARIANT_NETWORK the first variant's output is wired to when
    # it is created, so a new node publishes its default content.
    FIRST_VARIANT_INPUT: str | None = None

    def variant_network(self):
        return self.native().node(self.VARIANT_NETWORK)

    def _variant_outputs(self) -> dict:
        """The network's output nodes, keyed by outputidx."""
        return {
            child.evalParm('outputidx'): child
            for child in self.variant_network().children()
            if child.type().name() == 'output'
        }

    def variant_name(self, variant_index: int) -> str:
        return self.parm(f'variant_name{variant_index}').eval()

    def sync_variants(self):
        """Reconcile the network's output nodes with the multiparm rows."""
        network = self.variant_network()
        outputs = self._variant_outputs()
        count = self.parm('variants').eval()
        for index in set(outputs) - set(range(count)):
            outputs[index].destroy()
        for index in range(count):
            out_node = outputs.get(index)
            if out_node is None:
                out_node = network.createNode('output')
                out_node.parm('outputidx').set(index)
                out_node.moveToGoodPosition()
                if index == 0 and self.FIRST_VARIANT_INPUT is not None:
                    out_node.setInput(0, network.node(self.FIRST_VARIANT_INPUT))
            out_node.setName(f'OUT_{self.variant_name(index + 1)}')

    def rename_variant(self, variant_index: int, variant_name: str):
        """Rename row ``variant_index``'s output node after its new name."""
        out_node = self._variant_outputs()[variant_index - 1]
        out_node.setName(f'OUT_{variant_name}')

    def variant_output(self, variant_index: int):
        return self._variant_outputs()[variant_index - 1]


def variant_output_path(fetch_node) -> str:
    """The output node path the variant loop's FETCH_VARIANT merges.

    Called from FETCH_VARIANT's objpath1 expression inside
    th::create_asset_model's for-each, which iterates the 'variants' rows
    in order and sets ITERATION to the 0-based row. The output is found by
    its outputidx, not by its OUT_<variant> name, so an artist renaming the
    output node inside the editable network no longer breaks the cook.
    """
    hda_node = fetch_node.parent().parent()
    iteration = hou.contextOption('ITERATION')
    index = int(iteration) if iteration is not None else 0
    output = CreateAssetModel(hda_node)._variant_outputs().get(index)
    return output.path() if output is not None else ''


class CreateAssetModel(VariantAsset):
    VARIANT_NETWORK = 'variant_sopnet/create_variants'


class CreateAssetLookdev(VariantAsset):
    VARIANT_NETWORK = 'lookdev_variant_subnet/lookdev_subnet'
    # The shipped material library is the lookdev's default content.
    FIRST_VARIANT_INPUT = 'material_library'


_CLASSES = {
    'create_asset': CreateAsset,
    'create_asset_model': CreateAssetModel,
    'create_asset_lookdev': CreateAssetLookdev,
}


def wrap(native) -> CreateAsset:
    """The wrapper for a th::create_asset* node."""
    type_name = native.type().nameComponents()[2]
    if type_name not in _CLASSES:
        raise TypeError(f'{native.path()} is not a th::create_asset node')
    return _CLASSES[type_name](native)


# HDA callbacks and expressions ---------------------------------------------

def effective_primpath(native) -> str:
    """The hidden 'effective_primpath' parm's value (a pythonexprs)."""
    return wrap(native).effective_primpath()


def on_created(native):
    node = wrap(native)
    if isinstance(node, VariantAsset):
        native.parm('variants').set(1)
        native.parm('variant_name1').set('default')
        node.sync_variants()
    node._update_labels()


def select_entity(kwargs):
    """Entity button: pick the asset, or 'from_context'."""
    wrap(kwargs['node']).pick_entity('Select Asset')


def add_variant(kwargs):
    """'variants' multiparm callback — fires on both add and remove.

    The sync runs once the callback has returned: editing the network from
    inside a multiparm callback crashes Houdini.
    """
    node_path = kwargs['node'].path()

    def _sync():
        hou.ui.removeEventLoopCallback(_sync)
        wrap(hou.node(node_path)).sync_variants()

    hou.ui.addEventLoopCallback(_sync)


def on_variant_name(kwargs):
    """A variant was renamed — rename its output node."""
    wrap(kwargs['node']).rename_variant(
        int(kwargs['script_multiparm_index']), kwargs['script_value']
    )


def jump_to_variant(kwargs):
    """Show the row's output node in the network editors."""
    target = wrap(kwargs['node']).variant_output(
        int(kwargs['script_multiparm_index'])
    )
    target.setCurrent(True, clear_all_selected=True)
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() == hou.paneTabType.NetworkEditor:
            pane_tab.setCurrentNode(target)
            pane_tab.homeToSelection()
