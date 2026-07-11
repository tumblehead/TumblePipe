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

from _pipeline_houdini import run_on_main_thread, session_nc_type
import _pipeline_uris as uris

if TYPE_CHECKING:
    from _pipeline_catalog import PipelineCatalog

log = logging.getLogger(__name__)


class SceneManager:
    """Scene-state lifecycle for the Pipeline catalog."""

    def __init__(self, catalog: "PipelineCatalog") -> None:
        self._catalog = catalog

    def apply_scene_timeline(
        self, asset_id: str, *, force_frame_range: bool = False,
    ) -> None:
        """Apply FPS and frame range from config to the active Houdini
        scene after a workfile load. Mirrors the old project_browser
        behavior so opening a shot workfile through the asset browser
        lands with the correct timeline.

        FPS is always applied. The frame range is only re-applied for
        **animatable** (time-dependent) entities — shots resolve their
        own range and production can change a shot's length, so the
        config range stays authoritative on open. **Non-animatable**
        entities (assets, ``animatable: false``) store the playbar range
        in their own hip: forcing the config range on every open would
        shrink the timeline back to the schema default (as small as a
        single frame) and discard whatever range the artist dragged out.
        So on open we leave their saved range alone.

        Pass ``force_frame_range=True`` at *workfile creation* to stamp
        the config range regardless of animatable — that's the one time
        a non-animatable entity should get a sensible starting range
        (otherwise a fresh asset workfile would sit at Houdini's 1-240
        default). After that the artist owns it.

        Only entities whose config can't be resolved at all get FPS-only
        treatment (``_get_frame_range_obj`` returns ``None``).
        """
        from tumblepipe.pipe.houdini import util
        frame_range = self._catalog._get_frame_range_obj(asset_id)
        if frame_range is not None:
            if force_frame_range or self._catalog._is_entity_animatable(asset_id):
                util.set_frame_range(frame_range)
        fps = self._catalog._get_fps(asset_id)
        if fps is not None:
            util.set_fps(fps)

    def refresh_scene_imports(self) -> None:
        """Re-execute every import node in the loaded scene so the latest
        published versions flow in.

        Restores the import side of the old project_browser
        auto-import/update-on-open behavior (``main._refresh_scene``):
        each ``th::import_*`` node re-resolves its ``latest`` reference
        and rewrites its prims/geometry. Only meant to run on the GUI
        thread (it mutates the network), so callers invoke it from inside
        the ``run_on_main_thread`` open tick, right after
        :meth:`apply_scene_timeline`.

        Scoped to import nodes only. The old refresh also re-executed
        ``th::create_model`` (rebuilds model metadata/geometry) and
        ``th::build_comp`` (a COP comp) — both heavy, non-import nodes
        that cooked large swaths of the graph on open without pulling in
        any newer published version. They're intentionally excluded so a
        plain open re-resolves references without cooking the comp.

        Each node type is wrapped in its own try/except: a single bad
        node (stale HDA, missing export, cross-project reference) must
        not abort the whole refresh. Node wrappers already no-op when
        ``is_valid()`` is false, matching the old behavior.
        """
        try:
            import tumblepipe.pipe.houdini.nodes as ns
            from tumblepipe.pipe.houdini.lops import (
                import_shot, import_assets,
                import_asset, import_layer,
            )
            from tumblepipe.pipe.houdini.sops import import_rigs
            from tumblepipe import resolver
        except Exception:
            log.exception("Auto-refresh: failed to import node wrappers")
            return

        # (wrapper class, node type name, network context). Import nodes
        # only — create_model / build_comp are deliberately excluded (see
        # docstring); they cook heavily without re-resolving references.
        node_specs = [
            (import_shot.ImportShot, "import_shot", "Lop"),
            (import_assets.ImportAssets, "import_assets", "Lop"),
            (import_asset.ImportAsset, "import_asset", "Lop"),
            (import_layer.ImportLayer, "import_layer", "Lop"),
            (import_rigs.ImportRigs, "import_rigs", "Sop"),
        ]

        # Each import_shot / import_asset execute() requests a global
        # resolver-cache refresh (it dirties every composed stage in the
        # session). Defer them so the whole batch costs one refresh at the
        # end instead of one per node.
        executed = 0
        with resolver.deferred_refresh():
            for wrapper_cls, type_name, context in node_specs:
                try:
                    nodes = ns.list_by_node_type(type_name, context)
                except Exception:
                    log.exception(
                        "Auto-refresh: listing %s nodes failed", type_name,
                    )
                    continue
                for native in nodes:
                    try:
                        node = wrapper_cls(native)
                        if not node.is_valid():
                            continue
                        node.execute()
                        executed += 1
                    except Exception:
                        log.exception(
                            "Auto-refresh: executing %s node failed", type_name,
                        )

        if executed:
            log.info("Auto-refresh: re-executed %d import node(s)", executed)

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
                from tumblepipe.pipe.houdini import util
                if proj is not None:
                    self._catalog._activate_project(proj)
                # Manual update mode so the reload itself doesn't trigger
                # a live full-graph cook - same guard as the three open
                # paths in WorkfileManager.
                with util.update_mode(hou.updateMode.Manual):
                    hou.hipFile.load(hip)
                    log.info("Reloaded scene: %s", hip)
                    # Reconcile timeline + imports exactly like the open
                    # paths - same unforced apply_scene_timeline call, so
                    # reload and open agree on which entities get the
                    # config range re-applied (animatable only) and which
                    # keep their saved range (assets). Without this a
                    # reload would land on whatever stale fps the saved hip
                    # carried while open reconciled it - a silent divergence.
                    asset_id = self.get_scene_asset_id()
                    if asset_id is not None:
                        self.apply_scene_timeline(asset_id)
                        if self._catalog._prefs.auto_refresh_on_open:
                            self.refresh_scene_imports()
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
        """Save the loaded scene as the next workfile version of its own context.

        The ``hou.hipFile.save`` runs on Houdini's main thread (like
        :meth:`reload_current_scene` and the open paths). The quick action can
        fire off the GUI thread, and saving the scene off-thread can capture a
        mid-cook / inconsistent state and persist a stale ``.hip`` — which is
        how a Save could drop the last few minutes of work while Houdini's own
        backup kept it. The old (QWidget) Project Browser saved on the GUI
        thread implicitly; this restores that.
        """
        run_on_main_thread(lambda: self._save_scene(refresh_cb))

    def emergency_save_current_scene(self, refresh_cb=None) -> None:
        """Save the loaded scene **inline** on the calling thread.

        The normal :meth:`save_current_scene` defers to ``run_on_main_thread``
        (``hou.ui.addEventLoopCallback``) so it never persists a mid-cook
        scene. But that event-loop callback is frozen while Houdini sits in its
        crash-report ("send report to SideFX") dialog — the queued save
        silently never runs, which is the capability the off-thread save used
        to provide by accident. This path runs the save directly so it still
        completes from the quick-action context-menu handler (whose nested
        event loop pumps during the crash dialog).

        Saving off the main thread is officially unsupported and may itself
        fail, but during a crash a maybe-save beats a guaranteed loss. This is
        a deliberate, explicitly-labeled escape hatch — never the default Save
        path — surfaced via :meth:`PipelineCatalog.get_quick_action_menu_items`.
        """
        self._save_scene(refresh_cb)

    def _save_scene(self, refresh_cb=None) -> None:
        """Write the next workfile version of the loaded scene's context.

        Runs synchronously on the calling thread. :meth:`save_current_scene`
        defers this to the main thread via ``run_on_main_thread``;
        :meth:`emergency_save_current_scene` calls it inline.
        """
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

            # Match Houdini's Ctrl+S extension (license-driven), else the
            # file is rewritten to a path the pipeline didn't record.
            next_path = next_hip_file_path(
                prev_ctx.entity_uri, prev_ctx.department_name,
                nc_type=session_nc_type(),
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
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after save failed")

    def publish_current_scene(self, refresh_cb=None) -> None:
        """Open the export window (ProcessDialog) for the loaded scene.

        Runs on Houdini's main thread: the quick action can fire off the GUI
        thread, and opening a Qt dialog (or touching ``hou.hipFile``) off-thread
        is unsupported — the same reason Save/Reload marshal back here. One
        dialog publishes the whole asset (all its export/build tasks, grouped by
        entity, user-toggleable, with the Local/Farm choice). A bare per-node
        ``execute()`` opened one window per export node; ``3e2d12a`` dodged that
        by going headless, which removed the window users publish through — this
        restores the window without the one-per-node bug.
        """
        run_on_main_thread(lambda: self._publish_scene(refresh_cb))

    def _publish_scene(self, refresh_cb=None) -> None:
        try:
            import hou

            scene_ctx = self.get_loaded_scene_context()
            if scene_ctx is None:
                hou.ui.setStatusMessage(
                    "Publish: the loaded scene has no pipeline context.",
                    severity=hou.severityType.Warning,
                )
                return
            # Activate the loaded scene's project so the export node wrappers,
            # config and storage resolve against the correct project before we
            # collect and run its publish tasks.
            scene_proj = self._catalog._project_for_hip_path(Path(hou.hipFile.path()))
            if scene_proj is not None:
                self._catalog._activate_project(scene_proj)

            from tumblepipe.pipe.houdini.ui.dialog_launcher import (
                open_process_dialog_for_publish,
            )
            # Blocks on the modal dialog until the user runs/closes it.
            open_process_dialog_for_publish(scene_ctx, dialog_title="Publish")

            # Drop caches so the next browse re-scans the freshly published
            # versions (refresh_cb below repaints the detail panel).
            self.refresh_asset(None, None)
        except Exception:
            log.exception("Publish failed")
        finally:
            # Always notify the browser so the spinner/detail can settle even if
            # publish bailed early or raised.
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after publish failed")

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

    def prepare_scene_swap(self):
        """Handle the current scene's unsaved changes before a scene swap.

        Opening a workfile swaps the whole Houdini scene, so the loaded
        scene's unsaved changes must be dealt with first. Houdini's
        native save prompt would overwrite the *current* version in
        place — which the pipeline never wants — so we intercept it here
        and make "save" mean "save a NEW version".

        Returns:
            ``None``  — the user cancelled; the caller must abort the swap.
            ``True``  — handled here (saved a new version, discarded, or
                        the scene was already clean); the caller should
                        load with ``suppress_save_prompt=True``.
            ``False`` — the current scene has no pipeline context to
                        version up (untitled / off-pipeline); the caller
                        should let Houdini's native prompt handle it
                        rather than risk writing to an unknown location.
        """
        try:
            import hou
            if not hou.hipFile.hasUnsavedChanges():
                return True
        except Exception:
            return True

        # Untitled / off-pipeline scenes have no version to bump — defer
        # to Houdini's native prompt rather than guessing a destination.
        if self.get_loaded_scene_context() is None:
            return False

        # Opt-in: version up silently, no prompt.
        if self._catalog._prefs.autosave_on_scene_change:
            try:
                self.save_current_scene()
            except Exception:
                log.exception("Autosave on scene change failed")
            return True

        # Otherwise ask — but "Save" always means a new version, never an
        # in-place overwrite of the current workfile.
        try:
            import hou
            choice = hou.ui.displayMessage(
                "The current scene has unsaved changes.\n\n"
                "Save a new version before switching?",
                buttons=("Save new version", "Discard changes", "Cancel"),
                severity=hou.severityType.ImportantMessage,
                default_choice=0,
                close_choice=2,
                title="Save Scene",
            )
        except Exception:
            # Can't prompt — fall back to Houdini's native prompt rather
            # than silently discarding the user's work.
            return False

        if choice == 0:
            try:
                self.save_current_scene()
            except Exception:
                log.exception("Save on scene change failed")
            return True
        if choice == 1:
            return True  # discard: suppress the native prompt too
        return None  # cancel

