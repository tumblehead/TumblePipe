from tempfile import TemporaryDirectory
from pathlib import Path
import logging

import hou

logger = logging.getLogger(__name__)

from tumblepipe.api import path_str, api
from tumblepipe.util.progress import report_progress
from tumblepipe.util.io import copy_version_contents
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import get_fps
from tumblepipe.pipe.houdini import nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode
from tumblepipe.pipe.paths import (
    SHARED_CHANNEL,
    get_shared_layer_file_name,
    latest_export_path,
    next_export_path,
)
from tumblepipe.pipe.context import save_export_context


class LayerSplit(FrameRangeNode, EntityNode):

    def list_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return ['from_context']
        context_name = 'assets' if entity_type == 'asset' else 'shots'
        departments = self.scoped_departments(
            context_name, include_generated=False,
        )
        return ['from_context'] + [dept.name for dept in departments]


    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        self.parm('entity_label').set(self._entity_label())

        self.parm('department_label').set(self._department_label())

    def _initialize(self):
        """Refresh the labels. Both 'entity' and 'department' keep their
        'from_context' defaults — baking the workfile's URI in here would
        pin the node to whichever entity it was born in."""
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
        from tumblepipe.pipe.houdini.ui.dialog_launcher import (
            open_process_dialog_for_node
        )
        open_process_dialog_for_node(self, dialog_title="Export Shared Layer")

    def _execute(self):
        """Internal execution - export the shared layer to _shared path."""
        self._update_labels()
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        frame_range = self.get_frame_range()
        frame_step = frame_range.step_size

        # Get full range (including roll)
        full_range = frame_range.full_range()

        logger.info(
            f"Starting shared layer export: uri={entity_uri}, dept={department_name}, "
            f"frames={full_range.first_frame}-{full_range.last_frame}, step={frame_step}"
        )

        fps = get_fps(entity_uri)
        if fps is None:
            raise self._error(
                f'no fps configured for {entity_uri} or the project'
            )

        # Get next version path for shared export
        export_path = next_export_path(entity_uri, SHARED_CHANNEL, department_name)
        version_name = export_path.name

        # Get export filename
        file_name = get_shared_layer_file_name(
            entity_uri,
            department_name,
            version_name
        )

        # Export to temp first, then copy to network
        root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
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
            report_progress(f"cooking frames {full_range.first_frame}-{full_range.last_frame}")
            self.parm('export_lopoutput').set(path_str(temp_path / file_name))
            self.parm('export_execute').pressButton()

            # Save context file to temp
            save_export_context(temp_path, entity_uri, department_name, version_name, channel_name='_shared')

            # Copy all files from temp to export path
            report_progress(f"copying {version_name} to server")
            # context.json last: it marks the version finished.
            copy_version_contents(temp_path, export_path)

        logger.info(
            f"Shared layer export completed: uri={entity_uri}, dept={department_name}, "
            f"version={version_name}, output={export_path / file_name}"
        )

        return version_name

        # Downstream references use the entity URI form
        # ({entity_uri}?dept={department_name}&channel=_shared); the custom
        # USD resolver handles dynamic version lookup at runtime, eliminating
        # the need for copy-based "latest" directories.

    def open_location(self):
        """Open the latest shared export location in file browser."""
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        export_path = latest_export_path(entity_uri, SHARED_CHANNEL, department_name)
        if export_path is None:
            raise self._error(
                f"{entity_uri} has no shared '{department_name}' export yet"
            )
        hou.ui.showInFileBrowser(path_str(export_path))


def create(scene, name):
    return ns.create_node(scene, name, LayerSplit, 'layer_split')


def on_created(raw_node):
    # Set node style
    ns.set_node_style(raw_node)

    # Validate node type
    raw_node_type = raw_node.type()
    node_type = ns.find_node_type('layer_split', 'Lop')
    if raw_node_type != node_type:
        return

    node = LayerSplit(raw_node)
    node._initialize()


def execute(kwargs):
    raw_node = kwargs['node']
    node = LayerSplit(raw_node)
    node.execute()


def open_location(kwargs):
    raw_node = kwargs['node']
    node = LayerSplit(raw_node)
    node.open_location()


def select(kwargs):
    """Entity button."""
    LayerSplit(kwargs['node']).pick_entity()


def validate(kwargs):
    """HDA button callback to run stage validation."""
    from tumblepipe.pipe.houdini.validators import validate_stage_for_department

    raw_node = kwargs['node']
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

    node = LayerSplit(raw_node)
    entity_uri = node.require_entity_uri()
    department = node.require_department_name()

    uri_str = str(entity_uri)
    entity_context = 'shots' if uri_str.startswith('entity:/shots/') else 'assets'

    root = stage.GetPseudoRoot()
    result = validate_stage_for_department(
        root, entity_context, department, {'entity_uri': uri_str}
    )

    if result.passed:
        # A warning does not fail validation, but saying "no issues found"
        # while holding a non-empty issue list is a lie — and the most common
        # warning is "No stage available for validation", i.e. validation could
        # not run at all.
        if result.warnings:
            hou.ui.displayMessage(
                result.format_message(),
                severity=hou.severityType.Warning,
                title="Validation Passed With Warnings"
            )
        else:
            hou.ui.displayMessage(
                "Validation passed - no issues found.",
                severity=hou.severityType.Message,
                title="Validation Passed"
            )
        return

    from tumblepipe.pipe.houdini.ui.validation_dialog import (
        ValidationConfirmDialog,
    )
    entity_name = uri_str.rsplit('/', 1)[-1] if uri_str else 'unknown'
    dialog = ValidationConfirmDialog(
        validation_result=result,
        department=department,
        entity_name=entity_name,
        parent=hou.qt.mainWindow(),
        read_only=True,
    )
    dialog.exec_()

