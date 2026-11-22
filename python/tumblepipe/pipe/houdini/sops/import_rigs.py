from pathlib import Path

import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.config.entities import is_terminal_entity
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.asset_rows import AssetRows
from tumblepipe.pipe.houdini.sops import import_rig
from tumblepipe.config.channels import CHANNEL_KEY, list_channels
from tumblepipe.pipe.houdini import prim_metadata
from tumblepipe.pipe.paths import get_rig_export_path
from tumblepipe.pipe.usd.instances import copies_are_animated


def has_rig_export(asset_uri: Uri, channel_name: str = 'default') -> bool:
    """Whether ``asset_uri`` has published a rig in ``channel_name``. A shot
    imports props and sets that never get one, so the animation template
    adds a row only for an asset that has.

    A SOP rig: when the newest rig is a LOP rig (``import_rig.is_lop_rig``)
    the SOP rig nodes cannot load it, so the asset counts as having none
    here and is brought in with th::import_layer instead."""
    version_name = import_rig.latest_rig_version(asset_uri, channel_name)
    if version_name is None:
        return False
    return not import_rig.is_lop_rig(asset_uri, channel_name, version_name)


def rig_channel(asset_uri: Uri, preferred: str) -> str | None:
    """The channel to import ``asset_uri``'s rig from: ``preferred`` (the
    channel the shot imports the asset in) when it has a rig, else its
    first channel that has one; None when no channel has."""
    channel_names = list_channels(asset_uri)
    if preferred in channel_names:
        channel_names = [preferred] + [
            name for name in channel_names if name != preferred
        ]
    return next(
        (name for name in channel_names if has_rig_export(asset_uri, name)),
        None
    )


def discover_rig_imports(stage, animated=None):
    """``(rows, unrigged)`` for the rigs ``stage`` animates.

    Rows are ``(asset_uri, channel, 'latest', instances)``, one per asset
    whose prims are tagged animatable (prim_metadata.ANIMATABLE_KEY), with
    a row instance per tagged prim. ``unrigged`` names the animatable
    assets with no rig published, which get no row. A prim imported
    before the tag existed is judged by ``animated`` — the rule the import
    nodes stamp with, copies_are_animated by default — until its import
    runs again.
    """
    if stage is None:
        return [], []
    if animated is None:
        animated = copies_are_animated
    verdicts = {}

    def untagged(asset_uri):
        if asset_uri not in verdicts:
            verdicts[asset_uri] = animated(asset_uri)
        return verdicts[asset_uri]

    counts, channels = {}, {}
    for asset_info in prim_metadata.list_animatable_assets(
        stage.GetPseudoRoot(), untagged
    ):
        metadata = asset_info['metadata']
        asset_uri = Uri.parse(metadata['uri'])
        counts[asset_uri] = counts.get(asset_uri, 0) + 1
        channels.setdefault(asset_uri, metadata[CHANNEL_KEY])

    rows, unrigged = [], []
    for asset_uri in sorted(counts, key=str):
        channel = rig_channel(asset_uri, channels[asset_uri])
        if channel is None:
            unrigged.append(asset_uri)
            continue
        rows.append((asset_uri, channel, 'latest', counts[asset_uri]))
    return rows, unrigged


def keep_row_choices(rows, current):
    """Discovered ``rows`` with the artist's choices kept: an asset in
    ``current`` ({asset_uri: (channel, version)}, the rows already on the
    node) keeps that channel and version; Instances always come from the
    stage."""
    return [
        (asset_uri, *current.get(asset_uri, (channel, version)), instances)
        for asset_uri, channel, version, instances in rows
    ]


def split_unpublished(rig_imports):
    """``(to_import, unpublished)``: the rows with instances to import, and
    the assets of the 'latest' rows that have no rig published.

    Those rows are skipped and named in the node comment, not imported as
    a dead character: the animation template before migration v16 added a
    row for every asset in the shot (Magnus). A pinned version still has to
    exist, so its row stays in and fails loudly on import.
    """
    to_import, unpublished = [], []
    for row in rig_imports:
        asset_uri, channel, version, instances = row
        if instances <= 0:
            continue
        if version == 'latest' and not has_rig_export(asset_uri, channel):
            unpublished.append(asset_uri)
        else:
            to_import.append(row)
    return to_import, unpublished


