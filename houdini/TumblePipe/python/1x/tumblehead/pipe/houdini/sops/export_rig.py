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

api = default_client()


class ExportRig(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        return [entity.uri for entity in asset_entities]

    def get_asset_uri(self) -> Uri | None:
        asset_uris = self.list_asset_uris()
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm('asset').eval()
        if len(asset_uri_raw) == 0: return asset_uris[0]
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in asset_uris: return None
        return asset_uri

    def set_asset_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('asset').set(str(asset_uri))

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
        asset_uri = self.get_asset_uri()
        export_path = get_rig_export_path(asset_uri)
        version_path = get_next_version_path(export_path)
        version_name = version_path.name
        timestamp = dt.datetime.now().isoformat()

        # Prepare rig export
        rig_file_name = '_'.join(asset_uri.segments[1:] + ['rig', version_name]) + '.bgeo.sc'
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
                uri = str(asset_uri),
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

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return

    # Set the default values from context
    node.set_asset_uri(context.entity_uri)

def execute():
    raw_node = hou.pwd()
    node = ExportRig(raw_node)
    node.execute()