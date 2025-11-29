from pathlib import Path

import hou

from tumblehead.api import default_client, path_str
from tumblehead.util.uri import Uri
from tumblehead.config.timeline import FrameRange, get_frame_range, get_fps
from tumblehead.config.department import list_departments
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.houdini.util as util
from tumblehead.pipe.paths import (
    get_workfile_context,
    load_entity_context,
    latest_staged_path
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/import_shot')

def _context_from_workfile():
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return None
    # Check if entity_uri is a shot (entity:/shots/sequence/shot)
    if not str(context.entity_uri).startswith('entity:/shots/'): return None
    if len(context.entity_uri) != 4: return None
    return context

def _get_group_context():
    """Check if current workfile is part of a group context"""
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return None
    # Check if entity_uri is a group (entity:/groups/...)
    if str(context.entity_uri).startswith('entity:/groups/'):
        return context
    return None

def _entity_from_context_json():
    # Path to current workfile
    file_path = Path(hou.hipFile.path())
    if not file_path.exists(): return None

    # Look for context.json in the workfile directory
    context_json_path = file_path.parent / "context.json"
    if not context_json_path.exists(): return None

    # Load context using shared helper
    context = load_entity_context(context_json_path)
    if context is None: return None

    # Verify it's a shot entity
    if context.entity_uri.purpose != 'entity': return None
    if len(context.entity_uri.segments) < 1: return None
    if context.entity_uri.segments[0] != 'shots': return None

    return context

def _indent(lines):
    """Indent lines of code by 4 spaces."""
    return [f"    {line}" for line in lines]

def _set_shot_metadata_script(shot_uri, frame_range, fps, version_name):
    """Generate Python script to set shot metadata in USD stage."""

    # Generate metadata prim path (keeps all segments: /METADATA/shots/seq/shot)
    from tumblehead.pipe.houdini.util import uri_to_metadata_prim_path
    metadata_prim_path = uri_to_metadata_prim_path(shot_uri)

    header = [
        'import hou',
        '',
        'from tumblehead.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
        'def update(root):'
    ]

    content = [
        f"# Create metadata prim for shot",
        f"prim_path = '{metadata_prim_path}'",
        f"prim = root.GetPrimAtPath(prim_path)",
        "if not prim.IsValid():",
        f"    prim = stage.DefinePrim(prim_path)",
        "",
        "# Set shot metadata",
        "metadata = {",
        f"    'uri': '{str(shot_uri)}',",
        f"    'start_frame': {frame_range.start_frame},",
        f"    'end_frame': {frame_range.end_frame},",
        f"    'start_roll': {frame_range.start_roll},",
        f"    'end_roll': {frame_range.end_roll},",
        f"    'fps': {fps},",
        f"    'version': '{version_name}',",
        "    'inputs': []",
        "}",
        "",
        "util.set_metadata(prim, metadata)",
    ]

    footer = ["", "update(root)"]

    script = header + _indent(content) + footer
    return '\n'.join(script)

class ImportShot(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_shot_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return list(shot_entities)

    def list_asset_department_names(self):
        asset_departments = list_departments('assets')
        if len(asset_departments) == 0: return []
        asset_department_names = [d.name for d in asset_departments]
        build_shot_defaults_uri = Uri.parse_unsafe('defaults:/houdini/lops/build_shot')
        default_values = api.config.get_properties(build_shot_defaults_uri)
        return [
            asset_department_name
            for asset_department_name in default_values['departments']['asset']
            if asset_department_name in asset_department_names
        ]

    def list_shot_department_names(self):
        shot_departments = list_departments('shots')
        shot_department_names = [d.name for d in shot_departments]
        build_shot_defaults_uri = Uri.parse_unsafe('defaults:/houdini/lops/build_shot')
        default_values = api.config.get_properties(build_shot_defaults_uri)
        return [
            shot_department_name
            for shot_department_name in default_values['departments']['shot']
            if shot_department_name in shot_department_names
        ]
    
    def get_entity_source(self):
        return self.parm('entity_source').eval()

    def get_shot_uri(self) -> Uri | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                context = _entity_from_context_json()
                if context is None: return None
                return context.entity_uri
            case 'from_settings':
                shot_uris = self.list_shot_uris()
                if len(shot_uris) == 0: return None
                shot_uri_raw = self.parm('shot').eval()
                if len(shot_uri_raw) == 0: return shot_uris[0]
                shot_uri = Uri.parse_unsafe(shot_uri_raw)
                if shot_uri not in shot_uris: return None
                return shot_uri
            case _:
                raise AssertionError(f'Unknown entity source: {entity_source}')
        
    def get_exclude_asset_department_names(self):
        return list(filter(len, self.parm('asset_departments').eval().split(' ')))

    def get_exclude_shot_department_names(self):
        return list(filter(len, self.parm('shot_departments').eval().split(' ')))
    
    def get_asset_department_names(self):
        asset_department_names = self.list_asset_department_names()
        if len(asset_department_names) == 0: return []
        exclude_asset_department_names = self.get_exclude_asset_department_names()
        return [
            asset_department_name
            for asset_department_name in asset_department_names
            if asset_department_name not in exclude_asset_department_names
        ]

    def get_shot_department_names(self):
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        exclude_shot_department_names = self.get_exclude_shot_department_names()
        return [
            shot_department_name
            for shot_department_name in shot_department_names
            if shot_department_name not in exclude_shot_department_names
        ]
    
    def get_downstream_shot_department_names(self):
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        context = _context_from_workfile()
        if context is None: return []
        department_name = context.department_name
        if department_name not in shot_department_names: return []
        shot_department_index = shot_department_names.index(department_name)
        return shot_department_names[shot_department_index + 1:]

    def get_frame_range_source(self):
        return self.parm('frame_range').eval()

    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_config':
                shot_uri = self.get_shot_uri()
                if shot_uri is None: return None
                frame_range = get_frame_range(shot_uri)
                return frame_range
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                )
            case _:
                assert False, f'Unknown frame range token: {frame_range_source}'
    
    def get_include_downstream_departments(self):
        return bool(self.parm('include_downstream_departments').eval())
            
    def get_include_procedurals(self):
        return bool(self.parm('include_procedurals').eval())
    
    def set_entity_source(self, entity_source):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources: return
        self.parm('entity_source').set(entity_source)

    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if shot_uri not in shot_uris: return
        self.parm('shot').set(str(shot_uri))
    
    def set_exclude_asset_department_names(self, exclude_asset_department_names):
        asset_department_names = self.list_asset_department_names()
        self.parm('asset_departments').set(' '.join([
            department_name
            for department_name in exclude_asset_department_names
            if department_name in asset_department_names
        ]))

    def set_exclude_shot_department_names(self, exclude_shot_department_names):
        shot_department_names = self.list_shot_department_names()
        self.parm('shot_departments').set(' '.join([
            department_name
            for department_name in exclude_shot_department_names
            if department_name in shot_department_names
        ]))
    
    def set_frame_range_source(self, frame_range_source):
        match frame_range_source:
            case 'from_config': self.parm('frame_range').set('from_config')
            case 'from_settings': self.parm('frame_range').set('from_settings')
            case _: assert False, f'Invalid frame range source {frame_range_source}'
        self.state.frame_range_source = frame_range_source
    
    def set_frame_range(self, frame_range):
        self.parm('frame_settingsx').set(frame_range.start_frame)
        self.parm('frame_settingsy').set(frame_range.end_frame)
        self.parm('roll_settingsx').set(frame_range.start_roll)
        self.parm('roll_settingsy').set(frame_range.end_roll)
    
    def set_include_downstream_departments(self, include_downstream_departments):
        self.parm('include_downstream_departments').set(int(include_downstream_departments))
    
    def set_include_procedurals(self, include_procedurals):
        self.parm('include_procedurals').set(int(include_procedurals))

    def _execute_group_import(self, group_context):
        """Execute group import - loads all member staged files with frame offsets"""

        # Get group and filter to shot members only
        # Extract group name from context URI (entity:/groups/{context}/{name})
        group_name = group_context.entity_uri.segments[-1] if len(group_context.entity_uri.segments) > 0 else None
        if not group_name:
            print("Error: Invalid group context URI")
            return

        # Get group context (shots or assets) from URI
        group_type_context = group_context.entity_uri.segments[1] if len(group_context.entity_uri.segments) > 1 else 'shots'

        groups = api.config.list_groups(group_type_context)
        group = next((g for g in groups if g.uri.segments[-1] == group_name), None)
        if not group:
            print(f"Error: Group '{group_name}' not found in configuration")
            return

        # Filter to shot members only
        shot_members = []
        for member_uri in group.members:
            # Check if member_uri is a shot (entity:/shots/...)
            if str(member_uri).startswith('entity:/shots/'):
                shot_members.append(member_uri)

        if not shot_members:
            print(f"Warning: Group '{group_name}' has no shot members")
            return

        # Get nodes
        context = self.native()
        dive_node = context.node('dive')
        output_node = context.node('output')

        if dive_node is None or output_node is None:
            print("Error: Required nodes 'dive' or 'output' not found")
            return

        # Clear existing nodes in dive
        for child in dive_node.children():
            child.destroy()

        # Create merge node for all member imports
        merge_node = dive_node.createNode('merge', 'merge_members')
        merge_node.setComment(f'Merge all member shots for group: {group_name}')

        # Track previous node for connections
        prev_node = None

        # Process each member shot
        for member_index, member_uri in enumerate(shot_members):

            # Get staged file path for this member (member_uri is already the shot URI)
            staged_file_path = latest_staged_path(member_uri)

            if staged_file_path is None or not staged_file_path.exists():
                print(f"Warning: No staged file found for {member_uri}")
                continue

            # Calculate member's frame offset in group timeline
            # Offset is the sum of all previous members' frame ranges
            member_offset_start = 0
            for prev_member_uri in shot_members[:member_index]:
                prev_frame_range = get_frame_range(prev_member_uri)
                if prev_frame_range:
                    member_offset_start += len(prev_frame_range.full_range())

            # Get member's original frame range
            member_frame_range = get_frame_range(member_uri)
            member_offset_end = member_offset_start + len(member_frame_range.full_range()) - 1

            # Create node name and prim path from URI segments
            member_uri_name = '_'.join(member_uri.segments[1:])
            member_prim_path = '/'.join(member_uri.segments[1:])

            # Calculate timeshift offset
            # Formula: offset = -member_start_frame + member_group_offset_start
            # This converts member's timeline (e.g., 1001-1050) to group timeline (e.g., 0-49)
            timeshift_offset = -member_frame_range.start_frame + member_offset_start

            # Create subnet for this member
            member_subnet_name = f'member_{member_uri_name}'
            member_subnet = dive_node.createNode('subnet', member_subnet_name)
            member_subnet.setComment(
                f'Member: {member_uri}\n'
                f'Original range: {member_frame_range.start_frame}-{member_frame_range.end_frame}\n'
                f'Group range: {member_offset_start}-{member_offset_end}'
            )

            # Create import node inside subnet
            import_node = member_subnet.createNode('sublayer', 'import_staged')
            import_node.parm('filepath1').set(path_str(staged_file_path))
            import_node.setComment(f'Staged file for {member_uri}')

            # Create mute node for excluded departments
            mute_node = member_subnet.createNode('muteprims', 'mute_excluded')
            mute_node.setInput(0, import_node)

            # Build mute paths based on excluded departments for this member
            mute_paths = []

            # Mute excluded shot departments (using URI path for USD prim paths)
            for dept in self.get_exclude_shot_department_names():
                mute_paths.append(f'*/shots/{member_prim_path}/{dept}/*')

            # Mute excluded asset departments (all assets)
            for dept in self.get_exclude_asset_department_names():
                mute_paths.append(f'*/assets/*/{dept}/*')

            # Set mute paths parameter (space-separated)
            mute_node.parm('mutepaths').set(' '.join(mute_paths))

            # Create timeshift node to offset member timeline to group timeline
            timeshift_node = member_subnet.createNode('timeshift', 'group_offset')
            timeshift_node.setInput(0, mute_node)
            timeshift_node.parm('offset').set(timeshift_offset)
            timeshift_node.setComment(
                f'Offset member timeline to group timeline\n'
                f'Member range: {member_frame_range.start_frame}-{member_frame_range.end_frame}\n'
                f'Group offset: {member_offset_start}-{member_offset_end}\n'
                f'Timeshift: {timeshift_offset}'
            )

            # Create metadata node for this member
            metadata_node = member_subnet.createNode('pythonscript', 'metadata')
            metadata_node.setInput(0, timeshift_node)

            # Generate metadata script
            version_name = staged_file_path.parent.name
            metadata_script = _set_shot_metadata_script(
                member_uri,
                member_frame_range,
                get_fps(),
                version_name
            )
            metadata_node.parm('python').set(metadata_script)

            # Create subnet output
            subnet_output = member_subnet.createNode('output', 'output0')
            subnet_output.setInput(0, metadata_node)
            subnet_output.setDisplayFlag(True)
            subnet_output.setRenderFlag(True)

            # Layout nodes in subnet
            member_subnet.layoutChildren()

            # Connect member subnet to merge node
            merge_node.setInput(member_index, member_subnet)

            # Track for final connection
            prev_node = merge_node

        # Connect merge to dive output
        if prev_node is not None:
            output_node.setInput(0, prev_node)

        # Layout nodes
        dive_node.layoutChildren()

        # Set group frame range - calculate total frames across all members
        total_frames = 0
        for member_uri in shot_members:
            member_frame_range = get_frame_range(member_uri)
            if member_frame_range:
                total_frames += len(member_frame_range.full_range())
        if total_frames > 0:
            group_frame_range = FrameRange(
                start_frame=0,
                end_frame=total_frames - 1,
                start_roll=0,
                end_roll=0
            )
            util.set_frame_range(group_frame_range)

    def execute(self):

        # Check if we're in a group context
        group_context = _get_group_context()

        if group_context:
            # GROUP IMPORT IMPLEMENTATION
            return self._execute_group_import(group_context)

        # Get nodes
        context = self.native()
        import_node = context.node('import')
        mute_node = context.node('mute')
        metadata_node = context.node('metadata')
        context.node('procedurals')

        # Get parameters
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return

        # Get staged file path
        staged_file_path = latest_staged_path(shot_uri)
        if staged_file_path is None: return
        if not staged_file_path.exists(): return

        # Set import node filepath
        import_node.parm('filepath1').set(path_str(staged_file_path))

        # Create prim path from URI segments
        shot_prim_path = '/'.join(shot_uri.segments[1:])

        # Build mutepaths based on excluded departments
        mute_paths = []

        # Mute excluded shot departments
        for dept in self.get_exclude_shot_department_names():
            mute_paths.append(f'*/shots/{shot_prim_path}/{dept}/*')

        # Mute excluded asset departments (all assets)
        for dept in self.get_exclude_asset_department_names():
            mute_paths.append(f'*/assets/*/{dept}/*')

        # Set mutepaths parameter (space-separated)
        mute_node.parm('mutepaths').set(' '.join(mute_paths))

        # Set frame range
        frame_range = self.get_frame_range()
        if frame_range is not None:
            util.set_frame_range(frame_range)

        # Set shot metadata script
        if frame_range is not None and staged_file_path is not None:
            version_name = staged_file_path.parent.name

            # Generate the metadata Python script
            metadata_script = _set_shot_metadata_script(
                shot_uri,
                frame_range,
                get_fps(),
                version_name
            )

            # Set the script on the metadata pythonscript node
            metadata_node.parm('python').set(metadata_script)

def create(scene, name):
    node_type = ns.find_node_type('import_shot', 'Lop')
    assert node_type is not None, 'Could not find import_shot node type'
    native = scene.node(name)
    if native is not None: return ImportShot(native)
    return ImportShot(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Change entity source to settings if we have no context
    entity = _entity_from_context_json()
    if entity is not None: return
    node = ImportShot(raw_node)
    node.parm('entity_source').set('from_settings')

def execute():
    raw_node = hou.pwd()
    node = ImportShot(raw_node)
    node.execute()