
from tumblepipe.api import path_str, api
from tumblepipe.util.uri import Uri
from tumblepipe.config.entities import is_terminal_entity
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.sops import rename_packed
from tumblepipe.pipe.paths import (
    get_latest_version_path,
    get_rig_export_path,
    list_version_paths,
)
from tumblepipe.pipe.paths.naming import entity_stem


def latest_rig_version(asset_uri: Uri, channel_name: str) -> str | None:
    """The version 'latest' loads: the newest finished rig export
    (``get_latest_version_path``), None while there is none. A failed rig
    export leaves a newer, empty folder; taking the newest folder loaded it
    and failed with "has no rig file"."""
    version_path = get_latest_version_path(get_rig_export_path(asset_uri, channel_name))
    return None if version_path is None else version_path.name


# The top world control TumbleRig builds on every rig (apex_joints: Main,
# then Layout, Offset_NN chained under it). Its t/r/s are the rig parms
# Main_t/Main_r/Main_s, with no rest offset and no parent, so setting them
# moves the whole character.
PLACEMENT_CONTROL = 'Main'


def _shot_stage(native):
    """The stage fed into the th::animate this node sits in (its IN_stage),
    None when it is not inside one."""
    ancestor = native.parent()
    while ancestor is not None:
        stage_node = ancestor.node('IN_stage')
        if stage_node is not None and ancestor.type().category().name() == 'Lop':
            return stage_node.stage()
        ancestor = ancestor.parent()
    return None


def layout_placement(native, prim_path: str):
    """The world transform the shot stage gives ``prim_path`` at the start
    of the playbar, as (translate, rotate, scale) for the placement control;
    None outside th::animate, without that prim, or at the identity.

    Layout places a character on its USD prim, but the rig loads at the
    origin: every copy of a character started stacked there in APEX, and
    th::animate's zero_transforms then wiped the layout placement off the
    prim (Martin). Starting the rig at the placement keeps both in step:
    the posed geometry is world space, and zero_transforms is right.
    """
    import hou
    from pxr import Usd, UsdGeom

    stage = _shot_stage(native)
    if stage is None:
        return None
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid() or not prim.IsA(UsdGeom.Xformable):
        return None
    time = Usd.TimeCode(hou.playbar.playbackRange()[0])
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time)
    matrix = hou.Matrix4([list(matrix.GetRow(row)) for row in range(4)])
    if matrix.isAlmostEqual(hou.hmath.identityTransform()):
        return None
    parts = matrix.explode(transform_order='srt', rotate_order='xyz')
    return (
        tuple(parts['translate']),
        tuple(parts['rotate']),
        tuple(parts['scale']),
    )


def place_character(sop_node, translate, rotate, scale):
    """Python SOP body: set the rig parms of the placement control on the
    packed '.rig' of the character in ``sop_node``'s geometry. Warns and
    leaves the rig as it is when the rig has no such control."""
    import apex
    import hou

    geo = sop_node.geometry()
    values = {
        f'{PLACEMENT_CONTROL}_t': hou.Vector3(translate),
        f'{PLACEMENT_CONTROL}_r': hou.Vector3(rotate),
        f'{PLACEMENT_CONTROL}_s': hou.Vector3(scale),
    }
    for prim in geo.prims():
        name = prim.attribValue('name') if geo.findPrimAttrib('name') else ''
        if not name.endswith('.rig'):
            continue
        rig_geo = prim.getEmbeddedGeometry().freeze()
        graph = apex.Graph()
        graph.loadFromGeometry(rig_geo)
        if not graph.matchGraphInputs(f'{PLACEMENT_CONTROL}_t'):
            raise hou.NodeWarning(
                f"{name} has no '{PLACEMENT_CONTROL}' control: the "
                "character starts at the origin, not at its layout placement."
            )
        parms = rig_geo.dictAttribValue('parms')
        for key, value in values.items():
            parms[key] = value
        rig_geo.setGlobalAttribValue('parms', parms)
        prim.setEmbeddedGeometry(rig_geo)


