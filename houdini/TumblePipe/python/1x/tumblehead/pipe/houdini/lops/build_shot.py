from pathlib import Path

import hou

from tumblehead.api import default_client
from tumblehead.util.uri import Uri
from tumblehead.config.timeline import FrameRange, get_frame_range, get_fps
from tumblehead.config.department import list_departments
from tumblehead.config.procedurals import list_procedural_names
from tumblehead.config.variants import list_variants, DEFAULT_VARIANT
from tumblehead.config.scene import get_scene
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.houdini.util as util
from tumblehead.pipe.paths import (
    get_workfile_context,
    load_entity_context
)
from tumblehead.pipe.houdini.lops import import_layer
from tumblehead.pipe import graph

class Mode:
    Latest = 'Latest'
    Strict = 'Strict'

api = default_client()


def _ensure_node(stage, kind, name):
    node = stage.node(name)
    if node is not None: return node
    return stage.createNode(kind, name)

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _clear_scene(dive_node, output_node):

    # Clear output connections
    for input in output_node.inputConnections():
        output_node.setInput(input.inputIndex(), None)

    # Delete all nodes other than inputs and outputs
    for node in dive_node.children():
        if node.name() == output_node.name(): continue
        node.destroy()

def _context_from_workfile():
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
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

def _resolve_versions_strict(shot_uri: Uri):
    assert False, 'Not implemented'

def _get(context, *keys):
    current = context
    for key in keys:
        if not isinstance(current, dict): return None
        if key not in current: return None
        current = current[key]
    return current

def _update_script(instances):

    # Prepare script
    script = [
        'import json',
        'import pxr',
        'import hou',
        '',
        'from tumblehead.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        ''
    ]

    # Update metadata instance names
    for prim_path, instance_name in instances:
        prim_var = f'prim_{instance_name}'
        metadata_var = f'metadata_{instance_name}'
        script += [
            f'{prim_var} = root.GetPrimAtPath("{prim_path}")',
            f'{metadata_var} = util.get_metadata({prim_var})',
            f'{metadata_var}["instance"] = "{instance_name}"',
            f'util.set_metadata({prim_var}, {metadata_var})',
            ''
        ]
    
    # Done
    return script

