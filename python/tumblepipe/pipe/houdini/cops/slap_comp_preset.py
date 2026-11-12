"""The TumblePipe slap comp preset (#81): Houdini's Slap Comp Block set to
``beauty``, and the button that publishes it for farm renders.

The Tab menu tool *TumblePipe Slap Comp* (``toolbar/tumblepipe.shelf``, in
any COP network) puts down the stock Slap Comp Import, Block Begin and Block
End with every slap comp AOV named ``beauty``, TumblePipe's colour AOV, not
Karma's ``C`` (``pipe.slap_comp.preset_nodes``). They are stock nodes, so
anything Houdini's own block does they do.

The Block End gets a **TumblePipe** folder of spare parms: **Entity** (the
shot or asset, ``from_context`` = the workfile's), **Use on Farm Renders**
and **Publish to Farm**, which compiles the block (the Block to Geometry
COP's program, what husk's ``--slap-comp`` reads) into the entity's next
``export:/<entity>/_slapcomp/`` version (``pipe.slap_comp.publish``). Farm
renders of the entity apply the newest one.
"""

from pathlib import Path

import hou

from tumblepipe.api import api, get_user_name
from tumblepipe.util.uri import Uri
from tumblepipe.config.entities import get_entity_type, is_terminal_entity
from tumblepipe.pipe import slap_comp
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.scene_context import scene_entity_uri

FROM_CONTEXT = 'from_context'

FOLDER_PARM = 'th_slapcomp_folder'
ENTITY_PARM = 'th_entity'
FARM_PARM = 'th_use_on_farm'
PUBLISH_PARM = 'th_publish'
PUBLISHED_PARM = 'th_published'

PUBLISH_CALLBACK = (
    'import tumblepipe.pipe.houdini.cops.slap_comp_preset as th_node; '
    'th_node.publish_kwargs(kwargs)'
)

TITLE = 'TumblePipe Slap Comp'


###############################################################################
# The preset
###############################################################################
def build(parent, position=None, aovs=(slap_comp.BEAUTY,)) -> dict:
    """Put the preset down in COP network ``parent`` at ``position`` (network
    units, default the origin); returns its nodes by spec name
    (``slap_comp.IMPORT_NAME`` ...). Taken names get a number, the same one
    on all three, so their references to each other hold."""
    taken = {child.name() for child in parent.children()}
    suffix = slap_comp.free_suffix(taken)
    specs = slap_comp.preset_nodes(aovs, suffix)
    origin = hou.Vector2(position) if position is not None else hou.Vector2(0.0, 0.0)
    nodes = {}
    for spec in specs:
        node = parent.createNode(spec.type_name, spec.name)
        node.setPosition(origin + hou.Vector2(spec.position))
        # The count first: the rows exist only once it is set.
        node.parm(spec.count_parm).set(len(spec.aovs))
        for index, aov in enumerate(spec.aovs):
            node.parm(slap_comp.row_name(spec.row_parm, index)).set(aov)
        for name, value in spec.parms.items():
            node.parm(name).set(value)
        for name, expression in spec.expressions.items():
            node.parm(name).setExpression(expression, hou.exprLanguage.Hscript)
        for input_index, source_name, output_index in spec.inputs:
            node.setInput(input_index, parent.node(source_name), output_index)
        nodes[spec.role] = node
    end = nodes[slap_comp.END_NAME]
    add_farm_parms(end)
    # As the stock recipe: the import shows the layers, the end is the block.
    nodes[slap_comp.IMPORT_NAME].setGenericFlag(hou.nodeFlag.Display, True)
    end.setGenericFlag(hou.nodeFlag.Render, True)
    return nodes


def add_farm_parms(end) -> None:
    """The Block End's TumblePipe folder: Entity, Use on Farm Renders and
    Publish to Farm. Added once; a second call leaves it as it is."""
    if end.parm(PUBLISH_PARM) is not None:
        return
    group = end.parmTemplateGroup()
    folder = hou.FolderParmTemplate(
        FOLDER_PARM, 'TumblePipe', folder_type=hou.folderType.Simple,
    )
    entity = hou.StringParmTemplate(
        ENTITY_PARM, 'Entity', 1, default_value=(FROM_CONTEXT,),
        help=(
            'The shot or asset whose farm renders apply this slap comp: an '
            'entity URI (entity:/shots/010/sh020), or from_context for the '
            "workfile's own."
        ),
    )
    farm = hou.ToggleParmTemplate(
        FARM_PARM, 'Use on Farm Renders', default_value=True,
        help=(
            "On: Publish to Farm compiles this block for the entity's farm "
            'renders. Off: it publishes a version without one, so farm '
            'renders stop applying a slap comp.'
        ),
    )
    publish_button = hou.ButtonParmTemplate(
        PUBLISH_PARM, 'Publish to Farm',
        script_callback=PUBLISH_CALLBACK,
        script_callback_language=hou.scriptLanguage.Python,
        help=(
            "Compile the block into the entity's next slap comp version. "
            'Every farm render of the entity submitted after applies the '
            'newest one with husk --slap-comp. A render with a slap comp '
            'renders whole frames: set Farm Submit\'s Tiles to 1.'
        ),
    )
    published = hou.LabelParmTemplate(
        PUBLISHED_PARM, 'Published', column_labels=('',),
    )
    for template in (entity, farm, publish_button, published):
        folder.addParmTemplate(template)
    group.append(folder)
    end.setParmTemplateGroup(group)


