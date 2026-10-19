"""Shared base class for the th:: HDA node wrappers.

The LOP/SOP/COP wrapper classes under lops/, sops/ and cops/ bind a pipeline
entity (asset/shot), department, channel and version to a Houdini node
through a common set of parms ('entity', 'department', 'channel', 'version',
...). EntityNode owns how those parms resolve. Anything wrapper-specific —
indexed multiparm access (import_assets/import_rigs), shot-only context
resolution (import_shot, LOP playblast), extra validation (export_rig,
create_model) — stays an override in the subclass.

Resolution contract. The 'entity' and 'department' parms hold either a
concrete value or the literal 'from_context' sentinel, which resolves
against the workfile the node lives in on every evaluation. That sentinel is
the default for every entity-aware th:: HDA: a baked URI pins the node to
whichever entity it was born in, so a copied scene or a renamed entity keeps
addressing the old one.

- 'from_context' that the open scene cannot answer (an unsaved scene, a
  Multi's workfile, an asset workfile for a shot-only node) is *unset*, and
  the getters return None. That is a state the node's labels and menus show
  ("from_context: none"), so they must render through it.
- Every other value is explicit, and is either valid or an error: an empty
  parm, an entity that is not in the project, a department or channel the
  entity does not have all raise EntityNodeError. Nothing is ever inferred —
  an empty entity parm used to resolve to the first entity in the project,
  and an unknown channel to 'default', which published and imported the
  wrong thing without a word.
- Actions (import, export, cache, playblast) need a value, so they call the
  require_* getters, which raise EntityNodeError for the unset case too,
  saying why 'from_context' did not resolve.
"""


import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import (
    Department,
    list_departments,
    list_entity_departments,
)
from tumblepipe.config.channels import list_channels
from tumblepipe.config.entities import get_entity_type
from tumblepipe.pipe.paths import Context
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.scene_context import entity_in_contexts, scene_context


FROM_CONTEXT = 'from_context'


class EntityNodeError(ns.NodeError):
    """A th:: node's entity/department/channel/version parms do not resolve
    to something the node can act on. The message names the node and the
    parm, and says how to fix it."""


