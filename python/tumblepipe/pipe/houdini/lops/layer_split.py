import logging

import hou

logger = logging.getLogger(__name__)

from tumblepipe.api import path_str, project_temp_dir
from tumblepipe.util.io import copy_version_contents
from tumblepipe.config.timeline import get_fps
from tumblepipe.pipe.houdini import nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode
from tumblepipe.pipe.houdini.lops.layer_cook import LayerWriter
from tumblepipe.pipe.paths import (
    SHARED_CHANNEL,
    get_shared_layer_file_name,
    latest_export_path,
    next_export_path,
)
from tumblepipe.pipe.context import save_export_context
from tumblepipe.pipe.houdini.nodes import button


class LayerSplit(FrameRangeNode, EntityNode, LayerWriter):

    def list_department_names(self) -> list[str]:
        return self.department_choices(include_generated=False)


    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        self.parm('entity_label').set(self._entity_label())

        self.parm('department_label').set(self._department_label())

    def _initialize(self):
        """Refresh the labels. Both 'entity' and 'department' keep their
        'from_context' defaults — baking the workfile's URI in here would
        pin the node to whichever entity it was born in."""
        self._update_labels()

    def execute(self, force_local: bool = False, render_range=None):
        """
        Execute export.

        If force_local=True, executes directly (used by ProcessDialog callbacks).
        Otherwise, opens the ProcessDialog for task selection and execution.
        ``render_range`` (a ``BlockRange``, rolls included) exports those
        frames instead of the node's Frame Range, for one submit (#76); the
        node's parms are not changed.
        """
        if force_local:
            return self._execute(render_range)
        # Open ProcessDialog
        from tumblepipe.pipe.houdini.ui.dialog_launcher import (
            open_process_dialog_for_node
        )
        open_process_dialog_for_node(self, dialog_title="Export Shared Layer")

    def _execute(self, render_range=None):
        """Internal execution - export the shared layer to _shared path."""
        self._update_labels()
        entity_uri = self.require_entity_uri()
        department_name = self.require_department_name()
        frame_range = self.get_frame_range()

        # Get full range (including roll), or the one set for this submit
        full_range = (
            render_range if render_range is not None
            else frame_range.full_range()
        )

        logger.info(
            f"Starting shared layer export: uri={entity_uri}, dept={department_name}, "
            f"frames={full_range.first_frame}-{full_range.last_frame}"
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
        with project_temp_dir() as temp_path:

            # Set fps metadata
            self.parm('set_metadata_fps').set(fps)

            self.cook_layer(temp_path, file_name, full_range)

            # Save context file to temp
            save_export_context(temp_path, entity_uri, department_name, version_name, channel_name='_shared')

            # Copy all files from temp to export path
            # context.json last: it marks the version finished. The copy
            # reports its own progress (bytes, total, throughput).
            copy_version_contents(
                temp_path, export_path,
                label=f"copying {version_name} to server",
            )

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


@button('Layer Split')
def execute(kwargs):
    raw_node = kwargs['node']
    node = LayerSplit(raw_node)
    node.execute()


@button('Layer Split')
def open_location(kwargs):
    raw_node = kwargs['node']
    node = LayerSplit(raw_node)
    node.open_location()


@button('Layer Split')
def select(kwargs):
    """Entity button."""
    LayerSplit(kwargs['node']).pick_entity()


@button('Validate')
def validate(kwargs):
    """The Validate button."""
    from tumblepipe.pipe.houdini.validators import show_validation
    show_validation(LayerSplit(kwargs['node']), kwargs['node'])

