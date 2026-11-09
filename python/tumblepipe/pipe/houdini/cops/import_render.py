"""th::import_render: one channel's finished render, into a comp.

The comp's ``import_shot``. It reads one render department's version of one
channel of a shot and outputs the beauty (``rgba``) and a cable of every AOV
the comp reads (``aovs``), each field named after its AOV. What it resolves
is ``pipe.comp.resolve_render``; this module points the File COPs inside the
node's ``dive`` subnet at it. They are regenerated only when the resolved
version changes, never on a plain cook.

The node joins ``scene_imports.REFRESH_SPECS``: opening a workfile
re-resolves an import on ``latest``, like every other import. When its
version moves, the light mixes reading its cable sync their rows and the
export_comps re-check which channels the comp reads.
"""

import hou

from tumblepipe.api import path_str, api
from tumblepipe.config.channels import list_channels
from tumblepipe.config.department import list_departments
from tumblepipe.util.uri import Uri
from tumblepipe.pipe import comp
from tumblepipe.pipe.paths import get_render_context
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode

TYPE_NAME = 'import_render'

# The editable subnet the File COPs live in, its cable pack, and the
# node's outputs (in order).
DIVE = 'dive'
PACK = 'pack'
RGBA_OUTPUT = 0
AOVS_OUTPUT = 1

# Parm values the node stores what it last resolved in, so a refresh that
# resolves the same version touches nothing.
RESOLVED_PARM = 'resolved'
FIELDS_PARM = 'fields'

# A File COP's 'missingdata' token for a missing frame: output empty (zero),
# so a frame outside the render's range (a pre-roll) does not fail the cook.
MISSING_EMPTY = 1
# File COP 'colorspace' tokens: the scene's colour space for the beauty and
# its light groups, raw for data passes.
COLORSPACE_AUTO = 0
COLORSPACE_RAW = 1


def render_department_names() -> list[str]:
    return [d.name for d in list_departments('render')]


def resolve(shot_uri: Uri, channel: str, department: str, version: str) -> comp.Resolved:
    """``pipe.comp.resolve_render`` on the shot's renders on disk."""
    renders = get_render_context(shot_uri, purpose='render').renders
    return comp.resolve_render(
        renders, render_department_names(), channel, department, version
    )


def version_menu(shot_uri: Uri | None, department: str, channel: str) -> list[str]:
    """The Version menu: ``latest``, then a concrete department's complete
    versions newest first."""
    if department == comp.BEST or shot_uri is None: return [comp.LATEST]
    renders = get_render_context(shot_uri, purpose='render').renders
    return [comp.LATEST] + comp.list_versions(renders, department, channel)


