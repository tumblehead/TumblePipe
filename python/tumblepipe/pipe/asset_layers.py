"""What is actually inside an asset's staged layer.

The Layer Stack shows ``Asset: SET   v0033`` — one opaque row for a whole
composed asset. This answers the next question: which of SET's department
layers went into that, at which versions, and which sub-assets does it drag
in behind it.

Everything here is asked of the resolver rather than re-derived, because the
answer depends on the *mode* the asking node is in and getting that wrong is
the whole bug this exists to expose (see ``designs/asset-layer-inspector.md``
§2, §4.1):

- ``latest`` — the resolver ignores every recorded pin at every level of the
  cascade (``src/resolver/src/resolve.rs`` ``pick_version``) and takes the
  newest export on disk. Nothing can be "stale": newest *is* what loads.
- ``current`` / a specific version — pins are honoured, so a newer export on
  disk is real staleness: it is sitting there and not composing.

No ``hou`` here — this is a plain data read, and it stays testable outside
Houdini.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from tumblepipe import resolver
from tumblepipe.util.uri import Uri
from tumblepipe.config.department import list_entity_departments
from tumblepipe.pipe.paths import (
    latest_export_path,
    latest_hip_file_path_with_context,
    version_name_from_path,
)
from tumblepipe.pipe.usd import read_asset_department_layers

logger = logging.getLogger(__name__)


def _has_workfile(asset_uri: Uri, department: str) -> bool:
    """Whether there is a workfile to open for this department.

    Group-aware via ``latest_hip_file_path_with_context``, so a department
    covered by a Multi reports the group's workfile rather than nothing.
    """
    try:
        path = latest_hip_file_path_with_context(asset_uri, department)
    except Exception:
        logger.debug(
            "Workfile probe failed for %s/%s", asset_uri, department,
            exc_info=True,
        )
        return False
    return path is not None and path.exists()


class LayerStatus(Enum):
    """Why a department is (or isn't) part of the composed asset."""

    COMPOSED = 'composed'
    #: In the build, and a newer export exists that isn't composing. Only
    #: reachable in pinned mode — in latest mode the newest export is what
    #: loads, so there is nothing to be behind.
    STALE = 'stale'
    #: Has an export on disk, but this staged build predates it. Re-staging
    #: would pick it up. Not the same as stale: it isn't in the build at all.
    NOT_IN_BUILD = 'not_in_build'
    #: Assigned to the asset, never exported. Shown rather than hidden —
    #: "this department exists and has produced nothing" is information.
    NEVER_EXPORTED = 'never_exported'
    #: Not a render layer, so it never composes into a staged asset no matter
    #: how often it exports (``_store_asset_stage`` references only renderable
    #: department exports). A rig is the archetype: real work, real exports,
    #: and correctly absent from the composed asset. Distinct from
    #: NOT_IN_BUILD, which promises a re-stage would pick it up — for a
    #: non-renderable department that promise would be a lie.
    NOT_RENDERABLE = 'not_renderable'


@dataclass(frozen=True)
class DepartmentLayer:
    department: str
    status: LayerStatus
    #: The version composing into the asset, or None when it isn't composing.
    composed: Optional[str]
    #: The newest export on disk, or None when it has never exported.
    latest: Optional[str]
    #: The variant the composed ref names — not always ``default``; a
    #: ``_shared`` layer resolves against a different export tree entirely.
    variant: str
    #: True when there is a workfile to open. Exports and workfiles are
    #: independent: a department with no export can still have plenty of
    #: work in progress, and a non-renderable one (a rig) has a workfile
    #: worth opening precisely because it never shows up in the build.
    has_workfile: bool = False


@dataclass(frozen=True)
class NestedAsset:
    uri: str
    name: str
    composed: Optional[str]


@dataclass(frozen=True)
class AssetLayerReport:
    asset_uri: str
    name: str
    #: The asset's staged version that the *shot* is composing.
    staged_version: Optional[str]
    #: False when the asking node floats to latest. Drives whether staleness
    #: is even a meaningful question.
    pinned: bool
    departments: list[DepartmentLayer]
    nested: list[NestedAsset]


def _resolve_version(ref_uri: str, pinned: bool) -> Optional[str]:
    """The version ``ref_uri`` lands on, resolved as the asking node would."""
    with resolver.latest_mode(not pinned):
        try:
            resolved = resolver.try_resolve_entity_uri(ref_uri)
        except Exception:
            logger.debug("Inspector resolve failed: %s", ref_uri, exc_info=True)
            return None
    if resolved is None:
        return None
    return version_name_from_path(resolved)


def build_asset_layer_report(
    asset_uri: Uri,
    staged_file_path: Path,
    pinned: bool,
    variant: str = 'default',
    staged_version: Optional[str] = None,
) -> AssetLayerReport:
    """Describe the department layers composing ``staged_file_path``.

    ``staged_file_path`` must be the staged file the asking node actually
    resolved for this asset — not "the latest" and not the pin recorded
    upstream. In latest mode those differ, and reading the wrong one reports
    a build nobody is looking at.

    ``pinned`` is the asking node's mode: False when it floats to latest.
    ``variant`` is the asset variant the asking node composed, used to look
    up departments that are *not* in the build (the ones that are name their
    own variant in the ref).

    Departments are listed in **pool order**, honouring the asset's own
    assignment, because pool order is pipeline order and is load-bearing
    (``designs/per-entity-departments.md`` §3). A department that is assigned
    but absent from the build still gets a row: unassigning or failing to
    export is not a reason to hide that a department exists.
    """
    composed_refs, nested_refs = read_asset_department_layers(
        asset_uri, staged_file_path
    )
    by_department = {ref.department: ref for ref in composed_refs}

    renderable: dict[str, bool] = {}
    try:
        assigned = list_entity_departments(asset_uri)
        roster = [d.name for d in assigned]
        renderable = {d.name: d.renderable for d in assigned}
    except Exception:
        # A config read that throws must not blank the dialog — fall back to
        # reporting exactly what composed, which is the question that was
        # asked. Order is then the build's (reverse pipeline order), so say
        # nothing about pipeline order in that case.
        logger.warning(
            "Could not read departments for %s; reporting composed layers "
            "only", asset_uri, exc_info=True
        )
        roster = [r.department for r in composed_refs if r.department]

    # Anything that composed but isn't in the roster still gets reported: it
    # is in the build, so it is real work, whatever the config now says — and
    # it demonstrably composes, so it is renderable whatever the config says
    # too.
    for ref in composed_refs:
        if ref.department and ref.department not in roster:
            roster.append(ref.department)
            renderable.setdefault(ref.department, True)

    departments: list[DepartmentLayer] = []
    for name in roster:
        ref = by_department.get(name)
        # A composed ref names its own variant (a _shared layer resolves
        # against a different export tree). One that isn't composing doesn't,
        # so fall back to the variant the asset itself composed under.
        layer_variant = ref.variant if ref is not None else variant

        latest_path = latest_export_path(asset_uri, layer_variant, name)
        latest = latest_path.name if latest_path is not None else None

        if ref is None:
            if not renderable.get(name, True):
                # Never composes, by design — do not imply a re-stage helps.
                status = LayerStatus.NOT_RENDERABLE
            elif latest is not None:
                status = LayerStatus.NOT_IN_BUILD
            else:
                status = LayerStatus.NEVER_EXPORTED
            departments.append(DepartmentLayer(
                department=name,
                status=status,
                composed=None,
                latest=latest,
                variant=layer_variant,
                has_workfile=_has_workfile(asset_uri, name),
            ))
            continue

        composed = _resolve_version(ref.uri, pinned)
        if composed is None:
            # The ref is in the build but does not resolve — the export it
            # names is gone. Report the recorded pin rather than claiming
            # nothing composed.
            composed = ref.version

        stale = (
            pinned
            and composed is not None
            and latest is not None
            and composed != latest
        )
        departments.append(DepartmentLayer(
            department=name,
            status=LayerStatus.STALE if stale else LayerStatus.COMPOSED,
            composed=composed,
            latest=latest,
            variant=layer_variant,
            has_workfile=_has_workfile(asset_uri, name),
        ))

    nested: list[NestedAsset] = []
    for ref in nested_refs:
        nested.append(NestedAsset(
            uri=ref.base,
            name=ref.base.rstrip('/').split('/')[-1],
            composed=_resolve_version(ref.uri, pinned) or ref.version,
        ))

    return AssetLayerReport(
        asset_uri=str(asset_uri),
        name=str(asset_uri).rstrip('/').split('/')[-1],
        staged_version=staged_version,
        pinned=pinned,
        departments=departments,
        nested=nested,
    )
