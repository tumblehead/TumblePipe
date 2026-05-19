import loputils
import hou

from tumblepipe.util.uri import Uri
from tumblepipe.config.groups import get_group
from tumblepipe.pipe.houdini import util
from tumblepipe.pipe.houdini.lops import (
    import_shot,
    export_layer,
    animate
)
from tumblepipe.pipe.houdini.sops import (
    import_rigs
)

def _edit_camera(parent_node, name: str, prim_path: str):
    camera_node = parent_node.createNode('camera', name)
    camera_node.parm('createprims').set(0)
    camera_node.parm('primpattern').set(prim_path)
    loputils.setAllControlParameters(camera_node, 'none')
    return camera_node

def _scrape_rig_imports(import_native):
    rig_imports = dict()
    stage = import_native.stage()
    if stage is None:
        return rig_imports
    root = stage.GetPseudoRoot()
    for asset_info in util.list_assets(root):
        asset_uri = Uri.parse_unsafe(asset_info['uri'])
        if asset_uri not in rig_imports:
            rig_imports[asset_uri] = 0
        rig_imports[asset_uri] += 1
    return rig_imports

def _build_animate_dive(animate_node, rig_imports: dict):
    """Populate the inner anim_dive of an animate HDA with rigs and apex nodes."""
    inner_animate_node = animate_node.native().node('anim_dive')
    output_animate_node = inner_animate_node.node('output0')

    # Import the rigs
    rigs_node = import_rigs.create(inner_animate_node, 'import_rigs')
    rig_imports_list = [(uri, 'default', count) for uri, count in rig_imports.items()]
    rigs_node.set_rig_imports(rig_imports_list)
    rigs_node.execute()

    # Create the scene animate nodes
    scene_animate_node = inner_animate_node.createNode(
        'apex::sceneanimate',
        'scene_animate'
    )
    scene_animate_node.setInput(0, rigs_node.native())
    invoke_scene_node = inner_animate_node.createNode(
        'apex::sceneinvoke',
        'scene_invoke'
    )
    invoke_scene_node.parm('shapepattern').set('Base')
    invoke_scene_node.setInput(0, scene_animate_node)
    output_animate_node.setInput(0, invoke_scene_node)

    inner_animate_node.layoutChildren()

def _create_entity(scene_node, entity_uri: Uri, department_name: str):

    # Import the assets (source node - no input needed)
    import_node = import_shot.create(scene_node, 'import_shot')
    import_node.execute()

    # Scrape the assets (check if stage is available)
    rig_imports = _scrape_rig_imports(import_node.native())

    # Create the camera edit node
    camera_edit_node = _edit_camera(scene_node, 'camera_edit', '/cameras/render_camera')
    camera_edit_node.setInput(0, import_node.native())

    # Create the animate node and populate anim_dive
    animate_node = animate.create(scene_node, 'animate_shot')
    animate_node.setInput(0, camera_edit_node)
    _build_animate_dive(animate_node, rig_imports)

    # Create animation camera
    anim_camera = hou.node('/obj').createNode('lopimportcam', 'anim_camera')
    anim_camera.parm('loppath').set('/stage/import_shot')
    anim_camera.parm('primpath').set('/cameras/render_camera')

    # Create the export node
    export_node = export_layer.create(scene_node, 'export_shot')
    export_node.setInput(0, animate_node.native())

    scene_node.layoutChildren()

def _create_group(scene_node, group_uri: Uri, department_name: str):
    group = get_group(group_uri)
    if group is None: return

    # Create per-member nodes
    for member_uri in group.members:
        member_name = '_'.join(member_uri.segments[1:])

        # Import the assets (source node - no input needed)
        import_node = import_shot.create(scene_node, f'import_shot_{member_name}')
        import_node.set_shot_uri(member_uri)
        import_node.set_department_name(department_name)
        import_node.execute()

        # Scrape the assets (check if stage is available)
        rig_imports = _scrape_rig_imports(import_node.native())

        # Create the camera edit node
        camera_edit_node = _edit_camera(scene_node, f'camera_edit_{member_name}', '/cameras/render_camera')
        camera_edit_node.setInput(0, import_node.native())

        # Create the animate node and populate anim_dive
        animate_node = animate.create(scene_node, f'animate_shot_{member_name}')
        animate_node.setInput(0, camera_edit_node)
        _build_animate_dive(animate_node, rig_imports)

        # Create animation camera
        anim_camera = hou.node('/obj').createNode('lopimportcam', f'anim_camera_{member_name}')
        anim_camera.parm('loppath').set(f'/stage/import_shot_{member_name}')
        anim_camera.parm('primpath').set('/cameras/render_camera')

        # Create the export node
        export_node = export_layer.create(scene_node, f'export_shot_{member_name}')
        export_node.setInput(0, animate_node.native())
        export_node.set_entity_uri(member_uri)
        export_node.set_department_name(department_name)

    scene_node.layoutChildren()

def create(scene_node, entity_uri: Uri, department_name: str):
    if entity_uri.purpose == 'entity': return _create_entity(scene_node, entity_uri, department_name)
    elif entity_uri.purpose == 'groups': return _create_group(scene_node, entity_uri, department_name)
