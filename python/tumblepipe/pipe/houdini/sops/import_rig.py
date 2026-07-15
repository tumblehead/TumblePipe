import hou

from tumblepipe.api import path_str, api
from tumblepipe.util.uri import Uri
from tumblepipe.config.entities import is_terminal_entity
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.sops import rename_packed
from tumblepipe.pipe.paths import list_version_paths

def _clear_scene(dive_node, output_node):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than inputs and outputs
    for node in dive_node.children():
        if node.name() == output_node.name(): continue
        node.destroy()

class ImportRig(EntityNode):

    # import_rig only ever addresses assets, so 'from_context' inside a
    # shot workfile resolves to nothing rather than to the shot.
    ENTITY_CONTEXTS = ('assets',)

    def __init__(self, native):
        super().__init__(native)

    def list_entity_uris(self) -> list[str]:
        return ['from_context'] + [str(uri) for uri in self.list_asset_uris()]

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

    def list_version_names(self):
        """List available versions including 'latest'."""
        asset_uri = self.get_entity_uri()
        if asset_uri is None:
            return ['latest']
        variant_name = self.get_variant_name()
        export_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / variant_name / 'rig'
        asset_path = api.storage.resolve(export_uri)
        version_paths = list_version_paths(asset_path)
        version_names = [version_path.name for version_path in version_paths]
        return ['latest'] + version_names

    def get_instances(self):
        return self.parm('instances').eval()

    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)
    
    def set_instances(self, instances):
        self.parm('instances').set(instances)

    def _update_labels(self):
        """Update label parameters to show current entity selection."""
        entity_raw = self.parm('entity').eval()
        entity_uri = self.get_entity_uri()
        if entity_raw == 'from_context':
            self.parm('entity_label').set(
                f'from_context: {entity_uri}' if entity_uri else 'from_context: none'
            )
        elif entity_uri:
            self.parm('entity_label').set(str(entity_uri))
        else:
            self.parm('entity_label').set('none')

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name()
        if version_name == 'latest':
            version_names = self.list_version_names()
            actual_versions = [v for v in version_names if v != 'latest']
            if actual_versions:
                version_name = actual_versions[-1]
        self.parm('version_label').set(version_name)

    def latest(self):
        """Set version to 'latest' (auto-resolves to newest on execute)."""
        self.parm('version').set('latest')
    
    def execute(self):
        self._update_labels()

        # Nodes
        context = self.native()
        import_node = context.node('import')
        bypass_node = context.node('bypass')
        dive_node = context.node('dive')
        output_node = dive_node.node('output')

        # Clear scene
        _clear_scene(dive_node, output_node)

        # Parameters
        asset_uri = self.get_entity_uri()
        if asset_uri is None:
            ns.set_node_comment(context, "Bypassed: No asset selected")
            bypass_node.parm('input').set(0)
            return
        variant_name = self.get_variant_name()
        version_name = self.get_version_name()
        instances = self.get_instances()

        # Resolve 'latest' to actual version
        if version_name == 'latest':
            version_names = self.list_version_names()
            actual_versions = [v for v in version_names if v != 'latest']
            if not actual_versions:
                ns.set_node_comment(context, "Bypassed: No versions available")
                bypass_node.parm('input').set(0)
                return
            version_name = actual_versions[-1]  # Get last (newest)

        # Create filename from URI segments
        uri_name = '_'.join(asset_uri.segments[1:])

        # Paths (includes variant)
        export_uri = Uri.parse_unsafe('export:/') / asset_uri.segments / variant_name / 'rig' / version_name
        version_path = api.storage.resolve(export_uri)
        file_path = version_path / f'{uri_name}_rig_{version_name}.bgeo.sc'
        if not file_path.exists():
            ns.set_node_comment(context, "Bypassed: Rig file not found")
            bypass_node.parm('input').set(0)
            return

        # Update version_label with resolved version
        self.parm('version_label').set(version_name)
        
        # Load rig
        import_node.parm('file').set(path_str(file_path))
        import_node.parm('reload').pressButton()
        
        # Create instances
        prev_node = dive_node.indirectInputs()[0]
        source_node = dive_node.indirectInputs()[1]
        base_name = asset_uri.segments[-1]  # Last segment is the asset name
        prim_base_path = '/'.join(asset_uri.segments[1:])  # e.g., "char/hero"
        prim_parent_path = '/'.join(asset_uri.segments[1:-1])  # e.g., "char"
        for instance_index in range(instances):
            anchor_node = source_node
            instance_name = base_name if instances == 1 else f'{base_name}{instance_index}'

            # Rename the packed primitives if necessary
            if instances > 1:
                rename_node = rename_packed.create(dive_node, f'{uri_name}_{instance_name}_rename')
                rename_node.set_from_path(f'/{prim_base_path}/*')
                rename_node.set_to_path(f'/{prim_parent_path}/{instance_name}/*')
                rename_node.native().setInput(0, anchor_node)
                anchor_node = rename_node.native()

            # Create the add character to scene node
            add_node = dive_node.createNode('apex::sceneaddcharacter', f'{uri_name}_{instance_name}_add')
            add_node.parm('charactername').deleteAllKeyframes()
            add_node.parm('charactername').set(instance_name)

            # Connect the nodes
            add_node.setInput(0, prev_node)
            add_node.setInput(1, anchor_node)
            prev_node = add_node
        
        # Connect to output
        output_node.setInput(0, prev_node)

        # Layout the dive nodes
        dive_node.layoutChildren()

        # Disable bypass
        bypass_node.parm('input').set(1)

        # Set success comment
        asset_name = asset_uri.segments[-1]
        ns.set_node_comment(context, f"Imported: {asset_name}\n{version_name}")

def create(scene, name):
    return ns.create_node(scene, name, ImportRig, 'import_rig', 'Sop')

def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_DIVE)

def on_created(raw_node):
    set_style(raw_node)
    node = ImportRig(raw_node)
    node._update_labels()

def latest():
    raw_node = hou.pwd()
    node = ImportRig(raw_node)
    node.latest()

def execute():
    raw_node = hou.pwd()
    node = ImportRig(raw_node)
    node.execute()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportRig(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=True,
        current_selection=node.parm('entity').eval(),
        title="Select Asset",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node.execute()