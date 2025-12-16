from pathlib import Path
import os
import re

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

    # Verify it's a shot entity (not a group)
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
        # Get instance count (default 1 if not specified)
        instances = asset_info.get('instances', 1)

        content.extend([
            "",
            f"# Asset: {asset_uri_str}",
            f"asset_prim = stage.DefinePrim('{asset_prim_path}', 'Scope')",
            f"util.set_metadata(asset_prim, {{",
            f"    'uri': '{asset_uri_str}',",
            f"    'instance': '{asset_name}',",
            f"    'instances': {instances},",
            f"    'inputs': {asset_inputs!r}",
            f"}})",
        ])

    footer = ["", "update(root)"]

    script = header + _indent(content) + footer
    return '\n'.join(script)

def _get_scene_assets(shot_uri: Uri) -> list[dict]:
    """Get current asset data from scene context.json, including inherited assets.

    Reads scene reference from root layer's context.json (same source as USD composition).
    Also includes inherited assets from parent scenes in the hierarchy.

    Args:
        shot_uri: The shot entity URI

    Returns:
        List of asset dicts with keys: asset, instance, variant, instances, inputs
        Inherited assets appear first, direct scene assets can override them.
    """
    from tumblehead.pipe.paths import latest_export_path, get_scene_latest_path
    from tumblehead.config.variants import DEFAULT_VARIANT
    from tumblehead.config.scenes import get_inherited_assets

    # Get root layer to find scene reference (same approach as graph.py Fix 2)
    root_version_path = latest_export_path(shot_uri, DEFAULT_VARIANT, 'root')
    if root_version_path is None:
        return []

    # Read scene reference from root's context.json
    root_context_path = root_version_path / 'context.json'
    root_context_data = load_json(root_context_path)
    if root_context_data is None:
        return []

    scene_ref = root_context_data.get('parameters', {}).get('scene')
    if scene_ref is None:
        return []

    # Follow scene reference to get current assets
    scene_uri = Uri.parse_unsafe(scene_ref)
    scene_layer_path = get_scene_latest_path(scene_uri)
    if scene_layer_path is None:
        return []

    scene_context_path = scene_layer_path.parent / 'context.json'
    if not scene_context_path.exists():
        return []

    scene_context = load_json(scene_context_path)
    if scene_context is None:
        return []

    # Get direct scene assets
    scene_assets = scene_context.get('parameters', {}).get('assets', [])

    # Get inherited assets from parent scenes
    inherited = get_inherited_assets(scene_uri)

    # Build result: inherited first, then direct (direct overrides inherited)
    result = []
    seen_assets = set()

    # Add inherited assets (from furthest parent to closest)
    for entry, parent_uri in reversed(inherited):
        if entry.asset not in seen_assets:
            asset_uri = Uri.parse_unsafe(entry.asset)
            result.append({
                'asset': entry.asset,
                'instance': asset_uri.segments[-1],
                'variant': entry.variant,
                'instances': entry.instances,
                'inputs': []
            })
            seen_assets.add(entry.asset)

    # Add/override with direct scene assets
    for asset_data in scene_assets:
        asset_uri_str = asset_data['asset']
        asset_uri = Uri.parse_unsafe(asset_uri_str)
        new_entry = {
            'asset': asset_uri_str,
            'instance': asset_uri.segments[-1],
            'variant': asset_data.get('variant', 'default'),
            'instances': asset_data.get('instances', 1),
            'inputs': []
        }
        if asset_uri_str in seen_assets:
            # Override inherited entry
            for i, item in enumerate(result):
                if item['asset'] == asset_uri_str:
                    result[i] = new_entry
                    break
        else:
            result.append(new_entry)
            seen_assets.add(asset_uri_str)

    return result