class ImportRig(EntityNode):

    # import_rig only ever addresses assets, so 'from_context' inside a
    # shot workfile resolves to nothing rather than to the shot.
    ENTITY_CONTEXTS = ('assets',)

    def __init__(self, native):
        super().__init__(native)

    def list_entity_uris(self) -> list[str]:
        return ['from_context'] + [str(uri) for uri in self.list_asset_uris()]

    def list_asset_uris(self) -> list[Uri]:
        with api.config.coherent():
            asset_uris = api.config.list_entity_uris(
                filter=Uri.parse('entity:/assets'),
                closure=True
            )
            return [
                uri for uri in asset_uris
                if is_terminal_entity(api.config, uri)
            ]

    def list_version_names(self):
        """List available versions including 'latest'."""
        asset_uri = self.get_entity_uri()
        if asset_uri is None:
            return ['latest']
        channel_name = self.get_channel_name()
        export_uri = Uri.parse('export:/') / asset_uri.segments / channel_name / 'rig'
        asset_path = api.storage.resolve(export_uri)
        version_paths = list_version_paths(asset_path)
        version_names = [version_path.name for version_path in version_paths]
        return ['latest'] + version_names

    def get_instances(self):
        return self.parm('instances').eval()

    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names:
            raise self._error(
                f"rig version '{version_name}' is not one of {version_names}"
            )
        self.parm('version').set(version_name)
    
    def set_instances(self, instances):
        self.parm('instances').set(instances)

    def _update_labels(self):
        """Update label parameters to show current entity selection."""
        self.parm('entity_label').set(self._entity_label())

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name()
        if version_name == 'latest':
            asset_uri = self.get_entity_uri()
            if asset_uri is not None:
                version_name = (
                    latest_rig_version(asset_uri, self.get_channel_name())
                    or version_name
                )
        self.parm('version_label').set(version_name)

    def latest(self):
        """Set version to 'latest' (auto-resolves to newest on execute)."""
        self.parm('version').set('latest')
    
    def execute(self):
        self._update_labels()

        # Nodes
        context = self.native()
        import_node = context.node('import')
        bypass_node = context.node('bypass')
        dive_node = context.node('dive')
        output_node = dive_node.node('output')

        # Clear scene
        ns.clear_network(dive_node, output_node)

        # Parameters
        asset_uri = self.require_entity_uri()
        channel_name = self.get_channel_name()
        version_name = self.get_version_name()
        instances = self.get_instances()

        # Resolve 'latest' to actual version. No rig exported yet is the one
        # expected empty state; everything else raises.
        if version_name == 'latest':
            version_name = latest_rig_version(asset_uri, channel_name)
            if version_name is None:
                ns.set_node_comment(context, "Bypassed: No versions available")
                bypass_node.parm('input').set(0)
                return

        uri_name = entity_stem(asset_uri)

        # Paths (includes channel)
        export_uri = Uri.parse('export:/') / asset_uri.segments / channel_name / 'rig' / version_name
        version_path = api.storage.resolve(export_uri)
        file_path = version_path / f"{entity_stem(asset_uri, 'rig', version_name)}.bgeo.sc"
        if not file_path.exists():
            raise self._error(
                f'rig {version_name} of {asset_uri} has no rig file: '
                f'{file_path}'
            )

        # Update version_label with resolved version
        self.parm('version_label').set(version_name)
        
        # Load rig
        import_node.parm('file').set(path_str(file_path))
        import_node.parm('reload').pressButton()
        
        # Create instances
        prev_node = dive_node.indirectInputs()[0]
        source_node = dive_node.indirectInputs()[1]
        base_name = asset_uri.segments[-1]  # Last segment is the asset name
        prim_base_path = '/'.join(asset_uri.segments[1:])  # e.g., "char/hero"
        prim_parent_path = '/'.join(asset_uri.segments[1:-1])  # e.g., "char"
        for instance_index in range(instances):
            anchor_node = source_node
            instance_name = base_name if instances == 1 else f'{base_name}{instance_index}'

            # Rename the packed primitives if necessary
            if instances > 1:
                rename_node = rename_packed.create(dive_node, f'{uri_name}_{instance_name}_rename')
                rename_node.set_from_path(f'/{prim_base_path}/*')
                rename_node.set_to_path(f'/{prim_parent_path}/{instance_name}/*')
                rename_node.native().setInput(0, anchor_node)
                anchor_node = rename_node.native()

            # Start the character where layout placed it
            placement = layout_placement(
                context, f'/{prim_parent_path}/{instance_name}'
            )
            if placement is not None:
                place_node = dive_node.createNode(
                    'python', f'{uri_name}_{instance_name}_place'
                )
                place_node.parm('python').set(
                    'from tumblepipe.pipe.houdini.sops import import_rig\n'
                    f'import_rig.place_character(hou.pwd(), *{placement!r})\n'
                )
                place_node.setInput(0, anchor_node)
                anchor_node = place_node

            # Create the add character to scene node
            add_node = dive_node.createNode('apex::sceneaddcharacter', f'{uri_name}_{instance_name}_add')
            add_node.parm('charactername').deleteAllKeyframes()
            add_node.parm('charactername').set(instance_name)

            # Connect the nodes
            add_node.setInput(0, prev_node)
            add_node.setInput(1, anchor_node)
            prev_node = add_node
        
        # Connect to output
        output_node.setInput(0, prev_node)

        # Layout the dive nodes
        dive_node.layoutChildren()

        # Disable bypass
        bypass_node.parm('input').set(1)

        # Set success comment
        asset_name = asset_uri.segments[-1]
        ns.set_node_comment(context, f"Imported: {asset_name}\n{version_name}")

def create(scene, name):
    return ns.create_node(scene, name, ImportRig, 'import_rig', 'Sop')

def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_DIVE)
    node = ImportRig(raw_node)
    node._update_labels()


def execute(kwargs):
    raw_node = kwargs['node']
    node = ImportRig(raw_node)
    node.execute()

def select(kwargs):
    """Entity button: pick the asset, then import its rig."""
    node = ImportRig(kwargs['node'])
    if node.pick_entity('Select Asset'):
        node.execute()