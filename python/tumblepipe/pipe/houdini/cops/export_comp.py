"""th::export_comp: the end of a comp, and its Submit.

The comp's ``export_layer``: one RGBA input, rendered by the node's own
``render`` ROP on the farm. Layer ``comp`` is the merged comp, written as the
composite version's cross-channel layer (``composite/slapcomp/v####``, where
the review MP4, the daily, the notify and the edit's take picker look); a
channel name writes a per-channel output into that channel's folder.

**Submit** asks Run on (Render farm / This machine) and submits *every*
export_comp in the workfile as one composite version per shot (a Multi's
workfile holds one set per member). It freezes the imports: each
import_render on ``latest`` is pinned, in the job, to the version it resolved
here, so the farm renders what the artist saw; the workfile itself is not
changed. Before it submits, it lists the shot channels no import reads and
asks.
"""

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

import hou

from tumblepipe.api import path_str, get_user_name, api
from tumblepipe.config.channels import list_channels
from tumblepipe.util.uri import Uri
from tumblepipe.pipe import comp
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.farm_node import FarmNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode

TYPE_NAME = 'export_comp'

# The ROP inside the node that the composite worker renders.
RENDER_NODE = 'render'

# The Discord channels the composite job's notifies post to.
FULL_DISCORD_CHANNEL = 'comp'
PREVIEW_DISCORD_CHANNEL = 'previews'


class ExportComp(FrameRangeNode, FarmNode, EntityNode):

    ENTITY_CONTEXTS = ('shots',)

    def list_layer_names(self) -> list[str]:
        """The Layer menu: the merged comp, then the shot's channels."""
        shot_uri = self.get_entity_uri()
        channels = list_channels(shot_uri) if shot_uri is not None else []
        return [comp.MERGED_LAYER] + channels

    def get_layer_name(self) -> str:
        layer = self.parm('layer').eval()
        if layer == comp.MERGED_LAYER: return layer
        shot_uri = self.require_entity_uri()
        if layer not in list_channels(shot_uri):
            raise self._error(
                f"layer '{layer}' is neither '{comp.MERGED_LAYER}' nor a "
                f'channel of {shot_uri}'
            )
        return layer

    def set_layer_name(self, layer: str):
        self.parm('layer').set(layer)

    def get_step_size(self) -> int:
        return max(1, self.parm('stepsize').eval())

    def get_preview_frames(self) -> bool:
        return bool(self.parm('preview_frames').eval())

    def get_copy_to_edit(self) -> bool:
        return bool(self.parm('copy_to_edit').eval())

    def render_node(self):
        return self.native().node(RENDER_NODE)

    def unread_channels(self) -> list[str]:
        """The shot's channels no import_render of the shot reads; only a
        merged comp (Layer ``comp``) checks."""
        from tumblepipe.pipe.houdini.cops.import_render import find_imports
        if self.parm('layer').eval() != comp.MERGED_LAYER: return []
        shot_uri = self.get_entity_uri()
        if shot_uri is None: return []
        read = [
            channel for node in find_imports(shot_uri)
            for channel in node.read_channels()
        ]
        return comp.unread_channels(list_channels(shot_uri), read)

    def refresh_warning(self) -> str:
        """Show the unread-channel warning on the node (empty when none)."""
        try:
            warning = comp.unread_warning(self.unread_channels())
        except ns.NodeError:
            warning = ''
        self.parm('warning').set(warning)
        native = self.native()
        if warning:
            ns.set_node_comment(native, f'Warning: {warning}')
        elif native.comment().startswith('Warning: '):
            native.setComment('')
        return warning

    def _update_labels(self):
        self.parm('entity_label').set(self._entity_label())

    def open_location(self):
        shot_uri = self.require_entity_uri()
        root = api.storage.resolve(
            Uri.parse('render:/render') / shot_uri.segments / comp.COMPOSITE_DEPARTMENT
            / comp.output_folder(self.get_layer_name())
        )
        if not root.exists():
            raise self._error(f'{path_str(root)} does not exist yet')
        hou.ui.showInFileBrowser(path_str(root))


def find_exports() -> list[ExportComp]:
    return [ExportComp(native) for native in ns.list_by_node_type(TYPE_NAME, 'Cop')]


def refresh_warnings():
    """Re-check every export_comp's unread channels."""
    for node in find_exports():
        node.refresh_warning()


###############################################################################
# Submit
###############################################################################
@dataclass
class ShotSubmit:
    """One composite version: a shot's export_comps and its frozen imports."""
    shot_uri: Uri
    settings: ExportComp
    exports: list = field(default_factory=list)     # [{node_path, layer}]
    plan: dict = field(default_factory=dict)        # comp.freeze_plan
    unread: list = field(default_factory=list)


