import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.util import result
from tumblepipe.config.entities import is_terminal_entity
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.sops import import_rig
from tumblepipe.config.variants import list_variants
from tumblepipe.pipe.paths import list_version_paths

def _clear_scene(dive_node, output_node):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than inputs and outputs
    for node in dive_node.children():
        if node.name() == output_node.name(): continue
        node.destroy()

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _insert(data, path, value):
    for key in path[:-1]:
        data = data.setdefault(key, {})
    data[path[-1]] = value

class ImportRigs(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        with api.config.coherent():
            asset_uris = api.config.list_entity_uris(
                filter=Uri.parse_unsafe('entity:/assets'),
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
            other_uri = Uri.parse_unsafe(other_uri_raw)
            if other_uri in all_uris:
                used_uris.add(other_uri)
        return [uri for uri in all_uris if uri not in used_uris]

    def get_entity_uri(self, index: int) -> Uri | None:
        asset_uris = self.list_entity_uris(index)
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm(f'entity{index}').eval()
        if len(asset_uri_raw) == 0:
            asset_uri = asset_uris[0]
            self.parm(f'entity{index}').set(str(asset_uri))
            return asset_uri
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in self.list_asset_uris():
            asset_uri = asset_uris[0]
            self.parm(f'entity{index}').set(str(asset_uri))
        return asset_uri

    def get_instances(self, index: int) -> int:
        return self.parm(f'instances{index}').eval()

    def list_variant_names(self, index: int) -> list[str]:
        """List available variant names for the asset at this index."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return ['default']
        return list_variants(asset_uri)

    def get_variant_name(self, index: int) -> str:
        """Get selected variant name for this index, defaults to 'default'."""
        variant_names = self.list_variant_names(index)
        variant_name = self.parm(f'variant{index}').eval()
        if not variant_name or variant_name not in variant_names:
            return 'default'
        return variant_name

    def set_variant_name(self, index: int, variant_name: str):
        """Set variant name for this index."""
        self.parm(f'variant{index}').set(variant_name)

    def get_rig_imports(self) -> list[tuple[Uri, str, str, int]]:
        """Returns list of (asset_uri, variant, version, instances) for all rig imports."""
        rig_imports = []
        count = self.parm('rig_imports').eval()
        for index in range(1, count + 1):
            asset_uri = self.get_entity_uri(index)
            if asset_uri is None: continue
            variant = self.get_variant_name(index)
            version = self.get_version_name(index)
            instances = self.get_instances(index)
            rig_imports.append((asset_uri, variant, version, instances))
        return rig_imports

    def set_entity_uri(self, index: int, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm(f'entity{index}').set(str(asset_uri))

    def set_instances(self, index: int, instances: int):
        self.parm(f'instances{index}').set(instances)

    def list_version_names(self, index: int) -> list[str]:
        """List available versions for the rig at this index."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return ['latest']
        variant_name = self.get_variant_name(index)
        export_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / variant_name / 'rig'
        asset_path = api.storage.resolve(export_uri)
        version_paths = list_version_paths(asset_path)
        version_names = [vp.name for vp in version_paths]
        return ['latest'] + version_names

    def get_version_name(self, index: int) -> str:
        """Get selected version name for this index. Default is 'latest'."""
        version_name = self.parm(f'version{index}').eval()
        if len(version_name) == 0:
            return 'latest'
        return version_name

    def set_version_name(self, index: int, version_name: str):
        """Set version name for this index."""
        self.parm(f'version{index}').set(version_name)

    def _update_labels(self, index: int):
        """Update label parameters for the given index."""
        entity_uri = self.get_entity_uri(index)
        if entity_uri:
            self.parm(f'entity_label{index}').set(str(entity_uri))
        else:
            self.parm(f'entity_label{index}').set('none')

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name(index)
        if version_name == 'latest':
            version_names = self.list_version_names(index)
            actual_versions = [v for v in version_names if v != 'latest']
            if actual_versions:
                version_name = actual_versions[-1]
        self.parm(f'version_label{index}').set(version_name)

    def set_rig_imports(self, rig_imports: list[tuple[Uri, str, str, int]]):
        """Set rig imports from list of (asset_uri, variant, version, instances) tuples."""
        self.parm('rig_imports').set(0)
        for asset_uri, variant, version, instances in rig_imports:
            if instances == 0: continue
            index = self.parm('rig_imports').eval() + 1
            self.parm('rig_imports').set(index)
            self.set_entity_uri(index, asset_uri)
            self.set_variant_name(index, variant)
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
        _clear_scene(dive_node, output_node)

        # Parameters
        rig_imports = self.get_rig_imports()

        # Check if any rigs to import
        active_imports = [
            (uri, var, ver, inst)
            for uri, var, ver, inst in rig_imports if inst > 0
        ]
        if not active_imports:
            ns.set_node_comment(context, "Bypassed: No rigs configured")
            context.bypass(True)
            return result.Value(None)

        # Build asset nodes
        prev_node = None
        for asset_uri, variant, version, instances in rig_imports:
            if instances == 0: continue

            # Create node name from URI segments (include variant for uniqueness)
            uri_name = '_'.join(asset_uri.segments[1:])
            node_name = f'{uri_name}_{variant}_import' if variant != 'default' else f'{uri_name}_import'

            # Import the rig
            rig_node = import_rig.create(dive_node, node_name)
            rig_node.set_entity_uri(asset_uri)
            rig_node.parm('variant').set(variant)  # Set variant on import_rig node
            rig_node.set_instances(instances)
            # Honour the selected version; 'latest' auto-resolves on execute.
            if version == 'latest':
                rig_node.latest()
            else:
                rig_node.set_version_name(version)
            rig_node.execute()

            # Connect the rig
            if prev_node is not None:
                _connect(prev_node, rig_node.native())
            prev_node = rig_node.native()

        # Connect to output
        if prev_node is not None:
            _connect(prev_node, output_node)

        # Layout the nodes
        dive_node.layoutChildren()

        # Set success comment
        rig_count = len(active_imports)
        ns.set_node_comment(context, f"Imported: {rig_count} rig{'s' if rig_count != 1 else ''}")

        # Done
        return result.Value(None)

def create(scene, name):
    return ns.create_node(scene, name, ImportRigs, 'import_rigs', 'Sop')

def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ImportRigs(raw_node)
    node.execute()

def select(index: int):
    """HDA button callback to open entity selector dialog."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportRigs(raw_node)

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