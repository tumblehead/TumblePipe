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

A shot no import_render reads (a slap comp in the render scene) has
nothing to comp on its own: it comps the render of the same Farm Submit
(#80), so Submit refuses with :class:`NoRenderToComp` and offers to open
Farm Submit with the shot's Render and Comp ticked.
"""

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil

import hou

from tumblepipe.api import path_str, get_user_name, api
from tumblepipe.config.channels import list_channels
from tumblepipe.farm.specs import preview_frames
from tumblepipe.util.uri import Uri
from tumblepipe.pipe import comp
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.farm_node import FarmNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode, FrameRangeSource

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

    def get_preview_frame_list(self) -> str:
        """The Preview Frames List text (empty: first, middle and last). A
        node made before the parm existed has none."""
        parm = self.parm('preview_frame_list')
        return '' if parm is None else parm.eval()

    def preview_for(self, first_frame: int, last_frame: int) -> preview_frames.PreviewFrames:
        """What the Preview Frames List asks for over the comp's frames, on
        the node's step (``farm.specs.preview_frames.parse``)."""
        return preview_frames.parse(
            self.get_preview_frame_list(), first_frame, last_frame, self.get_step_size()
        )

    def preview_warnings(self) -> list[str]:
        """What the Preview Frames List drops over the node's Frame Range
        (a Submit reads it against the frames it comps: with From Config,
        what the renders cover); none while Preview Frames is off."""
        if not self.get_preview_frames(): return []
        try:
            frames = self.get_frame_range().full_range()
        except Exception:
            # No shot or range yet (a fresh node, an unset entity): nothing
            # to check against. Submit checks again over its real frames.
            return []
        return list(self.preview_for(frames.first_frame, frames.last_frame).warnings)

    def get_copy_to_edit(self) -> bool:
        return bool(self.parm('copy_to_edit').eval())

    def render_node(self):
        return self.native().node(RENDER_NODE)

    def reads_imports(self) -> bool:
        """Whether a th::import_render reads the node's shot. Without one
        (a slap comp in the render scene, #80) the comp reads the render
        of its Farm Submit instead."""
        from tumblepipe.pipe.houdini.cops.import_render import find_imports
        shot_uri = self.get_entity_uri()
        return shot_uri is None or bool(find_imports(shot_uri))

    def unread_channels(self) -> list[str]:
        """The shot's channels no import_render of the shot reads; only a
        merged comp (Layer ``comp``) checks, and only one that reads
        imports at all: a comp of its Farm Submit's render reads the
        channels that render renders (#80)."""
        from tumblepipe.pipe.houdini.cops.import_render import find_imports
        if self.parm('layer').eval() != comp.MERGED_LAYER: return []
        shot_uri = self.get_entity_uri()
        if shot_uri is None: return []
        imports = find_imports(shot_uri)
        if not imports: return []
        read = [
            channel for node in imports
            for channel in node.read_channels()
        ]
        return comp.unread_channels(list_channels(shot_uri), read)

    def refresh_warning(self) -> str:
        """Show the unread-channel and Preview Frames List warnings on the
        node (empty when none), or the note that it comps its Farm
        Submit's render when no import reads its shot."""
        try:
            warning = comp.unread_warning(self.unread_channels())
            if not self.reads_imports():
                warning = comp.SCENE_COMP_NOTE
        except ns.NodeError:
            warning = ''
        warning = '\n'.join(line for line in [warning, *self.preview_warnings()] if line)
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
class NoRenderToComp(ns.NodeError):
    """A Submit of a shot no th::import_render reads: such a comp reads the
    render of the same Farm Submit (#80), and this Submit renders none."""

    def __init__(self, message: str, shot_uri: Uri):
        super().__init__(message)
        self.shot_uri = shot_uri


@dataclass
class ShotSubmit:
    """One composite version: a shot's export_comps and its frozen imports."""
    shot_uri: Uri
    settings: ExportComp
    exports: list = field(default_factory=list)     # [{node_path, layer}]
    plan: dict = field(default_factory=dict)        # comp.freeze_plan
    unread: list = field(default_factory=list)
    # The frames every frozen read covers (comp.covered_range), None when
    # unknown: what From Context comps (#43).
    covered: tuple | None = None


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
            # A comp with no import reads the render of its Farm Submit
            # (#80); this Submit renders nothing.
            raise NoRenderToComp(
                f'{submit.settings.path()}: {comp.no_render_message(submit.shot_uri)}',
                submit.shot_uri,
            )
        submit.plan = comp.freeze_plan(states)
        try:
            reads = comp.provenance(submit.plan)
        except comp.CompError as error:
            raise submit.settings._error(str(error)) from None
        submit.covered = _covered(submit.shot_uri, reads)
    return list(submits.values())


def _covered(shot_uri: Uri, reads: dict) -> tuple | None:
    from tumblepipe.pipe.paths import get_render_context
    renders = get_render_context(shot_uri, purpose = 'render').renders
    return comp.covered_range(comp.read_ranges(renders, reads).values())


def comp_range(submit: ShotSubmit) -> tuple[int, int]:
    """The frames ``submit`` comps: with Frame Range From Context, the
    frames every read covers (the renders' ranges, which can be shorter
    than the shot's with its roll), else the shot's; any other source as
    the node says."""
    node = submit.settings
    if node.get_frame_range_source() == FrameRangeSource.ENTITY and submit.covered is not None:
        return submit.covered
    render_range = node.get_frame_range().full_range()
    return render_range.first_frame, render_range.last_frame


def job_config(submit: ShotSubmit, department: str, input_path: str) -> dict:
    """The composite pipeline's config for one ShotSubmit."""
    node = submit.settings
    first_frame, last_frame = comp_range(submit)
    tasks = dict(full_composite = dict(channel_name = FULL_DISCORD_CHANNEL))
    preview_entry = {}
    if node.get_preview_frames():
        tasks['partial_composite'] = dict(channel_name = PREVIEW_DISCORD_CHANNEL)
        preview_entry = preview_frames.config_entry(node.preview_for(first_frame, last_frame))
    return dict(
        entity = dict(uri = str(submit.shot_uri), department = department),
        settings = dict(
            user_name = get_user_name(),
            purpose = 'render',
            priority = node.get_priority(),
            pool_name = node.get_pool_name(),
            input_path = input_path,
            first_frame = first_frame,
            last_frame = last_frame,
            step_size = node.get_step_size(),
            batch_size = node.get_batch_size(),
            copy_to_edit = node.get_copy_to_edit(),
            **preview_entry,
        ),
        exports = submit.exports,
        imports = submit.plan,
        tasks = tasks,
    )


def outside_renders(submits: list[ShotSubmit]) -> list[str]:
    """A line per shot whose comp frames reach past what its reads
    rendered: those frames comp over empty input (#43)."""
    lines = []
    for submit in submits:
        if submit.covered is None: continue
        first, last = comp_range(submit)
        if first < submit.covered[0] or last > submit.covered[1]:
            lines.append(
                f'{submit.shot_uri}: the comp is {first}-{last}, the renders '
                f'it reads cover {submit.covered[0]}-{submit.covered[1]}'
            )
    return lines


def preview_drops(submits: list[ShotSubmit]) -> list[str]:
    """A line per Preview Frames List warning of each shot, read against
    the frames the shot comps (``comp_range``)."""
    lines = []
    for submit in submits:
        node = submit.settings
        if not node.get_preview_frames(): continue
        preview = node.preview_for(*comp_range(submit))
        lines += [f'{submit.shot_uri}: {warning}' for warning in preview.warnings]
    return lines


def _confirm_unread(submits: list[ShotSubmit]) -> bool:
    lines = [
        f'{submit.shot_uri}: {", ".join(submit.unread)}'
        for submit in submits if submit.unread
    ]
    outside = outside_renders(submits)
    drops = preview_drops(submits)
    if not lines and not outside and not drops: return True
    if not hou.isUIAvailable(): return True
    message = ''
    if lines:
        message += (
            'These shot channels are not read by any th::import_render in '
            'the comp, so they will not be in it:\n\n' + '\n'.join(lines) + '\n\n'
            'Add an import_render column for each (or make the next version '
            'from the template), or submit without them.'
        )
    if outside:
        message += ('\n\n' if message else '') + (
            'These comps reach past the frames their renders cover; those '
            'frames comp over empty (transparent black) input:\n\n'
            + '\n'.join(outside) + '\n\n'
            'Set Frame Range to From Context to comp what was rendered.'
        )
    if drops:
        message += ('\n\n' if message else '') + (
            'The Preview Frames List drops these frames:\n\n'
            + '\n'.join(drops) + '\n\n'
            'Previews use the frames that are left (first, middle and last '
            'when none are).'
        )
    choice = hou.ui.displayMessage(
        message,
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
        try:
            submits = plan_submits(node)
        except NoRenderToComp as error:
            if not hou.isUIAvailable(): raise
            _offer_farm_submit(error)
            return
        if not _confirm_unread(submits): return
        shots = len(submits)
        farm_submit.submit(
            'the composite' if shots == 1 else f'the composites of {shots} shots',
            'Submit Composite',
            lambda: submit(node, submits),
        )


def _offer_farm_submit(error: NoRenderToComp):
    """Say why the Submit has nothing to comp, and offer Farm Submit with
    the shot's Render and Comp ticked: there the comp reads that render."""
    from tumblepipe.pipe.houdini.ui import farm_submit
    from tumblepipe.pipe.houdini.scene_context import scene_context
    choice = hou.ui.displayMessage(
        str(error),
        buttons = ('Open Farm Submit', 'Cancel'),
        default_choice = 0, close_choice = 1,
        severity = hou.severityType.Warning,
        title = 'Submit Composite',
    )
    if choice != 0: return
    context = scene_context()
    farm_submit.open_comp_grid(
        error.shot_uri,
        department = context.department_name if context is not None else None,
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
