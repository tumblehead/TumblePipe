from pathlib import Path

import hou

from tumblehead.api import default_client, path_str
from tumblehead.util.io import load_json
from tumblehead.util.uri import Uri
from tumblehead.config.timeline import FrameRange, get_frame_range, get_fps
from tumblehead.config.department import list_departments
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.houdini.util as util
from tumblehead.pipe.paths import (
    get_workfile_context,
    load_entity_context,
    current_staged_file_path,
    get_latest_staged_file_path,
    get_staged_file_path,
    list_version_paths
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

def _set_shot_metadata_script(shot_uri, frame_range, fps, version_name, assets):
    """Generate Python script to set shot metadata in USD stage."""

    # Generate metadata prim path (keeps all segments: /_METADATA/_shots/_seq/_shot)
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

    # Add asset metadata prims
    for asset_info in assets:
        asset_uri_str = asset_info['asset']
        asset_uri = Uri.parse_unsafe(asset_uri_str)
        asset_prim_path = uri_to_metadata_prim_path(asset_uri)
        asset_name = asset_uri.segments[-1]
        # Propagate inputs from staged context.json to preserve composition history
        asset_inputs = asset_info.get('inputs', [])

        content.extend([
            "",
            f"# Asset: {asset_uri_str}",
            f"asset_prim = stage.DefinePrim('{asset_prim_path}', 'Scope')",
            f"util.set_metadata(asset_prim, {{",
            f"    'uri': '{asset_uri_str}',",
            f"    'instance': '{asset_name}',",
            f"    'inputs': {asset_inputs!r}",
            f"}})",
        ])

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
        return [entity.uri for entity in shot_entities]

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

    def list_department_names(self) -> list[str]:
        shot_departments = list_departments('shots')
        return [dept.name for dept in shot_departments]

    def list_version_names(self) -> list[str]:
        """List available staged versions including 'latest' and 'current'."""
        shot_uri = self.get_shot_uri()
        if shot_uri is None:
            return ['latest', 'current']

        # Get staged directory
        staged_uri = Uri.parse_unsafe('export:/') / shot_uri.segments / '_staged'
        staged_path = api.storage.resolve(staged_uri)

        # Get versioned directories (v0001, v0002, etc.)
        version_paths = list_version_paths(staged_path)
        version_names = [vp.name for vp in version_paths]

        # Add special options at the beginning
        return ['latest', 'current'] + version_names

    def get_version_name(self) -> str:
        """Get selected version name. Default is 'latest'."""
        version_name = self.parm('version').eval()
        if len(version_name) == 0:
            return 'latest'  # Default to latest
        return version_name

    def get_department_name(self) -> str | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                context = _context_from_workfile()
                if context is None: return None
                return context.department_name
            case 'from_settings':
                department_names = self.list_department_names()
                if len(department_names) == 0: return None
                department_name = self.parm('department').eval()
                if len(department_name) == 0: return department_names[0]
                if department_name not in department_names: return None
                return department_name
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

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

    def set_department_name(self, department_name: str):
        self.parm('department').set(department_name)

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

    def execute(self):

        # Get nodes
        context = self.native()
        import_node = context.node('import')
        mute_node = context.node('mute')
        metadata_node = context.node('metadata')
        context.node('procedurals')

        # Get parameters
        shot_uri = self.get_shot_uri()
        if shot_uri is None:
            raise ValueError("No shot selected. Check entity source and shot parameter.")

        # Get staged file path based on version selection
        version_name = self.get_version_name()

        if version_name == 'latest':
            # Use _staged/latest/ directory
            staged_file_path = get_latest_staged_file_path(shot_uri)
        elif version_name == 'current':
            # Use highest numbered version (previous default behavior)
            staged_file_path = current_staged_file_path(shot_uri)
        else:
            # Use specific version
            staged_file_path = get_staged_file_path(shot_uri, version_name)

        if staged_file_path is None:
            raise FileNotFoundError(f"No staged build found for {shot_uri}")
        if not staged_file_path.exists():
            raise FileNotFoundError(f"Staged file not found: {staged_file_path}")

        # Read context.json for asset information
        context_path = staged_file_path.parent / 'context.json'
        context_data = load_json(context_path) if context_path.exists() else None
        assets = []
        if context_data is not None:
            assets = context_data.get('parameters', {}).get('assets', [])

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
                version_name,
                assets
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

    node = ImportShot(raw_node)

    # Change entity source to settings if we have no context
    entity = _entity_from_context_json()
    if entity is None:
        node.parm('entity_source').set('from_settings')

    # Always set default shot (ensures department menu works correctly)
    shot_uris = node.list_shot_uris()
    if shot_uris:
        node.set_shot_uri(shot_uris[0])

def execute():
    raw_node = hou.pwd()
    node = ImportShot(raw_node)
    node.execute()