class EntityNode(ns.Node):

    # Knobs for the default list_department_names(): which department
    # context to list and which boolean department flag to filter on.
    DEPARTMENT_CONTEXT: str = 'assets'
    DEPARTMENT_FILTER: str = 'publishable'

    # Which entity contexts the 'from_context' sentinel is allowed to
    # resolve to. None accepts both. Nodes that only ever address one side
    # (import_asset/import_rig never import a shot) narrow this so that
    # 'from_context' in the wrong kind of workfile resolves to nothing
    # rather than to an entity they cannot handle.
    ENTITY_CONTEXTS: tuple[str, ...] | None = None

    # The parm holding the entity URI or 'from_context'. 'entity' on every
    # node but the few whose saved scenes spell it 'shot' or 'asset'.
    ENTITY_PARM: str = 'entity'

    def _error(self, message: str) -> EntityNodeError:
        return EntityNodeError(f'{self.path()}: {message}')

    # Workfile context

    def _workfile_context(self) -> Context | None:
        """The context of the open workfile, None for a non-pipeline scene."""
        return scene_context()

    def _from_context_entity_uri(self) -> Uri | None:
        return entity_in_contexts(
            self._workfile_context(), contexts=self.ENTITY_CONTEXTS,
        )

    def _from_context_failure(self) -> str:
        """Why 'from_context' resolves no entity here, for error messages."""
        context = self._workfile_context()
        if context is None:
            return (
                f'the open scene ({hou.hipFile.path()}) is not a pipeline '
                'workfile. Pick the entity with the Entity button.'
            )
        if context.entity_uri.purpose != 'entity':
            return (
                f'this workfile belongs to {context.entity_uri}, a group, '
                'not an entity. Pick one of its members with the Entity '
                'button.'
            )
        return (
            f"this workfile's entity {context.entity_uri} is not one of "
            f"the {' or '.join(self.ENTITY_CONTEXTS or ())} this node "
            'addresses. Pick the entity with the Entity button.'
        )

    # Entity listing

    def list_asset_uris(self) -> list[Uri]:
        return api.config.list_entity_uris(
            filter=Uri.parse('entity:/assets'),
            closure=True
        )

    def list_shot_uris(self) -> list[Uri]:
        return api.config.list_entity_uris(
            filter=Uri.parse('entity:/shots'),
            closure=True
        )

    def list_entity_uris(self) -> list[str]:
        """'from_context', then every entity under ENTITY_CONTEXTS."""
        uris = []
        for context in self.ENTITY_CONTEXTS or ('assets', 'shots'):
            uris += api.config.list_entity_uris(
                filter=Uri.parse(f'entity:/{context}'),
                closure=True
            )
        return [FROM_CONTEXT] + [str(uri) for uri in uris]

    # Entity resolution

    def get_entity_uri(self) -> Uri | None:
        """The addressed entity; None when 'from_context' is unset here.

        Raises EntityNodeError for an explicit value that is not an entity
        this node can address (including an empty parm).
        """
        entity_uri_raw = self.parm(self.ENTITY_PARM).eval()
        if entity_uri_raw == FROM_CONTEXT:
            return self._from_context_entity_uri()
        if entity_uri_raw not in self.list_entity_uris():  # Compare strings
            raise self._error(
                f"entity '{entity_uri_raw}' is not an entity this node can "
                'address. Pick one with the Entity button.'
            )
        return Uri.parse(entity_uri_raw)

    def require_entity_uri(self) -> Uri:
        """The addressed entity, for actions that cannot run without one."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            raise self._error(
                f"entity is 'from_context', but {self._from_context_failure()}"
            )
        return entity_uri

    def get_entity_type(self) -> str | None:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return None
        return get_entity_type(entity_uri)

    def set_entity_uri(self, entity_uri: Uri):
        if str(entity_uri) not in self.list_entity_uris():  # Compare strings
            raise self._error(
                f'{entity_uri} is not an entity this node can address'
            )
        self.parm(self.ENTITY_PARM).set(str(entity_uri))
        self._update_labels()

    def pick_entity(
        self, title: str = 'Select Entity',
        contexts: tuple[str, ...] | None = None,
        ) -> bool:
        """The Entity button: let the artist pick one of the entities this
        node addresses (narrowed to ``contexts`` when given), or
        'from_context'. True when a pick was made."""
        from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

        entity_filter = {
            ('assets',): 'assets', ('shots',): 'shots',
        }.get(contexts or self.ENTITY_CONTEXTS, 'both')
        dialog = EntitySelectorDialog(
            api=api,
            entity_filter=entity_filter,
            include_from_context=True,
            current_selection=self.parm(self.ENTITY_PARM).eval(),
            title=title,
            parent=hou.qt.mainWindow()
        )
        if not dialog.exec_():
            return False
        selected_uri = dialog.get_selected_uri()
        if not selected_uri:
            return False
        self.parm(self.ENTITY_PARM).set(selected_uri)
        self._update_labels()
        return True

    def _entity_label(self) -> str:
        """The 'entity_label' text: what the Entity parm resolves to."""
        entity_raw = self.parm(self.ENTITY_PARM).eval()
        if entity_raw != FROM_CONTEXT:
            return entity_raw
        entity_uri = self._from_context_entity_uri()
        return f'{FROM_CONTEXT}: {entity_uri if entity_uri else "none"}'

    # Departments

    def _department_label(self) -> str:
        """The 'department_label' text: what the Department parm resolves to."""
        department_raw = self.parm('department').eval()
        if department_raw != FROM_CONTEXT:
            return department_raw
        context = self._workfile_context()
        return f'{FROM_CONTEXT}: {context.department_name if context else "none"}'

    def scoped_departments(
        self, context: str, include_generated: bool = True,
        ) -> list[Department]:
        """The department pool for ``context``, narrowed to the node's entity.

        An entity may be scoped to a subset of its context's pool, and a menu
        on a node addressing that entity should offer only what the entity
        actually has. The narrowing only applies when the entity draws from
        the context being listed — a node listing *asset* departments while
        addressing a shot (import_shot) still gets the whole pool.

        Order is the pool's throughout: it is the pipeline order, and the
        downstream computations below index into it.
        """
        entity_uri = self.get_entity_uri()
        if (
            entity_uri is not None
            and entity_uri.segments
            and entity_uri.segments[0] == context
        ):
            return list_entity_departments(
                entity_uri, include_generated=include_generated,
            )
        return list_departments(context, include_generated=include_generated)

    def list_department_names(self):
        """The departments the node offers. Nodes with a 'department' parm
        override this to lead with 'from_context'; this default lists the
        departments the 'departments' exclusion parm offers."""
        return [
            d.name for d in self.scoped_departments(self.DEPARTMENT_CONTEXT)
            if getattr(d, self.DEPARTMENT_FILTER)
        ]

    def get_department_name(self) -> str | None:
        """The 'department' parm resolved; None when 'from_context' is unset.

        'from_context' takes the workfile's own department — also in a
        Multi's workfile, which covers departments even though it resolves
        no single entity. A concrete value must be one the node offers for
        its entity, or EntityNodeError is raised.
        """
        department_name = self.parm('department').eval()
        if department_name == FROM_CONTEXT:
            context = self._workfile_context()
            if context is None:
                return None
            return context.department_name
        department_names = self.list_department_names()
        if department_name not in department_names:
            offered = ', '.join(n for n in department_names if n != FROM_CONTEXT)
            raise self._error(
                f"department '{department_name}' is not one this node offers "
                f'for {self.get_entity_uri() or "its entity"} '
                f'(offered: {offered or "none"})'
            )
        return department_name

    def require_department_name(self) -> str:
        """The resolved department, for actions that cannot run without one."""
        department_name = self.get_department_name()
        if department_name is None:
            raise self._error(
                f"department is 'from_context', but the open scene "
                f'({hou.hipFile.path()}) is not a pipeline workfile. '
                'Pick the department on the node.'
            )
        return department_name

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names:
            raise self._error(
                f"department '{department_name}' is not one of "
                f"{department_names}"
            )
        self.parm('department').set(department_name)
        self._update_labels()

    def get_exclude_department_names(self):
        return list(filter(len, self.parm('departments').eval().split()))

    def set_exclude_department_names(self, exclude_department_names):
        """Exclude whichever of ``exclude_department_names`` this node's
        entity has. Narrowing is the point, not a fallback: import_assets
        hands its node-level exclusion (a union over every row's asset) to
        each row's import_asset, and a department an asset lacks has nothing
        to exclude."""
        department_names = self.list_department_names()
        self.parm('departments').set(' '.join([
            department_name
            for department_name in exclude_department_names
            if department_name in department_names
        ]))

    # Channels

    def list_channel_names(self) -> list[str]:
        """The entity's channels, 'default' first; none while unset."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return []
        return list_channels(entity_uri)

    def get_channel_name(self) -> str:
        """The 'channel' parm, which must name a channel of the entity."""
        channel_name = self.parm('channel').eval()
        entity_uri = self.require_entity_uri()
        channel_names = list_channels(entity_uri)
        if channel_name not in channel_names:
            raise self._error(
                f"channel '{channel_name}' is not a channel of {entity_uri} "
                f"(listed: {', '.join(channel_names)}). Fix the node's "
                'Channel parm, or register the channel on the entity.'
            )
        return channel_name

    def set_channel_name(self, channel_name: str):
        self.parm('channel').set(channel_name)

    # Versions

    def get_version_name(self) -> str:
        """The 'version' parm: 'latest', 'current' or a version name."""
        version_name = self.parm('version').eval()
        if len(version_name) == 0:
            raise self._error(
                "version is empty. Pick a version, or 'latest'."
            )
        return version_name

    # Layerbreak / import mode

    def get_include_layerbreak(self) -> bool:
        return bool(self.parm('include_layerbreak').eval())

    def set_include_layerbreak(self, include_layerbreak: bool):
        self.parm('include_layerbreak').set(int(include_layerbreak))

    def get_import_mode(self) -> str:
        """'reference' (pipeline metadata + layerbreak) or 'inline' (baked
        into the export)."""
        return self.parm('import_mode').evalAsString()

    def set_import_mode(self, import_mode: str):
        self.parm('import_mode').set(import_mode)

    # Label hook

    def _update_labels(self):
        """Refresh label parms after a selection change. Default no-op;
        wrappers with label parms override this."""
