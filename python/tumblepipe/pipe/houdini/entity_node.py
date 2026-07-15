"""Shared base class for the th:: HDA node wrappers.

The LOP/SOP wrapper classes under lops/ and sops/ bind a pipeline entity
(asset/shot), department, variant and version to a Houdini node through a
common set of parms ('entity', 'department', 'variant', 'version', ...).
EntityNode hoists only the methods whose bodies were textually identical
across the adopting wrappers (or identical after parameterizing via the
DEPARTMENT_* / ENTITY_CONTEXTS class attributes). Anything wrapper-specific
— indexed multiparm access (import_assets/import_rigs), shot-only context
resolution (import_shot, LOP playblast), extra validation (export_rig,
create_model) — stays an override in the subclass.

The 'entity' parm holds either a concrete URI or the literal 'from_context'
sentinel, which resolves against the workfile the node lives in on every
evaluation. That sentinel is the default for every entity-aware th:: HDA:
a baked URI pins the node to whichever entity it was born in, so a copied
scene or a renamed entity keeps addressing the old one. Note that an
*empty* entity parm is NOT neutral — get_entity_uri() below resolves it to
the first entity in the project — so new parms must default to the sentinel,
never to ''. scripts/verify_entity_from_context.py audits this.
"""

from pathlib import Path

import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import (
    Department,
    list_departments,
    list_entity_departments,
)
from tumblepipe.config.variants import get_entity_type, list_variants
from tumblepipe.pipe.paths import get_workfile_context
import tumblepipe.pipe.houdini.nodes as ns


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

    def __init__(self, native):
        super().__init__(native)

    # Entity listing

    def list_asset_uris(self) -> list[Uri]:
        return api.config.list_entity_uris(
            filter=Uri.parse_unsafe('entity:/assets'),
            closure=True
        )

    def list_shot_uris(self) -> list[Uri]:
        return api.config.list_entity_uris(
            filter=Uri.parse_unsafe('entity:/shots'),
            closure=True
        )

    def list_entity_uris(self) -> list[str]:
        uris = self.list_asset_uris() + self.list_shot_uris()
        return ['from_context'] + [str(uri) for uri in uris]

    # Entity resolution

    def get_entity_type(self) -> str | None:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return None
        return get_entity_type(entity_uri)

    def get_entity_uri(self) -> Uri | None:
        entity_uri_raw = self.parm('entity').eval()
        if entity_uri_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            # Only accept entity URIs, not group URIs
            if context.entity_uri.purpose != 'entity':
                return None
            if (self.ENTITY_CONTEXTS is not None
                    and context.entity_uri.segments[0] not in self.ENTITY_CONTEXTS):
                return None
            return context.entity_uri
        # From settings
        entity_uris = self.list_entity_uris()
        if len(entity_uris) <= 1:  # Only 'from_context' means no real URIs
            return None
        if len(entity_uri_raw) == 0:
            return Uri.parse_unsafe(entity_uris[1])  # Skip 'from_context'
        if entity_uri_raw not in entity_uris:  # Compare strings
            return None
        return Uri.parse_unsafe(entity_uri_raw)

    def set_entity_uri(self, entity_uri: Uri):
        entity_uris = self.list_entity_uris()
        if str(entity_uri) not in entity_uris:  # Compare strings
            return
        self.parm('entity').set(str(entity_uri))
        self._update_labels()

    # Departments

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
        return [
            d.name for d in self.scoped_departments(self.DEPARTMENT_CONTEXT)
            if getattr(d, self.DEPARTMENT_FILTER)
        ]

    def set_department_name(self, department_name: str):
        department_names = self.list_department_names()
        if department_name not in department_names:
            return
        self.parm('department').set(department_name)
        self._update_labels()

    def get_exclude_department_names(self):
        return list(filter(len, self.parm('departments').eval().split()))

    def set_exclude_department_names(self, exclude_department_names):
        department_names = self.list_department_names()
        self.parm('departments').set(' '.join([
            department_name
            for department_name in exclude_department_names
            if department_name in department_names
        ]))

    # Variants

    def list_variant_names(self) -> list[str]:
        """List available variant names for current entity."""
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['default']
        return list_variants(entity_uri)

    def get_variant_name(self) -> str:
        """Get selected variant name, defaults to 'default'."""
        variant_names = self.list_variant_names()
        variant_name = self.parm('variant').eval()
        if not variant_name or variant_name not in variant_names:
            return 'default'
        return variant_name

    def set_variant_name(self, variant_name: str):
        """Set variant name."""
        self.parm('variant').set(variant_name)

    # Versions

    def get_version_name(self) -> str:
        """Get selected version name. Default is 'latest'."""
        version_name = self.parm('version').eval()
        if len(version_name) == 0:
            return 'latest'  # Default to latest
        return version_name

    # Layerbreak / import mode

    def get_include_layerbreak(self) -> bool:
        return bool(self.parm('include_layerbreak').eval())

    def set_include_layerbreak(self, include_layerbreak: bool):
        self.parm('include_layerbreak').set(int(include_layerbreak))

    def get_import_mode(self) -> str:
        """'reference' (pipeline metadata + layerbreak) or 'inline' (baked
        into the export). Nodes predating the parm read as 'reference'."""
        parm = self.parm('import_mode')
        if parm is None: return 'reference'
        return parm.evalAsString()

    def set_import_mode(self, import_mode: str):
        parm = self.parm('import_mode')
        if parm is not None:
            parm.set(import_mode)

    # Label hook

    def _update_labels(self):
        """Refresh label parms after a selection change. Default no-op;
        wrappers with label parms override this."""
