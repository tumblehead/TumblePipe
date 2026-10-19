"""th::import_shot's Layer Stack: one spare-parm row per loaded sublayer.

Each row is a toggle that drives its sublayer's enable, the version that
composed, and for an asset row a '...' button that opens the asset's
department layers (the AssetLayerDialog).
"""

from pathlib import Path

import hou

from tumblepipe import resolver
from tumblepipe.pipe.asset_layers import build_asset_layer_report
from tumblepipe.pipe.houdini.lops.shot_sublayers import (
    asset_uri_of,
    layer_display_name,
    layer_display_version,
)
from tumblepipe.pipe.usd.sublayer_refs import parse_entity_sublayer_uri


def update_layer_stack(native, layers_to_load: list[dict]):
    """Rebuild the import_shot ``native``'s Layer Stack folder for
    ``layers_to_load`` (parsed sublayers, in the Sublayer LOP's order)."""
    import_node = native.node('import')
    parm_group = native.parmTemplateGroup()

    # Rebuilt on every import
    if parm_group.find('layer_stack'):
        parm_group.remove('layer_stack')
    layer_folder = hou.FolderParmTemplate(
        'layer_stack', 'Layer Stack',
        folder_type=hou.folderType.Simple
    )

    # Pad the names into a column
    max_name_len = max(
        (len(layer_display_name(info)) for info in layers_to_load),
        default=20
    )
    for i, layer_info in enumerate(layers_to_load):
        safe_name = f"layer_{i}"
        toggle_parm = hou.ToggleParmTemplate(
            f'{safe_name}_enable',
            layer_display_name(layer_info).ljust(max_name_len + 2),
            default_value=True
        )
        toggle_parm.setJoinWithNext(True)
        layer_folder.addParmTemplate(toggle_parm)

        version_parm = hou.LabelParmTemplate(
            f'{safe_name}_version',
            '',
            column_labels=(layer_display_version(layer_info),),
            is_label_hidden=True
        )
        # Asset rows get a third column: an asset is a composed blob, and
        # this is the only way to see the department layers inside it.
        inspectable = layer_info['type'] == 'asset'
        if inspectable:
            version_parm.setJoinWithNext(True)
        layer_folder.addParmTemplate(version_parm)

        if inspectable:
            inspect_parm = hou.ButtonParmTemplate(
                f'{safe_name}_inspect',
                '...',
                # Spare-parm callbacks are serialized into the .hip; the
                # layer stack is rebuilt on every import (including the
                # scene-open refresh), so a saved button is replaced
                # before it can go stale.
                script_callback=(
                    'import tumblepipe.pipe.houdini.lops.import_shot as '
                    f'th_node; th_node.inspect_asset_layer(kwargs, {i})'
                ),
                script_callback_language=hou.scriptLanguage.Python,
            )
            inspect_parm.setHelp("Show the department layers composing this asset")
            layer_folder.addParmTemplate(inspect_parm)

    # Insert after the Selection folder (before Settings). Anchor on the
    # definition's `entity` parm, not the folder name: inserting a spare
    # folder makes Houdini spare-ify the HDA's containers under renamed
    # names (selection -> selection2), so a folder-name lookup goes stale
    # after the first import.
    parm_group.insertAfter(parm_group.containingFolder('entity'), layer_folder)
    native.setParmTemplateGroup(parm_group)

    # The sublayer enables read the row toggles
    for i in range(len(layers_to_load)):
        import_node.parm(f'enable{i+1}').setExpression(f'ch("../layer_{i}_enable")')


def inspect_asset_layer(native, index: int, pinned: bool):
    """Open the layer inspector for the asset on Layer Stack row ``index``.

    The ref is re-read from the node rather than baked into the callback
    string: spare-parm callbacks are serialized into the .hip.
    """
    ref = native.node('import').parm(f'filepath{index + 1}').eval()
    asset_uri = asset_uri_of(ref)
    if asset_uri is None:
        raise ValueError(f'Layer Stack row {index} ({ref}) is not an asset')

    # Resolve the row's own ref rather than looking up "the latest staged
    # file": in latest mode those are the same, but pinned they are not,
    # and reporting a build the node is not composing is the exact lie
    # this dialog exists to kill.
    with resolver.latest_mode(not pinned):
        staged = resolver.try_resolve_entity_uri(ref)
    if staged is None:
        hou.ui.displayMessage(
            f"{asset_uri.segments[-1]} has no staged build to inspect.",
            severity=hou.severityType.Warning,
        )
        return

    # The asset's own channel, from its ref — not the node's channel parm,
    # which is the *shot's*.
    staged_path = Path(staged)
    report = build_asset_layer_report(
        asset_uri,
        staged_path,
        pinned,
        channel=parse_entity_sublayer_uri(ref).channel,
        staged_version=staged_path.parent.name,
    )

    from tumblepipe.pipe.houdini.ui.asset_layer_dialog import AssetLayerDialog
    AssetLayerDialog(report, parent=hou.qt.mainWindow()).show()