def _parse_staged_sublayers(staged_file_path: Path) -> list[dict]:
    """Parse staged .usda file and extract sublayer info.

    Returns list of dicts with:
    - path: absolute Path to the layer file
    - type: 'shot_department' | 'asset' | 'root'
    - department: department name (for shot_department type)
    """
    sublayers = []
    staged_dir = staged_file_path.parent

    with open(staged_file_path, 'r') as f:
        content = f.read()

    # Extract sublayer paths from USDA
    for match in re.finditer(r'@([^@]+)@', content):
        rel_path = match.group(1)
        # Use normpath instead of resolve() to preserve drive letter (avoid UNC paths)
        abs_path = Path(os.path.normpath(staged_dir / rel_path))

        # Categorize layer
        if '/root/' in rel_path:
            sublayers.append({'path': abs_path, 'type': 'root', 'department': 'root'})
        elif '/_staged/' in rel_path:
            sublayers.append({'path': abs_path, 'type': 'asset', 'department': None})
        else:
            # Shot department layer - extract dept name from path
            # Pattern: ../../../{variant}/{dept}/v{version}/shots_{seq}_{shot}_{dept}_{version}.usd
            # parts: [0]='..' [1]='..' [2]='..' [3]=variant [4]=dept [5]=version [6]=filename
            parts = rel_path.split('/')
            dept = parts[4] if len(parts) > 4 else None
            sublayers.append({'path': abs_path, 'type': 'shot_department', 'department': dept})

    return sublayers


