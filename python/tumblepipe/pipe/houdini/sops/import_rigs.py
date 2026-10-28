import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.config.entities import is_terminal_entity
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNodeError
from tumblepipe.pipe.houdini.sops import import_rig
from tumblepipe.config.channels import list_channels
from tumblepipe.pipe.paths import list_version_paths


def has_rig_export(asset_uri: Uri, channel_name: str = 'default') -> bool:
    """Whether ``asset_uri`` has published a rig in ``channel_name``. A shot
    imports props and sets that never get one, so the animation template
    adds a row only for an asset that has."""
    export_uri = Uri.parse('export:/') / asset_uri.segments / channel_name / 'rig'
    return len(list_version_paths(api.storage.resolve(export_uri))) > 0


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


class ImportRigs(ns.Node):
    """One rig import per multiparm row. Each row's asset, channel and
    version are explicit: a row resolves exactly what it names, or raises
    EntityNodeError — an empty row is unset, never the first asset."""

    def __init__(self, native):
        super().__init__(native)

    def _error(self, message: str) -> EntityNodeError:
        return EntityNodeError(f'{self.path()}: {message}')

    def list_asset_uris(self) -> list[Uri]:
        with api.config.coherent():
            asset_uris = api.config.list_entity_uris(
                filter=Uri.parse('entity:/assets'),
                closure=True
            )
            return [
                uri for uri in asset_uris
                if is_terminal_entity(api.config, uri)
            ]

    def list_entity_uris(self, index: int) -> list[Uri]:
        """List asset URIs excluding those already used by other indices."""
        all_uris = self.list_asset_uris()
        if len(all_uris) == 0: return []
        count = self.parm('rig_imports').eval()
        used_uris = set()
        for other_index in range(1, count + 1):
            if other_index == index: continue
            other_uri_raw = self.parm(f'entity{other_index}').eval()
            if len(other_uri_raw) == 0: continue
            other_uri = Uri.parse(other_uri_raw)
            if other_uri in all_uris:
                used_uris.add(other_uri)
        return [uri for uri in all_uris if uri not in used_uris]

    def get_entity_uri(self, index: int) -> Uri | None:
        """The row's asset; None for a row with no asset picked yet.
        Read-only: the channel/version menus call this."""
        asset_uri_raw = self.parm(f'entity{index}').eval()
        if len(asset_uri_raw) == 0: return None
        asset_uri = Uri.parse(asset_uri_raw)
        if asset_uri in self.list_entity_uris(index):
            return asset_uri
        if asset_uri in self.list_asset_uris():
            raise self._error(
                f'row {index}: {asset_uri} is already imported by another '
                'row. Remove one of the rows, or raise its Instances.'
            )
        raise self._error(
            f'row {index}: {asset_uri} is not an asset in the project. '
            "Pick one with the row's Entity button."
        )

    def require_entity_uri(self, index: int) -> Uri:
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            raise self._error(
                f"row {index} has no asset. Pick one with the row's Entity "
                'button, or remove the row.'
            )
        return asset_uri

    def get_instances(self, index: int) -> int:
        return self.parm(f'instances{index}').eval()

    def list_channel_names(self, index: int) -> list[str]:
        """The row's asset's channels, 'default' first; none while unset."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return []
        return list_channels(asset_uri)

    def get_channel_name(self, index: int) -> str:
        """The row's Channel, which must be a channel of the row's asset."""
        asset_uri = self.require_entity_uri(index)
        channel_names = list_channels(asset_uri)
        channel_name = self.parm(f'channel{index}').eval()
        if channel_name not in channel_names:
            raise self._error(
                f"row {index}: channel '{channel_name}' is not a channel of "
                f"{asset_uri} (listed: {', '.join(channel_names)})"
            )
        return channel_name

    def set_channel_name(self, index: int, channel_name: str):
        """Set channel name for this index."""
        self.parm(f'channel{index}').set(channel_name)

    def get_rig_imports(self) -> list[tuple[Uri, str, str, int]]:
        """Returns list of (asset_uri, channel, version, instances) for all rig imports."""
        rig_imports = []
        count = self.parm('rig_imports').eval()
        for index in range(1, count + 1):
            asset_uri = self.require_entity_uri(index)
            channel = self.get_channel_name(index)
            version = self.get_version_name(index)
            instances = self.get_instances(index)
            rig_imports.append((asset_uri, channel, version, instances))
        return rig_imports

    def set_entity_uri(self, index: int, asset_uri: Uri):
        if asset_uri not in self.list_entity_uris(index):
            raise self._error(
                f'row {index}: {asset_uri} is not an asset in the project, '
                'or another row already imports it'
            )
        self.parm(f'entity{index}').set(str(asset_uri))

    def set_instances(self, index: int, instances: int):
        self.parm(f'instances{index}').set(instances)

    def list_version_names(self, index: int) -> list[str]:
        """List available versions for the rig at this index."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return ['latest']
        channel_name = self.get_channel_name(index)
        export_uri = Uri.parse('export:/') / asset_uri.segments / channel_name / 'rig'
        asset_path = api.storage.resolve(export_uri)
        version_paths = list_version_paths(asset_path)
        version_names = [vp.name for vp in version_paths]
        return ['latest'] + version_names

    def get_version_name(self, index: int) -> str:
        """The row's version: 'latest' or a version name."""
        version_name = self.parm(f'version{index}').eval()
        if len(version_name) == 0:
            raise self._error(
                f"row {index}: version is empty. Pick a version, or 'latest'."
            )
        return version_name

    def set_version_name(self, index: int, version_name: str):
        """Set version name for this index."""
        self.parm(f'version{index}').set(version_name)

    def _update_labels(self, index: int):
        """Update label parameters for the given index."""
        self.parm(f'entity_label{index}').set(
            self.parm(f'entity{index}').eval() or 'none'
        )

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name(index)
        if version_name == 'latest':
            version_names = self.list_version_names(index)
            actual_versions = [v for v in version_names if v != 'latest']
            if actual_versions:
                version_name = actual_versions[-1]
        self.parm(f'version_label{index}').set(version_name)

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
    
    def execute(self):

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
        active_imports, unpublished = split_unpublished(self.get_rig_imports())
        unpublished_note = (
            '\nNo rig published: '
            + ', '.join(uri.segments[-1] for uri in unpublished)
            if unpublished else ''
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
    node.execute()

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