def plan_submits(clicked: ExportComp) -> list[ShotSubmit]:
    """What a Submit from ``clicked`` sends: one ShotSubmit per shot with an
    export_comp in the workfile, each export_comp in exactly one, each
    shot's imports resolved and frozen. Raises the node error of the first
    import or export that does not resolve."""
    from tumblepipe.pipe.houdini.cops.import_render import find_imports
    clicked_shot = clicked.require_entity_uri()
    submits: dict[str, ShotSubmit] = dict()
    for node in find_exports():
        shot_uri = node.require_entity_uri()
        key = str(shot_uri)
        if key not in submits:
            submits[key] = ShotSubmit(shot_uri, node)
        if shot_uri == clicked_shot:
            submits[key].settings = clicked
        layer = node.get_layer_name()
        if any(export['layer'] == layer for export in submits[key].exports):
            raise node._error(
                f"another export_comp of {shot_uri} already writes layer "
                f"'{layer}'; one layer, one node"
            )
        submits[key].exports.append(dict(node_path = node.path(), layer = layer))
        submits[key].unread += [
            channel for channel in node.unread_channels()
            if channel not in submits[key].unread
        ]
    for submit in submits.values():
        states = []
        for node in find_imports(submit.shot_uri):
            states += node.import_states()
        if not states:
            raise submit.settings._error(
                f'no th::import_render reads {submit.shot_uri}: there is '
                'nothing to comp'
            )
        submit.plan = comp.freeze_plan(states)
        try:
            comp.provenance(submit.plan)
        except comp.CompError as error:
            raise submit.settings._error(str(error)) from None
    return list(submits.values())


def job_config(submit: ShotSubmit, department: str, input_path: str) -> dict:
    """The composite pipeline's config for one ShotSubmit."""
    node = submit.settings
    render_range = node.get_frame_range().full_range()
    tasks = dict(full_composite = dict(channel_name = FULL_DISCORD_CHANNEL))
    if node.get_preview_frames():
        tasks['partial_composite'] = dict(channel_name = PREVIEW_DISCORD_CHANNEL)
    return dict(
        entity = dict(uri = str(submit.shot_uri), department = department),
        settings = dict(
            user_name = get_user_name(),
            purpose = 'render',
            priority = node.get_priority(),
            pool_name = node.get_pool_name(),
            input_path = input_path,
            first_frame = render_range.first_frame,
            last_frame = render_range.last_frame,
            step_size = node.get_step_size(),
            batch_size = node.get_batch_size(),
            copy_to_edit = node.get_copy_to_edit(),
        ),
        exports = submit.exports,
        imports = submit.plan,
        tasks = tasks,
    )


def _confirm_unread(submits: list[ShotSubmit]) -> bool:
    lines = [
        f'{submit.shot_uri}: {", ".join(submit.unread)}'
        for submit in submits if submit.unread
    ]
    if not lines: return True
    if not hou.isUIAvailable(): return True
    choice = hou.ui.displayMessage(
        'These shot channels are not read by any th::import_render in the '
        'comp, so they will not be in it:\n\n' + '\n'.join(lines) + '\n\n'
        'Add an import_render column for each (or make the next version '
        'from the template), or submit without them.',
        buttons = ('Submit anyway', 'Cancel'),
        default_choice = 1, close_choice = 1,
        severity = hou.severityType.Warning,
        title = 'Submit Composite',
    )
    return choice == 0


def submit(node: ExportComp, submits: list[ShotSubmit] | None = None):
    """Submit every export_comp of the workfile, one composite version per
    shot, frozen to the imports' versions now."""
    from tumblepipe.farm.pipelines import composite as composite_job
    from tumblepipe.pipe.houdini.scene_context import scene_context
    if submits is None:
        submits = plan_submits(node)
    context = scene_context()
    department = (
        context.department_name if context is not None
        else comp.COMPOSITE_DEPARTMENT
    )
    hou.hipFile.save()
    workfile_path = Path(hou.hipFile.path())
    root_temp_path = api.storage.resolve(Uri.parse('temp:/'))
    root_temp_path.mkdir(parents = True, exist_ok = True)
    for shot_submit in submits:
        with TemporaryDirectory(dir = path_str(root_temp_path)) as temp_dir:
            input_path = Path(temp_dir) / 'workfile.hip'
            shutil.copyfile(workfile_path, input_path)
            relative_input_path = input_path.relative_to(temp_dir)
            composite_job.submit(
                job_config(shot_submit, department, path_str(relative_input_path)),
                {input_path: relative_input_path},
            )


def create(scene, name):
    return ns.create_node(scene, name, ExportComp, TYPE_NAME, 'Cop')


def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_EXPORT)
    node = ExportComp(raw_node)
    node._update_labels()
    node.refresh_warning()


def on_loaded(raw_node):
    node = ExportComp(raw_node)
    node._update_labels()
    node.refresh_warning()


def submit_kwargs(kwargs):
    from tumblepipe.pipe.houdini.ui import farm_submit
    node = ExportComp(kwargs['node'])
    with ns.node_errors_as_message('Submit Composite'):
        submits = plan_submits(node)
        if not _confirm_unread(submits): return
        shots = len(submits)
        farm_submit.submit(
            'the composite' if shots == 1 else f'the composites of {shots} shots',
            'Submit Composite',
            lambda: submit(node, submits),
        )


def open_location(kwargs):
    node = ExportComp(kwargs['node'])
    with ns.node_errors_as_message('Open Location'):
        node.open_location()


def select_entity(kwargs):
    node = ExportComp(kwargs['node'])
    if node.pick_entity('Select Shot', contexts=('shots',)):
        node.refresh_warning()


def refresh(kwargs):
    ExportComp(kwargs['node']).refresh_warning()