class BuildShot(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def _load_scene_context(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return None
        match self.get_mode():
            case Mode.Latest:
                # Get departments strictly before current (exclusive)
                # This ensures we don't include our own previous exports
                upstream_shot_departments = self.get_upstream_shot_department_names()
                asset_departments = self.get_asset_department_names()

                # Get asset variants from scene configuration
                scene = get_scene(shot_uri)
                asset_variants = {}
                for entry in scene.assets:
                    asset_uri = Uri.parse_unsafe(entry.asset)
                    asset_variants[asset_uri] = entry.variant

                # Scan dependency graph and resolve versions
                g = graph.scan(api)
                version_names = graph.resolve_shot_build(
                    g,
                    api,
                    shot_uri,
                    upstream_shot_departments,
                    asset_departments,
                    asset_variants=asset_variants
                )
            case Mode.Strict:
                version_names = _resolve_versions_strict(shot_uri)
        return version_names
    
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

    def get_shot_uri(self) -> Uri | None:
        shot_uri_raw = self.parm('shot').eval()
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

    def get_mode(self):
        match self.parm('mode').eval():
            case Mode.Latest: return Mode.Latest
            case Mode.Strict: return Mode.Strict
            case _: assert False, 'Invalid mode'
        
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

    def list_department_names(self) -> list[str]:
        shot_departments = list_departments('shots')
        names = [dept.name for dept in shot_departments]
        return ['from_context'] + names

    def get_department_name(self) -> str | None:
        department_name = self.parm('department').eval()
        if department_name == 'from_context':
            context = _context_from_workfile()
            if context is None: return None
            return context.department_name
        # From settings
        department_names = self.list_department_names()
        if len(department_names) == 0: return None
        if len(department_name) == 0: return department_names[0]
        if department_name not in department_names: return None
        return department_name

    def get_downstream_shot_department_names(self):
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        if department_name not in shot_department_names: return []
        shot_department_index = shot_department_names.index(department_name)
        return shot_department_names[shot_department_index + 1:]

    def get_upstream_shot_department_names(self):
        """Get shot departments strictly BEFORE current department (exclusive).

        For dependency resolution, we want assets from departments that came
        before us in the pipeline, NOT including our own previous exports.
        """
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        if department_name not in shot_department_names: return []
        shot_department_index = shot_department_names.index(department_name)
        return shot_department_names[:shot_department_index]

    # -------------------------------------------------------------------------
    # Variant Methods
    # -------------------------------------------------------------------------
    def list_shot_variant_names(self) -> list[str]:
        """List variants available for the current shot."""
        shot_uri = self.get_shot_uri()
        if shot_uri is None:
            return [DEFAULT_VARIANT]
        return list_variants(shot_uri)

    def list_asset_variant_names(self, asset_uri: Uri) -> list[str]:
        """List variants available for a specific asset."""
        return list_variants(asset_uri)

    def get_shot_variant_name(self) -> str:
        """Get the selected shot variant name."""
        variant_names = self.list_shot_variant_names()
        variant_name = self.parm('shot_variant').eval()
        if len(variant_name) == 0:
            return DEFAULT_VARIANT
        if variant_name not in variant_names:
            return DEFAULT_VARIANT
        return variant_name

    def set_shot_variant_name(self, variant_name: str):
        """Set the shot variant name."""
        variant_names = self.list_shot_variant_names()
        if variant_name not in variant_names:
            return
        self.parm('shot_variant').set(variant_name)

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

    def _update_labels(self):
        """Update label parameters to show resolved values when 'from_context' is selected."""
        shot_raw = self.parm('shot').eval()
        if shot_raw == 'from_context':
            shot_uri = self.get_shot_uri()
            self.parm('shot_label').set(str(shot_uri) if shot_uri else '')
        else:
            self.parm('shot_label').set('')

        department_raw = self.parm('department').eval()
        if department_raw == 'from_context':
            department_name = self.get_department_name()
            self.parm('department_label').set(department_name if department_name else '')
        else:
            self.parm('department_label').set('')

    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if str(shot_uri) not in shot_uris: return  # Compare strings
        self.parm('shot').set(str(shot_uri))

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)

    def set_mode(self, mode):
        match mode:
            case Mode.Latest: self.parm('mode').set(Mode.Latest)
            case Mode.Strict: self.parm('mode').set(Mode.Strict)
            case _: assert False, f'Invalid mode {mode}'
        self.state.mode = mode
    
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

    def execute(self):

        # Clear scene
        context = self.native()
        dive_node = context.node('dive')
        output_node = dive_node.node('output')
        _clear_scene(dive_node, output_node)

        # Parameters
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return
        frame_range = self.get_frame_range()
        include_asset_departments = self.get_asset_department_names()
        include_shot_departments = self.get_shot_department_names()
        downstream_shot_departments = self.get_downstream_shot_department_names()

        # Check if we have any departments to load
        if len(include_asset_departments) == 0: return
        if len(include_shot_departments) == 0: return

        # Set the shot fps (skip if not configured)
        fps = get_fps()
        if fps is not None:
            self.parm('shot_fps').set(fps)

        # Update scene without updating the viewport
        with util.update_mode(hou.updateMode.Manual):

            # Get the resolved paths
            scene_context = self._load_scene_context()
            if scene_context is None: return

            # Collection nodes
            merge_node = _ensure_node(dive_node, 'merge', 'merge')
            merge_node.parm('mergestyle').set('separate')

            # Set frame range and FPS
            util.set_frame_range(frame_range)
            fps = get_fps()
            if fps is not None:
                util.set_fps(fps)

            # Load assets
            for asset_uri, instance_names in scene_context['assets'].items():
                uri_name = '_'.join(asset_uri.segments[1:])

                # Prepare
                prev_node = None

                # Load asset department layers
                for dept in list_departments('assets'):
                    asset_department = dept.name
                    if asset_department not in include_asset_departments: continue

                    # Check if asset department layer is available
                    resolved_layer_path = _get(
                        scene_context,
                        'asset_layers',
                        asset_department,
                        asset_uri
                    )
                    if resolved_layer_path is None: continue

                    # Load asset department
                    version_name = resolved_layer_path.name
                    asset_layer_node = import_layer.create(
                        dive_node, (
                            'asset'
                            f'_{uri_name}'
                            f'_{asset_department}'
                            '_import'
                        )
                    )
                    asset_layer_node.set_entity_uri(asset_uri)
                    asset_layer_node.set_department_name(asset_department)
                    asset_layer_node.set_version_name(version_name)
                    asset_layer_node.set_stage_type('asset')
                    asset_layer_node.set_include_layerbreak(False)
                    asset_layer_node.execute()

                    # Connect to previous node
                    if prev_node is not None:
                        _connect(prev_node, asset_layer_node.native())
                    prev_node = asset_layer_node.native()

                # Check if we need to duplicate asset
                instances = len(instance_names)
                properties = api.config.get_properties(asset_uri)
                animatable = properties.get('animatable', False) if properties else False

                if instances > 1:

                    # Create duplicate subnet
                    duplicate_subnet = dive_node.createNode('subnet', f'{uri_name}_duplicate')
                    duplicate_subnet.node('output0').destroy()
                    duplicate_subnet_input = duplicate_subnet.indirectInputs()[0]
                    duplicate_subnet_output = duplicate_subnet.createNode('output', 'output')
                    _connect(prev_node, duplicate_subnet)
                    prev_node = duplicate_subnet_input

                    # Duplicate asset
                    from tumblehead.pipe.houdini.util import uri_to_prim_path
                    asset_prim_path = uri_to_prim_path(asset_uri)
                    duplicate_node = duplicate_subnet.createNode('duplicate', 'asset_duplicate')
                    duplicate_node.parm('sourceprims').set(asset_prim_path)
                    duplicate_node.parm('ncy').set(instances)
                    duplicate_node.parm('duplicatename').set('`@srcname``@copy`')
                    duplicate_node.parm('makeinstances').set(int(not animatable))
                    _connect(prev_node, duplicate_node)
                    prev_node = duplicate_node

                    # Duplicate metadata
                    from tumblehead.pipe.houdini.util import uri_to_metadata_prim_path
                    asset_metadata_path = uri_to_metadata_prim_path(asset_uri)
                    duplicate_metadata_node = duplicate_subnet.createNode('duplicate', 'metadata_duplicate')
                    duplicate_metadata_node.parm('sourceprims').set(asset_metadata_path)
                    duplicate_metadata_node.parm('ncy').set(instances)
                    duplicate_metadata_node.parm('duplicatename').set('`@srcname``@copy`')
                    duplicate_metadata_node.parm('parentprimtype').set('')
                    _connect(prev_node, duplicate_metadata_node)
                    prev_node = duplicate_metadata_node

                    # Update metadata instance names
                    python_node = duplicate_subnet.createNode('pythonscript', 'metadata_update')
                    asset_metadata_base = asset_metadata_path.rsplit('/', 1)[0]  # /_METADATA/_assets/_char
                    python_node.parm('python').set('\n'.join(_update_script([
                        (f'{asset_metadata_base}/{instance_name}', instance_name)
                        for instance_name in instance_names
                    ])))
                    _connect(prev_node, python_node)
                    prev_node = python_node

                    # Connect last node to output
                    _connect(prev_node, duplicate_subnet_output)
                    prev_node = duplicate_subnet

                    # Layout duplicate subnet
                    duplicate_subnet.layoutChildren()

                # Load shot department layers
                # For non-animatable assets: load once for all instances (bundled)
                # For animatable assets: load per instance (separate files)
                anchor_node = prev_node

                if not animatable:
                    # Non-animatable assets: load bundled shot layer once
                    # Use first instance name for the import node (file doesn't contain instance in name)
                    list(instance_names)[0]

                    # Prepare
                    upstream_department_nodes = []
                    downstream_department_nodes = []

                    # Load shot department layers
                    for dept in list_departments('shots'):
                        shot_department = dept.name
                        if shot_department not in include_shot_departments: continue

                        # Check if the shot department layer is available
                        layer_lookup = _get(
                            scene_context,
                            'shot_layers',
                            shot_department
                        )
                        if layer_lookup is None: continue
                        resolved_layer_path, layer_assets = layer_lookup

                        # Get shot department data
                        if asset_uri not in layer_assets: continue
                        # For non-animatable, check if any instance exists in the layer
                        has_instances = any(
                            inst in layer_assets[asset_uri]
                            for inst in instance_names
                        )
                        if not has_instances: continue
                        version_name = resolved_layer_path.name

                        # Load shot layer once for all instances (bundled file)
                        asset_shot_layer_node = import_layer.create(
                            dive_node, (
                                'asset'
                                f'_{uri_name}'
                                f'_{shot_department}'
                                '_bundled'
                            )
                        )
                        asset_shot_layer_node.set_entity_uri(shot_uri)
                        asset_shot_layer_node.set_department_name(shot_department)
                        asset_shot_layer_node.set_version_name(version_name)
                        asset_shot_layer_node.set_stage_type('asset')
                        asset_shot_layer_node.set_layer_asset_uri(asset_uri)
                        # Don't set instance_name for non-animatable assets (list will be empty)
                        asset_shot_layer_node.set_include_layerbreak(False)
                        asset_shot_layer_node.execute()

                        # Store department nodes
                        if shot_department in downstream_shot_departments:
                            downstream_department_nodes.append(asset_shot_layer_node.native())
                        else:
                            upstream_department_nodes.append(asset_shot_layer_node.native())

                    # Connect upstream department nodes
                    for upstream_department_node in upstream_department_nodes:
                        _connect(anchor_node, upstream_department_node)
                        anchor_node = upstream_department_node

                    # Connect downstream department nodes
                    if len(downstream_department_nodes) > 0:

                        # Create departments switch
                        downstream_switch_node = dive_node.createNode('switch', (
                            f'{uri_name}'
                            '_downstream'
                        ))
                        downstream_switch_node.parm('input').setExpression(f'ch("{self.parm("include_downstream_departments").path()}")')
                        _connect(anchor_node, downstream_switch_node)

                        # Connect downstream department nodes
                        for downstream_department_node in downstream_department_nodes:
                            _connect(anchor_node, downstream_department_node)
                            anchor_node = downstream_department_node

                        # Connect last node to switch
                        _connect(anchor_node, downstream_switch_node)
                        anchor_node = downstream_switch_node

                    # Non-animatable assets don't support procedurals - connect directly to merge
                    _connect(anchor_node, merge_node)

                else:
                    # Animatable assets: load shot layer per instance (existing behavior)
                    for instance_name in instance_names:
                        prev_node = anchor_node

                        # Prepare
                        upstream_department_nodes = []
                        downstream_department_nodes = []

                        # Load shot department layers
                        for dept in list_departments('shots'):
                            shot_department = dept.name
                            if shot_department not in include_shot_departments: continue

                            # Check if the shot department layer is available
                            layer_lookup = _get(
                                scene_context,
                                'shot_layers',
                                shot_department
                            )
                            if layer_lookup is None: continue
                            resolved_layer_path, layer_assets = layer_lookup

                            # Get shot department data
                            if asset_uri not in layer_assets: continue
                            if instance_name not in layer_assets[asset_uri]: continue
                            version_name = resolved_layer_path.name

                            # Load shot layer
                            asset_shot_layer_node = import_layer.create(
                                dive_node, (
                                    'asset'
                                    f'_{uri_name}'
                                    f'_{instance_name}'
                                    f'_{shot_department}'
                                )
                            )
                            asset_shot_layer_node.set_entity_uri(shot_uri)
                            asset_shot_layer_node.set_department_name(shot_department)
                            asset_shot_layer_node.set_version_name(version_name)
                            asset_shot_layer_node.set_stage_type('asset')
                            asset_shot_layer_node.set_layer_asset_uri(asset_uri)
                            asset_shot_layer_node.set_instance_name(instance_name)
                            asset_shot_layer_node.set_include_layerbreak(False)
                            asset_shot_layer_node.execute()

                            # Store department nodes
                            if shot_department in downstream_shot_departments:
                                downstream_department_nodes.append(asset_shot_layer_node.native())
                            else:
                                upstream_department_nodes.append(asset_shot_layer_node.native())

                        # Connect upstream department nodes
                        for upstream_department_node in upstream_department_nodes:
                            _connect(prev_node, upstream_department_node)
                            prev_node = upstream_department_node

                        # Connect downstream department nodes
                        if len(downstream_department_nodes) > 0:

                            # Create departments switch
                            downstream_switch_node = dive_node.createNode('switch', (
                                f'{uri_name}'
                                f'_{instance_name}'
                                '_downstream'
                            ))
                            downstream_switch_node.parm('input').setExpression(f'ch("{self.parm("include_downstream_departments").path()}")')
                            _connect(prev_node, downstream_switch_node)

                            # Connect downstream department nodes
                            for downstream_department_node in downstream_department_nodes:
                                _connect(prev_node, downstream_department_node)
                                prev_node = downstream_department_node

                            # Connect last node to switch
                            _connect(prev_node, downstream_switch_node)
                            prev_node = downstream_switch_node

                        # Create a procedurals subnet
                        include_procedual_parm = self.parm('include_procedurals')
                        asset_procedurals_subnet = dive_node.createNode('subnet', f'{uri_name}_{instance_name}_procedurals')
                        asset_procedurals_subnet.node('output0').destroy()
                        asset_procedurals_subnet_input = asset_procedurals_subnet.indirectInputs()[0]
                        asset_procedurals_subnet_output = asset_procedurals_subnet.createNode('output', 'output')
                        _connect(prev_node, asset_procedurals_subnet)
                        prev_node = asset_procedurals_subnet_input

                        # Load asset procedural nodes
                        asset_procedural_names = list_procedural_names(
                            shot_uri, asset_uri
                        )
                        for asset_procedural_name in asset_procedural_names:
                            node_type = ns.find_node_type(asset_procedural_name, 'Lop')
                            assert node_type is not None, f'Could not find {asset_procedural_name} node type'
                            asset_procedural_node = asset_procedurals_subnet.createNode(node_type.name(), asset_procedural_name)
                            asset_procedural_node.parm('lop_activation').setExpression(f'ch("{include_procedual_parm.path()}")')
                            _connect(prev_node, asset_procedural_node)
                            prev_node = asset_procedural_node

                        # Connect last node to output
                        _connect(prev_node, asset_procedurals_subnet_output)
                        prev_node = asset_procedurals_subnet

                        # Layout procedurals subnet
                        asset_procedurals_subnet.layoutChildren()

                        # Connect last node to output
                        _connect(prev_node, merge_node)

            # Import shot scene layers
            upstream_department_nodes = []
            downstream_department_nodes = []
            for dept in list_departments('shots'):
                shot_department = dept.name
                if shot_department not in include_shot_departments: continue

                # Check if the shot department layer is available
                layer_lookup = _get(
                    scene_context,
                    'shot_layers',
                    shot_department
                )
                if layer_lookup is None: continue
                resolved_layer_path, _ = layer_lookup

                # Create a department subnet
                version_name = resolved_layer_path.name
                department_subnet = dive_node.createNode('subnet', f'{shot_department}_import')
                department_subnet.node('output0').destroy()
                department_subnet_input = department_subnet.indirectInputs()[0]
                department_subnet_output = department_subnet.createNode('output', 'output')
                prev_node = department_subnet_input

                # Load department layer
                layer_node = import_layer.create(department_subnet, 'layer_import')
                layer_node.set_entity_uri(shot_uri)
                layer_node.set_department_name(shot_department)
                layer_node.set_version_name(version_name)
                layer_node.set_include_layerbreak(False)
                layer_node.execute()

                # Connect layer node
                _connect(prev_node, layer_node.native())
                prev_node = layer_node.native()

                # Connect last node to output
                _connect(prev_node, department_subnet_output)
                prev_node = department_subnet

                # Layout department subnet
                department_subnet.layoutChildren()

                # Store department nodes
                if shot_department in downstream_shot_departments:
                    downstream_department_nodes.append(department_subnet)
                else:
                    upstream_department_nodes.append(department_subnet)
            
            # Connect upstream department subnets
            prev_node = merge_node
            for upstream_department_node in upstream_department_nodes:
                _connect(prev_node, upstream_department_node)
                prev_node = upstream_department_node

            # Connect downstream department subnets
            if len(downstream_department_nodes) > 0:
                
                # Create departments switch
                downstream_switch_node = dive_node.createNode('switch', 'downstream_switch')
                downstream_switch_node.parm('input').setExpression(f'ch("{self.parm("include_downstream_departments").path()}")')
                _connect(prev_node, downstream_switch_node)

                # Connect downstream department subnets
                for downstream_department_node in downstream_department_nodes:
                    _connect(prev_node, downstream_department_node)
                    prev_node = downstream_department_node
                
                # Connect last node to switch
                _connect(prev_node, downstream_switch_node)
                prev_node = downstream_switch_node
            
            # Connect last node to output
            _connect(prev_node, output_node)

            # Layout scene
            dive_node.layoutChildren()

def create(scene, name):
    node_type = ns.find_node_type('build_shot', 'Lop')
    assert node_type is not None, 'Could not find build_shot node type'
    native = scene.node(name)
    if native is not None: return BuildShot(native)
    return BuildShot(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    node = BuildShot(raw_node)

    # If no context, set first available shot
    entity = _entity_from_context_json()
    if entity is None:
        shot_uris = node.list_shot_uris()
        if len(shot_uris) > 1:  # Skip 'from_context'
            node.set_shot_uri(Uri.parse_unsafe(shot_uris[1]))

def execute():
    raw_node = hou.pwd()
    node = BuildShot(raw_node)
    node.execute()