from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.util.io import load_json
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.context as ctx
from tumblehead.pipe.paths import (
    list_version_paths,
    get_workfile_context
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/import_shot_layer')

class ImportShotLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_shot_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return [entity.uri for entity in shot_entities]

    def list_department_names(self):
        shot_departments = list_departments('shots')
        if len(shot_departments) == 0: return []
        shot_department_names = [dept.name for dept in shot_departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in shot_department_names
        ]
    
    def list_version_names(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return []
        department_name = self.get_department_name()
        if department_name is None: return []
        export_uri = (
            Uri.parse_unsafe('export:/') /
            shot_uri.segments /
            department_name
        )
        layer_path = api.storage.resolve(export_uri)
        version_paths = list_version_paths(layer_path)
        version_names = [path.name for path in version_paths]
        return version_names
    
    def _get_instance_names(self) -> dict:

        # Parameters
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        version_name = self.get_version_name()

        # Check paramters
        if shot_uri is None: return dict()
        if department_name is None: return dict()
        if version_name is None: return dict()

        # Load context
        export_uri = (
            Uri.parse_unsafe('export:/') /
            shot_uri.segments /
            department_name /
            version_name
        )
        layer_path = api.storage.resolve(export_uri)
        context_path = layer_path / 'context.json'
        if not context_path.exists(): return dict()
        context_data = load_json(context_path)
        layer_info = ctx.find_output(
            context_data,
            entity = str(shot_uri),
            department = department_name
        )
        if layer_info is None: return dict()
        parameters = layer_info['parameters']

        # Find assets instances
        asset_instances = dict()
        for asset_datum in parameters['assets']:
            asset_uri = Uri.parse_unsafe(asset_datum['asset'])
            instance_name = asset_datum['instance']
            if asset_uri not in asset_instances:
                asset_instances[asset_uri] = list()
            asset_instances[asset_uri].append(instance_name)
        return asset_instances
    
    def list_asset_uris(self) -> list[Uri]:
        asset_instances = self._get_instance_names()
        match self.get_stage_type():
            case 'asset': return list(asset_instances.keys())
            case _: return list()

    def list_instance_names(self):
        asset_instances = self._get_instance_names()
        match self.get_stage_type():
            case 'asset':
                asset_uri = self.get_asset_uri()
                if asset_uri not in asset_instances: return list()
                properties = api.config.get_properties(asset_uri)
                animatable = properties['animatable']
                if not animatable: return list()
                return asset_instances[asset_uri]
            case _:
                return list()

    def get_shot_uri(self) -> Uri | None:
        shot_uris = self.list_shot_uris()
        if len(shot_uris) == 0: return None
        shot_uri_raw = self.parm('shot').eval()
        if len(shot_uri_raw) == 0: return shot_uris[0]
        shot_uri = Uri.parse_unsafe(shot_uri_raw)
        if shot_uri not in shot_uris: return None
        return shot_uri
    
    def get_department_name(self) -> str | None:
        department_names = self.list_department_names()
        if len(department_names) == 0: return None
        department_name = self.parm('department').eval()
        if len(department_name) == 0: return department_names[0]
        if department_name not in department_names: return None
        return department_name

    def get_version_name(self) -> str | None:
        version_names = self.list_version_names()
        if len(version_names) == 0: return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0: return version_names[-1]
        if version_name == 'latest': return version_names[-1]
        if version_name not in version_names: return None
        return version_name
    
    def get_stage_type(self) -> str:
        return self.parm('stage_type').eval()
    
    def get_asset_uri(self) -> Uri | None:
        asset_uris = self.list_asset_uris()
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm('asset').eval()
        if len(asset_uri_raw) == 0: return asset_uris[0]
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in asset_uris: return None
        return asset_uri

    def get_instance_name(self) -> str | None:
        instance_names = self.list_instance_names()
        if len(instance_names) == 0: return None
        instance_name = self.parm('instance').eval()
        if len(instance_name) == 0: return instance_names[0]
        if instance_name not in instance_names: return None
        return instance_name

    def get_include_layerbreak(self) -> bool:
        return bool(self.parm('include_layerbreak').eval())
    
    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if shot_uri not in shot_uris: return
        self.parm('shot').set(str(shot_uri))
    
    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)
    
    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)
    
    def set_stage_type(self, stage_type):
        match stage_type:
            case 'asset': self.parm('stage_type').set('asset')
            case 'cameras': self.parm('stage_type').set('cameras')
            case 'lights': self.parm('stage_type').set('lights')
            case 'volumes': self.parm('stage_type').set('volumes')
            case 'collections': self.parm('stage_type').set('collections')
            case 'render': self.parm('stage_type').set('render')
            case 'scene': self.parm('stage_type').set('scene')
            case _: assert False, f'Invalid stage type {stage_type}'
    
    def set_asset_uri(self, asset_uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('asset').set(str(asset_uri))
    
    def set_instance_name(self, instance_name):
        instance_names = self.list_instance_names()
        if instance_name not in instance_names: return
        self.parm('instance').set(instance_name)
    
    def set_include_layerbreak(self, include_layerbreak):
        self.parm('include_layerbreak').set(int(include_layerbreak))
    
    def latest(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return
        self.set_version_name(version_names[-1])

    def execute(self):

        # Parameters
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        version_name = self.get_version_name()

        # Check parameters
        if shot_uri is None: return
        if department_name is None: return
        if version_name is None: return

        # Paths
        export_uri = (
            Uri.parse_unsafe('export:/') /
            shot_uri.segments /
            department_name /
            version_name
        )
        input_version_path = api.storage.resolve(export_uri)
        
        # Get layer file path
        def _get_layer_file_name():
            stage_type = self.get_stage_type()
            match stage_type:
                case 'asset':

                    # Get asset parameters
                    asset_uri = self.get_asset_uri()
                    if asset_uri is None: return

                    # Check if asset is animatable to determine filename format
                    properties = api.config.get_properties(asset_uri)
                    animatable = properties['animatable']

                    if animatable:
                        # Animatable assets include instance_name in filename
                        instance_name = self.get_instance_name()
                        if instance_name is None: return
                        return '.'.join([
                            '_'.join(asset_uri.segments + [
                                instance_name,
                                department_name,
                                version_name
                            ]),
                            'usd'
                        ])
                    else:
                        # Non-animatable assets don't use instance_name
                        return '.'.join([
                            '_'.join(asset_uri.segments + [
                                department_name,
                                version_name
                            ]),
                            'usd'
                        ])
                case 'cameras':
                    return f'cameras_{version_name}.usd'
                case 'lights':
                    return f'lights_{version_name}.usd'
                case 'volumes':
                    return f'volumes_{version_name}.usd'
                case 'collections':
                    return f'collections_{version_name}.usd'
                case 'render':
                    return f'render_{version_name}.usd'
                case 'scene':
                    return f'scene_{version_name}.usd'
                case _:
                    assert False, f'Invalid stage type {stage_type}'
        
        input_file_path = input_version_path / _get_layer_file_name()

        # Import layer file
        self.parm('import_enable1').set(1 if input_file_path.exists() else 0)
        self.parm('import_filepath1').set(path_str(input_file_path))

        # Update the version label on the node UI
        self.parm('version_label').set(version_name)

def create(scene, name):
    node_type = ns.find_node_type('import_shot_layer', 'Lop')
    assert node_type is not None, 'Could not find import_shot_layer node type'
    native = scene.node(name)
    if native is not None: return ImportShotLayer(native)
    return ImportShotLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('import_shot_layer', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = ImportShotLayer(raw_node)

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return

    # Set the default values from context
    node.set_shot_uri(context.entity_uri)

def execute():
    raw_node = hou.pwd()
    node = ImportShotLayer(raw_node)
    node.execute()