class ImportRender(EntityNode):

    ENTITY_CONTEXTS = ('shots',)

    # Menus (read-only; Houdini evaluates them on every redraw)

    def list_channel_names(self) -> list[str]:
        shot_uri = self.get_entity_uri()
        if shot_uri is None: return []
        return list_channels(shot_uri)

    def list_department_names(self) -> list[str]:
        return [comp.BEST] + comp.readable_departments(render_department_names())

    def list_version_names(self) -> list[str]:
        return version_menu(
            self.get_entity_uri(),
            self.get_department_setting(),
            self.get_channel_name(),
        )

    # Parms

    def get_channel_name(self) -> str:
        return self.parm('channel').eval()

    def set_channel_name(self, channel: str):
        self.parm('channel').set(channel)

    def get_department_setting(self) -> str:
        return self.parm('department').eval()

    def get_version_setting(self) -> str:
        return self.parm('version').eval()

    def pin(self, department: str, version: str):
        """Set the Department and Version parms (the farm's freeze)."""
        self.parm('department').set(department)
        self.parm('version').set(version)

    def get_resolved(self) -> tuple[str, str] | None:
        """``(department, version)`` the node last resolved, None if none."""
        raw = self.parm(RESOLVED_PARM).eval()
        if not raw or ' ' not in raw: return None
        department, version = raw.split(' ', 1)
        return department, version

    def get_fields(self) -> list[str]:
        """The fields of the ``aovs`` cable as last built."""
        return [field for field in self.parm(FIELDS_PARM).eval().split() if field]

    # Resolution

    def resolve(self) -> comp.Resolved:
        """What the node's parms resolve to now; raises the node's error with
        the reason when nothing does."""
        shot_uri = self.require_entity_uri()
        channel = self.get_channel_name()
        if channel not in list_channels(shot_uri):
            raise self._error(f'"{channel}" is not a channel of {shot_uri}')
        try:
            resolved = resolve(
                shot_uri, channel,
                self.get_department_setting(), self.get_version_setting(),
            )
        except comp.CompError as error:
            raise self._error(str(error)) from None
        problems = comp.render_problems({channel: resolved.aovs})
        if problems:
            raise self._error(_unbuildable_message(shot_uri, resolved, problems))
        return resolved

    def _update_labels(self, text: str | None = None):
        self.parm('entity_label').set(self._entity_label())
        if text is not None:
            self.parm('version_label').set(text)

    def execute(self) -> comp.Resolved:
        """Resolve, and point the File COPs at it when the version moved.

        Returns what resolved. A version change syncs the downstream light
        mixes' rows; every export_comp re-checks the channels it reads.
        """
        previous = self.get_resolved()
        try:
            resolved = self.resolve()
        except ns.NodeError as error:
            self._update_labels(f'none: {str(error).split(": ", 1)[-1]}')
            raise
        current = (resolved.department, resolved.version)
        moved = previous is not None and previous != current
        if previous != current or not self._dive_built(resolved.fields):
            self._build(resolved)
        label = resolved.label
        if moved:
            label += f' (was {previous[1] if previous[0] == current[0] else " ".join(previous)})'
        self._update_labels(label)
        if moved or previous is None:
            _sync_downstream_mixes(self.native())
        _refresh_exports()
        return resolved

    def _dive_built(self, fields: list[str]) -> bool:
        dive = self.native().node(DIVE)
        return dive is not None and self.get_fields() == fields and all(
            dive.node(field) is not None for field in fields
        )

    def _build(self, resolved: comp.Resolved):
        """Rebuild the dive: one File COP per field into the cable pack, the
        beauty to ``rgba``. Nothing outside the dive changes."""
        dive = self.native().node(DIVE)
        if dive is None:
            raise self._error(f'the node has no {DIVE} subnet (a broken HDA)')
        for child in dive.children():
            if child.name() in ('inputs', 'outputs'): continue
            child.destroy()
        outputs = dive.node('outputs')
        pack = dive.createNode('cablepack', PACK)
        fields = resolved.fields
        for index, field in enumerate(fields):
            aov = resolved.aovs[field]
            file_node = dive.createNode('file', field)
            _configure_file(file_node, aov, field)
            file_node.setPosition(hou.Vector2(index * 2.5, 2.0))
            pack.setInput(index, file_node, 0)
            if field == comp.BEAUTY:
                outputs.setInput(RGBA_OUTPUT, file_node, 0)
        # Name the fields once every input is wired: wiring an input resizes
        # the pack's field list to the inputs.
        pack.parm('fields').set(len(fields))
        for index, field in enumerate(fields):
            pack.parm(f'fieldname{index + 1}').set(field)
        pack.setPosition(hou.Vector2(max(len(fields) - 1, 0) * 1.25, 0.0))
        outputs.setInput(AOVS_OUTPUT, pack, 0)
        outputs.setPosition(hou.Vector2(max(len(fields) - 1, 0) * 1.25, -1.5))
        self.parm(RESOLVED_PARM).set(f'{resolved.department} {resolved.version}')
        self.parm(FIELDS_PARM).set(' '.join(fields))

    def open_location(self):
        resolved = self.get_resolved()
        shot_uri = self.require_entity_uri()
        root = api.storage.resolve(
            Uri.parse('render:/render') / shot_uri.segments
        )
        path = root
        if resolved is not None:
            path = root / resolved[0] / self.get_channel_name() / resolved[1]
        if not path.exists():
            raise self._error(f'{path_str(path)} does not exist yet')
        hou.ui.showInFileBrowser(path_str(path))


