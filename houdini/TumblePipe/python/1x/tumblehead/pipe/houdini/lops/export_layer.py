from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import shutil
import json

import hou

from tumblehead.api import get_user_name, path_str, fix_path, default_client
from tumblehead.util.io import store_json
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.variants import get_entity_type, list_variants
from tumblehead.config.timeline import FrameRange, get_frame_range, get_fps
from tumblehead.apps.deadline import Deadline
from tumblehead.pipe.houdini.lops import submit_render
from tumblehead.pipe.houdini import util
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    latest_export_path,
    next_export_path,
    get_workfile_context,
    get_layer_file_name
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/export_layer')

class ExportLayerError(Exception):
    """Raised when export_layer encounters a validation or execution error."""
    pass

def _clear_dive(dive_node):
    for node in dive_node.children():
        node.destroy()

def _ensure_node(stage, kind, name):
    node = stage.node(name)
    if node is not None: return node
    return stage.createNode(kind, name)

def _export_prim(dive_node, prim_path, render_range, step, file_path):

    def _get_destination_path(prim_path):
        parts = prim_path.split('/')
        if len(parts) == 2: return '/'
        return '/'.join(parts[:-1])

    # Parameters
    name = file_path.stem
    input = dive_node.indirectInputs()[0]

    # Create the nodes
    isolate_node = _ensure_node(dive_node, 'graftbranches', f'{name}_isolate')
    export_node = _ensure_node(dive_node, 'rop_usd', f'{name}_export')
    isolate_node.setInput(1, input)
    export_node.setInput(0, isolate_node)

    # Isolate the prim
    isolate_node.parm('primpath').set('')
    isolate_node.parm('srcprimpath1').set(prim_path)
    isolate_node.parm('dstprimpath1').set(_get_destination_path(prim_path))

    # Export the prim
    export_node.parm('trange').set(1)
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f2').deleteAllKeyframes()
    export_node.parm('f1').set(render_range.first_frame)
    export_node.parm('f2').set(render_range.last_frame)
    export_node.parm('f3').set(step)
    export_node.parm('lopoutput').set(path_str(file_path))
    export_node.parm('execute').pressButton()

def _export_prims(dive_node, prim_paths, render_range, step, file_path):

    def _get_destination_path(prim_path):
        parts = prim_path.split('/')
        if len(parts) == 2: return '/'
        return '/'.join(parts[:-1])

    # Parameters
    name = file_path.stem
    input = dive_node.indirectInputs()[0]

    # Create the nodes - destroy and recreate to avoid stale parameters
    existing_isolate = dive_node.node(f'{name}_isolate')
    if existing_isolate is not None:
        existing_isolate.destroy()
    existing_export = dive_node.node(f'{name}_export')
    if existing_export is not None:
        existing_export.destroy()

    isolate_node = dive_node.createNode('graftbranches', f'{name}_isolate')
    export_node = dive_node.createNode('rop_usd', f'{name}_export')
    isolate_node.setInput(1, input)
    export_node.setInput(0, isolate_node)

    # Isolate all prims (add each to graftbranches)
    isolate_node.parm('primpath').set('')
    isolate_node.parm('primcount').set(len(prim_paths))

    for idx, prim_path in enumerate(prim_paths, start=1):
        isolate_node.parm(f'srcprimpath{idx}').set(prim_path)
        isolate_node.parm(f'dstprimpath{idx}').set(_get_destination_path(prim_path))

    # Export the prims
    export_node.parm('trange').set(1)
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f2').deleteAllKeyframes()
    export_node.parm('f1').set(render_range.first_frame)
    export_node.parm('f2').set(render_range.last_frame)
    export_node.parm('f3').set(step)
    export_node.parm('lopoutput').set(path_str(file_path))
    export_node.parm('execute').pressButton()

