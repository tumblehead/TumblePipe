"""th::import_shot's Layer Stack: one spare-parm row per loaded sublayer.

Each row is a toggle that drives its sublayer's enable, the version that
composed, an Inspect button that shows the layer's file in the OS file
browser, and for an asset row a '...' button that opens the asset's
department layers (the AssetLayerDialog).
"""

import re
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
from tumblepipe.util import desktop


_LAYER_ROW_PARM = re.compile(r'^layer_\d+_(enable|version|reveal|inspect)$')


def artist_spare_names(instance_names, defined_names) -> list[str]:
    """The node's own parms to carry over when the Layer Stack is rebuilt
    on the definition's interface: every parm the instance has that the
    definition does not, except the Layer Stack rows (rebuilt anyway).
    In the instance's order, each once."""
    defined = set(defined_names)
    kept: list[str] = []
    for name in instance_names:
        if name in defined or _LAYER_ROW_PARM.match(name) or name in kept:
            continue
        kept.append(name)
    return kept


def _leaf_templates(group_or_folder):
    """Every non-folder parm template under ``group_or_folder``, in order."""
    for template in group_or_folder.parmTemplates():
        if template.type() == hou.parmTemplateType.Folder:
            yield from _leaf_templates(template)
        elif template.type() != hou.parmTemplateType.FolderSet:
            yield template


def _base_group(native):
    """The interface the Layer Stack is inserted into: the HDA
    definition's, as it is now, plus any spare parm the artist added.

    Not the instance's own group: once setParmTemplateGroup has run, the
    node keeps that group, and an HDA update no longer reaches it. A parm
    the definition gained later (1.104.0's Browse...) was appended at the
    bottom, under Actions, and one it hid (the jade icon button) stayed
    visible (Remy, 2026-11-13).
    """
    definition = native.type().definition()
    if definition is None:
        return native.parmTemplateGroup()
    group = definition.parmTemplateGroup()
    instance = {t.name(): t for t in _leaf_templates(native.parmTemplateGroup())}
    defined = [t.name() for t in _leaf_templates(group)]
    for name in artist_spare_names(instance, defined):
        group.append(instance[name])
    return group


def update_layer_stack(native, layers_to_load: list[dict]):
    """Rebuild the import_shot ``native``'s Layer Stack folder for
    ``layers_to_load`` (parsed sublayers, in the Sublayer LOP's order)."""
    import_node = native.node('import')
    parm_group = _base_group(native)

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
        version_parm.setJoinWithNext(True)
        layer_folder.addParmTemplate(version_parm)

        # Every row: show the layer's file in the OS file browser. The
        # ref is re-read from the node, not baked into the callback.
        reveal_parm = hou.ButtonParmTemplate(
            f'{safe_name}_reveal',
            'Inspect',
            script_callback=(
                'import tumblepipe.pipe.houdini.lops.import_shot as '
                f'th_node; th_node.reveal_layer(kwargs, {i})'
            ),
            script_callback_language=hou.scriptLanguage.Python,
        )
        reveal_parm.setHelp("Show this layer's file in the file browser")
        if inspectable:
            reveal_parm.setJoinWithNext(True)
        layer_folder.addParmTemplate(reveal_parm)

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


def resolve_row_file(ref: str, pinned: bool) -> Path | None:
    """The file a Layer Stack row's ref composes (its own ref, in the node's
    latest/pinned mode), or None if it resolves to no file on disk."""
    with resolver.latest_mode(not pinned):
        resolved = resolver.try_resolve_entity_uri(ref)
    if not resolved:
        return None
    path = Path(resolved)
    return path if path.exists() else None


def reveal_layer(native, index: int, pinned: bool):
    """A Layer Stack row's Inspect button: show that row's layer file in the
    OS file browser, or say why there is nothing to show."""
    ref = native.node('import').parm(f'filepath{index + 1}').eval()
    path = resolve_row_file(ref, pinned)
    if path is None:
        hou.ui.displayMessage(
            f"{ref} has no file on disk to show.",
            severity=hou.severityType.Warning,
        )
        return
    desktop.reveal_in_file_browser(path)


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
