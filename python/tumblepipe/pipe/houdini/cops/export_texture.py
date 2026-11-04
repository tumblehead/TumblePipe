"""``th::export_texture`` (COP): publish Copernicus layers as a texture set.

One input per role (base colour, roughness, metalness, normal, height, AO,
opacity; ``tumblepipe.pipe.textures.ROLES`` order). Publish writes every
connected input to a staging folder through the HDA's inner ``rop_image``
nodes, then publishes them as the next version of the set
(``textures.publish_set``): the images, a MaterialX material and
``context.json``, in an asset's ``_textures`` (Owner Asset, the Entity
parm) or the project library.

Only the base colour is colour-converted when written, into the colour
space the publish tags it with (sRGB - Texture, or ACEScg for EXR); the
data maps are written raw, so roughness 0.5 stays 0.5 in the file. The
first input passes through, so the node can sit inline.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import hou

from tumblepipe.api import get_user_name
from tumblepipe.pipe import textures as T
from tumblepipe.pipe.context import announce_change
from tumblepipe.pipe.provenance import scene_producer
from tumblepipe.util import events
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode

logger = logging.getLogger(__name__)

TYPE_NAME = 'export_texture'

OWNER_ASSET = 'asset'
OWNER_LIBRARY = 'library'

#: File formats offered, as written.
FORMATS = ('exr', 'png', 'tif', 'jpg')


def rop_name(role: str) -> str:
    """The inner ``rop_image`` writing *role*'s input."""
    return f'write_{role}'


class ExportTexture(EntityNode):

    ENTITY_CONTEXTS = ('assets',)

    def get_owner(self) -> str:
        return self.parm('owner').evalAsString()

    def owner_uri(self):
        if self.get_owner() == OWNER_LIBRARY:
            return None
        return self.require_entity_uri()

    def get_set_name(self) -> str:
        name = self.parm('set').eval().strip()
        T.check_set_name(name)
        return name

    def get_format(self) -> str:
        fmt = self.parm('format').evalAsString()
        if fmt not in FORMATS:
            raise self._error(f"format {fmt!r} is not one of {FORMATS}")
        return fmt

    def set_dir(self) -> Path:
        return T.set_dir(self.get_set_name(), self.owner_uri())

    def connected_roles(self) -> list[str]:
        native = self.native()
        return [role for index, role in enumerate(T.ROLES)
                if index < len(native.inputs()) and native.inputs()[index] is not None]

    def status(self) -> str:
        try:
            folder = self.set_dir()
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            return str(exc).split(': ', 1)[-1]
        latest = T.latest_version(folder)
        roles = self.connected_roles()
        wired = ', '.join(roles) if roles else 'no inputs connected'
        if latest is None:
            return f'Not published yet ({wired})'
        return f'Latest {latest} ({wired})'

    def write_maps(self, folder: Path) -> dict[str, str]:
        """Write every connected input into *folder*; role -> file.

        The inner writers read the hidden ``staging`` and ``diffuse_space``
        parms (the HDA is locked, so its insides cannot be set): the base
        colour is converted into the space the publish tags it with, every
        other map is written raw."""
        fmt = self.get_format()
        native = self.native()
        native.parm('staging').set(folder.as_posix())
        native.parm('diffuse_space').set(T.colour_space('diffuse', f'x.{fmt}'))
        maps = {}
        try:
            for role in self.connected_roles():
                target = folder / f'{role}.{fmt}'
                native.node(rop_name(role)).render()
                if not target.is_file():
                    raise self._error(f'{role} was not written to {target}')
                maps[role] = str(target)
        finally:
            native.parm('staging').set('')
        return maps

    def publish(self) -> Path:
        """Publish the connected inputs as the next version; the version."""
        roles = self.connected_roles()
        if not roles:
            raise self._error('connect at least one input to publish')
        set_name = self.get_set_name()
        owner = self.owner_uri()
        folder = T.set_dir(set_name, owner)
        with tempfile.TemporaryDirectory(prefix='th-export-texture-') as tmp:
            maps = self.write_maps(Path(tmp))
            version = T.publish_set(
                folder, set_name, maps, owner=owner, source='cop',
                user=get_user_name(), producer=scene_producer(self.path()))
        announce_change(events.EXPORT, owner or T.set_uri(set_name),
                        f'textures/{set_name}')
        logger.info('published %s %s', set_name, version.name)
        return version

    def _update_labels(self):
        self.parm('entity_label').set(self._entity_label())


def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_EXPORT)
    if raw_node.type() != ns.find_node_type(TYPE_NAME, 'Cop'):
        return
    ExportTexture(raw_node)._update_labels()


def publish(kwargs):
    node = ExportTexture(kwargs['node'])
    try:
        version = node.publish()
    except (ns.NodeError, T.TextureError, hou.OperationFailed) as exc:
        if hou.isUIAvailable():
            hou.ui.displayMessage(str(exc), severity=hou.severityType.Error,
                                  title='Export Texture')
            return
        raise
    if hou.isUIAvailable():
        hou.ui.setStatusMessage(
            f'Published {node.get_set_name()} {version.name}',
            severity=hou.severityType.Message)


def select(kwargs):
    node = ExportTexture(kwargs['node'])
    node.pick_entity(title='Select Asset', contexts=('assets',))


def on_entity_changed(kwargs):
    ExportTexture(kwargs['node'])._update_labels()


def status(raw_node) -> str:
    try:
        return ExportTexture(raw_node).status()
    except Exception as exc:  # noqa: BLE001 - a label must always draw
        return f'Error: {exc}'
