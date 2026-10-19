"""The ``th::cache`` nodes, LOP and SOP: one implementation, two outputs.

Both HDAs write versioned caches under the addressed workfile's folder and
differ in three things only, which the subclasses pin as class attributes:

- ``CATEGORY``: the node type category (``Lop`` / ``Sop``);
- ``STORAGE_SEGMENT``: the folder under the workfile's base path
  (``lops_cache`` / ``cache``);
- ``EXTENSION``: what a version's file is called after its version name —
  one ``.usd`` stage for the LOP, ``$F4`` ``.bgeo.sc`` frames for the SOP.

The on-disk layout is ``<location>:/<workspace>/<STORAGE_SEGMENT>/<name>/
<version>/<version><EXTENSION>``; ``cache_file_path`` is the only place that
builds it, so a version is only ever read in the format it was written in.

The HDAs' callbacks call the module-level functions here with their
``kwargs``; the node's category picks the class.
"""

from pathlib import Path

import hou

from tumblepipe.api import path_str, api
from tumblepipe import naming
from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.frame_range import FrameRangeNode, FrameRangeSource
from tumblepipe.pipe.paths import (
    list_version_paths,
    get_workspace_relpath,
)


class Location:
    Project = 'project'
    Proxy = 'proxy'


class CacheBase(FrameRangeNode, EntityNode):
    """A th::cache node.

    The 'entity' and 'department' parms (both default 'from_context') address
    which workfile's cache directory this node reads and writes. Leaving both
    at 'from_context' targets the node's own workfile — the common case.
    Pointing 'department' (or 'entity') elsewhere lets a node load a cache
    another workfile produced.

    'entity' also feeds the entity frame-range source, which reads the
    entity's authored range — start/end plus pre/post roll — straight out of
    the database instead of making the artist retype it.

    list_cache_locations() walks the actual cache nodes, so the
    export-by-reference guard stays in agreement with _get_cache_path() no
    matter where the parms point.
    """

    CATEGORY: str
    STORAGE_SEGMENT: str
    EXTENSION: str

    # Department resolution: EntityNode.get_department_name over this list.
    # Unlike import_layer, the list is NOT filtered to publishable
    # departments — a cache can be written from, and loaded into, any
    # workfile.

    def list_department_names(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['from_context']
        names = [d.name for d in self.scoped_departments(entity_uri.segments[0])]
        return ['from_context'] + names

    # Cache path --------------------------------------------------------------

    @classmethod
    def resolve_location(cls, location_name: str, base_path) -> Path:
        """The storage folder of ``base_path``'s caches at ``location_name``."""
        return api.storage.resolve(Uri.parse(
            f'{location_name}:/{base_path}/{cls.STORAGE_SEGMENT}'
        ))

    @classmethod
    def cache_file_path(
        cls, cache_path: Path, cache_name: str, version_name: str,
        ) -> Path:
        """The file one cache version is written to and loaded from."""
        return cache_path / cache_name / version_name / f'{version_name}{cls.EXTENSION}'

    def _cache_base_path(self):
        """The project-relative base this node's cache anchors under.

        None while 'from_context' is unset: such a node addresses no cache,
        so its menus are empty and list_cache_locations() exempts nothing
        for it. An explicit entity/department that does not resolve raises.
        """
        entity_uri = self.get_entity_uri()
        if entity_uri is None: return None
        department_name = self.get_department_name()
        if department_name is None: return None
        return get_workspace_relpath(entity_uri, department_name)

    def _get_cache_path(self):
        base_path = self._cache_base_path()
        if base_path is None: return None
        return self.resolve_location(self.get_location_name(), base_path)

    def _require_cache_path(self):
        """The cache directory, for cache()/load(), which need one."""
        return self.resolve_location(
            self.get_location_name(),
            get_workspace_relpath(
                self.require_entity_uri(), self.require_department_name()
            ),
        )

    def _next_version_name(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return 'v0001'
        latest_version_code = naming.get_version_code(version_names[-1])
        return naming.get_version_name(latest_version_code + 1)

    def list_cache_names(self):
        cache_path = self._get_cache_path()
        if cache_path is None: return []
        if not cache_path.exists(): return []
        return sorted(
            item_path.name
            for item_path in cache_path.iterdir()
            if item_path.is_dir()
        )

    def list_version_names(self):
        cache_path = self._get_cache_path()
        if cache_path is None: return []
        if not cache_path.exists(): return []
        cache_name = self.get_cache_name()
        if cache_name is None: return []
        version_names = list(filter(
            naming.is_valid_version_name,
            [path.name for path in list_version_paths(cache_path / cache_name)]
        ))
        version_names.sort(key=naming.get_version_code)
        return version_names

    def get_cache_name(self):
        result = self.parm('name').eval()
        if len(result) == 0: return None
        return result

    def get_version_name(self):
        """The version to load; None until one is picked (a fresh node, or
        one that has not cached yet). A picked version that is not on disk
        raises."""
        version_name = self.parm('version').eval()
        if len(version_name) == 0: return None
        self._require_version_on_disk(version_name)
        return version_name

    def _require_version_on_disk(self, version_name):
        version_names = self.list_version_names()
        if version_name not in version_names:
            raise self._error(
                f"cache version '{version_name}' does not exist "
                f"(on disk: {', '.join(version_names) or 'none'})"
            )

    def get_location_name(self):
        location_name = self.parm('location').eval()
        if location_name not in (Location.Project, Location.Proxy):
            raise self._error(f"unknown cache location '{location_name}'")
        return location_name

    def _update_labels(self):
        self.parm('entity_label').set(self._entity_label())

    def set_cache_name(self, name):
        self.parm('name').set('' if name is None else name)

    def set_version_name(self, version_name):
        self._require_version_on_disk(version_name)
        self.parm('version').set(version_name)

    def set_location_name(self, location_name):
        if location_name not in (Location.Project, Location.Proxy):
            raise self._error(f"unknown cache location '{location_name}'")
        self.parm('location').set(location_name)

    # Actions -----------------------------------------------------------------

    def _point_inner_cache(self, output_path: Path):
        """Aim the inner cache node at ``output_path`` over the node's range."""
        native = self.native()
        frame_range = self.get_frame_range()
        render_range = frame_range.full_range()
        native.parm('cache/file').set(path_str(output_path))
        native.parm('cache/f1').set(render_range.first_frame)
        native.parm('cache/f2').set(render_range.last_frame)
        native.parm('cache/f3').set(frame_range.step_size)
        if self.get_frame_range_source() == FrameRangeSource.SINGLE_FRAME:
            native.parm('timeshift/frame').set(render_range.first_frame)
            native.parm('switch/input').set(0)
        else:
            native.parm('switch/input').set(1)

    def _clear_inner_cache(self):
        native = self.native()
        native.parm('cache/file').set('')
        native.parm('cache/loadfromdisk').set(0)

    def cache(self):
        cache_name = self.get_cache_name()
        if cache_name is None:
            self._clear_inner_cache()
            return
        version_name = self._next_version_name()
        output_path = self.cache_file_path(
            self._require_cache_path(), cache_name, version_name
        )
        native = self.native()
        self._point_inner_cache(output_path)
        native.parm('cache/execute').pressButton()
        native.parm('cache/loadfromdisk').set(1)
        self.set_version_name(version_name)

    def load(self):
        cache_name = self.get_cache_name()
        version_name = self.get_version_name()
        if cache_name is None or version_name is None:
            self._clear_inner_cache()
            return
        output_path = self.cache_file_path(
            self._require_cache_path(), cache_name, version_name
        )
        native = self.native()
        self._point_inner_cache(output_path)
        native.parm('cache/loadfromdisk').set(1)

        # Node state: green on the latest version, orange behind it
        latest_version_name = self.list_version_names()[-1]
        native.setColor(
            hou.Color(0, .8, .1)
            if version_name == latest_version_name else
            hou.Color(1, 0.6, 0)
        )
        ns.set_node_comment(
            native,
            f'Latest version: {latest_version_name}\n'
            f'Loaded {version_name}'
        )

    def latest(self):
        version_names = self.list_version_names()
        if len(version_names) == 0: return
        self.set_version_name(version_names[-1])


class LopCache(CacheBase):
    """The LOP th::cache: one ``.usd`` stage per version."""
    CATEGORY = 'Lop'
    STORAGE_SEGMENT = 'lops_cache'
    EXTENSION = '.usd'


class SopCache(CacheBase):
    """The SOP th::cache: ``$F4`` frames of compressed ``.bgeo.sc``."""
    CATEGORY = 'Sop'
    STORAGE_SEGMENT = 'cache'
    EXTENSION = '.$F4.bgeo.sc'


CACHE_CLASSES: dict[str, type[CacheBase]] = {
    cls.CATEGORY: cls for cls in (LopCache, SopCache)
}


def wrap(native) -> CacheBase:
    """The cache wrapper for a th::cache node of either category."""
    category = native.type().category().name()
    if category not in CACHE_CLASSES:
        raise TypeError(f'{native.path()} is not a th::cache node ({category})')
    return CACHE_CLASSES[category](native)


def list_cache_locations() -> list[Path]:
    """Every cache directory the th::cache nodes in this scene address.

    These directories hold versioned caches on shared storage, so exported
    layers may keep composition arcs into them (published by reference). The
    list is derived by walking the actual cache nodes, LOP and SOP, rather
    than the workfile's own location, so it stays in agreement with each
    node's ``_get_cache_path()`` even when a node's entity/department parms
    point it at another workfile's cache. Both storage purposes (project +
    proxy) are exempted per base path; a purpose with no cache on disk is a
    harmless no-op exemption.
    """
    seen = set()
    locations = []
    for cls in CACHE_CLASSES.values():
        for raw_node in ns.list_by_node_type('cache', cls.CATEGORY):
            base_path = cls(raw_node)._cache_base_path()
            if base_path is None: continue
            for location_name in (Location.Project, Location.Proxy):
                location = cls.resolve_location(location_name, base_path)
                key = str(location)
                if key in seen: continue
                seen.add(key)
                locations.append(location)
    return locations


def create(scene, name, cls: type[CacheBase]) -> CacheBase:
    return ns.create_node(scene, name, cls, 'cache', cls.CATEGORY)


# HDA callbacks ---------------------------------------------------------------

def on_created(raw_node):
    # 'entity' and 'department' keep their 'from_context' defaults (never
    # bake a concrete value here — see EntityNode).
    ns.set_node_style(raw_node)
    wrap(raw_node)._update_labels()


def select(kwargs):
    """Entity picker button callback."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = kwargs['node']
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
            wrap(raw_node)._update_labels()


def latest(kwargs):
    wrap(kwargs['node']).latest()


def cache(kwargs):
    wrap(kwargs['node']).cache()


def load(kwargs):
    wrap(kwargs['node']).load()
