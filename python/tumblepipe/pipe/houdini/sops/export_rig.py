import datetime as dt


from tumblepipe.api import (
    get_user_name,
    path_str
)
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.paths.naming import entity_stem
from tumblepipe.pipe.context import save_layer_context
from tumblepipe.pipe.provenance import scene_producer
from tumblepipe.pipe.paths import (
    get_next_version_path,
    get_rig_export_path,
)

class ExportRig(EntityNode):

    # A rig is always an asset's, so 'from_context' inside a shot workfile
    # resolves to nothing rather than to the shot.
    ENTITY_CONTEXTS = ('assets',)

    def __init__(self, native):
        super().__init__(native)


    def get_department_name(self) -> str:
        return 'rig'

    def set_channel_name(self, channel_name: str):
        channel_names = self.list_channel_names()
        if channel_name not in channel_names:
            raise self._error(
                f"channel '{channel_name}' is not one of {channel_names}"
            )
        self.parm('channel').set(channel_name)

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
        open_process_dialog_for_node(self, dialog_title="Export Rig")

    def _execute(self):
        """Internal execution - export rig geometry."""
        # Nodes
        context = self.native()
        export_node = context.node('export')

        # Parameters
        entity_uri = self.require_entity_uri()
        channel_name = self.get_channel_name()
        export_path = get_rig_export_path(entity_uri, channel_name)
        version_path = get_next_version_path(export_path)
        version_name = version_path.name
        timestamp = dt.datetime.now().isoformat()

        # Prepare rig export
        rig_file_name = f"{entity_stem(entity_uri, 'rig', version_name)}.bgeo.sc"
        output_file_path = version_path / rig_file_name
        export_node.parm('file').set(path_str(output_file_path))

        # Export rig
        version_path.mkdir(parents=True, exist_ok=True)
        export_node.parm('execute').pressButton()

        # The export node saves synchronously (foreground), so once pressButton
        # returns the cache must exist. A missing file means the cook failed -
        # raise so the process dialog marks the task Failed with a clear reason
        # instead of writing a context that points at nothing (or appearing to
        # hang).
        if not output_file_path.exists():
            raise RuntimeError(
                f"Rig export produced no output file: {output_file_path}. "
                f"Check the export node for cook errors."
            )

        # Write context
        save_layer_context(
            target_path=version_path,
            entity_uri=entity_uri,
            department_name='rig',
            version_name=version_name,
            timestamp=timestamp,
            user_name=get_user_name(),
            producer=scene_producer(context.path())
        )

        return version_name

def create(scene, name):
    return ns.create_node(scene, name, ExportRig, 'export_rig', 'Sop')

def execute(kwargs):
    raw_node = kwargs['node']
    node = ExportRig(raw_node)
    node.execute()