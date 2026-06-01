"""Scene-state interactions for the Pipeline catalog.

Save / Publish / Reload / Autosave-on-swap, plus the readonly
helpers that derive context from the currently-loaded ``.hip``
file (entity ref, dept, version, project lookup). All of these
talk to ``hou.hipFile`` and the tumblepipe context layer.

Same backref pattern as :class:`WorkfileManager` and
:class:`DetailSectionBuilder`: every method calls multiple catalog
services (project activator, asset resolver, cache invalidation,
detail / card refresh hooks, workfile-side timeline application).
The win is file separation — these methods used to be scattered
across the catalog at various depths; now they're together.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from _pipeline_houdini import run_on_main_thread
import _pipeline_uris as uris

if TYPE_CHECKING:
    from _pipeline_catalog import PipelineCatalog

log = logging.getLogger(__name__)


class SceneManager:
    """Scene-state lifecycle for the Pipeline catalog."""

    def __init__(self, catalog: "PipelineCatalog") -> None:
        self._catalog = catalog

    def apply_scene_timeline(self, asset_id: str) -> None:
        """Apply FPS and frame range from config to the active Houdini
        scene after a workfile load. Mirrors the old project_browser
        behavior so opening a shot workfile through the asset browser
        lands with the correct timeline.

        Assets (no frame_start/end in config) silently get FPS only —
        ``_get_frame_range_obj`` returns ``None`` for them.
        """
        from tumblepipe.pipe.houdini import util
        frame_range = self._catalog._get_frame_range_obj(asset_id)
        if frame_range is not None:
            util.set_frame_range(frame_range)
        fps = self._catalog._get_fps(asset_id)
        if fps is not None:
            util.set_fps(fps)

    def get_loaded_scene_context(self):
        """Return the current scene's ``Context`` if it has pipeline metadata.

        Falls back to parsing the context from the hip file path for
        migrated projects that don't have ``context.json``.
        """
        try:
            import hou
            from tumblepipe.pipe.paths import get_workfile_context
            hip_str = hou.hipFile.path()
            if not hip_str:
                return None
            hip_path = Path(hip_str)
            ctx = get_workfile_context(hip_path)
            if ctx is None:
                ctx = self.context_from_hip_path(hip_path)
            return ctx
        except Exception:
            return None

    def reload_current_scene(self, refresh_cb=None) -> None:
        """Reload the currently-loaded .hip from disk.

        Like :meth:`_open_version_now`, this defers the load to the
        next event-loop tick so the QAction click handler can return
        before Houdini tears down scene Qt state. The loaded scene's
        project is re-activated before the load so the env is
        consistent.
        """
        def _settle():
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after reload failed")

        try:
            import hou
            hip = hou.hipFile.path()
        except Exception:
            log.exception("Reload Scene: failed to read current hip path")
            _settle()
            return
        if not hip:
            _settle()
            return

        target_proj = self._catalog._project_for_hip_path(Path(hip))

        def _do_reload(proj=target_proj):
            try:
                import hou
                if proj is not None:
                    self._catalog._activate_project(proj)
                hou.hipFile.load(hip)
                log.info("Reloaded scene: %s", hip)
                self._catalog._request_global_detail_refresh()
            except Exception:
                log.exception("Reload Scene failed")
            finally:
                _settle()

        run_on_main_thread(_do_reload)

    def get_scene_asset_id(self) -> str | None:
        """Return the asset_id (``PROJECT/CATEGORY/Name``) for the
        currently loaded .hip file, or ``None`` if it can't be determined.
        """
        try:
            import hou
            from tumblepipe.pipe.paths import get_workfile_context
            hip_path = Path(hou.hipFile.path())
            scene_ctx = get_workfile_context(hip_path)
            if scene_ctx is None:
                return None
            proj = self._catalog._project_for_hip_path(hip_path)
            if proj is None:
                return None
            # Build the asset_id from entity URI segments:
            # entity:/assets/CATEGORY/NAME → PROJECT/CATEGORY/NAME
            uri = scene_ctx.entity_uri
            if uri is not None and len(uri.segments) >= 3:
                return f"{proj.name}/{uri.segments[1]}/{uri.segments[2]}"
        except Exception:
            pass
        return None

    def scene_matches_asset(self, asset_id: str) -> bool:
        """True iff the currently loaded .hip belongs to ``asset_id``."""
        return self.get_scene_dept_version(asset_id) is not None

    def get_scene_dept_version(
        self, asset_id: str,
    ) -> tuple[str, str] | None:
        """Return ``(dept, version)`` of the loaded scene if it belongs
        to ``asset_id``'s project + entity, else ``None``."""
        if not asset_id:
            return None
        target_uri = self._catalog._resolver.uri_for(asset_id)
        if target_uri is None:
            return None
        target_proj = self._catalog._resolver.project_for(asset_id)
        try:
            import hou
            from tumblepipe.pipe.paths import get_workfile_context
            hip_path = Path(hou.hipFile.path())
            scene_ctx = get_workfile_context(hip_path)
            if scene_ctx is None:
                return None
            if str(scene_ctx.entity_uri) != str(target_uri):
                return None
            # Also confirm the loaded scene actually lives under this
            # project's project_path — otherwise two projects with the
            # same entity URI shape would falsely match.
            scene_proj = self._catalog._project_for_hip_path(hip_path)
            if scene_proj is None or (
                target_proj is not None
                and scene_proj.name != target_proj.name
            ):
                return None
            return (scene_ctx.department_name, scene_ctx.version_name or "")
        except Exception:
            return None

    def context_from_hip_path(self, hip_path: Path):
        """Derive a pipeline Context from the hip file's path when
        ``get_workfile_context`` returns ``None`` (e.g. migrated
        projects without ``context.json``).

        Path convention:
        ``{PROJECT}/shots/{seq}/{shot}/{dept}/{prefix}_{version}.hip``
        ``{PROJECT}/assets/{cat}/{name}/{dept}/{prefix}_{version}.hip``
        """
        try:
            from tumblepipe.pipe.paths import Context

            parts = hip_path.parts
            # Find 'assets' or 'shots' in the path to anchor parsing.
            for i, seg in enumerate(parts):
                if seg in ("assets", "shots") and i + 3 < len(parts):
                    kind = seg                # "assets" or "shots"
                    cat_or_seq = parts[i + 1]  # category or sequence
                    name = parts[i + 2]        # asset/shot name
                    dept = parts[i + 3]        # department
                    break
            else:
                return None

            uri = uris.entity(kind, cat_or_seq, name)
            # Extract version from filename
            stem = hip_path.stem
            tail = stem.rsplit("_", 1)
            version = tail[1] if len(tail) == 2 else None

            return Context(
                entity_uri=uri,
                department_name=dept,
                version_name=version,
            )
        except Exception:
            log.debug(
                "Failed to parse context from path %s", hip_path,
            )
            return None

    def save_current_scene(self, refresh_cb=None) -> None:
        """Save the loaded scene as the next workfile version of its own context."""
        try:
            import hou
            from tumblepipe.pipe.paths import (
                next_hip_file_path, get_workfile_context, Context,
            )
            from tumblepipe.pipe.context import save_context, save_entity_context

            hip_path = Path(hou.hipFile.path())
            prev_ctx = get_workfile_context(hip_path)
            # Fallback for migrated projects without context.json
            if prev_ctx is None:
                prev_ctx = self.context_from_hip_path(hip_path)
            if prev_ctx is None:
                hou.ui.setStatusMessage(
                    "Save: current scene has no pipeline context.",
                    severity=hou.severityType.Warning,
                )
                return

            # Make sure the loaded scene's project is active before
            # we resolve the next path / save / write context json.
            scene_proj = self._catalog._project_for_hip_path(hip_path)
            if scene_proj is not None:
                self._catalog._activate_project(scene_proj)

            next_path = next_hip_file_path(
                prev_ctx.entity_uri, prev_ctx.department_name, nc_type=None,
            )
            hou.hipFile.save(str(next_path))

            next_ctx = get_workfile_context(next_path) or Context(
                entity_uri=prev_ctx.entity_uri,
                department_name=prev_ctx.department_name,
                version_name=Path(next_path).stem.rsplit("_", 1)[-1],
            )
            save_context(
                Path(next_path).parent, prev_ctx, next_ctx,
                file_extension=Path(next_path).suffix.lstrip("."),
            )
            save_entity_context(Path(next_path).parent, next_ctx)

            log.info("Saved next version: %s", next_path)
            hou.ui.setStatusMessage(
                f"Saved {Path(next_path).name}",
                severity=hou.severityType.Message,
            )
            # Drop scan caches so the next browse query re-scans.
            self._catalog.invalidate_cache()
        except Exception:
            log.exception("Save failed")
        finally:
            # Always notify the browser so the detail panel and quick-
            # action label re-sync — even on partial-success saves where
            # hipFile.save() succeeded but context.json writing raised.
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after save failed")

    def publish_current_scene(self, refresh_cb=None) -> None:
        """Execute matching ExportLayer / ExportRig nodes for the loaded scene."""
        try:
            self.publish_current_scene_impl()
        finally:
            # Always notify the browser so the spinner/detail can settle
            # even if publish bailed early or raised.
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after publish failed")

    def publish_current_scene_impl(self) -> None:
        import hou

        scene_ctx = self.get_loaded_scene_context()
        if scene_ctx is None:
            hou.ui.setStatusMessage(
                "Publish: the loaded scene has no pipeline context.",
                severity=hou.severityType.Warning,
            )
            return
        # Activate the loaded scene's project so the export node
        # wrappers + storage resolve against the correct config.
        scene_proj = self._catalog._project_for_hip_path(Path(hou.hipFile.path()))
        if scene_proj is not None:
            self._catalog._activate_project(scene_proj)
        entity_uri = scene_ctx.entity_uri
        published = 0
        scanned = 0
        mismatched: list[tuple[str, str]] = []  # (path, entity_uri)
        target = str(entity_uri)

        try:
            from tumblepipe.pipe.houdini.lops.export_layer import ExportLayer
            import tumblepipe.pipe.houdini.nodes as ns
            for node in ns.list_by_node_type("export_layer", "Lop"):
                scanned += 1
                try:
                    wrapper = ExportLayer(node)
                    ent = str(wrapper.get_entity_uri())
                    if ent == target:
                        wrapper.execute()
                        published += 1
                        log.info("Published ExportLayer %s", node.path())
                    else:
                        mismatched.append((node.path(), ent))
                except Exception:
                    log.exception(
                        "ExportLayer wrap/execute failed: %s", node.path(),
                    )
        except Exception:
            log.exception("Failed scanning export_layer LOPs")

        try:
            from tumblepipe.pipe.houdini.sops.export_rig import ExportRig
            import tumblepipe.pipe.houdini.nodes as ns
            for node in ns.list_by_node_type("export_rig", "Sop"):
                scanned += 1
                try:
                    wrapper = ExportRig(node)
                    ent = str(wrapper.get_asset_uri())
                    if ent == target:
                        wrapper.execute()
                        published += 1
                        log.info("Published ExportRig %s", node.path())
                    else:
                        mismatched.append((node.path(), ent))
                except Exception:
                    log.exception(
                        "ExportRig wrap/execute failed: %s", node.path(),
                    )
        except Exception:
            log.exception("Failed scanning export_rig SOPs")

        if published == 0:
            if scanned == 0:
                msg = "Publish: no export_layer / export_rig nodes in scene."
            else:
                msg = (
                    f"Publish: {scanned} export node(s) in scene "
                    f"but none match {entity_uri}."
                )
                log.warning(
                    "Publish mismatch for %s. Scanned nodes: %s",
                    target, mismatched,
                )
            hou.ui.setStatusMessage(msg, severity=hou.severityType.Warning)
            return

        log.info("Published %d export node(s) for %s", published, entity_uri)
        hou.ui.setStatusMessage(
            f"Published {published} export node(s).",
            severity=hou.severityType.Message,
        )
        # refresh_cb is dispatched by the outer wrapper's finally;
        # only drop caches here so the next browse re-scans.
        self.refresh_asset(None, None)

    def refresh_asset(self, asset_id, refresh_cb) -> None:
        """Drop catalog caches for this asset and trigger a re-fetch."""
        if asset_id is not None:
            self._catalog._dept_versions.clear(asset_id)
        # Drop the discovery cache so the next browse query re-scans.
        self._catalog.invalidate_cache()
        if callable(refresh_cb):
            try:
                refresh_cb()
            except Exception:
                log.exception("Detail refresh callback failed")

    def autosave_before_scene_swap(self) -> None:
        """Version-up the current scene if the user opted in and there are
        unsaved changes. Called from the scene-load paths to bypass
        Houdini's "save changes?" prompt without losing the user's WIP.

        No-op when the pref is off, when the hip is clean, or when the
        current scene has no pipeline context (untitled, off-pipeline,
        etc. — falling back to Houdini's native prompt is safer than
        silently writing to an unknown location).
        """
        if not self._catalog._prefs.autosave_on_scene_change:
            return
        try:
            import hou
            if not hou.hipFile.hasUnsavedChanges():
                return
        except Exception:
            return
        # _save_current_scene already guards on context + activates the
        # correct project, and is safe to call on the GUI thread.
        try:
            self.save_current_scene()
        except Exception:
            log.exception("Autosave on scene change failed")

