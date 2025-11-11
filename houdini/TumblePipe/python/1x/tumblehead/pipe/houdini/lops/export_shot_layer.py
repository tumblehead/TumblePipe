from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import shutil
import json

import hou

from tumblehead.api import get_user_name, path_str, default_client
from tumblehead.util.io import store_json, load_json
from tumblehead.apps.deadline import Deadline
from tumblehead.config import FrameRange
from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini.lops import import_shot_layer
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    get_next_version_path,
    latest_shot_export_path,
    ShotEntity
)

api = default_client()

def _entity_from_context_json():

    # Path to current workfilw
    file_path = Path(hou.hipFile.path())
    if not file_path.exists(): return None

    # Look for context.json in the workfile directory
    context_json_path = file_path.parent / "context.json"
    if not context_json_path.exists(): return None
    context_data = load_json(context_json_path)
    if context_data is None: return None

    # Parse the loaded context
    if context_data.get('entity') != 'shot': return None
    return ShotEntity(
        sequence_name = context_data['sequence'],
        shot_name = context_data['shot'],
        department_name = context_data['department']
    )

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

    def list_sequence_names(self):
        return api.config.list_sequence_names()
    
    def list_shot_names(self):
        sequence_name = self.get_sequence_name()
        if sequence_name is None: return []
        return api.config.list_shot_names(sequence_name)
    
    def list_department_names(self):
        shot_department_names = api.config.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/export_shot_layer')
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in shot_department_names
        ]
    
    def list_downstream_department_names(self):
        shot_department_names = api.config.list_shot_department_names()
        if len(shot_department_names) == 0: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        if department_name not in shot_department_names: return []
        department_index = shot_department_names.index(department_name)
        return shot_department_names[department_index + 1:]

    def list_pool_names(self):
        try: deadline = Deadline()
        except: return []
        pool_names = deadline.list_pools()
        if len(pool_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/submit_render')
        return [
            pool_name
            for pool_name in default_values['pools']
            if pool_name in pool_names
        ]
    
    def get_entity_source(self):
        return self.parm('entity_source').eval()

    def get_sequence_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.sequence_name
            case 'from_settings':
                sequence_names = self.list_sequence_names()
                if len(sequence_names) == 0: return None
                sequence_name = self.parm('sequence').eval()
                if len(sequence_name) == 0: return sequence_names[0]
                if sequence_name not in sequence_names: return None
                return sequence_name
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

    def get_shot_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.shot_name
            case 'from_settings':
                shot_names = self.list_shot_names()
                if len(shot_names) == 0: return None
                shot_name = self.parm('shot').eval()
                if len(shot_name) == 0: return shot_names[0]
                if shot_name not in shot_names: return None
                return shot_name
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

    def get_department_name(self):
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                entity_data = _entity_from_context_json()
                return entity_data.department_name
            case 'from_settings':
                department_names = self.list_department_names()
                if len(department_names) == 0: return None
                department_name = self.parm('department').eval()
                if len(department_name) == 0: return department_names[0]
                if department_name not in department_names: return None
                return department_name
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

    def get_downstream_department_names(self):
        department_names = self.list_downstream_department_names()
        if len(department_names) == 0: return []
        default_values = api.config.resolve('defaults:/houdini/lops/export_shot_layer')
        selected_department_names = list(filter(len, self.parm('export_departments').eval().split(' ')))
        if len(selected_department_names) == 0: return default_values.get('downstream_departments', [])
        selected_department_names.sort(key = department_names.index)
        return selected_department_names
    
    def get_pool_name(self):
        pool_names = self.list_pool_names()
        if len(pool_names) == 0: return None
        pool_name = self.parm('export_pool').eval()
        if pool_name == '': return pool_names[0]
        return pool_name

    def get_priority(self):
        return self.parm('export_priority').eval()

    def get_frame_range_source(self):
        return self.parm('frame_range').eval()

    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_context':
                sequence_name = self.get_sequence_name()
                shot_name = self.get_shot_name()
                frame_range = api.config.get_frame_range(sequence_name, shot_name)
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
    
    def set_entity_source(self, entity_source):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources: return
        self.parm('entity_source').set(entity_source)
    
    def set_sequence_name(self, sequence_name):
        sequence_names = self.list_sequence_names()
        if sequence_name not in sequence_names: return
        self.parm('sequence').set(sequence_name)
    
    def set_shot_name(self, shot_name):
        shot_names = self.list_shot_names()
        if shot_name not in shot_names: return
        self.parm('shot').set(shot_name)
    
    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)

    def get_export_type(self):
        return self.parm('export_type').eval()
    
    def execute(self, force_local=False):
        if force_local:
            return self._export_local()
        export_type = self.get_export_type()
        match export_type:
            case 'local': return self._export_local()
            case 'farm': return self._export_farm()
            case _: assert False, f'Unknown export type: {export_type}'
    
    def _export_local(self):

        # Nodes
        native = self.native()
        stage_node = native.node('IN_stage')
        dive_node = native.node('dive')
        _clear_dive(dive_node)

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        frame_range, step = self.get_frame_range()
        render_range = frame_range.full_range()
        user_name = get_user_name()
        timestamp = dt.datetime.now()
        fps = api.config.get_fps()

        # Paths
        export_path = api.storage.resolve(
            'export:'
            '/shots'
            f'/{sequence_name}'
            f'/{shot_name}'
            f'/{department_name}'
        )
        version_path = get_next_version_path(export_path)
        version_name = version_path.name

        # Prepare for stage scrape
        root = stage_node.stage().GetPseudoRoot()

        # Scrape stage for assets
        assets = dict()
        asset_inputs = set()
        for asset_metadata in util.list_assets(root):
            asset_path = (
                f'/{asset_metadata["category"]}'
                f'/{asset_metadata["instance"]}'
            )
            assets[asset_path] = asset_metadata
            asset_inputs.update(set(map(json.dumps, asset_metadata['inputs'])))
        
        # Scrape stage for kits
        kits = list()
        kit_inputs = set()
        for kit_metadata in util.list_kits(root):
            kits.append(kit_metadata)
            kit_inputs.update(set(map(json.dumps, kit_metadata['inputs'])))
        
        # Set fps
        self.parm('set_metadata_fps').set(fps)
        
        # Cache the stage
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)

            # Set cache file path
            cache_path = temp_path / f'{sequence_name}_{shot_name}_{version_name}.usd'
            self.parm('cache_file').set(path_str(cache_path))
            self.parm('cache_f1').set(render_range.first_frame)
            self.parm('cache_f2').set(render_range.last_frame)
            self.parm('cache_f3').set(step)
            self.parm('cache_execute').pressButton()
            self.parm('cache_loadfromdisk').set(1)
            self.parm('bypass_input').set(1)

            # Export changes to assets
            # First, group assets by (category, asset) to handle bundling
            asset_groups = dict()
            for asset_path, asset_metadata in assets.items():
                category_name = asset_metadata['category']
                asset_name = asset_metadata['asset']
                instance_name = asset_metadata['instance']
                asset_key = (category_name, asset_name)
                if asset_key not in asset_groups:
                    asset_groups[asset_key] = []
                asset_groups[asset_key].append((asset_path, instance_name))

            # Export each asset group
            parameter_assets = set()
            for (category_name, asset_name), instances in asset_groups.items():

                # Check if asset is animatable
                animatable = api.config.get_asset_animatable(category_name, asset_name)

                if animatable:
                    # Animatable assets: export each instance separately
                    for asset_path, instance_name in instances:
                        output_file_name = (
                            f'asset_'
                            f'{category_name}_'
                            f'{asset_name}_'
                            f'{instance_name}_'
                            f'{department_name}_'
                            f'{version_name}.usd'
                        )
                        output_file_path = temp_path / output_file_name
                        _export_prim(dive_node, asset_path, render_range, step, output_file_path)
                        parameter_assets.add(json.dumps(dict(
                            category = category_name,
                            asset = asset_name,
                            instance = instance_name
                        )))
                else:
                    # Non-animatable assets: bundle all instances into one file
                    output_file_name = (
                        f'asset_'
                        f'{category_name}_'
                        f'{asset_name}_'
                        f'{department_name}_'
                        f'{version_name}.usd'
                    )
                    output_file_path = temp_path / output_file_name
                    # Export all instance paths together into one bundled file
                    instance_paths = [asset_path for asset_path, _ in instances]
                    _export_prims(dive_node, instance_paths, render_range, step, output_file_path)

                    # Still track all instances in metadata
                    for asset_path, instance_name in instances:
                        parameter_assets.add(json.dumps(dict(
                            category = category_name,
                            asset = asset_name,
                            instance = instance_name
                        )))
            
            # Find all kits
            parameter_kits = set()
            for kit_metadata in kits:
                category_name = kit_metadata['category']
                kit_name = kit_metadata['kit']
                parameter_kits.add(json.dumps(dict(
                    category = category_name,
                    kit = kit_name
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
                    context = 'shot',
                    sequence = sequence_name,
                    shot = shot_name,
                    department = department_name,
                    version = version_name,
                    timestamp = timestamp.isoformat(),
                    user = user_name,
                    parameters = dict(
                        assets = list(map(json.loads, parameter_assets)),
                        kits = list(map(json.loads, parameter_kits))
                    )
                )]
            )
            store_json(context_path, context)

            # Copy all files to output path
            version_path.mkdir(parents=True, exist_ok=True)
            for temp_item_path in temp_path.iterdir():
                if temp_item_path.name == 'stage': continue
                if temp_item_path.name == cache_path.name: continue
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
        dive_node.layoutChildren()

        # Update node comment
        native.setComment(
            'last export: '
            f'{version_name} \n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")} \n'
            f'by {user_name}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

    def _export_farm(self):
        """Submit export job to farm"""
        # Import job module
        try:
            from tumblehead.farm.jobs.houdini.publish import job as publish_job
        except ImportError as e:
            hou.ui.displayMessage(
                f"Failed to import publish job module: {str(e)}",
                severity=hou.severityType.Error
            )
            return
        
        # Get parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        
        if not all([sequence_name, shot_name, department_name]):
            hou.ui.displayMessage(
                "Missing required parameters (sequence, shot, or department)",
                severity=hou.severityType.Error
            )
            return
        
        frame_range, _step = self.get_frame_range()
        
        # Get farm parameters
        downstream_deps = self.get_downstream_department_names()
        pool_name = self.get_pool_name()
        priority = self.get_priority()
        
        # Build config
        config = {
            'entity': {
                'tag': 'shot',
                'sequence_name': sequence_name,
                'shot_name': shot_name,
                'department_name': department_name
            },
            'settings': {
                'priority': priority,
                'pool_name': pool_name,
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
        try:
            publish_job.submit(config, {})
            
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
            
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to submit farm job: {str(e)}",
                severity=hou.severityType.Error
            )
            return

    def open_location(self):
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        export_path = latest_shot_export_path(
            sequence_name,
            shot_name,
            department_name
        )
        if export_path is None: return
        if not export_path.exists(): return
        hou.ui.showInFileBrowser(f'{path_str(export_path)}/')

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

    # Change entity source to settings if we have no context
    entity = _entity_from_context_json()
    if entity is not None: return
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