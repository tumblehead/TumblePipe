from pathlib import Path
import subprocess
import platform
import json
from datetime import datetime

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.variants import get_entity_type
from tumblehead.pipe.houdini import nodes as ns
from tumblehead.pipe.paths import (
    get_workfile_context,
    next_shared_export_path,
    latest_shared_export_path,
    get_shared_layer_file_name
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/layer_split')


def _save_context_file(export_path: Path, entity_uri: Uri, department_name: str):
    """Save context.json with export metadata."""
    context_path = export_path / 'context.json'
    context_data = {
        'entity_uri': str(entity_uri),
        'department': department_name,
        'variant': '_shared',
        'version': export_path.name,
        'timestamp': datetime.now().isoformat(),
        'user': api.user.name
    }
    with context_path.open('w') as f:
        json.dump(context_data, f, indent=2)


class LayerSplit(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_entity_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        return (
            [entity.uri for entity in shot_entities] +
            [entity.uri for entity in asset_entities]
        )

    def get_entity_type(self) -> str | None:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return None
        return get_entity_type(entity_uri)

    def list_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return []
        context_name = 'assets' if entity_type == 'asset' else 'shots'
        departments = list_departments(context_name, include_generated=False)
        return [dept.name for dept in departments]

    def get_entity_uri(self) -> Uri | None:
        entity_uris = self.list_entity_uris()
        if len(entity_uris) == 0:
            return None

        # Check entity source
        entity_source = self.parm('entity_source').eval()
        if entity_source == 0:
            # From context - parse from hip file path
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            return context.entity_uri

        # From settings - use entity parameter
        entity_uri_raw = self.parm('entity').eval()
        if len(entity_uri_raw) == 0:
            return entity_uris[0]
        entity_uri = Uri.parse_unsafe(entity_uri_raw)
        if entity_uri not in entity_uris:
            return None
        return entity_uri

    def get_department_name(self) -> str | None:
        department_names = self.list_department_names()
        if len(department_names) == 0:
            return None
        department_name = self.parm('department').eval()
        if len(department_name) == 0:
            return department_names[0]
        if department_name not in department_names:
            return None
        return department_name

    def set_entity_uri(self, entity_uri: Uri):
        entity_uris = self.list_entity_uris()
        if entity_uri not in entity_uris:
            return
        self.parm('entity').set(str(entity_uri))

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names:
            return
        self.parm('department').set(department_name)

    def set_entity_source(self, source: str):
        """Set entity source: 'from_context' (0) or 'from_settings' (1)."""
        if source == 'from_context':
            self.parm('entity_source').set(0)
        elif source == 'from_settings':
            self.parm('entity_source').set(1)

    def execute(self):
        """Export the shared layer to _shared path."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            raise ValueError('No valid entity URI')

        department_name = self.get_department_name()
        if department_name is None:
            raise ValueError('No valid department name')

        # Get next version path for shared export
        export_path = next_shared_export_path(entity_uri, department_name)
        version_name = export_path.name

        # Ensure export directory exists
        export_path.mkdir(parents=True, exist_ok=True)

        # Get export filename
        file_name = get_shared_layer_file_name(
            entity_uri,
            department_name,
            version_name
        )
        file_path = export_path / file_name

        # Get the export subnetwork and configure it
        export_subnet = self.native().node('export')
        if export_subnet is None:
            raise ValueError('Export subnetwork not found')

        export_node = export_subnet.node('usd_rop')
        if export_node is None:
            raise ValueError('USD ROP not found in export subnetwork')

        # Configure export node
        export_node.parm('lopoutput').set(path_str(file_path))

        # Execute export
        export_node.parm('execute').pressButton()

        # Save context file
        _save_context_file(export_path, entity_uri, department_name)

        print(f'Exported shared layer to: {file_path}')

    def open_location(self):
        """Open the latest shared export location in file browser."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return

        department_name = self.get_department_name()
        if department_name is None:
            return

        # Get latest shared export path
        export_path = latest_shared_export_path(entity_uri, department_name)
        if export_path is None or not export_path.exists():
            # Fall back to parent directory
            export_uri = (
                Uri.parse_unsafe('export:/') /
                entity_uri.segments /
                '_shared' /
                department_name
            )
            export_path = api.storage.resolve(export_uri)

        # Open in file browser
        if platform.system() == 'Windows':
            subprocess.run(['explorer', path_str(export_path)])
        elif platform.system() == 'Darwin':
            subprocess.run(['open', path_str(export_path)])
        else:
            subprocess.run(['xdg-open', path_str(export_path)])


def create(scene, name):
    node_type = ns.find_node_type('layer_split', 'Lop')
    assert node_type is not None, 'Could not find layer_split node type'
    native = scene.node(name)
    if native is not None:
        return LayerSplit(native)
    return LayerSplit(scene.createNode(node_type.name(), name))


def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)


def on_created(raw_node):
    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('layer_split', 'Lop')
    if raw_node_type != node_type:
        return
    node = LayerSplit(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None:
        return

    # Set the default values from context
    node.set_entity_uri(context.entity_uri)
    if context.department_name:
        node.set_department_name(context.department_name)


def execute():
    raw_node = hou.pwd()
    node = LayerSplit(raw_node)
    node.execute()


def open_location():
    raw_node = hou.pwd()
    node = LayerSplit(raw_node)
    node.open_location()
