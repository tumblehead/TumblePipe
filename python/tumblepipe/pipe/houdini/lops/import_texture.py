"""``th::import_texture`` (LOP): a published texture set's material, pinned.

References the MaterialX material a texture set version carries
(``tumblepipe.pipe.textures``) onto the stage at Material Path, by default
``<input prim>/mtl/<set>``. The set is an asset's (the Entity parm,
``from_context`` by default) or the project library's (Owner).

The version is pinned: a new node, a new set or entity, and Update take the
newest finished version and write it into Version; nothing changes when a
newer one is published. Status says when one is, so a lighter's material
only moves when someone presses Update.

The inner Reference LOP reads the hidden ``layer`` parm, which holds the
version's layer spelled ``$TH_EXPORT_PATH/...`` so the scene follows the
project to the farm.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tumblepipe.pipe import textures as T
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode

logger = logging.getLogger(__name__)

TYPE_NAME = 'import_texture'

OWNER_ASSET = 'asset'
OWNER_LIBRARY = 'library'


class ImportTexture(EntityNode):

    ENTITY_CONTEXTS = ('assets',)

    # Owner

    def get_owner(self) -> str:
        return self.parm('owner').evalAsString()

    def owner_uri(self):
        """The asset whose sets are listed, None for the library (or while
        ``from_context`` resolves to nothing)."""
        if self.get_owner() == OWNER_LIBRARY:
            return None
        return self.get_entity_uri()

    def sets_dir(self) -> Path | None:
        if self.get_owner() == OWNER_LIBRARY:
            return T.sets_dir(None)
        entity_uri = self.get_entity_uri()
        return None if entity_uri is None else T.sets_dir(entity_uri)

    # Set and version

    def list_set_names(self) -> list[str]:
        folder = self.sets_dir()
        return [] if folder is None else T.list_sets(folder)

    def get_set_name(self) -> str:
        return self.parm('set').eval().strip()

    def set_dir(self) -> Path | None:
        folder = self.sets_dir()
        name = self.get_set_name()
        if folder is None or not name:
            return None
        return folder / name

    def list_version_names(self) -> list[str]:
        """Finished versions, newest first."""
        folder = self.set_dir()
        return [] if folder is None else list(reversed(
            T.finished_versions(folder)))

    def latest_version_name(self) -> str | None:
        folder = self.set_dir()
        return None if folder is None else T.latest_version(folder)

    def status(self) -> str:
        """One line for the Status parm."""
        if self.get_owner() != OWNER_LIBRARY and self.get_entity_uri() is None:
            return 'No asset: set Entity, or switch Owner to Project library'
        name = self.get_set_name()
        if not name:
            sets = self.list_set_names()
            return (f'Pick a set ({len(sets)} published)' if sets
                    else 'No texture sets published here yet')
        folder = self.set_dir()
        finished = [] if folder is None else T.finished_versions(folder)
        return T.pin_note(name, self.parm('version').eval(), finished)

    def update(self):
        """Pin the newest finished version (none: clear Version)."""
        latest = self.latest_version_name()
        self.parm('version').set(latest or '')
        self.sync_layer()

    def sync_layer(self):
        """Point the inner Reference at the pinned version's layer."""
        self.parm('layer').set(self.layer_path())

    def layer_path(self) -> str:
        folder = self.set_dir()
        version = self.parm('version').eval()
        if folder is None or not version:
            return ''
        data = T.read_version(folder / version)
        if data is None:
            return ''
        return T.portable_path(folder / version / data['layer'])

    def _update_labels(self):
        self.parm('entity_label').set(self._entity_label())


def create(scene, name):
    return ns.create_node(scene, name, ImportTexture, TYPE_NAME)


def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    if raw_node.type() != ns.find_node_type(TYPE_NAME, 'Lop'):
        return
    node = ImportTexture(raw_node)
    node._update_labels()


# Callbacks (the HDA's parms call these with kwargs)

def _node(kwargs) -> ImportTexture:
    return ImportTexture(kwargs['node'])


def on_source_changed(kwargs):
    """Owner, Entity or Set changed: pin that set's newest version."""
    node = _node(kwargs)
    node._update_labels()
    node.update()


def on_version_changed(kwargs):
    _node(kwargs).sync_layer()


def update(kwargs):
    _node(kwargs).update()


def select(kwargs):
    node = _node(kwargs)
    if node.pick_entity(title='Select Asset', contexts=('assets',)):
        node.update()


def set_menu(raw_node) -> list[str]:
    from tumblepipe.pipe.houdini import menus

    return menus.list_to_menu(ImportTexture(raw_node).list_set_names())


def version_menu(raw_node) -> list[str]:
    from tumblepipe.pipe.houdini import menus

    return menus.list_to_menu(ImportTexture(raw_node).list_version_names())


def status(raw_node) -> str:
    """The Status parm's expression. Never raises: it is drawn constantly."""
    try:
        return ImportTexture(raw_node).status()
    except Exception as exc:  # noqa: BLE001 - a label must always draw
        return f'Error: {exc}'


def place(parent, set_name: str, owner_uri=None, name: str | None = None,
          version: str | None = None):
    """A new ``th::import_texture`` in *parent* reading *set_name*'s newest
    version, or *version* when given (the asset browser's drop; a shared
    library's set is copied in and pinned to that copy). *owner_uri* None
    is the library."""
    raw = parent.createNode(
        ns.find_node_type(TYPE_NAME, 'Lop').name(),
        name or f'import_{set_name}')
    node = ImportTexture(raw)
    if owner_uri is None:
        raw.parm('owner').set(OWNER_LIBRARY)
    else:
        raw.parm('entity').set(str(owner_uri))
    raw.parm('set').set(set_name)
    node._update_labels()
    if version:
        raw.parm('version').set(version)
        node.sync_layer()
    else:
        node.update()
    return raw