class ImportRigs(AssetRows):
    """One rig import per multiparm row (see AssetRows): the row's asset's
    published rig, in its channel, at its version."""

    COUNT_PARM = 'rig_imports'
    VERSION_TOKENS = ('latest',)

    def _offers(self, asset_uri: Uri) -> bool:
        """Only real assets: the template's empty categories list as leaves
        too, and have no rig to import."""
        return is_terminal_entity(api.config, asset_uri)

    def _version_folder(self, asset_uri: Uri, channel_name: str) -> Path:
        return get_rig_export_path(asset_uri, channel_name)

    def _latest_version(self, asset_uri: Uri, channel_name: str) -> str | None:
        return import_rig.latest_rig_version(asset_uri, channel_name)

    def set_rig_imports(self, rig_imports: list[tuple[Uri, str, str, int]]):
        """Set rig imports from list of (asset_uri, channel, version, instances) tuples."""
        self.parm('rig_imports').set(0)
        for asset_uri, channel, version, instances in rig_imports:
            if instances == 0: continue
            index = self.parm('rig_imports').eval() + 1
            self.parm('rig_imports').set(index)
            self.set_entity_uri(index, asset_uri)
            self.set_channel_name(index, channel)
            self.set_version_name(index, version)
            self.set_instances(index, instances)
    
    def get_stage(self):
        """The stage Discover reads: the Stage parm's LOP, or with it
        empty, the stage fed into the th::animate this node sits in."""
        stage_path = self.parm('stage_path').eval()
        if len(stage_path) == 0:
            stage = import_rig._shot_stage(self.native())
            if stage is None:
                raise self._error(
                    'not inside a th::animate, so there is no shot stage to '
                    'discover rigs in. Set Stage to a LOP node.'
                )
            return stage
        lop_node = self.native().node(stage_path)
        if lop_node is None or not isinstance(lop_node, hou.LopNode):
            raise self._error(f"Stage '{stage_path}' is not a LOP node")
        stage = lop_node.stage()
        if stage is None:
            raise self._error(f"Stage '{stage_path}' has no stage: {lop_node.errors()}")
        return stage

    def discover(self):
        """Rows from the stage's animatable prims (discover_rig_imports),
        then Import. A row the stage still animates keeps its channel and
        version; the rest of the rows go."""
        rows, unrigged = discover_rig_imports(self.get_stage())
        current = {}
        for index in range(1, self.parm('rig_imports').eval() + 1):
            asset_uri_raw = self.parm(f'entity{index}').eval()
            if len(asset_uri_raw) == 0:
                continue
            current[Uri.parse(asset_uri_raw)] = (
                self.parm(f'channel{index}').eval(),
                self.parm(f'version{index}').eval() or 'latest',
            )
        self.set_rig_imports(keep_row_choices(rows, current))
        self.execute(unrigged)

    def execute(self, unrigged=()):
        """Import every row. ``unrigged``: animatable assets Discover found
        with no rig, named in the node comment."""
        with self._rows_read_once():
            self._execute(unrigged)

    def _execute(self, unrigged):

        # Update labels for all entries
        count = self.parm('rig_imports').eval()
        for index in range(1, count + 1):
            self._update_labels(index)

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        ns.clear_network(dive_node, output_node)

        # Parameters
        active_imports, unpublished = split_unpublished(self.get_row_imports())
        unpublished_note = ''.join(
            f'\n{label}: ' + ', '.join(uri.segments[-1] for uri in uris)
            for label, uris in (
                ('No rig published', unpublished),
                ('Animatable, no rig published', unrigged),
            )
            if uris
        )

        # Check if any rigs to import
        if not active_imports:
            ns.set_node_comment(
                context, f"Bypassed: No rigs configured{unpublished_note}")
            context.bypass(True)
            return
        # Undo a bypass left by an earlier empty run, or the node reports
        # "Imported" while passing nothing through.
        ns.clear_auto_bypass(context)

        # Build asset nodes
        prev_node = None
        for asset_uri, channel, version, instances in active_imports:

            # Create node name from URI segments (include channel for uniqueness)
            uri_name = '_'.join(asset_uri.segments[1:])
            node_name = f'{uri_name}_{channel}_import' if channel != 'default' else f'{uri_name}_import'

            # Import the rig
            rig_node = import_rig.create(dive_node, node_name)
            rig_node.set_entity_uri(asset_uri)
            rig_node.parm('channel').set(channel)
            rig_node.set_instances(instances)
            # Honour the selected version; 'latest' auto-resolves on execute.
            if version == 'latest':
                rig_node.latest()
            else:
                rig_node.set_version_name(version)
            rig_node.execute()

            # Connect the rig
            if prev_node is not None:
                ns.connect_next(prev_node, rig_node.native())
            prev_node = rig_node.native()

        # Connect to output
        if prev_node is not None:
            ns.connect_next(prev_node, output_node)

        # Layout the nodes
        dive_node.layoutChildren()

        # Set success comment
        rig_count = len(active_imports)
        ns.set_node_comment(
            context,
            f"Imported: {rig_count} rig{'s' if rig_count != 1 else ''}"
            f"{unpublished_note}",
        )

def create(scene, name):
    return ns.create_node(scene, name, ImportRigs, 'import_rigs', 'Sop')

def execute(kwargs):
    raw_node = kwargs['node']
    node = ImportRigs(raw_node)
    try:
        node.execute()
    finally:
        import_rig._refresh_ghost(raw_node)

def discover(kwargs):
    """The Discover button: rows from the stage's animatable prims."""
    node = ImportRigs(kwargs['node'])
    try:
        with ns.node_errors_as_message('Discover Rigs'):
            node.discover()
    finally:
        import_rig._refresh_ghost(kwargs['node'])

def select(kwargs):
    """A row's Entity button: pick that row's asset."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    index = kwargs['parm'].multiParmInstanceIndices()[0]
    node = ImportRigs(kwargs['node'])
    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=False,
        current_selection=node.parm(f'entity{index}').eval(),
        title="Select Asset",
        parent=hou.qt.mainWindow()
    )
    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm(f'entity{index}').set(selected_uri)
            node._update_labels(index)