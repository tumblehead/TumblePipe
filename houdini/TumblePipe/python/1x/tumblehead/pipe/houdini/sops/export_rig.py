from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import (
    get_user_name,
    path_str,
    default_client
)
from tumblehead.util.uri import Uri
from tumblehead.util.io import store_json
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    get_next_version_path,
    get_rig_export_path,
    get_workfile_context
)
from tumblehead.config.variants import list_variants

api = default_client()


class ExportRig(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_entity_uris(self) -> list[str]:
        """List entity URIs as strings, with 'from_context' as first option."""
        asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        uris = [str(entity.uri) for entity in asset_entities]
        return ['from_context'] + uris

    def get_entity_uri(self) -> Uri | None:
        entity_uri_raw = self.parm('entity').eval()
        if entity_uri_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            # Only accept entity URIs, not group URIs
            if context.entity_uri.purpose != 'entity':
                return None
            # Verify it's an asset entity
            if context.entity_uri.segments[0] != 'assets':
                return None
            return context.entity_uri
        # From settings
        entity_uris = self.list_entity_uris()
        if len(entity_uris) <= 1:  # Only 'from_context' means no real URIs
            return None
        if len(entity_uri_raw) == 0:
            return Uri.parse_unsafe(entity_uris[1])  # Skip 'from_context'
        if entity_uri_raw not in entity_uris:
            return None
        return Uri.parse_unsafe(entity_uri_raw)

    def set_entity_uri(self, entity_uri: Uri):
        entity_uris = self.list_entity_uris()
        uri_str = str(entity_uri)
        if uri_str not in entity_uris:
            return
        self.parm('entity').set(uri_str)

    def get_department_name(self) -> str:
        return 'rig'

    def list_variant_names(self) -> list[str]:
        """List available variant names for current asset."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['default']
        return list_variants(entity_uri)

    def get_variant_name(self) -> str:
        """Get selected variant name, defaults to 'default'."""
        variant_names = self.list_variant_names()
        variant_name = self.parm('variant').eval()
        if not variant_name or variant_name not in variant_names:
            return 'default'
        return variant_name

    def execute(self, force_local: bool = False):
        """
        Execute export.

        If force_local=True, executes directly (used by ProcessDialog callbacks).
        Otherwise, opens the ProcessDialog for task selection and execution.
        """
        if force_local:
            return self._execute()
        # Open ProcessDialog
        from tumblehead.pipe.houdini.ui.project_browser.utils.process_executor import (
            open_process_dialog_for_node
        )
        open_process_dialog_for_node(self, dialog_title="Export Rig")

    def _execute(self):
        """Internal execution - export rig geometry."""
        # Nodes
        context = self.native()
        export_node = context.node('export')

        # Parameters
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        export_path = get_rig_export_path(entity_uri, variant_name)
        version_path = get_next_version_path(export_path)
        version_name = version_path.name
        timestamp = dt.datetime.now().isoformat()

        # Prepare rig export
        rig_file_name = '_'.join(entity_uri.segments[1:] + ['rig', version_name]) + '.bgeo.sc'
        output_file_path = version_path / rig_file_name
        export_node.parm('file').set(path_str(output_file_path))

        # Export rig
        version_path.mkdir(parents=True, exist_ok=True)
        export_node.parm('execute').pressButton()

        # Write context
        context_path = version_path / 'context.json'
        context = dict(
            inputs = [],
            outputs = [dict(
                uri = str(entity_uri),
                department = 'rig',
                version = version_name,
                timestamp = timestamp,
                user = get_user_name(),
                parameters = {}
            )]
        )
        store_json(context_path, context)

def create(scene, name):
    node_type = ns.find_node_type('export_rig', 'Sop')
    assert node_type is not None, 'Could not find export_rig node type'
    native = scene.node(name)
    if native is not None: return ExportRig(native)
    return ExportRig(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('export_rig', 'Sop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ExportRig(raw_node)

    # Parse scene file path - if no context, set first available asset
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None:
        entity_uris = node.list_entity_uris()
        if len(entity_uris) > 1:  # Skip 'from_context'
            node.set_entity_uri(Uri.parse_unsafe(entity_uris[1]))
        return

    # Default is 'from_context', which will resolve from workfile context
    # Only need to set explicitly if not an asset workfile
    if context.entity_uri.segments[0] != 'assets':
        entity_uris = node.list_entity_uris()
        if len(entity_uris) > 1:  # Skip 'from_context'
            node.set_entity_uri(Uri.parse_unsafe(entity_uris[1]))

def execute():
    raw_node = hou.pwd()
    node = ExportRig(raw_node)
    node.execute()