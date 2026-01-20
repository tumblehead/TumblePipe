from pathlib import Path

import hou

from tumblehead.api import path_str, default_client
from tumblehead.config.timeline import FrameRange
from tumblehead.pipe.houdini import util
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    list_version_paths
)

api = default_client()

def _connect(node1, node2):
    port = len(node2.inputs())
    node2.setInput(port, node1)

def _clear_children(scene_node):
    for child_node in scene_node.children():
        if child_node.type().name() == 'output': continue
        child_node.destroy()

"""
cache_config = {
    'cache_name': str,
    'cache_path': Path,
    'primitive_path: str,
    'point_attribs': list[str],
    'vertex_attribs': list[str],
    'primitive_attribs': list[str],
    'detail_attribs': list[str],
    'simulation': bool,
    'frame_first': int,
    'frame_last': int,
    'frame_step': int
}
"""

def _create_exporter_node(scene_node, node_name, config):
    
    # Create exporter subnet node
    exporter_node = scene_node.createNode('subnet', node_name)
    exporter_node_input = exporter_node.indirectInputs()[0]

    # Create attribute delete node
    prune_node = exporter_node.createNode('attribdelete', 'prune_attribs')
    prune_node.parm('negate').set(1)
    prune_node.parm('ptdel').set(' '.join(config['point_attribs']))
    prune_node.parm('vtxdel').set(' '.join(config['vertex_attribs']))
    prune_node.parm('primdel').set(' '.join(config['primitive_attribs']))
    prune_node.parm('dtldel').set(' '.join(config['detail_attribs']))
    _connect(exporter_node_input, prune_node)

    # Create file cache node
    cache_node = exporter_node.createNode('filecache', 'export_cache')
    cache_node.parm('filemethod').set(1)
    cache_node.parm('file').set(path_str(config['cache_path']))
    cache_node.parm('cachesim').set(int(config['simulation']))
    cache_node.parm('trange').set(1)
    cache_node.parm('f1').deleteAllKeyframes()
    cache_node.parm('f2').deleteAllKeyframes()
    cache_node.parm('f3').deleteAllKeyframes()
    cache_node.parm('f1').set(config['frame_first'])
    cache_node.parm('f2').set(config['frame_last'])
    cache_node.parm('f3').set(config['frame_step'])
    _connect(prune_node, cache_node)
    return cache_node

def _create_importer_node(scene_node, node_name, config):

    # File paths
    cache_path = config['cache_path']
    cache_dir = cache_path.parent
    manifest_path = cache_dir / 'manifest.usd'
    topology_path = cache_dir / 'topology.usd'

    # Create the geo clip sequence node
    clip_node = scene_node.createNode('geoclipsequence', node_name)
    clip_node.parm('sample_behavior').set('multi')
    clip_node.parm('sample_f1').deleteAllKeyframes()
    clip_node.parm('sample_f2').deleteAllKeyframes()
    clip_node.parm('sample_f3').deleteAllKeyframes()
    clip_node.parm('sample_f1').set(config['frame_first'])
    clip_node.parm('sample_f2').set(config['frame_last'])
    clip_node.parm('sample_f3').set(config['frame_step'])
    clip_node.parm('primpath').set(config['primitive_path'])
    clip_node.parm('clipprimpath').set(config['primitive_path'])
    clip_node.parm('loadclipfilepath').set(
        f'{path_str(cache_path)}'
        ':SDF_FORMAT_ARGS:sampleframe=$F'
    )
    clip_node.parm('manifestfile').set(path_str(manifest_path))
    clip_node.parm('topologyfile').set(path_str(topology_path))
    clip_node.parm('setendframe').set(1)
    clip_node.parm('startframe').deleteAllKeyframes()
    clip_node.parm('startframe').set(config['frame_first'])
    clip_node.parm('endframe').set(config['frame_last'])
    return clip_node

