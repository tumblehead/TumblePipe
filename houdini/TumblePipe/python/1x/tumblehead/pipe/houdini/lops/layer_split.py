from tempfile import TemporaryDirectory
from pathlib import Path
import subprocess
import platform
import shutil

import hou

from tumblehead.api import path_str, fix_path, default_client
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.variants import get_entity_type
from tumblehead.config.timeline import get_frame_range as config_get_frame_range, get_fps, FrameRange
from tumblehead.pipe.houdini import nodes as ns
from tumblehead.pipe.paths import (
    get_workfile_context,
    next_shared_export_path,
    latest_shared_export_path,
    get_shared_layer_file_name
)
from tumblehead.pipe.context import save_export_context

api = default_client()


class LayerSplit(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_entity_uris(self) -> list[str]:
        shot_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        uris = (
            [entity.uri for entity in shot_entities] +
            [entity.uri for entity in asset_entities]
        )
        return ['from_context'] + [str(uri) for uri in uris]

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
        entity_uri_raw = self.parm('entity').eval()
        if entity_uri_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            # Only accept entity URIs, not group URIs
            if context.entity_uri.purpose != 'entity':
                return None
            return context.entity_uri
        # From settings
        entity_uris = self.list_entity_uris()
        if len(entity_uris) <= 1:  # Only 'from_context' means no real URIs
            return None
        if len(entity_uri_raw) == 0:
            return Uri.parse_unsafe(entity_uris[1])  # Skip 'from_context'
        if entity_uri_raw not in entity_uris:  # Compare strings
            return None
        return Uri.parse_unsafe(entity_uri_raw)

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

    def get_frame_range_source(self) -> str:
        return self.parm('frame_range').eval()

    def get_frame_range(self) -> tuple[FrameRange, int] | None:
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_context':
                entity_uri = self.get_entity_uri()
                if entity_uri is None:
                    return None
                frame_range = config_get_frame_range(entity_uri)
                if frame_range is None:
                    return None
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range source: {frame_range_source}'

    def set_entity_uri(self, entity_uri: Uri):
        entity_uris = self.list_entity_uris()
        if str(entity_uri) not in entity_uris:  # Compare strings
            return
        self.parm('entity').set(str(entity_uri))

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names:
            return
        self.parm('department').set(department_name)

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        entity_raw = self.parm('entity').eval()
        if entity_raw == 'from_context':
            entity_uri = self.get_entity_uri()
            if entity_uri:
                self.parm('entity_label').set(f'from_context: {entity_uri}')
            else:
                self.parm('entity_label').set('from_context: none')
        else:
            self.parm('entity_label').set(entity_raw)

        department_name = self.get_department_name()
        if department_name:
            self.parm('department_label').set(department_name)
        else:
            self.parm('department_label').set('')

    def _initialize(self):
        """Initialize node with defaults from workfile context and update labels."""
        file_path = Path(hou.hipFile.path())
        context = get_workfile_context(file_path)

        if context is not None:
            # Set from context
            self.set_entity_uri(context.entity_uri)
            if context.department_name:
                self.set_department_name(context.department_name)

        # Update labels to show resolved values
        self._update_labels()

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
        open_process_dialog_for_node(self, dialog_title="Export Shared Layer")

    def _execute(self):
        """Internal execution - export the shared layer to _shared path."""
        self._update_labels()
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            raise ValueError('No valid entity URI')

        department_name = self.get_department_name()
        if department_name is None:
            raise ValueError('No valid department name')

        # Get frame range from settings or entity configuration
        frame_range_result = self.get_frame_range()
        if frame_range_result is None:
            raise ValueError(f'No frame range defined for {entity_uri}')
        frame_range, frame_step = frame_range_result

        # Get full range (including roll)
        full_range = frame_range.full_range()

        # Get fps (default to 24 if not set)
        fps = get_fps(entity_uri)
        if fps is None:
            fps = 24

        # Get next version path for shared export
        export_path = next_shared_export_path(entity_uri, department_name)
        version_name = export_path.name

        # Get export filename
        file_name = get_shared_layer_file_name(
            entity_uri,
            department_name,
            version_name
        )

        # Export to temp first, then copy to network
        root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Set fps metadata
            self.parm('set_metadata_fps').set(fps)

            # Set frame range parameters
            self.parm('export_f1').deleteAllKeyframes()
            self.parm('export_f2').deleteAllKeyframes()
            self.parm('export_f1').set(full_range.first_frame)
            self.parm('export_f2').set(full_range.last_frame)
            self.parm('export_f3').set(frame_step)

            # Configure and execute export to temp
            self.parm('export_lopoutput').set(path_str(temp_path / file_name))
            self.parm('export_execute').pressButton()

            # Save context file to temp
            save_export_context(temp_path, entity_uri, department_name, version_name, variant_name='_shared')

            # Copy all files from temp to export path
            export_path.mkdir(parents=True, exist_ok=True)
            for temp_item_path in temp_path.iterdir():
                output_item_path = export_path / temp_item_path.name
                if temp_item_path.is_file():
                    shutil.copy(temp_item_path, output_item_path)
                if temp_item_path.is_dir():
                    shutil.copytree(temp_item_path, output_item_path)

        print(f'Exported shared layer to: {export_path / file_name}')

        # Generate entity URI for downstream reference
        # The custom USD resolver handles dynamic version lookup at runtime,
        # eliminating the need for copy-based "latest" directories
        entity_uri_str = f"{entity_uri}?dept={department_name}&variant=_shared"
        print(f'Entity URI for sublayer reference: {entity_uri_str}')

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

    # Validate node type
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('layer_split', 'Lop')
    if raw_node_type != node_type:
        return

    node = LayerSplit(raw_node)
    node._initialize()


def execute():
    raw_node = hou.pwd()
    node = LayerSplit(raw_node)
    node.execute()


def open_location():
    raw_node = hou.pwd()
    node = LayerSplit(raw_node)
    node.open_location()


def select():
    """HDA button callback to open entity selector dialog."""
    from tumblehead.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = LayerSplit(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='both',
        include_from_context=True,
        current_selection=node.parm('entity').eval(),
        title="Select Entity",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node._update_labels()


def validate():
    """HDA button callback to run stage validation."""
    from tumblehead.pipe.houdini.validators import validate_stage

    raw_node = hou.pwd()
    stage_node = raw_node.node('IN_stage')

    if stage_node is None:
        hou.ui.displayMessage(
            "No stage input connected.",
            severity=hou.severityType.Warning
        )
        return

    stage = stage_node.stage()
    if stage is None:
        hou.ui.displayMessage(
            "No stage available for validation.",
            severity=hou.severityType.Warning
        )
        return

    root = stage.GetPseudoRoot()
    result = validate_stage(root)

    if result.passed:
        hou.ui.displayMessage(
            "Validation passed - no issues found.",
            severity=hou.severityType.Message,
            title="Validation Passed"
        )
    else:
        hou.ui.displayMessage(
            result.format_message(),
            severity=hou.severityType.Error,
            title="Validation Failed"
        )


def update_labels():
    """HDA callback to update label parameters when entity/department changes."""
    raw_node = hou.pwd()
    node = LayerSplit(raw_node)
    node._update_labels()