from pathlib import Path

import hou

from tumblepipe.api import path_str, api
from tumblepipe.config.timeline import FrameRange, get_frame_range
from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini import util
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    get_workspace_relpath,
)

class Location:
    Project = 'project'
    Proxy = 'proxy'

def _resolve_cache_location(location_name, base_path):
    return api.storage.resolve(
        Uri.parse_unsafe(f'{location_name}:/{base_path}/lops_cache')
    )

def list_cache_locations():
    """Every LOP cache directory the th::cache nodes in this scene address.

    These directories hold versioned caches on shared storage, so exported
    layers may keep composition arcs into them (published by reference). The
    list is derived by walking the actual cache nodes rather than the
    workfile's own location, so it stays in agreement with each node's
    ``_get_cache_path()`` even when a node's entity/department parms point it
    at another workfile's cache. Both storage purposes (project + proxy) are
    exempted per base path; a purpose with no cache on disk is a harmless
    no-op exemption.
    """
    seen = set()
    locations = []
    for raw_node in ns.list_by_node_type('cache', 'Lop'):
        base_path = Cache(raw_node)._cache_base_path()
        if base_path is None: continue
        for location_name in (Location.Project, Location.Proxy):
            location = _resolve_cache_location(location_name, base_path)
            if location is None: continue
            key = str(location)
            if key in seen: continue
            seen.add(key)
            locations.append(location)
    return locations

class Cache(EntityNode):
    """LOP cache wrapper.

    The 'entity' and 'department' parms (both default 'from_context') address
    which workfile's cache directory this node reads and writes. Leaving both
    at 'from_context' targets the node's own workfile — the common case, and
    identical to the pre-department behaviour. Pointing 'department' (or
    'entity') elsewhere lets a node load a cache another workfile produced.

    'entity' also feeds the 'from_config' frame-range source, which reads the
    shot's authored range — start/end plus pre/post roll — straight out of
    the database instead of making the artist retype it.

    list_cache_locations() above walks the actual cache nodes, so the
    export-by-reference guard stays in agreement with _get_cache_path() no
    matter where the parms point.
    """

    def __init__(self, native):
        super().__init__(native)

    # Department resolution (same idiom as import_layer/import_shot: a
    # 'from_context' sentinel plus the entity's departments). Unlike those
    # nodes, the list is NOT filtered to publishable departments — a cache can
    # be written from, and loaded into, any workfile.

    def list_department_names(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None or not entity_uri.segments:
            return ['from_context']
        names = [d.name for d in self.scoped_departments(entity_uri.segments[0])]
        return ['from_context'] + names

    def get_department_name(self):
        """The resolved department name driving the cache path, or None.

        'from_context' resolves to the workfile's own department; a concrete
        value is honoured only if it is a department of the addressed entity.
        """
        department_name_raw = self.parm('department').eval()
        if department_name_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None: return None
            return context.department_name
        department_names = self.list_department_names()
        if department_name_raw not in department_names: return None
        return department_name_raw

    # Cache path --------------------------------------------------------------

    def _cache_base_path(self):
        """The project-relative base this node's cache anchors under, or None."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None: return None
        department_name = self.get_department_name()
        if department_name is None: return None
        return get_workspace_relpath(entity_uri, department_name)

    def _get_cache_path(self):
        base_path = self._cache_base_path()
        if base_path is None: return None
        return _resolve_cache_location(self.get_location_name(), base_path)

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
        output_path = cache_path / cache_name / version_name / f'{version_name}.usd'

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
        output_path = cache_path / cache_name / version_name / f'{version_name}.usd'

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
    return ns.create_node(scene, name, Cache, 'cache')

def set_style(raw_node):
    ns.set_node_style(raw_node)

def on_created(raw_node):

    # Set node style. 'entity' and 'department' keep their 'from_context'
    # defaults (never bake a concrete value here — see EntityNode).
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