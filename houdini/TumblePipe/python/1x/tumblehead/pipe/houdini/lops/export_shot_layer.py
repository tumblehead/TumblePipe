from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import shutil
import json

import hou

from tumblehead.api import get_user_name, path_str, fix_path, default_client
from tumblehead.util.io import store_json, store_text
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.pipe.usd import generate_usda_content
from tumblehead.config.timeline import FrameRange, get_frame_range, get_fps
from tumblehead.apps.deadline import Deadline
from tumblehead.pipe.houdini.lops import submit_render
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.houdini import util
from tumblehead.pipe.paths import (
    latest_export_path,
    next_export_path,
    get_workfile_context
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/export_shot_layer')

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

    # Expor the prim
    export_node.parm('trange').set(1)
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f2').deleteAllKeyframes()
    export_node.parm('f1').set(render_range.first_frame)
    export_node.parm('f2').set(render_range.last_frame)
    export_node.parm('f3').set(step)
    export_node.parm('lopoutput').set(path_str(file_path))
    export_node.parm('execute').pressButton()

def _create_combined_layer_usda(
    version_path: Path,
    shot_uri: Uri,
    department_name: str,
    version_name: str,
    fps: float,
    frame_range: 'FrameRange'
):
    """
    Create a combined .usda file that references all exported USD files.

    This creates a single entry point file like 010_010_layout_v0013.usda that
    sublayers all the stage components (cameras, lights, volumes, etc.) and
    asset instance files.
    """
    # Collect all .usd files in the version directory
    usd_files = list(version_path.glob('*.usd'))
    if not usd_files:
        return

    # Define ordering for stage component files (these should come first)
    stage_component_order = ['cameras', 'lights', 'volumes', 'collections', 'render', 'scene']

    def get_sort_key(file_path: Path):
        name = file_path.stem.lower()
        # Check if this is a stage component file
        for idx, component in enumerate(stage_component_order):
            if name.startswith(component):
                return (0, idx, name)  # Stage components first, in defined order
        # Asset instance files come after stage components
        return (1, 0, name)  # Sort alphabetically within asset files

    # Sort files
    sorted_files = sorted(usd_files, key=get_sort_key)

    # Generate combined usda filename: {shot}_{department}_{version}.usda
    shot_name = '_'.join(shot_uri.segments)
    combined_filename = f'{shot_name}_{department_name}_{version_name}.usda'
    combined_path = version_path / combined_filename

    # Generate USDA content with timing metadata
    render_range_obj = frame_range.full_range()
    usda_content = generate_usda_content(
        layer_paths=sorted_files,
        output_path=combined_path,
        fps=fps,
        start_frame=render_range_obj.first_frame,
        end_frame=render_range_obj.last_frame
    )

    # Write the combined file
    store_text(combined_path, usda_content)


def _export_prims(dive_node, prim_paths, render_range, step, file_path):
    """Export multiple prims to a single bundled USD file"""

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
    # Set the number of prims in the multiparm
    isolate_node.parm('primcount').set(len(prim_paths))

    # Now set the individual parameters
    for idx, prim_path in enumerate(prim_paths, start=1):
        isolate_node.parm('srcprimpath{}'.format(idx)).set(prim_path)
        isolate_node.parm('dstprimpath{}'.format(idx)).set(_get_destination_path(prim_path))

    # Export the prims
    export_node.parm('trange').set(1)
    export_node.parm('f1').deleteAllKeyframes()
    export_node.parm('f2').deleteAllKeyframes()
    export_node.parm('f1').set(render_range.first_frame)
    export_node.parm('f2').set(render_range.last_frame)
    export_node.parm('f3').set(step)
    export_node.parm('lopoutput').set(path_str(file_path))
    export_node.parm('execute').pressButton()

class ExportShotLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_shot_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return [entity.uri for entity in shot_entities]

    def list_department_names(self) -> list[str]:
        shot_departments = list_departments('shots')
        if len(shot_departments) == 0: return []
        shot_department_names = [dept.name for dept in shot_departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in shot_department_names
        ]

    def list_downstream_department_names(self) -> list[str]:
        shot_departments = list_departments('shots')
        if len(shot_departments) == 0: return []
        shot_department_names = [dept.name for dept in shot_departments]
        department_name = self.get_department_name()
        if department_name is None: return []
        if department_name not in shot_department_names: return []
        department_index = shot_department_names.index(department_name)
        return shot_department_names[department_index + 1:]

    def list_pool_names(self) -> list[str]:
        try: deadline = Deadline()
        except: return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0: return []
        default_values = api.config.get_properties(submit_render.DEFAULTS_URI)
        if default_values is None: return []
        if 'pools' not in default_values: return []
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
        ]
    
    def get_entity_source(self) -> str:
        return self.parm('entity_source').eval()

    def get_shot_uri(self) -> Uri | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
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
                raise AssertionError(f'Unknown entity source token: {entity_source}')

    def get_department_name(self) -> str | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
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

    def get_downstream_department_names(self) -> list[str]:
        department_names = self.list_downstream_department_names()
        if len(department_names) == 0: return []
        default_values = api.config.get_properties(DEFAULTS_URI)
        selected_department_names = list(filter(len, self.parm('export_departments').eval().split(' ')))
        if len(selected_department_names) == 0: return default_values.get('downstream_departments', [])
        selected_department_names.sort(key = department_names.index)
        return selected_department_names
    
    def get_pool_name(self) -> str | None:
        pool_names = self.list_pool_names()
        if len(pool_names) == 0: return None
        pool_name = self.parm('export_pool').eval()
        if pool_name == '': return pool_names[0]
        return pool_name

    def get_priority(self) -> int:
        return self.parm('export_priority').eval()

    def get_frame_range_source(self) -> str:
        return self.parm('frame_range').eval()

    def get_frame_range(self) -> tuple[FrameRange, int] | None:
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_context':
                shot_uri = self.get_shot_uri()
                if shot_uri is None: return None
                frame_range = get_frame_range(shot_uri)
                if frame_range is None: return None
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range token: {frame_range_source}'
    
    def set_entity_source(self, entity_source: str):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources: return
        self.parm('entity_source').set(entity_source)
    
    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if shot_uri not in shot_uris: return
        self.parm('shot').set(str(shot_uri))
    
    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)

    def get_export_type(self):
        return self.parm('export_type').eval()
    
    def execute(self, force_local=False):
        if force_local: return self._export_local()
        export_type = self.get_export_type()
        match export_type:
            case 'local': return self._export_local()
            case 'farm': return self._export_farm()
            case _: assert False, f'Unknown export type: {export_type}'
    
    def _do_export(
        self,
        shot_uri,
        department_name,
        frame_offset,
        frame_range,
        step
        ):

        # Nodes
        native = self.native()
        stage_node = native.node('IN_stage')
        dive_node = native.node('dive')
        _clear_dive(dive_node)

        # Parameters
        render_range = frame_range.full_range()
        user_name = get_user_name()
        timestamp = dt.datetime.now()
        fps = get_fps()

        # Paths
        version_path = next_export_path(shot_uri, department_name)
        version_name = version_path.name

        # Prepare for stage scrape
        root = stage_node.stage().GetPseudoRoot()

        # Scrape stage for assets
        assets = dict()
        asset_inputs = set()
        for asset_metadata in util.list_assets(root):
            asset_uri = Uri.parse_unsafe(asset_metadata['uri'])
            asset_path = util.uri_to_prim_path(asset_uri)
            instance_name = asset_metadata['instance']
            asset_instance_path = f'{asset_path.rsplit("/", 1)[0]}/{instance_name}'
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

            # Export changes to assets
            asset_groups = dict()
            for asset_path, asset_metadata in assets.items():
                asset_uri = Uri.parse_unsafe(asset_metadata['uri'])
                instance_name = asset_metadata['instance']
                if asset_uri not in asset_groups:
                    asset_groups[asset_uri] = []
                asset_groups[asset_uri].append((asset_path, instance_name))

            # Export each asset group
            parameter_assets = set()
            for asset_uri, instances in asset_groups.items():

                # Check if asset is animatable
                properties = api.config.get_properties(asset_uri)
                if properties['animatable']:

                    # Animatable assets: export each instance separately
                    for asset_path, instance_name in instances:
                        output_file_name = '.'.join([
                            '_'.join(asset_uri.segments + [
                                instance_name,
                                department_name,
                                version_name
                            ]),
                            'usd'
                        ])
                        output_file_path = temp_path / output_file_name
                        _export_prim(dive_node, asset_path, render_range, step, output_file_path)
                        parameter_assets.add(json.dumps(dict(
                            asset = str(asset_uri),
                            instance = instance_name
                        )))
                else:

                    # Non-animatable assets: bundle all instances into one file
                    output_file_name = '.'.join([
                        '_'.join(asset_uri.segments + [
                            department_name,
                            version_name
                        ]),
                        'usd'
                    ])
                    output_file_path = temp_path / output_file_name
                    instance_paths = [asset_path for asset_path, _ in instances]
                    _export_prims(dive_node, instance_paths, render_range, step, output_file_path)

                    # Still track all instances in metadata
                    for asset_path, instance_name in instances:
                        parameter_assets.add(json.dumps(dict(
                            asset = str(asset_uri),
                            instance = instance_name
                        )))

            # Export changes to cameras
            _export_prim(dive_node, '/cameras', render_range, step, temp_path / f'cameras_{version_name}.usd')

            # Export changes to lights
            _export_prim(dive_node, '/lights', render_range, step, temp_path / f'lights_{version_name}.usd')

            # Export changes to volumes
            _export_prim(dive_node, '/volumes', render_range, step, temp_path / f'volumes_{version_name}.usd')

            # Export changes to collections
            _export_prim(dive_node, '/collections', render_range, step, temp_path / f'collections_{version_name}.usd')

            # Export changes to render vars
            _export_prim(dive_node, '/Render', render_range, step, temp_path / f'render_{version_name}.usd')
            
            # Export changes to scene
            _export_prim(dive_node, '/scene', render_range, step, temp_path / f'scene_{version_name}.usd')

            # Write layer context
            context_path = temp_path / 'context.json'
            context = dict(
                inputs = list(map(json.loads, asset_inputs)),
                outputs = [dict(
                    uri = str(shot_uri),
                    department = department_name,
                    version = version_name,
                    timestamp = timestamp.isoformat(),
                    user = user_name,
                    parameters = dict(
                        assets = list(map(json.loads, parameter_assets))
                    )
                )]
            )
            store_json(context_path, context)

            # Copy all files to output path (skip cache file)
            version_path.mkdir(parents=True, exist_ok=True)
            for temp_item_path in temp_path.iterdir():
                if temp_item_path.name == 'stage': continue
                if temp_item_path == cache_path: continue
                output_item_path = version_path / temp_item_path.name
                if temp_item_path.is_file():
                    shutil.copy(temp_item_path, output_item_path)
                if temp_item_path.is_dir():
                    shutil.copytree(temp_item_path, output_item_path)

            # Create combined .usda file for this export
            _create_combined_layer_usda(
                version_path=version_path,
                shot_uri=shot_uri,
                department_name=department_name,
                version_name=version_name,
                fps=fps,
                frame_range=frame_range
            )

            # Clear the cache
            self.parm('cache_loadfromdisk').set(0)
            self.parm('bypass_input').set(0)
            self.parm('cache_file').set('')
        
        # Layout the created nodes
        dive_node.layoutChildren()

        # Update node comment
        native.setComment(
            'last export: '
            f'{version_name} \n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")} \n'
            f'by {user_name}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

    def _export_local(self):

        # Paramaters
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        frame_range, step = self.get_frame_range()

        # Check parameters
        if shot_uri is None: return
        if department_name is None: return

        # Export single shot
        self._do_export(
            shot_uri,
            department_name,
            0,
            frame_range,
            step
        )

    def _export_farm(self):
        
        # Get parameters
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        frame_range, _step = self.get_frame_range()

        # Get farm parameters
        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()

        # Check parameters
        if shot_uri is None: return
        if department_name is None: return
        if downstream_deps is None: return
        if pool_name is None: return
        if priority is None: return
        
        # Build config
        config = {
            'settings': {
                'priority': priority,
                'pool_name': pool_name,
                'entity_uri': str(shot_uri),
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
        
        # Submit to farm
        from tumblehead.farm.jobs.houdini.publish import job as publish_job
        try: publish_job.submit(config, {})
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to submit farm job: {str(e)}",
                severity=hou.severityType.Error
            )
            return
            
        # Update node comment
        native = self.native()
        timestamp = dt.datetime.now()
        user_name = get_user_name()
        native.setComment(
            f'farm export submitted: \n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")} \n'
            f'by {user_name}\n'
            f'downstream: {", ".join(downstream_deps) if downstream_deps else "None"}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)
        
        # Show success message
        downstream_msg = f"\nDownstream: {', '.join(downstream_deps)}" if downstream_deps else ""
        hou.ui.displayMessage(
            f"Export job submitted to farm\n"
            f"Department: {department_name}"
            f"{downstream_msg}",
            title="Farm Export Submitted"
        )

    def open_location(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return
        department_name = self.get_department_name()
        if department_name is None: return
        export_path = latest_export_path(shot_uri, department_name)
        if export_path is None: return
        if not export_path.exists(): return
        hou.ui.showInFileBrowser(f'{path_str(export_path)}')

def create(scene, name):
    node_type = ns.find_node_type('export_shot_layer', 'Lop')
    assert node_type is not None, 'Could not find export_shot_layer node type'
    native = scene.node(name)
    if native is not None: return ExportShotLayer(native)
    return ExportShotLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Check if workfile context exists
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is not None: return  # Context exists → keep 'from_context'

    # No context → change entity source to settings
    node = ExportShotLayer(raw_node)
    node.set_entity_source('from_settings')

def execute():
    raw_node = hou.pwd()
    node = ExportShotLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ExportShotLayer(raw_node)
    node.open_location()