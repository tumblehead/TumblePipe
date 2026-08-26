"""Scene-state interactions for the Pipeline catalog.

Save / Publish / Render / Reload / Autosave-on-swap, plus the readonly
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

from _pipeline_houdini import report_failure, run_on_main_thread, session_nc_type
import _pipeline_uris as uris

if TYPE_CHECKING:
    from _pipeline_catalog import PipelineCatalog

log = logging.getLogger(__name__)


class SceneManager:
    """Scene-state lifecycle for the Pipeline catalog."""

    def __init__(self, catalog: "PipelineCatalog") -> None:
        self._catalog = catalog
        # One full traceback per session for a failing context resolve — see
        # get_loaded_scene_context.
        self._ctx_resolve_logged = False

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

    def refresh_scene_imports(self) -> tuple[int, int]:
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

        Returns ``(executed, failed)``. The failure count is the whole
        reason this reports back: swallowing per-node errors is right (one
        bad node must not abort the sweep) but *counting* them is what lets
        an artist-initiated Update Imports avoid claiming success over a
        scene where every node blew up. The open/reload callers ignore the
        return — a refresh nobody asked for stays quiet.
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
            return (0, 1)

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

        # Each LOP import wrapper's execute() requests a global resolver
        # refresh (a re-resolve + reload sweep over every loaded entity://
        # layer). Defer them so the whole batch costs one sweep at the
        # end instead of one per node.
        executed = 0
        failed = 0
        with resolver.deferred_refresh():
            for wrapper_cls, type_name, context in node_specs:
                try:
                    nodes = ns.list_by_node_type(type_name, context)
                except Exception:
                    log.exception(
                        "Auto-refresh: listing %s nodes failed", type_name,
                    )
                    failed += 1
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
                        failed += 1

        if executed or failed:
            log.info(
                "Auto-refresh: re-executed %d import node(s), %d failed",
                executed, failed,
            )
        return (executed, failed)

    def update_scene_imports(self, refresh_cb=None) -> None:
        """Re-execute the loaded scene's import nodes in place — no hip
        reload, no save prompt.

        This is the mid-session path for "an upstream department just
        published": :meth:`refresh_scene_imports` only ran on the open /
        reload flows, so an artist sitting in an open scene had no way to
        pull a new publish short of reloading (or, in practice, restarting
        Houdini). Marshals to the main thread (the refresh mutates the
        network) and runs in Manual update mode like the open paths, so
        the re-execute itself doesn't trigger a live full-graph cook.

        The loaded scene's project is re-activated first so the import
        wrappers resolve against the correct project config, matching
        :meth:`reload_current_scene`.
        """
        def _do_update():
            try:
                import hou
                from tumblepipe.pipe.houdini import util
                hip = hou.hipFile.path()
                proj = (
                    self._catalog._project_for_hip_path(Path(hip))
                    if hip else None
                )
                if proj is not None:
                    self._catalog._activate_project(proj)
                with util.update_mode(hou.updateMode.Manual):
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
                    try:
                        refresh_cb()
                    except Exception:
                        log.exception(
                            "Detail refresh after import update failed",
                        )

        run_on_main_thread(_do_update)

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
            # None here is indistinguishable from "this scene has no context",
            # so a raising resolve would otherwise vanish without a trace.
            # Full traceback once, then quiet: the detail panel and session
            # panel call this on every repaint, and a broken scene would
            # otherwise write one trace per frame into a shared project log.
            if self._ctx_resolve_logged:
                log.debug("Failed to resolve the loaded scene's context",
                          exc_info=True)
            else:
                self._ctx_resolve_logged = True
                log.exception("Failed to resolve the loaded scene's context")
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
        except Exception as exc:
            report_failure("Reload Scene", exc)
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
                    self._catalog._activate_project(proj)
                # Manual update mode so the reload itself doesn't trigger
                # a live full-graph cook - same guard as the three open
                # paths in WorkfileManager.
                with util.update_mode(hou.updateMode.Manual):
                    hou.hipFile.load(hip, suppress_save_prompt=decision)
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
        — the session panel — ask for it explicitly.
        """
        return self._scene_id(entities_only=False)

    def _scene_id(self, *, entities_only: bool) -> str | None:
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
            return self.id_from_scene_uri(
                scene_ctx.entity_uri, proj.name,
                entities_only=entities_only,
            )
        except Exception:
            # Debug, not exception: this runs per browsed row, so a broken
            # scene would otherwise fill the log with one trace per repaint.
            log.debug("Failed to resolve the loaded scene's asset id",
                      exc_info=True)
            return None

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
        if uri is None or not project_name:
            return None
        segments = list(uri.segments or ())
        if getattr(uri, "purpose", "") == "groups":
            if entities_only or not segments:
                return None
            return f"group:{project_name}:{'/'.join(segments)}"
        if len(segments) >= 3:
            return f"{project_name}/{segments[1]}/{segments[2]}"
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
            log.debug("Failed to match the loaded scene against %s",
                      asset_id, exc_info=True)
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
        if not self._catalog._prefs.prompt_note_on_save:
            return ""

        try:
            # Imported inside the try, not above it: a missing/broken Qt is
            # exactly the "no usable UI" case this degrades for, and an
            # ImportError escaping here would land in _save_scene's outer
            # handler and abandon the save entirely.
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
        except Exception:
            # No usable UI (headless, or Qt unavailable). Saving the artist's
            # work matters more than annotating it, so fall through to a
            # blank note rather than turning a failed dialog into a lost save.
            log.exception("Version note prompt failed — saving without a note")
            return ""

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
            from tumblepipe.pipe.context import commit_next_workfile

            hip_path = Path(hou.hipFile.path())
            prev_ctx = get_workfile_context(hip_path)
            # Fallback for migrated projects without context.json
            if prev_ctx is None:
                prev_ctx = self.context_from_hip_path(hip_path)
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
            scene_proj = self._catalog._project_for_hip_path(hip_path)
            if scene_proj is not None:
                self._catalog._activate_project(scene_proj)

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
            self._catalog.invalidate_cache()
            # Swap the saved entity's own card/list row in place — the
            # refresh_cb → _on_quick_action_done path only refreshes the
            # currently-displayed detail's card, which may be a
            # different asset than the one just saved. Group URIs
            # (groups:/ctx/name, two segments) use a different card id
            # scheme and are skipped here.
            try:
                segs = prev_ctx.entity_uri.segments
                if scene_proj is not None and len(segs) >= 3:
                    self._catalog._request_card_refresh_for_id(
                        f"{scene_proj.name}/{segs[1]}/{segs[2]}",
                    )
            except Exception:
                log.debug("card refresh after save failed", exc_info=True)
        except Exception as exc:
            # The artist believes a Save landed. Staying quiet here is the
            # most expensive silence in this file: the scene-swap paths go on
            # to load the next scene with suppress_save_prompt=True, so a
            # swallowed failure discards the very work the save was meant to
            # keep. report_failures is off only for the crash-time emergency
            # save, whose event loop cannot pump a modal.
            if report_failures:
                report_failure("Save", exc)
            else:
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
                # A dialog, not a status line: this is the whole outcome of
                # pressing Publish, and a status message that scrolls past is
                # why "nothing happens" was the reported symptom.
                hou.ui.displayMessage(
                    "Publish: the loaded scene has no pipeline context, so "
                    "there is nothing to publish. Save the scene through the "
                    "pipeline first.",
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
        except Exception as exc:
            report_failure("Publish", exc)
        finally:
            # Always notify the browser so the spinner/detail can settle even if
            # publish bailed early or raised.
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after publish failed")

    def render_current_scene(self, refresh_cb=None) -> None:
        """Open the Submit Jobs dialog for the loaded scene's entity.

        The quick-action sibling of :meth:`publish_current_scene`: same
        main-thread marshalling (the action can fire off the GUI thread,
        and opening a Qt dialog off-thread is unsupported), same project
        activation, but it lands in the render-first Submit Jobs dialog
        instead of the export ProcessDialog.
        """
        run_on_main_thread(lambda: self._render_scene(refresh_cb))

    def _render_scene(self, refresh_cb=None) -> None:
        try:
            import hou

            scene_ctx = self.get_loaded_scene_context()
            if scene_ctx is None:
                hou.ui.displayMessage(
                    "Render: the loaded scene has no pipeline context, so "
                    "there is nothing to submit. Open or save the scene "
                    "through the pipeline first.",
                    severity=hou.severityType.Warning,
                )
                return
            # Activate the loaded scene's project so department lookups and
            # entity properties resolve against the correct install.
            scene_proj = self._catalog._project_for_hip_path(
                Path(hou.hipFile.path()),
            )
            if scene_proj is not None:
                self._catalog._activate_project(scene_proj)

            uri = scene_ctx.entity_uri
            segments = uri.segments
            context = segments[0] if segments else None
            if context not in ("shots", "assets"):
                hou.ui.displayMessage(
                    f"Render: unsupported entity context for {uri} — only "
                    "shots and assets can be submitted.",
                    severity=hou.severityType.Warning,
                )
                return
            name = segments[-1]
            # Blocks on the modal dialog until the user submits/closes it.
            self._catalog._open_submit_jobs_dialog(
                [uri], [name], context,
                # The workfile the artist is in seeds the render department.
                department=scene_ctx.department_name,
            )
        except Exception as exc:
            report_failure("Render submit", exc)
        finally:
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after render submit failed")

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
            ``False`` — this cannot be handled here: the scene has no
                        pipeline context to version up (untitled /
                        off-pipeline), we could not determine whether it is
                        dirty, or we could not put the prompt on screen. The
                        caller should let Houdini's native prompt handle it
                        rather than risk writing to an unknown location — or,
                        worse, swapping over unsaved work while claiming to
                        have dealt with it.
        """
        try:
            import hou
        except Exception:
            return True  # no host to swap scenes in; nothing to lose

        try:
            if not hou.hipFile.hasUnsavedChanges():
                return True
        except Exception:
            # "Handled" here would suppress Houdini's own save prompt on a
            # scene we could not even ask about — the caller would swap over
            # unsaved work. Defer to the native prompt instead, matching what
            # the un-promptable branch below already does.
            log.exception(
                "Could not determine whether the scene has unsaved changes",
            )
            return False

        # Untitled / off-pipeline scenes have no version to bump — defer
        # to Houdini's native prompt rather than guessing a destination.
        if self.get_loaded_scene_context() is None:
            return False

        # Opt-in: version up silently, no prompt.
        if self._catalog._prefs.autosave_on_scene_change:
            try:
                self.save_current_scene(prompt_note=False)
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
                # No note prompt on this path, deliberately. We have already
                # told the caller "handled" by the time the save actually
                # runs (it is queued onto the main thread), so it will load
                # the next scene with suppress_save_prompt=True — a note
                # dialog the artist then cancelled would silently discard
                # the very changes they just asked to keep. Notes are worth
                # having; they are not worth a data-loss path.
                self.save_current_scene(prompt_note=False)
            except Exception:
                log.exception("Save on scene change failed")
            return True
        if choice == 1:
            return True  # discard: suppress the native prompt too
        return None  # cancel

