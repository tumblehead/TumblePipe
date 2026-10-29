"""Scene-state interactions for the Pipeline catalog.

Save / Publish / Render / Reload / Autosave-on-swap, plus the readonly
helpers that derive context from the currently-loaded ``.hip``
file (entity ref, dept, version, project lookup). All of these
talk to ``hou.hipFile`` and the tumblepipe context layer.

Its dependencies are handed in (resolver, project activator, browser
refresh, the catalog's cache invalidation, prefs, the per-asset version
overrides), not the catalog.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from tumblepipe.farm.submit import grid as farm_grid
from . import uris as uris
from .containers import GroupContainer
from .houdini import session_nc_type
from tumblepipe.pipe.houdini.ui.report import report_failure, run_on_main_thread

if TYPE_CHECKING:
    from .houdini import ProjectActivator
    from .prefs import PipelinePrefs
    from .refresh import BrowserRefresh
    from .resolver import AssetResolver
    from .types import DeptVersionStore

log = logging.getLogger(__name__)


class SceneManager:
    """Scene-state lifecycle for the Pipeline catalog."""

    def __init__(
        self,
        catalog_id: str,
        *,
        resolver: "AssetResolver",
        activator: "ProjectActivator",
        refresh: "BrowserRefresh",
        invalidate_cache: Callable[[], None],
        prefs: "PipelinePrefs",
        dept_versions: "DeptVersionStore",
    ) -> None:
        self._catalog_id = catalog_id
        self._resolver = resolver
        self._activator = activator
        self._refresh = refresh
        self._invalidate_cache = invalidate_cache
        self._prefs = prefs
        self._dept_versions = dept_versions

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

        An entity with no frame range gets FPS only.
        """
        from tumblepipe.pipe.houdini import playbar
        from .discovery import entity_animatable, entity_fps, entity_frame_range
        uri = self._resolver.uri_for_ready(asset_id)
        frame_range = entity_frame_range(self._catalog_id, asset_id, uri)
        if frame_range is not None and (
            force_frame_range
            or entity_animatable(self._catalog_id, asset_id, uri)
        ):
            playbar.set_frame_range(frame_range)
        fps = entity_fps(self._catalog_id, asset_id, uri)
        if fps is not None:
            playbar.set_fps(fps)

    def refresh_scene_imports(self) -> tuple[int, int]:
        """Re-execute every import node in the loaded scene so the latest
        published versions flow in. Returns ``(executed, failed)``.

        Opening a scene no longer calls this: ``load_hook`` refreshes on
        every ``hou.hipFile`` load, however the scene was opened. What is
        left is the artist's Update Imports, which reports the failure
        count rather than claiming success over a scene where every node
        failed. Must run on the GUI thread (it mutates the network).
        """
        from tumblepipe.pipe.houdini.scene_imports import (
            refresh_scene_imports,
        )
        return refresh_scene_imports()

    def update_scene_imports(self, refresh_cb=None) -> None:
        """Re-execute the loaded scene's import nodes in place — no hip
        reload, no save prompt.

        This is the mid-session path for "an upstream department just
        published": ``load_hook`` refreshes only when a scene loads, so
        without this an artist sitting in an open scene had to reload to
        pull a new publish. Marshals to the main thread (the refresh
        mutates the network) and runs in Manual update mode like a load,
        so the re-execute itself doesn't trigger a live full-graph cook.

        The loaded scene's project is re-activated first so the import
        wrappers resolve against the correct project config, matching
        :meth:`reload_current_scene`.
        """
        def _do_update():
            try:
                import hou
                from tumblepipe.pipe.houdini import playbar
                hip = hou.hipFile.path()
                proj = (
                    self._resolver.project_for_path(Path(hip))
                    if hip else None
                )
                if proj is not None:
                    self._activator.activate(proj)
                with playbar.update_mode(hou.updateMode.Manual):
                    executed, failed = self.refresh_scene_imports()
                if failed:
                    # The sweep deliberately survives a bad node, which used
                    # to mean "every import node failed" and "all good" showed
                    # the artist the same cheerful status line.
                    hou.ui.displayMessage(
                        f"Update Imports: {failed} import node(s) failed to "
                        f"update, {executed} succeeded. The scene may still "
                        "reference older published versions — see the log for "
                        "which nodes failed.",
                        severity=hou.severityType.Warning,
                    )
                else:
                    hou.ui.setStatusMessage(
                        f"Imports updated to latest published versions "
                        f"({executed} node(s)).",
                        severity=hou.severityType.Message,
                    )
            except Exception as exc:
                report_failure("Update Imports", exc)
            finally:
                if callable(refresh_cb):
                    refresh_cb()

        run_on_main_thread(_do_update)

    def get_loaded_scene_context(self):
        """Return the current scene's ``Context`` if it has pipeline metadata
        (its department's ``context.json``), else ``None``."""
        import hou
        from tumblepipe.pipe.paths import get_workfile_context
        hip_str = hou.hipFile.path()
        if not hip_str:
            return None
        return get_workfile_context(Path(hip_str))

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
                refresh_cb()

        import hou
        hip = hou.hipFile.path()
        if not hip:
            _settle()
            return

        target_proj = self._resolver.project_for_path(Path(hip))

        def _do_reload(proj=target_proj):
            try:
                import hou
                from tumblepipe.pipe.houdini import playbar
                # Same unsaved-changes interception as the three open
                # paths in WorkfileManager: without it, hou.hipFile.load
                # pops Houdini's native prompt whose "Save" overwrites
                # the current workfile version IN PLACE — the exact thing
                # prepare_scene_swap exists to prevent. Here "Save" means
                # saving a new version, then reloading the on-disk state
                # of the current one.
                decision = self.prepare_scene_swap()
                if decision is None:
                    return  # user cancelled; finally-block settles
                if proj is not None:
                    self._activator.activate(proj)
                # Manual update mode so the reload itself doesn't trigger
                # a live full-graph cook - same guard as the three open
                # paths in WorkfileManager.
                with playbar.update_mode(hou.updateMode.Manual):
                    hou.hipFile.load(hip, suppress_save_prompt=decision)
                    log.info("Reloaded scene: %s", hip)
                    # Reconcile the timeline exactly like the open paths -
                    # same unforced apply_scene_timeline call, so reload and
                    # open agree on which entities get the config range
                    # re-applied (animatable only) and which keep their saved
                    # range (assets). Without this a reload would land on
                    # whatever stale fps the saved hip carried while open
                    # reconciled it - a silent divergence. Imports were
                    # already refreshed by load_hook during the load.
                    asset_id = self.get_scene_asset_id()
                    if asset_id is not None:
                        self.apply_scene_timeline(asset_id)
                self._refresh.detail()
            except Exception as exc:
                report_failure("Reload Scene", exc)
            finally:
                _settle()

        run_on_main_thread(_do_reload)

    def get_scene_asset_id(self) -> str | None:
        """Return the asset_id (``PROJECT/CATEGORY/Name``) for the
        currently loaded .hip file, or ``None`` if it can't be determined.

        Entities only. A Multi's workfile is addressed by a container id,
        not an asset id, and yields ``None`` here — see
        :meth:`get_scene_id` when you want whatever addresses the loaded
        scene rather than specifically an entity.
        """
        return self._scene_id(entities_only=True)

    def get_scene_id(self) -> str | None:
        """Return the browser id addressing the loaded .hip — an entity
        id (``PROJECT/CATEGORY/Name``) *or* a Multi's container id
        (``group:PROJECT:<ctx>/<name>``) — or ``None``.

        Separate from :meth:`get_scene_asset_id` rather than folded into
        it: that method's callers feed the id to entity-shaped lookups
        (``_get_frame_range_obj`` resolves it through
        ``AssetResolver.uri_for``, which does not know container ids), so
        widening its contract would change reload behaviour for Multis as
        a side effect. Callers that genuinely mean "whatever is open"
        — :meth:`get_scene_dept_version`'s Multi branch — ask for it
        explicitly.
        """
        return self._scene_id(entities_only=False)

    def _scene_id(self, *, entities_only: bool) -> str | None:
        import hou
        from tumblepipe.pipe.paths import get_workfile_context
        hip_path = Path(hou.hipFile.path())
        scene_ctx = get_workfile_context(hip_path)
        if scene_ctx is None:
            return None
        proj = self._resolver.project_for_path(hip_path)
        if proj is None:
            return None
        return self.id_from_scene_uri(
            scene_ctx.entity_uri, proj.name,
            entities_only=entities_only,
        )

    @staticmethod
    def id_from_scene_uri(
        uri, project_name: str, *, entities_only: bool,
    ) -> str | None:
        """Map a loaded scene's entity URI + project to a browser id.

        Split out from the ``hou``/filesystem lookup around it so the
        mapping itself is testable — it is pure string math, and it is
        where the Multi bug lived.

        Two URI shapes reach here and they are not the same length. An
        entity is ``entity:/<kind>/<second>/<third>`` — three segments,
        and the id is ``PROJECT/<second>/<third>``. A Multi is
        ``groups:/<ctx>/<name>`` — **two**, and its id is
        ``group:PROJECT:<ctx>/<name>``, matching what
        ``GroupContainer.collection_id`` builds. A length check alone
        therefore rejects every Multi, which is right for "give me an
        asset id" and wrong for "what is open".
        """
        if uri.purpose == "groups":
            if entities_only:
                return None
            return GroupContainer(project_name, "/".join(uri.segments)).collection_id
        return uris.asset_id_for(project_name, uri)

    def scene_matches_asset(self, asset_id: str) -> bool:
        """True iff the currently loaded .hip belongs to ``asset_id``."""
        return self.get_scene_dept_version(asset_id) is not None

    def get_scene_dept_version(
        self, asset_id: str,
    ) -> tuple[str, str] | None:
        """Return ``(dept, version)`` of the loaded scene if it belongs
        to ``asset_id``'s project + entity, else ``None``.

        The detail panel accents the returned department in the selected
        asset's Departments section — "this row is the scene you are in".
        """
        if not asset_id:
            return None
        if asset_id.startswith("group:"):
            # A Multi's workfile carries a two-segment groups: URI, which
            # the entity-shaped resolver below has no answer for
            # (``uri_for_ready`` splits for PROJECT/CATEGORY/NAME), so a
            # Multi would never accent its open department. Compare on
            # the container id instead — ``get_scene_id`` is the mapping
            # that already knows both shapes, and it checks the project.
            if self.get_scene_id() != asset_id:
                return None
            scene_ctx = self.get_loaded_scene_context()
            if scene_ctx is None:
                return None
            return (
                scene_ctx.department_name, scene_ctx.version_name or "",
            )
        target_uri = self._resolver.uri_for_ready(asset_id)
        if target_uri is None:
            return None
        target_proj = self._resolver.project_for(asset_id)
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
        scene_proj = self._resolver.project_for_path(hip_path)
        if scene_proj is None or (
            target_proj is not None
            and scene_proj.name != target_proj.name
        ):
            return None
        return (scene_ctx.department_name, scene_ctx.version_name or "")

    def save_current_scene(self, refresh_cb=None, prompt_note: bool = True) -> None:
        """Save the loaded scene as the next workfile version of its own context.

        The ``hou.hipFile.save`` runs on Houdini's main thread (like
        :meth:`reload_current_scene` and the open paths). The quick action can
        fire off the GUI thread, and saving the scene off-thread can capture a
        mid-cook / inconsistent state and persist a stale ``.hip`` — which is
        how a Save could drop the last few minutes of work while Houdini's own
        backup kept it. The old (QWidget) Project Browser saved on the GUI
        thread implicitly; this restores that.

        ``prompt_note`` asks the artist for a version note first (subject to
        the ``prompt_note_on_save`` pref). It is on for the explicit Save
        action and off for saves the user did not directly ask for — see
        :meth:`_prompt_version_note` for why the distinction matters.
        """
        run_on_main_thread(lambda: self._save_scene(refresh_cb, prompt_note))

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

        Never prompts for a version note: the whole point of this path is that
        Houdini's event loop is wedged behind the crash dialog, so putting
        another modal in front of the save is exactly the wrong move. The
        version lands with a blank note. For the same reason it opts out of
        the error dialog on failure (``report_failures=False``) — a failed
        emergency save is logged and reported to the status bar, never modal.
        """
        self._save_scene(refresh_cb, prompt_note=False, report_failures=False)

    def _prompt_version_note(self, prev_ctx) -> str | None:
        """Ask for this save's version note. ``None`` means "cancel the save".

        Returns the note (possibly empty — a blank note is a valid answer,
        not a cancel) or ``None`` when the artist dismissed the dialog, in
        which case the caller must not save at all. That distinction is the
        reason this returns ``str | None`` rather than just a string.

        Runs *before* the version is reserved, so a cancel costs nothing: no
        number is burnt and no reservation stub is left behind for
        ``context_repair`` to sweep up. The trade is that the dialog can name
        the version it follows but not the one it will become — two artists
        saving the same department at once would make any number shown here a
        guess, and a wrong version in the prompt is worse than none.
        """
        if not self._prefs.prompt_note_on_save:
            return ""

        import hou
        from PySide6.QtWidgets import QInputDialog

        entity = "/".join(prev_ctx.entity_uri.segments[1:]) or str(
            prev_ctx.entity_uri
        )
        after = prev_ctx.version_name or "v0000"
        note, ok = QInputDialog.getMultiLineText(
            hou.qt.mainWindow(),
            "Save Version",
            f"Note for the next {prev_ctx.department_name} version of "
            f"{entity} (after {after}) — optional:",
            "",
        )
        if not ok:
            return None
        return note.strip()

    @staticmethod
    def _warn(message: str, as_dialog: bool) -> None:
        """Tell the artist an action produced nothing.

        ``as_dialog`` is the caller's "is anyone watching" answer: an action
        the artist just clicked gets a dialog, because a status line that
        scrolls past is indistinguishable from success. The crash-time
        emergency save passes False - it must never put a modal in front of a
        wedged event loop.
        """
        import hou
        if as_dialog:
            hou.ui.displayMessage(message, severity=hou.severityType.Warning)
        else:
            hou.ui.setStatusMessage(message, severity=hou.severityType.Warning)

    def _save_scene(
        self, refresh_cb=None, prompt_note: bool = False,
        report_failures: bool = True,
    ) -> None:
        """Write the next workfile version of the loaded scene's context.

        Runs synchronously on the calling thread. :meth:`save_current_scene`
        defers this to the main thread via ``run_on_main_thread``;
        :meth:`emergency_save_current_scene` calls it inline.

        ``prompt_note`` defaults to False so a caller that forgets it saves
        silently rather than blocking on an unexpected modal — the prompt is
        opt-in, and the one caller that wants it says so.
        """
        try:
            import hou
            from tumblepipe.pipe.paths import get_workfile_context
            from tumblepipe.pipe.houdini.workfile_save import commit_next_workfile

            hip_path = Path(hou.hipFile.path())
            prev_ctx = get_workfile_context(hip_path)
            if prev_ctx is None:
                # A dialog, not a status line: a Save that saved nothing must
                # not look like a Save that worked.
                self._warn(
                    "Save: the current scene has no pipeline context, so "
                    "there is no version to save it as. Open or create the "
                    "scene through the pipeline first.",
                    report_failures,
                )
                return

            # Ask for the note before touching anything: cancelling here
            # must leave the session exactly as it was, which it only does
            # while no version has been reserved and no project activated.
            note = ""
            if prompt_note:
                note = self._prompt_version_note(prev_ctx)
                if note is None:
                    log.info("Save cancelled at the version-note prompt")
                    return

            # Make sure the loaded scene's project is active before
            # we resolve the next path / save / write context json.
            scene_proj = self._resolver.project_for_path(hip_path)
            if scene_proj is not None:
                self._activator.activate(scene_proj)

            # Reserve + save + record the next version as one atomic commit
            # (pointer written last). Match Houdini's Ctrl+S extension
            # (license-driven) via nc_type, else the file is rewritten to a
            # path the pipeline didn't record.
            next_path = commit_next_workfile(
                prev_ctx.entity_uri, prev_ctx.department_name,
                prev_context=prev_ctx, nc_type=session_nc_type(),
                note=note,
            )

            log.info("Saved next version: %s", next_path)
            hou.ui.setStatusMessage(
                f"Saved {Path(next_path).name}",
                severity=hou.severityType.Message,
            )
            # Drop scan caches so the next browse query re-scans.
            self._invalidate_cache()
            # Swap the saved entity's own card/list row in place — the
            # refresh_cb → _on_quick_action_done path only refreshes the
            # currently-displayed detail's card, which may be a
            # different asset than the one just saved.
            if scene_proj is not None:
                self._refresh.card(
                    self.id_from_scene_uri(
                        prev_ctx.entity_uri, scene_proj.name,
                        entities_only=False,
                    ),
                )
        except Exception as exc:
            # The artist believes a Save landed. Staying quiet here is the
            # most expensive silence in this file: the scene-swap paths go on
            # to load the next scene with suppress_save_prompt=True, so a
            # swallowed failure discards the very work the save was meant to
            # keep. report_failures is off only for the crash-time emergency
            # save, whose event loop cannot pump a modal: it goes to the
            # status bar instead.
            if report_failures:
                report_failure("Save", exc)
            else:
                log.exception("Emergency save failed")
                import hou
                hou.ui.setStatusMessage(
                    f"Emergency save failed: {type(exc).__name__}: {exc}",
                    severity=hou.severityType.Error,
                )
        finally:
            if callable(refresh_cb):
                refresh_cb()

    def publish_current_scene(self, refresh_cb=None) -> None:
        """Open the export window (ProcessDialog) for the loaded scene.

        Runs on Houdini's main thread: the quick action can fire off the GUI
        thread, and opening a Qt dialog (or touching ``hou.hipFile``) off-thread
        is unsupported — the same reason Save/Reload marshal back here. One
        dialog publishes the whole asset (all its export/build tasks, grouped by
        entity, user-toggleable, with the Run on choice). A bare per-node
        ``execute()`` opened one window per export node; ``3e2d12a`` dodged that
        by going headless, which removed the window users publish through — this
        restores the window without the one-per-node bug.
        """
        run_on_main_thread(lambda: self._publish_scene(refresh_cb))

    def _publish_scene(self, refresh_cb=None) -> None:
        def _settle() -> None:
            # Always notify the browser so the spinner/detail can settle,
            # whether the publish ran, bailed early or raised.
            if callable(refresh_cb):
                refresh_cb()

        def _after_publish() -> None:
            try:
                # Drop caches so the next browse re-scans the freshly
                # published versions (_settle repaints the detail panel).
                self.refresh_asset(None, None)
            except Exception as exc:
                report_failure("Publish", exc)
            finally:
                _settle()

        try:
            import hou

            scene_ctx = self.get_loaded_scene_context()
            if scene_ctx is None:
                # A dialog, not a status line: this is the whole outcome of
                # pressing Publish, and a status message that scrolls past is
                # why "nothing happens" was the reported symptom.
                hou.ui.displayMessage(
                    "Publish: the loaded scene has no pipeline context, so "
                    "there is nothing to publish. Save the scene through the "
                    "pipeline first.",
                    severity=hou.severityType.Warning,
                )
                _settle()
                return
            # Activate the loaded scene's project so the export node wrappers,
            # config and storage resolve against the correct project before we
            # collect and run its publish tasks.
            scene_proj = self._resolver.project_for_path(Path(hou.hipFile.path()))
            if scene_proj is not None:
                self._activator.activate(scene_proj)

            from tumblepipe.pipe.houdini.ui.dialog_launcher import (
                open_process_dialog_for_publish,
            )
            # Returns at once; _after_publish runs when the window closes.
            dialog = open_process_dialog_for_publish(
                scene_ctx, dialog_title="Publish", on_closed=_after_publish,
            )
        except Exception as exc:
            report_failure("Publish", exc)
            _settle()
            return
        if dialog is None:
            _settle()

    def open_farm(self, refresh_cb=None) -> None:
        """Open the Farm Submit grid with the loaded workfile's publish ticked.

        Every entity is listed either way; the loaded workfile's entities
        (a Multi's members, for a group workfile) start with their
        department's publish cell ticked, and any other cell can be ticked
        by hand. With no pipeline workfile loaded the grid opens with
        nothing ticked. Shots, unless the loaded scene is an asset's — then
        assets, since the grid shows one context at a time.
        """
        run_on_main_thread(lambda: self._open_farm_grid(
            refresh_cb, tick_kinds=(farm_grid.PUBLISH,), action="Opening the Farm Submit dialog",
        ))

    def open_farm_manager(self, refresh_cb=None) -> None:
        """Open the Farm Manager: local and farm jobs, their tasks and logs."""
        def _open():
            try:
                import hou
                from tumblepipe.farm.manager.window import show_manager
                show_manager(parent=hou.qt.mainWindow())
            except Exception as exc:
                report_failure("Opening the Farm Manager", exc)
            finally:
                if callable(refresh_cb):
                    refresh_cb()
        run_on_main_thread(_open)

    def render_current_scene(self, refresh_cb=None) -> None:
        """Open the Farm Submit grid with the loaded workfile's Render ticked.

        The quick-action sibling of :meth:`open_farm`: the same grid of
        every entity, but seeded with the loaded entity's Render cell. The
        workfile's department pins the render cut. With no pipeline workfile
        loaded it opens with nothing ticked rather than refusing.
        """
        run_on_main_thread(lambda: self._open_farm_grid(
            refresh_cb, tick_kinds=(farm_grid.RENDER,), action="Render submit",
        ))

    def _open_farm_grid(self, refresh_cb, *, tick_kinds, action: str) -> None:
        """Open the Farm Submit grid, seeding ``tick_kinds`` on the loaded entity.

        Marshalled onto the main thread by the callers: the quick actions can
        fire off the GUI thread, and opening a Qt dialog (or touching
        ``hou.hipFile``) off-thread is unsupported. The loaded scene's
        project is activated first so department lookups and entity
        properties resolve against the correct install.

        No pipeline workfile is not an error: the grid lists every entity
        whatever is loaded, so it opens on shots with nothing ticked.
        """
        try:
            scene_ctx = self.get_loaded_scene_context()
            if scene_ctx is not None:
                import hou
                scene_proj = self._resolver.project_for_path(
                    Path(hou.hipFile.path()),
                )
                if scene_proj is not None:
                    self._activator.activate(scene_proj)
            uris, names, context, department = farm_seed(scene_ctx, _multi_members)
            open_submit_jobs_dialog(
                uris, names, context, department=department, tick_kinds=tick_kinds,
            )
        except Exception as exc:
            report_failure(action, exc)
        finally:
            if callable(refresh_cb):
                refresh_cb()

    def refresh_asset(self, asset_id, refresh_cb) -> None:
        """Drop catalog caches for this asset and trigger a re-fetch."""
        if asset_id is not None:
            self._dept_versions.clear(asset_id)
        # Drop the discovery cache so the next browse query re-scans.
        self._invalidate_cache()
        if callable(refresh_cb):
            refresh_cb()

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
            ``False`` — this cannot be handled here: the scene has no
                        pipeline context to version up (untitled /
                        off-pipeline). The caller should let Houdini's
                        native prompt handle it rather than risk writing to
                        an unknown location.

        Anything else that fails raises, so the swap aborts instead of
        going ahead over unsaved work.
        """
        import hou

        if not hou.hipFile.hasUnsavedChanges():
            return True

        # Untitled / off-pipeline scenes have no version to bump — defer
        # to Houdini's native prompt rather than guessing a destination.
        if self.get_loaded_scene_context() is None:
            return False

        # Opt-in: version up silently, no prompt.
        if self._prefs.autosave_on_scene_change:
            self.save_current_scene(prompt_note=False)
            return True

        # Otherwise ask — but "Save" always means a new version, never an
        # in-place overwrite of the current workfile.
        choice = hou.ui.displayMessage(
            "The current scene has unsaved changes.\n\n"
            "Save a new version before switching?",
            buttons=("Save new version", "Discard changes", "Cancel"),
            severity=hou.severityType.ImportantMessage,
            default_choice=0,
            close_choice=2,
            title="Save Scene",
        )

        if choice == 0:
            # No note prompt on this path, deliberately. We have already
            # told the caller "handled" by the time the save actually
            # runs (it is queued onto the main thread), so it will load
            # the next scene with suppress_save_prompt=True — a note
            # dialog the artist then cancelled would silently discard
            # the very changes they just asked to keep. Notes are worth
            # having; they are not worth a data-loss path.
            self.save_current_scene(prompt_note=False)
            return True
        if choice == 1:
            return True  # discard: suppress the native prompt too
        return None  # cancel


def farm_seed(scene_ctx, members_of) -> tuple[list, list[str], str, str | None]:
    """What the Farm Submit grid opens on: ``(uris, names, context, department)``.

    The loaded workfile's entity seeds the ticks and its department pins
    the preview cuts; with no pipeline workfile nothing is seeded and the
    grid opens on shots. A Multi workfile seeds its members (the dialog
    expands it), except an empty Multi, which seeds nothing: the dialog
    refuses a Multi with no members, right for **Submit Jobs…** on its card
    but not for a quick action whose workfile only suggests ticks.
    ``members_of(uri)`` lists a Multi's members.
    """
    from tumblepipe.farm.submit.settings import expand_targets, is_group_target

    if scene_ctx is None:
        return [], [], "shots", None
    uri = scene_ctx.entity_uri
    segments = uri.segments
    department = scene_ctx.department_name
    if not segments or segments[0] not in ("shots", "assets"):
        return [], [], "shots", department
    if is_group_target(uri) and not expand_targets([uri], members_of):
        return [], [], segments[0], department
    return [uri], [segments[-1]], segments[0], department


def _multi_members(group_uri) -> list:
    """Member URIs of the Multi at ``group_uri``; empty when it is gone."""
    from tumblepipe.config.groups import get_group
    group = get_group(group_uri)
    return list(group.members) if group is not None else []


#: The open Farm Submit dialog, held so it is not collected while shown.
_submit_dialog = None


def open_submit_jobs_dialog(
    uris: list, names: list[str], context: str,
    department: str | None = None,
    tick_kinds: tuple[str, ...] = (farm_grid.RENDER,),
) -> None:
    """Open the Farm Submit grid (:class:`SubmitJobsDialog`) for ``context``.

    ``uris`` start with ``tick_kinds`` ticked — Render, for the Render
    quick action and **Submit Jobs…**; nothing, for the Farm Submit quick
    action. The owning project must be active already, so department
    lookups resolve against the right install.

    Non-modal: Houdini stays usable while the grid is open, and the
    submission itself runs in another process once it is sent. Imported
    here rather than at module scope so opening the browser panel does
    not pull the dialog's Qt widgets in with it. Raises; the caller is the
    action's boundary.
    """
    global _submit_dialog
    import hou
    from .submit_jobs_dialog import SubmitJobsDialog
    dialog = SubmitJobsDialog(
        uris, names, context, parent=hou.qt.mainWindow(),
        department=department, tick_kinds=tick_kinds,
    )
    _submit_dialog = dialog
    dialog.show()
    dialog.raise_()
