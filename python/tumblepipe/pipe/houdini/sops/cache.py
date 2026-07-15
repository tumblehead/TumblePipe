from pathlib import Path

import hou

from tumblepipe.api import path_str, api
from tumblepipe.util.uri import Uri
from tumblepipe.config.timeline import FrameRange, get_frame_range
from tumblepipe.pipe.houdini import util
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.paths import (
    list_version_paths,
    get_workfile_context
)

def _cache_frames_exist(cache_path):
    return any(cache_path.parent.glob(cache_path.name.replace('$F4', '*')))

class Location:
    Project = 'project'
    Proxy = 'proxy'

class Cache(EntityNode):
    """SOP cache wrapper.

    The 'entity' parm (default 'from_context') is what ties the cache to the
    pipeline: it feeds the 'from_config' frame-range source, which reads the
    shot's authored range — start/end plus pre/post roll — straight out of
    the database instead of making the artist retype it.

    It deliberately does NOT drive _get_cache_path(): the cache directory is
    still derived from the workfile's own location on disk, because
    lops.cache.list_cache_locations() has to agree with it for the
    export-by-reference guard, and it cannot see a per-node entity parm.
    """

    def __init__(self, native):
        super().__init__(native)

    def _get_cache_path(self):
        file_path = Path(hou.hipFile.path())
        if not file_path.exists(): return None
        context = get_workfile_context(file_path)
        if context is None: return None
        base_path = '/'.join(file_path.parent.parts[-4:])
        match self.get_location_name():
            case Location.Project:
                return api.storage.resolve(Uri.parse_unsafe(f'project:/{base_path}/cache'))
            case Location.Proxy:
                return api.storage.resolve(Uri.parse_unsafe(f'proxy:/{base_path}/cache'))

    def _next_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return 'v0001'
        latest_version_name = version_names[-1]
        latest_version_code = api.naming.get_version_code(latest_version_name)
        next_version_code = latest_version_code + 1
        return api.naming.get_version_name(next_version_code)

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
        if cache_path is None: return []
        if not cache_path.exists(): return []
        cache_name = self.get_cache_name()
        if cache_name is None: return []
        cache_path = cache_path / cache_name
        version_names = list(filter(
            api.naming.is_valid_version_name,
            [path.name for path in list_version_paths(cache_path)]
        ))
        if len(version_names) == 0: return []
        version_names.sort(key=api.naming.get_version_code)
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
    
    def get_location_name(self):
        location_name = self.parm('location').eval()
        match location_name:
            case Location.Project: return Location.Project
            case Location.Proxy: return Location.Proxy
            case _: assert False, f'Invalid location index: {location_name}'
    
    def get_frame_range_source(self):
        return self.parm('frame_range').eval()

    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'single_frame':
                frame_index = hou.frame()
                return FrameRange(frame_index, frame_index, 0, 0), 1
            case 'playback_range':
                frame_range = util.get_frame_range()
                return frame_range, 1
            case 'from_config':
                # The entity's authored range, roll included.
                entity_uri = self.get_entity_uri()
                if entity_uri is None: return None
                frame_range = get_frame_range(entity_uri)
                if frame_range is None: return None
                return frame_range, frame_range.step_size
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range setting "{frame_range_source}"'

    def _update_labels(self):
        entity_raw = self.parm('entity').eval()
        entity_uri = self.get_entity_uri()
        if entity_raw == 'from_context':
            self.parm('entity_label').set(
                f'from_context: {entity_uri}' if entity_uri else 'from_context: none'
            )
        else:
            self.parm('entity_label').set(str(entity_uri) if entity_uri else 'none')
    
    def set_cache_name(self, name):
        self.parm('name').set('' if name is None else name)
    
    def set_version_name(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names: return
        self.parm('version').set(version_name)
    
    def set_location_name(self, location_name):
        match location_name:
            case Location.Project: self.parm('location').set(Location.Project)
            case Location.Proxy: self.parm('location').set(Location.Proxy)
            case _: assert False, f'Invalid location name: {location_name}'

    def cache(self):

        # Nodes
        native = self.native()

        # Check if name is defined
        cache_name = self.get_cache_name()
        if cache_name is None:
            native.parm('cache/file').set('')
            native.parm('cache/loadfromdisk').set(0)
            return

        # Frame range. 'from_config' yields nothing when the entity can't be
        # resolved (unsaved scene, no authored range) — refuse rather than
        # silently cache a single arbitrary frame.
        resolved_range = self.get_frame_range()
        if resolved_range is None:
            native.parm('cache/file').set('')
            native.parm('cache/loadfromdisk').set(0)
            ns.set_node_comment(
                native,
                'No frame range: the entity has none authored in the database.'
            )
            return

        # Output path
        version_name = self._next_version_name()
        cache_path = self._get_cache_path()
        output_path = cache_path / cache_name / version_name / f'{version_name}.$F4.bgeo.sc'

        # Cache the scene
        frame_range, frame_step = resolved_range
        render_range = frame_range.full_range()
        native.parm('cache/file').set(path_str(output_path))
        native.parm('cache/f1').set(render_range.first_frame)
        native.parm('cache/f2').set(render_range.last_frame)
        native.parm('cache/f3').set(frame_step)
        native.parm('cache/execute').pressButton()
        native.parm('cache/loadfromdisk').set(1)

        # Check the frame range source
        frame_range_source = self.get_frame_range_source()
        if frame_range_source == 'single_frame':
            native.parm('timeshift/frame').set(render_range.first_frame)
            native.parm('switch/input').set(0)
        else:
            native.parm('switch/input').set(1)

        # Set version name
        self.set_version_name(version_name)
    
    def load(self):

        # Nodes
        native = self.native()

        # Parameters
        cache_name = self.get_cache_name()
        version_name = self.get_version_name()

        # Check if version is defined
        if cache_name is None or version_name is None:
            native.parm('cache/file').set('')
            native.parm('cache/loadfromdisk').set(0)
            return

        # Frame range (see cache() — 'from_config' can yield nothing)
        resolved_range = self.get_frame_range()
        if resolved_range is None:
            native.parm('cache/file').set('')
            native.parm('cache/loadfromdisk').set(0)
            ns.set_node_comment(
                native,
                'No frame range: the entity has none authored in the database.'
            )
            return

        # Input path
        cache_path = self._get_cache_path()
        uncompressed_output_path = cache_path / cache_name / version_name / f'{version_name}.$F4.bgeo'
        compressed_output_path = cache_path / cache_name / version_name / f'{version_name}.$F4.bgeo.sc'
        output_path = (
            uncompressed_output_path
            if _cache_frames_exist(uncompressed_output_path)
            else compressed_output_path
        )

        # Cache the scene
        frame_range, frame_step = resolved_range
        render_range = frame_range.full_range()
        native.parm('cache/file').set(path_str(output_path))
        native.parm('cache/f1').set(render_range.first_frame)
        native.parm('cache/f2').set(render_range.last_frame)
        native.parm('cache/f3').set(frame_step)
        native.parm('cache/loadfromdisk').set(1)

        # Check the frame range source
        frame_range_source = self.get_frame_range_source()
        if frame_range_source == 'single_frame':
            native.parm('timeshift/frame').set(render_range.first_frame)
            native.parm('switch/input').set(0)
        else:
            native.parm('switch/input').set(1)
        
        # Set node state color
        latest_version_name = self.list_version_names()[-1]
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
    return ns.create_node(scene, name, Cache, 'cache', 'Sop')

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style. 'entity' keeps its 'from_context' default.
    set_style(raw_node)
    Cache(raw_node)._update_labels()

def select():
    """Entity picker button callback."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = Cache(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        include_from_context=True,
        current_selection=raw_node.parm('entity').eval(),
        title="Select Entity",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            raw_node.parm('entity').set(selected_uri)
            node._update_labels()

def latest():
    raw_node = hou.pwd()
    node = Cache(raw_node)
    version_names = node.list_version_names()
    if len(version_names) == 0: return
    latest_version_name = version_names[-1]
    node.set_version_name(latest_version_name)

def cache():
    raw_node = hou.pwd()
    node = Cache(raw_node)
    node.cache()

def load():
    raw_node = hou.pwd()
    node = Cache(raw_node)
    node.load()