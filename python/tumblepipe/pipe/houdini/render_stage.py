"""Shared LOP graph builder for the render stage.

The farm's stage tasks (farm/tasks/stage, farm/tasks/cloud_stage) and the
th::render_debug HDA all need the same composition: the shot's staged build
for one variant, the current department exports layered on top, root default
prims, render-settings overrides and AOV pruning. These used to be drifting
hand-copies of the same graph; this module is the single source, so the
debug node composes what the farm renders by construction.

Composition semantics (deliberate, see the render/farm divergence notes):

- The inner import_shot is pinned: ``version='current'`` keeps the staged
  file's frozen version parameters on every sublayer URI, so the exported
  stage resolves to the same files on every worker at every frame. The
  default ``latest`` mode strips versions, which makes the resolver float
  each URI to whatever is newest on disk at husk time.
- ``department='none'`` (explicit sentinel) disables import_shot's
  department/asset exclusion, so the graph does not change shape with the
  ambient workfile context (the farm has none, a debug session has an
  arbitrary one).
- The variant is set explicitly; without it every variant used to compose
  on top of the *default* variant's staged stack.
- One import_layer per renderable shot department re-applies the current
  exports (resolved to pinned filesystem paths at build time) on top of the
  staged stack, so a render picks up department publishes made since the
  last shot build.

The graph is built for ONE variant. Callers that render several variants
build one graph (and one export) per variant — chaining variant graphs
composes every variant into a single stage, which renders the last variant
for all of them.
"""

from pathlib import Path

from tumblepipe.api import (
    api,
    path_str,
    local_path
)
from tumblepipe.util.uri import Uri
from tumblepipe.util.io import load_json
from tumblepipe.config.department import list_departments
from tumblepipe.pipe.houdini import util
from tumblepipe.pipe.houdini.lops import (
    import_shot,
    import_layer
)

ROOT_DEFAULTS_URI = 'config:/usd/root_default_prims.usda'


def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)


def get_render_settings_script(render_settings_path: Path) -> str:
    """Python-node script that applies the render settings JSON's overrides.

    Re-reads the JSON at cook time and applies its ``overrides`` onto the
    ``/Render/rendersettings`` prim.
    """
    _render_settings_path = path_str(render_settings_path)
    return (
        'from pathlib import Path\n'
        'import json\n'
        'import hou\n'
        '\n'
        'def _load_json(path):\n'
        '    if not path.exists(): return None\n'
        '    with path.open("r") as file:\n'
        '        return json.load(file)\n'
        '\n'
        'def _edit_render_settings():\n'
        '    \n'
        '    # Get context\n'
        '    node = hou.pwd()\n'
        '    stage = node.editableStage()\n'
        '    root = stage.GetPseudoRoot()\n'
        '    \n'
        '    # Load render settings\n'
        f'    render_settings_path = Path("{_render_settings_path}")\n'
        '    render_settings_data = _load_json(render_settings_path)\n'
        '    if render_settings_data is None: return\n'
        '    if "overrides" not in render_settings_data: return\n'
        '    \n'
        '    # Get render settings prim\n'
        '    render_settings_prim = root.GetPrimAtPath(\n'
        '        "/Render/rendersettings"\n'
        '    )\n'
        '    if not render_settings_prim.IsValid(): return\n'
        '    \n'
        '    # Edit the render settings\n'
        '    overrides = render_settings_data["overrides"]\n'
        '    for property, value in overrides.items():\n'
        '        attribute = render_settings_prim.GetAttribute(property)\n'
        '        if not attribute.IsValid(): continue\n'
        '        attribute.Set(value)\n'
        '\n'
        '_edit_render_settings()\n'
    )


def _get_aov_names(render_settings_path: Path) -> set | None:
    render_settings_data = load_json(render_settings_path)
    if render_settings_data is None: return None
    if 'aov_names' not in render_settings_data: return None
    return set(render_settings_data['aov_names'])


