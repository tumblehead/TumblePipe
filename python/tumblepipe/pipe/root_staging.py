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
  another Root than the shot now points at;
- a shot is built when it got a new root layer or has no staged build at
  all (a new shot: import_shot imports nothing without one). One graph
  scan serves every build.

A shot whose Root changed only in its list needs none of the last two: its
root layer names the Root, and the import and the build read the Root's
newest export.

Only shots that have a Root, or whose root layer names one, are touched;
a shot that never had a Root keeps whatever it has.
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
    next_staged_file_path,
)
from tumblepipe.pipe.scene_build import (
    root_chain,
    export_scene_version,
    generate_root_version,
    root_layer_is_stale,
    shot_root,
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
        if scene_ref is not None or shot_root(shot_uri) is not None:
            found.append((shot_uri, scene_ref))
    return found


def sync_roots(shot_uris=None) -> RootSync:
    """Export what changed in the Roots and bring their shots up to date.

    ``shot_uris`` limits the shots looked at (every shot when None). Each
    Root and shot is its own step: a failure is logged, recorded in the
    result and does not stop the others.
    """
    from tumblepipe.pipe import graph
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
    dependency_graph = graph.scan()
    for shot_uri in to_build:
        try:
            frame_range = get_frame_range(shot_uri)
            if frame_range is None:
                raise ValueError(f'{shot_uri} has no frame range')
            build_staged(
                shot_uri,
                next_staged_file_path(shot_uri),
                frame_range.full_range(),
                dependency_graph=dependency_graph,
            )
        except Exception as error:
            log.exception("Building %s failed", shot_uri)
            result.failures.append((shot_uri, str(error)))
            continue
        result.built_shots.append(shot_uri)
    return result