def _configure_file(file_node, aov, field: str):
    render_range = aov.get_frame_range()
    frame_path = aov.get_aov_frame_path('$F4')
    file_node.parm('filename').set(path_str(frame_path))
    file_node.parm('videoframestart').deleteAllKeyframes()
    if render_range is not None:
        file_node.parm('videoframestart').set(render_range.first_frame)
    file_node.parm('missingdata').set(MISSING_EMPTY)
    file_node.parm('missingcolora').set(0)
    file_node.parm('colorspace').set(
        COLORSPACE_AUTO if comp.is_colour_aov(field) else COLORSPACE_RAW
    )
    file_node.parm('aovs').set(1)
    file_node.parm('aov1').set(comp.aov_plane_name(aov))
    file_node.parm('type1').set(comp.aov_signature(field))


def _unbuildable_message(shot_uri, resolved: comp.Resolved, problems) -> str:
    render_root = path_str(api.storage.resolve(
        Uri.parse('render:/render') / shot_uri.segments
    ))
    return (
        f'cannot comp {resolved.department} {resolved.version} of channel '
        f'"{resolved.channel}": ' + '; '.join(problems) + '. Renders are read '
        f'from {render_root}/<department>/<channel>/v####/<aov>/.'
    )


def find_imports(entity_uri: Uri | None = None) -> list[ImportRender]:
    """Every import_render in the scene; only those addressing
    ``entity_uri`` when given (a Multi's workfile holds several shots')."""
    found = []
    for native in ns.list_by_node_type(TYPE_NAME, 'Cop'):
        node = ImportRender(native)
        if entity_uri is not None:
            try:
                if node.get_entity_uri() != entity_uri: continue
            except ns.NodeError:
                continue
        found.append(node)
    return found


def upstream_import(native, output_index: int = AOVS_OUTPUT):
    """The import_render whose ``aovs`` cable reaches ``native``'s first
    input, following first inputs upstream; None when there is none."""
    seen = set()
    current = native
    while current is not None and current.path() not in seen:
        seen.add(current.path())
        connections = current.inputConnections()
        connection = next(
            (c for c in connections if c.inputIndex() == 0), None
        )
        if connection is None: return None
        source = connection.inputNode()
        if _is_import_render(source):
            return ImportRender(source)
        current = source
    return None


def _is_import_render(native) -> bool:
    if native is None: return False
    parts = native.type().name().split('::')
    return len(parts) >= 2 and parts[0] == 'th' and parts[1] == TYPE_NAME


def _sync_downstream_mixes(native):
    """Sync the rows of every light_mix that reads this import's cable."""
    from tumblepipe.pipe.houdini.cops import light_mix
    for mix in light_mix.find_mixes():
        source = upstream_import(mix.native())
        if source is None or source.path() != native.path(): continue
        try:
            mix.sync_rows()
        except ns.NodeError:
            pass


def _refresh_exports():
    from tumblepipe.pipe.houdini.cops import export_comp
    export_comp.refresh_warnings()


def create(scene, name):
    return ns.create_node(scene, name, ImportRender, TYPE_NAME, 'Cop')


def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    node = ImportRender(raw_node)
    node._update_labels()


def execute(kwargs):
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Import Render'):
        node.execute()


def changed(kwargs):
    """Channel, Department or Version changed: resolve again. A render that
    does not resolve shows on the Reads label, without a dialog."""
    node = ImportRender(kwargs['node'])
    try:
        node.execute()
    except ns.NodeError:
        pass


def open_location(kwargs):
    node = ImportRender(kwargs['node'])
    with ns.node_errors_as_message('Open Location'):
        node.open_location()


def select_entity(kwargs):
    node = ImportRender(kwargs['node'])
    node.pick_entity('Select Shot', contexts=('shots',))