###############################################################################
# Publishing
###############################################################################
def entity_uri(end) -> Uri:
    """The entity the Block End publishes for; raises ``ns.NodeError`` saying
    what to set when its Entity parm does not name one."""
    raw = end.evalParm(ENTITY_PARM).strip()
    if raw == FROM_CONTEXT:
        uri = scene_entity_uri(contexts=None)
        if uri is None:
            raise ns.NodeError(
                f'{end.path()}: Entity is from_context, and the open scene is '
                f"not a shot's or an asset's workfile. Save it as one, or set "
                f'Entity to the entity URI.'
            )
        return uri
    if not raw:
        raise ns.NodeError(f'{end.path()}: Entity is empty. Set it to the entity URI, or from_context.')
    try:
        uri = Uri.parse(raw)
    except Exception as exc:
        raise ns.NodeError(f'{end.path()}: Entity {raw!r} is not an entity URI ({exc}).') from exc
    if get_entity_type(uri) not in ('shot', 'asset') or not _in_project(uri):
        raise ns.NodeError(
            f"{end.path()}: Entity {raw!r} is not a shot or an asset of the project."
        )
    return uri


def _in_project(uri: Uri) -> bool:
    """Whether ``uri`` is a shot or asset the project has (not a category)."""
    context = uri.segments[0]
    listed = api.config.list_entity_uris(
        filter=Uri.parse(f'entity:/{context}'), closure=True,
    )
    return str(uri) in {str(u) for u in listed} and is_terminal_entity(api.config, uri)


def _row_names(node, count_parm: str, pattern: str) -> list[str]:
    return [
        node.evalParm(slap_comp.row_name(pattern, index))
        for index in range(node.evalParm(count_parm))
    ]


def block_inputs(end) -> list[str]:
    """The input names of the Block Begin(s) whose block ends at ``end``."""
    names = []
    for child in end.parent().children():
        if child.type().name() != 'block_begin':
            continue
        if child.node(child.evalParm('blockpath')) != end:
            continue
        names += _row_names(child, 'ports', 'name#')
    return names


def block_outputs(end) -> list[str]:
    return _row_names(end, 'ports', 'name#')


def compile_program(end, path: Path) -> None:
    """Compile the block ending at ``end`` into ``path``: the program a Block
    to Geometry COP outputs, the file husk's ``--slap-comp`` reads."""
    compiler = end.parent().createNode('blocktogeo', '__th_slapcomp_compile')
    try:
        compiler.parm('blockpath').set(end.path())
        geometry = compiler.geometry()
        errors = compiler.errors()
        if errors or geometry is None:
            raise ns.NodeError(
                f'{end.path()}: the block does not compile: '
                + ('; '.join(errors) if errors else 'no program')
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        geometry.saveToFile(str(path))
    finally:
        compiler.destroy()


def missing_aovs(entity: Uri, names: list[str]) -> list[str]:
    """``names`` the entity's renders do not write (the AOV names its
    exports declare); husk fails a frame whose slap comp reads or writes one."""
    from tumblepipe.config.channels import list_channels
    from tumblepipe.farm.pipelines.preview import aov_names
    rendered = set(aov_names(entity, list_channels(entity)))
    return sorted({name for name in names if name not in rendered})


def publish(end) -> slap_comp.Published:
    """Publish the block ending at ``end`` for its entity's farm renders
    (an empty version when Use on Farm Renders is off)."""
    entity = entity_uri(end)
    enabled = bool(end.evalParm(FARM_PARM))
    info = dict(
        block=end.path(),
        hip=hou.hipFile.path(),
        user=get_user_name(),
        houdini=hou.applicationVersionString(),
        inputs=block_inputs(end),
        outputs=block_outputs(end),
    )
    if enabled and not info['outputs']:
        raise ns.NodeError(f'{end.path()}: the block has no outputs, so it would change nothing.')
    published = slap_comp.publish(
        entity,
        (lambda path: compile_program(end, path)) if enabled else None,
        info,
    )
    end.parm(PUBLISHED_PARM).set(
        f'{entity} {published.version_name}' + ('' if enabled else ' (off)')
    )
    return published


def publish_kwargs(kwargs) -> None:
    end = kwargs['node']
    with ns.node_errors_as_message('Publish to Farm'):
        published = publish(end)
        message = (
            f'Published {published.version_name}: farm renders of '
            f'{entity_uri(end)} submitted from now on apply this slap comp. '
            f"Set Farm Submit's Tiles to 1 for them."
            if published.program_path is not None else
            f'Published {published.version_name} with Use on Farm Renders off: '
            f'farm renders of {entity_uri(end)} submitted from now on apply no slap comp.'
        )
        if published.program_path is not None:
            missing = missing_aovs(entity_uri(end), block_inputs(end) + block_outputs(end))
            if missing:
                message += (
                    f'\n\nThe block names AOVs the renders do not write: '
                    f"{', '.join(missing)}. husk fails a frame whose slap comp "
                    f'reads or writes an AOV the render lacks.'
                )
        if hou.isUIAvailable():
            hou.ui.displayMessage(message, title=TITLE)
        else:
            print(message)


###############################################################################
# The Tab menu tool
###############################################################################
def tool(kwargs) -> None:
    """*TumblePipe Slap Comp*: the preset, under the cursor of the network
    editor it was chosen in."""
    pane = kwargs.get('pane')
    if not isinstance(pane, hou.NetworkEditor):
        pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    parent = pane.pwd() if pane is not None else None
    if parent is None or parent.childTypeCategory() != hou.copNodeTypeCategory():
        hou.ui.displayMessage(
            'Put the TumblePipe slap comp down inside a COP network '
            '(a Copernicus network in /stage, /obj or /img).',
            title=TITLE, severity=hou.severityType.Warning,
        )
        return
    nodes = build(parent, pane.cursorPosition())
    for index, node in enumerate(nodes.values()):
        node.setSelected(True, clear_all_selected=(index == 0))