class ImportShot(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_shot_uris(self) -> list[str]:
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        uris = [entity.uri for entity in shot_entities]
        return ['from_context'] + [str(uri) for uri in uris]

    def list_asset_department_names(self):
        return [d.name for d in list_departments('assets') if d.renderable]

    def list_shot_department_names(self):
        return [d.name for d in list_departments('shots') if d.renderable]

    def list_department_names(self) -> list[str]:
        shot_departments = list_departments('shots')
        names = [dept.name for dept in shot_departments]
        return ['from_context'] + names

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
        department_name = self.parm('department').eval()
        if department_name == 'from_context':
            # Use same context source as get_shot_uri() for consistency
            context = _entity_from_context_json()
            if context is None: return None
            return context.department_name
        # From settings
        department_names = self.list_department_names()
        if len(department_names) == 0: return None
        if len(department_name) == 0: return department_names[1] if len(department_names) > 1 else None
        if department_name not in department_names: return None
        return department_name

    def get_shot_uri(self) -> Uri | None:
        shot_uri_raw = self.parm('entity').eval()
        if shot_uri_raw == 'from_context':
            context = _entity_from_context_json()
            if context is None: return None
            return context.entity_uri
        # From settings
        shot_uris = self.list_shot_uris()
        if len(shot_uris) <= 1: return None  # Only 'from_context' means no real URIs
        if len(shot_uri_raw) == 0: return Uri.parse_unsafe(shot_uris[1])  # Skip 'from_context'
        if shot_uri_raw not in shot_uris: return None  # Compare strings
        return Uri.parse_unsafe(shot_uri_raw)
    def get_downstream_shot_department_names(self):
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        if department_name not in shot_department_names: return []
        shot_department_index = shot_department_names.index(department_name)
        return shot_department_names[shot_department_index + 1:]

    def get_frame_range(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return None
        return get_frame_range(shot_uri)
            
    def get_include_procedurals(self):
        return bool(self.parm('include_procedurals').eval())

    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if str(shot_uri) not in shot_uris: return  # Compare strings
        self.parm('entity').set(str(shot_uri))

    def set_department_name(self, department_name: str):
        self.parm('department').set(department_name)

    def set_include_procedurals(self, include_procedurals):
        self.parm('include_procedurals').set(int(include_procedurals))

    def _update_labels(self):
        """Update label parameters to show current entity/department selection."""
        entity_raw = self.parm('entity').eval()
        if entity_raw == 'from_context':
            shot_uri = self.get_shot_uri()
            if shot_uri:
                self.parm('entity_label').set(f'from_context: {shot_uri}')
            else:
                self.parm('entity_label').set('from_context: none')
        else:
            # Specific entity URI selected
            self.parm('entity_label').set(entity_raw)

        department_raw = self.parm('department').eval()
        if department_raw == 'from_context':
            department_name = self.get_department_name()
            if department_name:
                self.parm('department_label').set(f'from_context: {department_name}')
            else:
                self.parm('department_label').set('from_context: none')
        else:
            # Specific department selected - no label needed
            self.parm('department_label').set('')

        # Update version label when 'latest' or 'current' is selected
        version_raw = self.parm('version').eval()
        if version_raw in ('latest', 'current', ''):
            shot_uri = self.get_shot_uri()
            if shot_uri is not None:
                # For 'latest', use current_staged_file_path to get actual version number
                # (get_latest_staged_file_path returns path in 'latest' directory)
                actual_file_path = current_staged_file_path(shot_uri)
                if actual_file_path is not None:
                    resolved_version = actual_file_path.parent.name
                    self.parm('version_label').set(resolved_version)
                else:
                    self.parm('version_label').set('')
            else:
                self.parm('version_label').set('')
        else:
            # Specific version selected - no label needed
            self.parm('version_label').set('')

    def _initialize(self):
        """Initialize node and update labels to show resolved values."""
        self._update_labels()

    def execute(self):
        self._update_labels()

        # Get nodes
        context = self.native()
        import_node = context.node('import')
        metadata_node = context.node('metadata')
        context.node('procedurals')

        # Get parameters
        shot_uri = self.get_shot_uri()
        if shot_uri is None:
            ns.set_node_comment(context, "Bypassed: No shot selected")
            context.bypass(True)
            return

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

        if staged_file_path is None or not staged_file_path.exists():
            # No staged build yet - set empty state and return
            import_node.parm('num_files').set(0)
            return

        # Get assets based on version type
        if version_name in ('latest', 'current'):
            # For latest/current: union of scene assets + context.json assets
            # - Scene assets: what's currently in live scene (USD composition follows latest)
            # - Context.json: what was built (has provenance inputs from department exports)
            scene_assets = _get_scene_assets(shot_uri)

            # Read context.json for additional assets from department exports
            context_path = staged_file_path.parent / 'context.json'
            context_data = load_json(context_path) if context_path.exists() else None
            context_assets = []
            if context_data is not None:
                context_assets = context_data.get('parameters', {}).get('assets', [])

            # Build lookup for context asset inputs (provenance tracking)
            context_inputs_lookup = {
                ctx_asset.get('asset'): ctx_asset.get('inputs', [])
                for ctx_asset in context_assets
            }

            # Union: use scene assets as base, but merge inputs from context
            # This preserves provenance info (which department introduced each asset)
            assets = []
            scene_asset_uris = set()
            for scene_asset in scene_assets:
                asset_uri = scene_asset['asset']
                scene_asset_uris.add(asset_uri)
                # Merge inputs from context if available
                merged_asset = scene_asset.copy()
                if asset_uri in context_inputs_lookup:
                    merged_asset['inputs'] = context_inputs_lookup[asset_uri]
                assets.append(merged_asset)

            # Add any context assets not in scene
            for ctx_asset in context_assets:
                if ctx_asset.get('asset') not in scene_asset_uris:
                    assets.append(ctx_asset)
        else:
            # For specific versions: only context.json (frozen state at build time)
            # USD dependencies are fixed in versioned builds
            context_path = staged_file_path.parent / 'context.json'
            context_data = load_json(context_path) if context_path.exists() else None
            assets = []
            if context_data is not None:
                assets = context_data.get('parameters', {}).get('assets', [])

        # Parse sublayers from staged file
        sublayers = _parse_staged_sublayers(staged_file_path)

        # Exclude current department AND downstream departments
        department_name = self.get_department_name()
        departments_to_exclude = set(self.get_downstream_shot_department_names())
        if department_name is not None:
            departments_to_exclude.add(department_name)

        # Filter out excluded layers - don't load them at all
        layers_to_load = list(reversed([
            info for info in sublayers
            if info['department'] is None or info['department'] not in departments_to_exclude
        ]))

        # Configure Sublayer LOP with filtered layers
        import_node.parm('num_files').set(len(layers_to_load))

        for i, info in enumerate(layers_to_load):
            layer_path = path_str(info['path'])
            import_node.parm(f'filepath{i+1}').set(layer_path)
            import_node.parm(f'enable{i+1}').set(1)

        # Set frame range and FPS
        frame_range = self.get_frame_range()
        if frame_range is not None:
            util.set_frame_range(frame_range)
            fps = get_fps(shot_uri)
            if fps is not None:
                util.set_fps(fps)

        # Set shot metadata script
        if frame_range is not None and staged_file_path is not None:
            version_name = staged_file_path.parent.name

            # Generate the metadata Python script
            metadata_script = _set_shot_metadata_script(
                shot_uri,
                frame_range,
                get_fps(shot_uri),
                version_name,
                assets
            )

            # Set the script on the metadata pythonscript node
            metadata_node.parm('python').set(metadata_script)

        # Handle asset duplication for assets with instances > 1
        self._setup_asset_duplication(context, assets)

        # Set success comment with import metadata
        resolved_version = staged_file_path.parent.name
        # Try to get timestamp and user from context.json
        if context_data is not None:
            outputs = context_data.get('outputs', [])
            if outputs:
                output = outputs[0]
                timestamp = output.get('timestamp', '')
                user = output.get('user', '')
                if timestamp and user:
                    ns.set_node_comment(context, f"Imported: {resolved_version}\n{timestamp}\nby {user}")
                else:
                    ns.set_node_comment(context, f"Imported: {resolved_version}")
            else:
                ns.set_node_comment(context, f"Imported: {resolved_version}")
        else:
            ns.set_node_comment(context, f"Imported: {resolved_version}")

    def _setup_asset_duplication(self, context, assets):
        """Configure duplication for assets with instance count > 1.

        Creates/updates duplicate nodes for each asset that needs multiple instances.
        The 'duplicates' subnet is part of the HDA template.
        """
        from tumblehead.pipe.houdini.util import uri_to_prim_path, uri_to_metadata_prim_path
        from tumblehead.api import default_client

        api = default_client()

        # Get the duplicates subnet (part of HDA template)
        duplicates_node = context.node('duplicates')

        # Clear existing duplicate nodes inside the subnet
        for child in list(duplicates_node.children()):
            if child.name() not in ('output0',) and not child.name().startswith('input'):
                child.destroy()

        # Get input and output of duplicates subnet
        duplicates_input = duplicates_node.indirectInputs()[0] if duplicates_node.indirectInputs() else None
        duplicates_output = duplicates_node.node('output0')

        prev_node = duplicates_input

        # Create duplicate nodes for each asset with instances > 1
        for asset_info in assets:
            instances = asset_info.get('instances', 1)
            if instances <= 1:
                continue

            asset_uri_str = asset_info.get('asset', '')
            if not asset_uri_str:
                continue

            asset_uri = Uri.parse_unsafe(asset_uri_str)
            asset_prim_path = uri_to_prim_path(asset_uri)
            asset_metadata_path = uri_to_metadata_prim_path(asset_uri)
            asset_name = asset_uri.segments[-1] if asset_uri.segments else 'asset'

            # Check if asset is animatable
            properties = api.config.get_properties(asset_uri)
            animatable = properties.get('animatable', False) if properties else False

            # Create asset duplicate node
            dup_node = duplicates_node.createNode('duplicate', f'{asset_name}_dup')
            dup_node.parm('sourceprims').set(asset_prim_path)
            dup_node.parm('ncy').set(instances)
            dup_node.parm('duplicatename').set('`@srcname``@copy`')
            dup_node.parm('makeinstances').set(int(not animatable))

            if prev_node:
                dup_node.setInput(0, prev_node)
            prev_node = dup_node

            # Create metadata duplicate node
            meta_dup_node = duplicates_node.createNode('duplicate', f'{asset_name}_meta_dup')
            meta_dup_node.parm('sourceprims').set(asset_metadata_path)
            meta_dup_node.parm('ncy').set(instances)
            meta_dup_node.parm('duplicatename').set('`@srcname``@copy`')
            meta_dup_node.parm('parentprimtype').set('')

            meta_dup_node.setInput(0, prev_node)
            prev_node = meta_dup_node

        # Connect last node to output
        if duplicates_output and prev_node and prev_node != duplicates_input:
            duplicates_output.setInput(0, prev_node)
        elif duplicates_output and duplicates_input:
            # No duplicates needed, connect input directly to output
            duplicates_output.setInput(0, duplicates_input)

        # Layout the subnet
        duplicates_node.layoutChildren()


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
    node._initialize()

def execute():
    raw_node = hou.pwd()
    node = ImportShot(raw_node)
    node.execute()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblehead.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportShot(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='shots',  # Only shots for import_shot
        include_from_context=True,
        current_selection=node.parm('entity').eval(),
        title="Select Shot",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node._update_labels()