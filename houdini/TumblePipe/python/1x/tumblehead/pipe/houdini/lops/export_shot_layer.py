from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt
import shutil
import json

import hou

from tumblehead.api import get_user_name, path_str, default_client
from tumblehead.config import FrameRange
from tumblehead.util.io import store_json
from tumblehead.pipe.houdini import util
from tumblehead.pipe.houdini.lops import import_shot_layer
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    get_next_version_path,
    get_workfile_context,
    ShotContext
)

api = default_client()

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
    
    def get_sequence_name(self):
        sequence_names = self.list_sequence_names()
        if len(sequence_names) == 0: return None
        sequence_name = self.parm('sequence').eval()
        if len(sequence_name) == 0: return sequence_names[0]
        if sequence_name not in sequence_names: return None
        return sequence_name

    def get_shot_name(self):
        shot_names = self.list_shot_names()
        if len(shot_names) == 0: return None
        shot_name = self.parm('shot').eval()
        if len(shot_name) == 0: return shot_names[0]
        if shot_name not in shot_names: return None
        return shot_name
    
    def get_department_name(self):
        department_names = self.list_department_names()
        if len(department_names) == 0: return None
        department_name = self.parm('department').eval()
        if len(department_name) == 0: return department_names[0]
        if department_name not in department_names: return None
        return department_name
    
    def get_frame_range_source(self):
        return self.parm('frame_range').eval()

    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_config':
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

    def execute(self):

        # Nodes
        context = self.native()
        stage_node = context.node('stage')
        cache_node = context.node('cache')
        bypass_node = context.node('bypass')
        dive_node = context.node('dive')
        _clear_dive(dive_node)

        # Parameters
        sequence_name = self.get_sequence_name()
        shot_name = self.get_shot_name()
        department_name = self.get_department_name()
        frame_range, step = self.get_frame_range()
        render_range = frame_range.full_range()
        timestamp = dt.datetime.now().isoformat()

        # Paths
        export_path = api.storage.resolve(f'export:/shots/{sequence_name}/{shot_name}/{department_name}')
        version_path = get_next_version_path(export_path)
        version_name = version_path.name

        # Prepare for stage scrape
        root = stage_node.stage().GetPseudoRoot()

        # Scrape stage for assets
        assets = dict()
        asset_inputs = set()
        for asset_metadata in util.list_assets(root):
            asset_path = f'/{asset_metadata["category"]}/{asset_metadata["instance"]}'
            assets[asset_path] = asset_metadata
            asset_inputs.update(set(map(json.dumps, asset_metadata['inputs'])))
        
        # Scrape stage for kits
        kits = list()
        kit_inputs = set()
        for kit_metadata in util.list_kits(root):
            kits.append(kit_metadata)
            kit_inputs.update(set(map(json.dumps, kit_metadata['inputs'])))
        
        # Cache the stage
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp_path = Path(temp_dir)

            # Set cache file path
            cache_path = temp_path / f'{sequence_name}_{shot_name}_{version_name}.usd'
            cache_node.parm('file').set(path_str(cache_path))
            cache_node.parm('f1').set(render_range.first_frame)
            cache_node.parm('f2').set(render_range.last_frame)
            cache_node.parm('f3').set(step)
            cache_node.parm('execute').pressButton()
            cache_node.parm('loadfromdisk').set(1)
            bypass_node.parm('input').set(1)

            # Export changes to assets
            parameter_assets = set()
            for asset_path, asset_metadata in assets.items():
                category_name = asset_metadata['category']
                asset_name = asset_metadata['asset']
                instance_name = asset_metadata['instance']
                output_file_name = f'asset_{category_name}_{asset_name}_{instance_name}_{department_name}_{version_name}.usd'
                output_file_path = temp_path / output_file_name
                _export_prim(dive_node, asset_path, render_range, step, output_file_path)
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
                    timestamp = timestamp,
                    user = get_user_name(),
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
            cache_node.parm('loadfromdisk').set(0)
            bypass_node.parm('input').set(0)
            cache_node.parm('file').set('')
            
        # Layout the created nodes
        dive_node.layoutChildren()

        # Clear import caches
        import_shot_layer.clear_cache()

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

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('export_shot_layer', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ExportShotLayer(raw_node)

    # Parse workfile path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return
    
    # Set the default values
    match context:
        case ShotContext(
            department_name,
            sequence_name,
            shot_name,
            version_name
            ):
            node.set_sequence_name(sequence_name)
            node.set_shot_name(shot_name)
            node.set_department_name(department_name)

def on_loaded(raw_node):

    # Set node style
    set_style(raw_node)

def execute():
    raw_node = hou.pwd()
    node = ExportShotLayer(raw_node)
    node.execute()