def build_render_stage_graph(
    scene_node,
    shot_uri: Uri,
    variant_name: str,
    render_settings_path: Path | None = None,
    name_prefix: str = '__'
    ):
    """Build the render-stage graph for one variant; return the last node.

    The chain is created inside ``scene_node`` with no input, so several
    variants can be built side by side in the same network. When
    ``render_settings_path`` is None the render-settings edit and AOV
    pruning stages are skipped (the debug node has no settings JSON).
    """

    included_department_names = [
        d.name for d in list_departments('shots') if d.renderable
    ]

    # Import the staged shot build, pinned to the current staged version
    # with no context-dependent exclusions (see module docstring).
    shot_node = import_shot.create(
        scene_node, f'{name_prefix}import_shot'
    )
    shot_node.set_shot_uri(shot_uri)
    shot_node.set_department_name('none')
    shot_node.set_variant_name(variant_name)
    shot_node.set_version_name('current')
    shot_node.set_include_procedurals(True)
    shot_node.execute()
    prev_node = shot_node.native()

    # Sublayer root defaults to get render settings and RenderVar prims
    root_defaults_path = local_path(api.storage.resolve(
        Uri.parse_unsafe(ROOT_DEFAULTS_URI)
    ))
    if root_defaults_path.exists():
        sublayer_node = scene_node.createNode(
            'sublayer', f'{name_prefix}root_defaults'
        )
        sublayer_node.parm('filepath1').set(path_str(root_defaults_path))
        _connect(prev_node, sublayer_node)
        prev_node = sublayer_node

    # Re-apply the current department exports for this variant on top of
    # the staged stack.
    variant_subnet = scene_node.createNode(
        'subnet', f'{name_prefix}variant_{variant_name}'
    )
    variant_subnet.node('output0').destroy()
    variant_subnet_input = variant_subnet.indirectInputs()[0]
    variant_subnet_output = variant_subnet.createNode('output', 'output')

    _connect(prev_node, variant_subnet)
    subnet_prev_node = variant_subnet_input

    for included_department_name in included_department_names:
        layer_node = import_layer.create(
            variant_subnet,
            included_department_name
        )
        layer_node.set_entity_uri(shot_uri)
        layer_node.set_department_name(included_department_name)
        layer_node.set_variant_name(variant_name)
        layer_node.set_version_name('current')
        layer_node.execute()
        _connect(subnet_prev_node, layer_node.native())
        subnet_prev_node = layer_node.native()

    _connect(subnet_prev_node, variant_subnet_output)
    variant_subnet.layoutChildren()
    prev_node = variant_subnet

    if render_settings_path is not None:

        # Apply the render settings overrides
        edit_render_settings_node = scene_node.createNode(
            'pythonscript',
            f'{name_prefix}edit_settings'
        )
        edit_render_settings_node.parm('python').set(
            get_render_settings_script(render_settings_path)
        )
        _connect(prev_node, edit_render_settings_node)
        prev_node = edit_render_settings_node

        # Prune the AOVs not in the render settings - use the composed
        # stage that includes the root defaults, since that is where the
        # RenderVar prims come from.
        included_aov_names = _get_aov_names(render_settings_path)
        if included_aov_names is not None:
            root = prev_node.stage().GetPseudoRoot()
            aov_paths = {
                aov_path.rsplit('/', 1)[1]: aov_path
                for aov_path in util.list_render_vars(root)
            }
            excluded_aov_names = set(aov_paths.keys()) - included_aov_names
            prune_aovs_node = scene_node.createNode(
                'prune', f'{name_prefix}prune_aovs'
            )
            prune_aovs_node.parm('primpattern1').set(
                ' '.join([
                    aov_paths[aov_name]
                    for aov_name in excluded_aov_names
                ])
            )
            _connect(prev_node, prune_aovs_node)
            prev_node = prune_aovs_node

    return prev_node