class SopModify(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def _get_cache_path(self):
        return Path('$HIP/sop_modify')

    def _next_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return 'v0001'
        latest_version_name = version_names[-1]
        latest_version_code = api.naming.get_version_code(latest_version_name)
        next_version_code = latest_version_code + 1
        return api.naming.get_version_name(next_version_code)
    
    def _list_cache_configs(self, version_name):
        result = list()
        cache_name = self.get_cache_name()
        if cache_name is None or version_name is None:
            return result
        cache_path = self._get_cache_path() / cache_name / version_name
        frame_range, step = self.get_frame_range()
        render_range = frame_range.full_range()
        for index in range(self.parm('exports').eval()):
            export_name = self.parm(f'export_name{index}').eval()
            prim_path = self.parm(f'export_prim_path{index}').eval()
            simulation = self.parm(f'export_simulation{index}').eval()
            export_path = cache_path / export_name / f'{export_name}.$F4.bgeo.sc'
            point_attribs = self.parm(f'export_point_attribs{index}').eval().split(' ')
            vertex_attribs = self.parm(f'export_vertex_attribs{index}').eval().split(' ')
            primitive_attribs = self.parm(f'export_primitive_attribs{index}').eval().split(' ')
            detail_attribs = self.parm(f'export_detail_attribs{index}').eval().split(' ')
            result.append(dict(
                cache_name = export_name,
                cache_path = export_path,
                primitive_path = prim_path,
                point_attribs = point_attribs,
                vertex_attribs = vertex_attribs,
                primitive_attribs = primitive_attribs,
                detail_attribs = detail_attribs,
                simulation = simulation,
                frame_first = render_range.first_frame,
                frame_last = render_range.last_frame,
                frame_step = step
            ))
        return result

    def list_cache_names(self):
        cache_path = self._get_cache_path()
        if cache_path is None: return []
        if not cache_path.exists(): return []
        cache_names = [
            item_path.name
            for item_path in cache_path.iterdir()
            if item_path.is_dir()
        ]
        if len(cache_names) == 0: return []
        cache_names.sort()
        return cache_names
    
    def list_version_names(self):
        cache_path = self._get_cache_path()
        base_path = Path(hou.hipFile.path()).parent
        cache_path = base_path / Path(*cache_path.parts[1:])
        if cache_path is None: return []
        cache_name = self.get_cache_name()
        if cache_name is None: return []
        cache_path = cache_path / cache_name
        version_names = list(filter(
            api.naming.is_valid_version_name,
            [path.name for path in list_version_paths(cache_path)]
        ))
        if len(version_names) == 0: return []
        version_names.sort(key = api.naming.get_version_code)
        return version_names
    
    def get_cache_name(self):
        result = self.parm('name').eval()
        if len(result) == 0: return None
        return result

    def get_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0: return version_names[0]
        if version_name not in version_names: return None
        return version_name
    
    def get_frame_range_source(self):
        return self.parm('frame_range').eval()
    
    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'playback_range':
                frame_range = util.get_frame_range()
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range setting "{frame_range_source}"'
    
    def set_cache_name(self, name):
        self.parm('name').set('' if name is None else name)
    
    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)

    def cache(self):

        # Nodes
        native = self.native()
        caching_node = hou.node(f'{native.path()}/sopnet/caching')
        caching_node_input = caching_node.indirectInputs()[0]

        # Clear existing caching nodes
        _clear_children(caching_node)

        # Generate the cache nodes
        exporter_nodes = list()
        version_name = self._next_version_name()
        with util.update_mode(hou.updateMode.Manual):
            for config in self._list_cache_configs(version_name):

                # Create the exporter nodes
                cache_name = config['cache_name']
                exporter_name = f'export_{cache_name}'
                exporter_node = _create_exporter_node(
                    caching_node,
                    exporter_name,
                    config
                )
                _connect(caching_node_input, exporter_node.parent())
                exporter_nodes.append(exporter_node)
        
        # Layout the created nodes
        caching_node.layoutChildren()

        # Export the caches
        for exporter_node in exporter_nodes:
            exporter_node.parm('execute').pressButton()

        # Set version name
        self.set_version_name(version_name)

        # Load the newly cached version
        self.load()
    
    def load(self):

        # Nodes
        native = self.native()
        loading_node = native.node('loading')
        loading_node_input = loading_node.indirectInputs()[0]
        loading_node_output = loading_node.node('output0')

        # Clear existing loading nodes
        _clear_children(loading_node)

        # Create the merge node
        merge_node = loading_node.createNode('merge', 'merge_imports')
        merge_node.parm('mergestyle').set('separate')
        loading_node_output.setInput(0, merge_node)

        # Generate the loading nodes
        version_name = self.get_version_name()
        with util.update_mode(hou.updateMode.Manual):
            for config in self._list_cache_configs(version_name):

                # Create the importer nodes
                cache_name = config['cache_name']
                importer_name = f'import_{cache_name}'
                importer_node = _create_importer_node(
                    loading_node,
                    importer_name,
                    config
                )
                _connect(loading_node_input, importer_node)
                _connect(importer_node, merge_node)
        
        # Layout the created nodes
        loading_node.layoutChildren()
        
        # Set node state color
        version_names = self.list_version_names()
        if len(version_names) == 0:
            return
        latest_version_name = version_names[-1]
        native.setColor(
            hou.Color(0, .8, .1)
            if version_name == latest_version_name else
            hou.Color(1, 0.6, 0)
        )

        # Update node comment
        native.setComment(
            f'Latest version: {latest_version_name}\n'
            f'Loaded {version_name}'
        )
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

def create(scene, name):
    node_type = ns.find_node_type('sop_modify', 'Lop')
    assert node_type is not None, 'Could not find sop_modify node type'
    native = scene.node(name)
    if native is not None: return SopModify(native)
    return SopModify(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

def latest():
    raw_node = hou.pwd()
    node = SopModify(raw_node)
    version_names = node.list_version_names()
    if len(version_names) == 0: return
    latest_version_name = version_names[-1]
    node.set_version_name(latest_version_name)

def cache():
    raw_node = hou.pwd()
    node = SopModify(raw_node)
    node.cache()

def load():
    raw_node = hou.pwd()
    node = SopModify(raw_node)
    node.load()