class ExportLayer(ns.Node):

    def __init__(self, native):
        super().__init__(native)

    def get_entity_type(self) -> str | None:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return None
        return get_entity_type(entity_uri)

    def list_entity_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        shot_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )
        return [e.uri for e in asset_entities] + [e.uri for e in shot_entities]

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )
        return [entity.uri for entity in asset_entities]

    def list_shot_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )
        return [entity.uri for entity in shot_entities]

    def list_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return []

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        # Exclude generated departments (like 'root') from export menus
        departments = list_departments(context_name, include_generated=False)
        if len(departments) == 0:
            return []

        department_names = [dept.name for dept in departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        if default_values is None:
            return department_names

        # Filter by configured defaults if available
        configured_depts = default_values.get('departments', [])
        if configured_depts:
            return [
                name for name in configured_depts
                if name in department_names
            ]
        return department_names

    def list_downstream_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return []

        context_name = 'assets' if entity_type == 'asset' else 'shots'
        departments = list_departments(context_name)
        if len(departments) == 0:
            return []

        department_names = [dept.name for dept in departments]
        department_name = self.get_department_name()
        if department_name is None:
            return []
        if department_name not in department_names:
            return []

        department_index = department_names.index(department_name)
        return department_names[department_index + 1:]

    def list_variant_names(self) -> list[str]:
        """List available variant names for current entity.

        Returns variants from entity config, always includes 'default'.
        """
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['default']
        return list_variants(entity_uri)

    def list_pool_names(self) -> list[str]:
        try:
            deadline = Deadline()
        except:
            return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0:
            return []
        default_values = api.config.get_properties(submit_render.DEFAULTS_URI)
        if default_values is None:
            return []
        if 'pools' not in default_values:
            return []
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
        ]

    def get_entity_source(self) -> str:
        return self.parm('entity_source').eval()

    def get_entity_uri(self) -> Uri | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
                if context is None:
                    return None
                return context.entity_uri
            case 'from_settings':
                entity_uris = self.list_entity_uris()
                if len(entity_uris) == 0:
                    return None
                entity_uri_raw = self.parm('entity').eval()
                if len(entity_uri_raw) == 0:
                    return entity_uris[0]
                entity_uri = Uri.parse_unsafe(entity_uri_raw)
                if entity_uri not in entity_uris:
                    return None
                return entity_uri
            case _:
                raise AssertionError(f'Unknown entity source: {entity_source}')

    def get_department_name(self) -> str | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
                if context is None:
                    return None
                return context.department_name
            case 'from_settings':
                department_names = self.list_department_names()
                if len(department_names) == 0:
                    return None
                department_name = self.parm('department').eval()
                if len(department_name) == 0:
                    return department_names[0]
                if department_name not in department_names:
                    return None
                return department_name
            case _:
                raise AssertionError(f'Unknown entity source: {entity_source}')

    def get_variant_name(self) -> str:
        """Get selected variant name, defaults to 'default'."""
        variant_names = self.list_variant_names()
        variant_name = self.parm('variant').eval()
        if not variant_name or variant_name not in variant_names:
            return 'default'
        return variant_name

    def get_downstream_department_names(self) -> list[str]:
        department_names = self.list_downstream_department_names()
        if len(department_names) == 0:
            return []
        default_values = api.config.get_properties(DEFAULTS_URI)
        selected = list(filter(len, self.parm('export_departments').eval().split(' ')))
        if len(selected) == 0:
            if default_values is None:
                return []
            return default_values.get('downstream_departments', [])
        selected.sort(key=department_names.index)
        return selected

    def get_pool_name(self) -> str | None:
        pool_names = self.list_pool_names()
        if len(pool_names) == 0:
            return None
        pool_name = self.parm('export_pool').eval()
        if pool_name == '':
            return pool_names[0]
        return pool_name

    def get_priority(self) -> int:
        return self.parm('export_priority').eval()

    def get_frame_range_source(self) -> str:
        return self.parm('frame_range').eval()

    def get_frame_range(self) -> tuple[FrameRange, int] | None:
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'single_frame':
                return FrameRange(1001, 1001, 0, 0), 1
            case 'playback_range':
                frame_range = util.get_frame_range()
                return frame_range, 1
            case 'from_context':
                entity_uri = self.get_entity_uri()
                if entity_uri is None:
                    return None
                frame_range = get_frame_range(entity_uri)
                if frame_range is None:
                    return None
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range source: {frame_range_source}'

    def get_export_type(self) -> str:
        """Get export type ('local' or 'farm')."""
        return self.parm('export_type').eval()

    def set_entity_source(self, entity_source: str):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources:
            return
        self.parm('entity_source').set(entity_source)

    def set_entity_uri(self, entity_uri: Uri):
        entity_uris = self.list_entity_uris()
        if entity_uri not in entity_uris:
            return
        self.parm('entity').set(str(entity_uri))

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names:
            return
        self.parm('department').set(department_name)

    def set_variant_name(self, variant_name: str):
        """Set variant name."""
        self.parm('variant').set(variant_name)

    def execute(self, force_local: bool = False):
        if force_local:
            return self._export_local()
        export_type = self.get_export_type()
        match export_type:
            case 'local':
                return self._export_local()
            case 'farm':
                return self._export_farm()
            case _:
                assert False, f'Unknown export type: {export_type}'

    def _export_local(self):
        native = self.native()
        stage_node = native.node('IN_stage')
        export_subnet = native.node('export')
        _clear_dive(export_subnet)

        # Get parameters
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        frame_range_result = self.get_frame_range()

        if entity_uri is None:
            raise ExportLayerError("Entity URI is not set. Check 'Entity Source' setting or workfile context.")
        if department_name is None:
            raise ExportLayerError(f"Department name is not set for entity: {entity_uri}")
        if frame_range_result is None:
            if str(entity_uri).startswith('entity:/assets/'):
                raise ExportLayerError(
                    f"Frame range could not be determined for asset: {entity_uri}. "
                    "Assets don't have frame ranges - use 'Single Frame' or 'Playback Range' instead."
                )
            raise ExportLayerError(f"Frame range could not be determined for entity: {entity_uri}")

        frame_range, step = frame_range_result
        render_range = frame_range.full_range()
        user_name = get_user_name()
        timestamp = dt.datetime.now()
        fps = get_fps()

        # Determine version path
        version_path = next_export_path(entity_uri, variant_name, department_name)
        version_name = version_path.name

        # Prepare for stage scrape
        root = stage_node.stage().GetPseudoRoot()

        # Check if we're exporting a shot (to add shot dept entry to inputs)
        is_shot_export = str(entity_uri).startswith('entity:/shots/')

        # Scrape stage for assets
        assets = dict()
        asset_inputs = set()
        for asset_metadata in util.list_assets(root):
            asset_uri = Uri.parse_unsafe(asset_metadata['uri'])
            asset_path = util.uri_to_prim_path(asset_uri)
            instance_name = asset_metadata['instance']
            asset_instance_path = f'{asset_path.rsplit("/", 1)[0]}/{instance_name}'

            # Add current shot department entry to inputs if exporting a shot
            if is_shot_export:
                shot_dept_entry = {
                    'uri': str(entity_uri),
                    'department': department_name,
                    'version': version_name
                }
                # Add to inputs if not already present
                existing_inputs = asset_metadata.get('inputs', [])
                if shot_dept_entry not in existing_inputs:
                    asset_metadata['inputs'] = existing_inputs + [shot_dept_entry]

            assets[asset_instance_path] = asset_metadata
            asset_inputs.update(set(map(json.dumps, asset_metadata['inputs'])))

        # Set fps
        self.parm('set_metadata_fps').set(fps)

        # Export the stage
        root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Cache the stage
            cache_path = temp_path / f'{version_name}.usd'
            self.parm('cache_file').set(path_str(cache_path))
            self.parm('cache_f1').set(render_range.first_frame)
            self.parm('cache_f2').set(render_range.last_frame)
            self.parm('cache_f3').set(step)
            self.parm('cache_execute').pressButton()
            self.parm('cache_loadfromdisk').set(1)
            self.parm('bypass_input').set(1)

            # Collect all prim paths to export (assets + stage components)
            all_export_prims = []

            # Collect asset prim paths
            parameter_assets = []
            for asset_path, asset_metadata in assets.items():
                all_export_prims.append(asset_path)
                parameter_assets.append(dict(
                    asset=asset_metadata['uri'],
                    instance=asset_metadata['instance'],
                    inputs=asset_metadata.get('inputs', [])
                ))

            # Collect stage component prim paths
            stage = stage_node.stage()
            stage_prims = ['/cameras', '/lights', '/volumes', '/collections', '/Render', '/scene']
            for prim_path in stage_prims:
                if stage.GetPrimAtPath(prim_path).IsValid():
                    all_export_prims.append(prim_path)

            # For asset exports, also capture the asset's own root prim (e.g., /CHAR/Moose)
            # This ensures materials, shaders, and other lookdev content is exported
            is_asset_export = str(entity_uri).startswith('entity:/assets/')
            if is_asset_export:
                asset_prim_path = util.uri_to_prim_path(entity_uri)
                if stage.GetPrimAtPath(asset_prim_path).IsValid():
                    all_export_prims.append(asset_prim_path)

            # Validate we have something to export - never silently create empty versions
            if not all_export_prims:
                raise ExportLayerError(
                    f"No exportable content found for {entity_uri} {department_name}. "
                    "Ensure the stage contains the asset prim, referenced assets, or stage components."
                )

            # Export everything into single file
            layer_file_name = get_layer_file_name(entity_uri, variant_name, department_name, version_name)
            _export_prims(export_subnet, all_export_prims, render_range, step, temp_path / layer_file_name)

            # Write layer context
            context_path = temp_path / 'context.json'
            context = dict(
                inputs=list(map(json.loads, asset_inputs)),
                outputs=[dict(
                    uri=str(entity_uri),
                    variant=variant_name,
                    department=department_name,
                    version=version_name,
                    timestamp=timestamp.isoformat(),
                    user=user_name,
                    parameters=dict(
                        assets=parameter_assets
                    )
                )]
            )
            store_json(context_path, context)

            # Copy all files to output path
            version_path.mkdir(parents=True, exist_ok=True)
            for temp_item_path in temp_path.iterdir():
                if temp_item_path.name == 'stage':
                    continue
                if temp_item_path == cache_path:
                    continue
                output_item_path = version_path / temp_item_path.name
                if temp_item_path.is_file():
                    shutil.copy(temp_item_path, output_item_path)
                if temp_item_path.is_dir():
                    shutil.copytree(temp_item_path, output_item_path)

            # Clear the cache
            self.parm('cache_loadfromdisk').set(0)
            self.parm('bypass_input').set(0)
            self.parm('cache_file').set('')

        # Layout the created nodes
        export_subnet.layoutChildren()

        # Update node comment
        native.setComment(
            f'last export: {version_name}\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

    def _export_farm(self):
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        frame_range_result = self.get_frame_range()

        if entity_uri is None:
            raise ExportLayerError("Entity URI is not set. Check 'Entity Source' setting or workfile context.")
        if department_name is None:
            raise ExportLayerError(f"Department name is not set for entity: {entity_uri}")
        if frame_range_result is None:
            if str(entity_uri).startswith('entity:/assets/'):
                raise ExportLayerError(
                    f"Frame range could not be determined for asset: {entity_uri}. "
                    "Assets don't have frame ranges - use 'Single Frame' or 'Playback Range' instead."
                )
            raise ExportLayerError(f"Frame range could not be determined for entity: {entity_uri}")

        frame_range, _step = frame_range_result

        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()

        if pool_name is None:
            raise ExportLayerError("No render pool available. Check Deadline configuration.")
        if priority is None:
            raise ExportLayerError("Priority is not set for farm export.")

        config = {
            'settings': {
                'priority': priority,
                'pool_name': pool_name,
                'entity_uri': str(entity_uri),
                'variant_name': variant_name,
                'department_name': department_name,
                'first_frame': frame_range.full_range().first_frame,
                'last_frame': frame_range.full_range().last_frame
            },
            'tasks': {
                'publish': {
                    'downstream_departments': downstream_deps
                }
            }
        }

        from tumblehead.farm.jobs.houdini.publish import job as publish_job
        try:
            publish_job.submit(config, {})
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to submit farm job: {str(e)}",
                severity=hou.severityType.Error
            )
            return

        native = self.native()
        timestamp = dt.datetime.now()
        user_name = get_user_name()
        native.setComment(
            f'farm export submitted:\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
            f'by {user_name}\n'
            f'downstream: {", ".join(downstream_deps) if downstream_deps else "None"}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

        downstream_msg = f"\nDownstream: {', '.join(downstream_deps)}" if downstream_deps else ""
        hou.ui.displayMessage(
            f"Export job submitted to farm\n"
            f"Department: {department_name}"
            f"{downstream_msg}",
            title="Farm Export Submitted"
        )
    
    def open_location(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            hou.ui.displayMessage("No entity selected.", severity=hou.severityType.Warning)
            return
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        if department_name is None:
            hou.ui.displayMessage("No department selected.", severity=hou.severityType.Warning)
            return

        export_path = latest_export_path(entity_uri, variant_name, department_name)
        if export_path is None:
            hou.ui.displayMessage(f"No exports found for {department_name}.", severity=hou.severityType.Warning)
            return
        if not export_path.exists():
            hou.ui.displayMessage(f"Export path does not exist: {export_path}", severity=hou.severityType.Warning)
            return
        hou.ui.showInFileBrowser(path_str(export_path))

def create(scene, name):
    node_type = ns.find_node_type('export_layer', 'Lop')
    assert node_type is not None, 'Could not find export_layer node type'
    native = scene.node(name)
    if native is not None:
        return ExportLayer(native)
    return ExportLayer(scene.createNode(node_type.name(), name))


def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):
    set_style(raw_node)

    node = ExportLayer(raw_node)

    # Check if workfile context exists
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None:
        node.set_entity_source('from_settings')

    # Always set default entity
    entity_uris = node.list_entity_uris()
    if entity_uris:
        node.set_entity_uri(entity_uris[0])

def execute():
    raw_node = hou.pwd()
    node = ExportLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ExportLayer(raw_node)
    node.open_location()
