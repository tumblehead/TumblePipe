"""Bring shots up to date with their Roots' configuration.

A Root's asset list and a shot's Root are configuration; what a shot
composes comes from exported files: the Root's layer
(``export/scenes/<path>/_staged``), the shot's root layer (``_root``, which
names its Root) and the shot's staged build. Editing the configuration
(the Entity Editor's Apply, the browser's Root edits) used to write nothing
else, so a layout workfile opened afterwards imported none of it until
someone ran Stage Root & Rebuild Shots by hand.

:func:`sync_roots` closes that gap, and does nothing a shot does not need:

- a Root is exported when its list differs from its newest export, or when
  a shot uses it (or it is a parent of one a shot uses) and it has never
  been exported;
- a shot gets a new root layer when it has none, or its newest one names
  another Root than the shot now points at, or carries other render
  settings than its configuration now resolves to;
- a shot is built when it got a new root layer or has no staged build at
  all (a new shot: import_shot imports nothing without one). One graph
  scan serves every build.

A shot whose Root changed only in its list needs none of the last two: its
root layer names the Root, and the import and the build read the Root's
newest export.

A shot's root layer also carries its render settings (``render.*``,
``pipe.usd.render_overrides``), inherited shot > sequence > all shots >
project > schema; ``root_layer_is_stale`` compares the override it recorded
with the one its configuration now asks for. So every shot that has a root
layer is looked at, with or without a Root: an edit of the project's or a
sequence's resolution reaches each built shot under it (Remy, 2026-11-12:
the Database Editor's edit of entity:/shots reached no root layer). A shot
with neither a Root nor a root layer (never built) has nothing to update:
its first build writes a root layer from the configuration then.

:func:`sync_after_edit` is the one trigger after a configuration edit,
whichever editor made it (the Entity Editor's Apply, the Database Editor's
Save): it maps the edited nodes onto the shots they reach and syncs those.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from tumblepipe.api import api
from tumblepipe.util.io import load_json_if_exists
from tumblepipe.util.uri import Uri
from tumblepipe.config.channels import read_published_channel
from tumblepipe.config.entities import is_terminal_entity
from tumblepipe.config.scene import get_inherited_scene_ref, get_scene_by_uri, list_scenes
from tumblepipe.config.timeline import get_frame_range
from tumblepipe.pipe.paths import (
    current_staged_file_path,
    get_latest_version_path,
    get_scene_staged_path,
    latest_root_version_path,
    next_staged_file_path,
)
from tumblepipe.pipe.scene_build import (
    root_chain,
    export_scene_version,
    generate_root_version,
    root_layer_is_stale,
)

log = logging.getLogger(__name__)

SHOTS_URI = Uri.parse('entity:/shots')


def root_export_is_stale(scene_uri: Uri) -> bool:
    """True when a Root's asset list differs from its newest export's, or
    it has never been exported and holds anything."""
    scene = get_scene_by_uri(scene_uri)
    if scene is None:
        return False
    current = sorted((e.asset, e.instances, e.channel) for e in scene.assets)
    latest = get_latest_version_path(get_scene_staged_path(scene_uri))
    context = None if latest is None else load_json_if_exists(latest / 'context.json')
    if context is None:
        return bool(current)
    exported = sorted(
        (a['asset'], a.get('instances', 1),
         read_published_channel(a, where=f'{latest}/context.json'))
        for a in context.get('parameters', {}).get('assets', [])
    )
    return current != exported


def _never_exported(scene_uri: Uri) -> bool:
    latest = get_latest_version_path(get_scene_staged_path(scene_uri))
    return latest is None or not (latest / 'context.json').exists()


@dataclass
class RootSync:
    """What :func:`sync_roots` wrote, and what failed."""
    exported_roots: list[Uri] = field(default_factory=list)
    new_root_layers: list[Uri] = field(default_factory=list)
    built_shots: list[Uri] = field(default_factory=list)
    failures: list[tuple[Uri, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.exported_roots or self.new_root_layers or self.built_shots)

    def summary(self) -> str:
        """One line for a status bar: what reached the shots."""
        parts = []
        if self.exported_roots:
            names = ', '.join('/'.join(u.segments) for u in self.exported_roots)
            parts.append(f"staged Root{'s' if len(self.exported_roots) > 1 else ''} {names}")
        shots = {str(u) for u in self.new_root_layers} | {str(u) for u in self.built_shots}
        if shots:
            parts.append(f"updated {len(shots)} shot{'s' if len(shots) > 1 else ''}")
        if self.failures:
            parts.append(f"{len(self.failures)} failed (see Python Shell)")
        return '; '.join(parts)


def _shots_with_roots(shot_uris) -> list[tuple[Uri, Uri | None]]:
    """(shot, the Root it points at) for the shots a sync concerns."""
    if shot_uris is None:
        shot_uris = [
            uri for uri in api.config.list_entity_uris(SHOTS_URI, closure=True)
            if is_terminal_entity(api.config, uri)
        ]
    found = []
    for shot_uri in shot_uris:
        scene_ref, _ = get_inherited_scene_ref(shot_uri)
        # A root layer, Root or not: it carries the shot's render settings,
        # which an edit anywhere up the cascade can make stale.
        if scene_ref is not None or latest_root_version_path(shot_uri) is not None:
            found.append((shot_uri, scene_ref))
    return found


#: Purposes whose edits can change what a shot's root layer should carry:
#: the entity tree (render settings, Root references), the schemas (the
#: weakest level of every inherited property) and the Roots themselves.
_ROOT_INPUTS = frozenset({'entity', 'schemas', 'scenes'})


def affected_shots(uris) -> list[Uri] | None:
    """The shots an edit of ``uris`` can reach: None for every shot, else
    the terminal shots at or under the edited ``entity:/shots/...`` nodes.

    ``uris`` None (the caller does not know what changed), the project
    ``entity:/``, a schema or a Root reach every shot; an edit of an asset
    or of another purpose (departments, groups, ...) reaches none."""
    if uris is None:
        return None
    shots: dict[str, Uri] = {}
    for uri in uris:
        if uri.purpose not in _ROOT_INPUTS:
            continue
        if uri.purpose != 'entity' or not uri.segments:
            return None
        if uri.segments[0] != SHOTS_URI.segments[0]:
            continue
        if is_terminal_entity(api.config, uri):
            shots[str(uri)] = uri
            continue
        for found in api.config.list_entity_uris(uri, closure=True):
            if is_terminal_entity(api.config, found):
                shots[str(found)] = found
    return list(shots.values())


def sync_after_edit(uris=None) -> RootSync:
    """Bring the shots an edit of ``uris`` reaches up to date (see
    :func:`affected_shots`); every shot when ``uris`` is None.

    The one trigger both editors call after they wrote the database, so a
    render setting edited on the project, all shots, a sequence or a shot
    reaches the root layer of every shot it changes, and of no other."""
    shots = affected_shots(None if uris is None else list(uris))
    if shots is not None and not shots:
        return RootSync()
    return sync_roots(shots)


def stage_after_database_save(uri: Uri) -> str:
    """The Database Editor's hook: :func:`sync_after_edit` for the node it
    saved, failures logged; the status line of what reached the shots."""
    result = sync_after_edit([uri])
    for failed, why in result.failures:
        log.warning("Updating %s after a Database Editor save: %s", failed, why)
    return result.summary()


def sync_roots(shot_uris=None) -> RootSync:
    """Export what changed in the Roots and bring their shots up to date.

    ``shot_uris`` limits the shots looked at (every shot when None). Each
    Root and shot is its own step: a failure is logged, recorded in the
    result and does not stop the others.
    """
    from tumblepipe.pipe.staged_build import build_staged

    result = RootSync()
    shots = _shots_with_roots(shot_uris)

    used = set()
    for _shot, scene_ref in shots:
        if scene_ref is not None:
            used.update(str(uri) for uri in root_chain(scene_ref))
    for scene in list_scenes():
        uri = scene.uri
        stale = root_export_is_stale(uri) or (str(uri) in used and _never_exported(uri))
        if not stale:
            continue
        try:
            export_scene_version(uri)
        except Exception as error:
            log.exception("Exporting Root %s failed", uri)
            result.failures.append((uri, str(error)))
            continue
        result.exported_roots.append(uri)

    to_build = []
    for shot_uri, _scene_ref in shots:
        try:
            if root_layer_is_stale(shot_uri):
                generate_root_version(shot_uri)
                result.new_root_layers.append(shot_uri)
                to_build.append(shot_uri)
            elif current_staged_file_path(shot_uri) is None:
                to_build.append(shot_uri)
        except Exception as error:
            log.exception("Updating the root layer of %s failed", shot_uri)
            result.failures.append((shot_uri, str(error)))

    if not to_build:
        return result
    for shot_uri in to_build:
        try:
            frame_range = get_frame_range(shot_uri)
            if frame_range is None:
                raise ValueError(f'{shot_uri} has no frame range')
            build_staged(
                shot_uri,
                next_staged_file_path(shot_uri),
                frame_range.full_range(),
            )
        except Exception as error:
            log.exception("Building %s failed", shot_uri)
            result.failures.append((shot_uri, str(error)))
            continue
        result.built_shots.append(shot_uri)
    return result
