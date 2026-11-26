"""The quick-action toolbar: its buttons, what a click runs, their menus,
outlines, badges, dots and hovers.

TumbleTrove asks the catalog for these by hook name
(``get_quick_actions``, ``execute_quick_action``, …); the catalog passes
each ask here, under the same name. The farm watcher
(``farm.manager.activity``) and the autosave timer (:mod:`.save_timer`)
start on the bar's first ask and stop with :meth:`QuickActions.cleanup`.
"""

from __future__ import annotations

import time
from pathlib import Path

from tumbletrove.asset_browser.api.types import MenuItem

from tumblepipe.pipe.houdini.ui.report import reporting, run_on_main_thread

from .menus import reporting_menu
from .types import package_icon


class QuickActions:
    """The catalog's quick actions. ``selected_asset`` and
    ``session_project`` give the selected card's id and the open scene's
    project when asked; ``refresh_bar`` asks TumbleTrove to draw the bar
    again, and ``on_farm_finished`` is told the farm batches that ended.
    The autosave timer reads ``_prefs`` and ``_scene`` off this."""

    #: How long Open Timeline's availability is trusted before Cinedex is
    #: looked for again (the install can change while Houdini runs).
    _TIMELINE_CHECK_S = 30.0

    def __init__(
        self, *, scene, ops, playback, index, resolver, prefs,
        selected_asset, session_project, refresh_bar, on_farm_finished,
    ) -> None:
        self._scene = scene
        self._ops = ops
        self._playback = playback
        self._index = index
        self._resolver = resolver
        self._prefs = prefs
        self._selected_asset = selected_asset
        self._session_project = session_project
        self._refresh_bar = refresh_bar
        self._on_farm_finished = on_farm_finished
        #: ``(when looked, ok, why)`` of Open Timeline's last lookup
        self._timeline_state: tuple[float, bool, str] | None = None
        #: the farm watcher, started on the first Farm Manager ask
        self._farm_activity = None

    def get_quick_actions(self):
        from tumbletrove.asset_browser.api.catalog import QuickAction
        ok, why = self._timeline_availability()
        return [
            QuickAction(
                id="open_timeline", label="Open Timeline", icon="film",
                tooltip="Play the selected shot's sequence in Cinedex, or "
                        "the sequence you are viewing; with neither, the "
                        "project's master edit, else every shot",
                # Not primary: a solid jade pill outshouted the row
                # (Magnus, 2026-11-15); an icon like its neighbours, first.
                enabled=ok, disabled_reason=why,
            ),
            QuickAction(id="save", label="Save", icon="save-all", tooltip="Save current scene"),
            QuickAction(id="publish", label="Publish", icon="send", tooltip="Publish exports"),
            QuickAction(
                # The one way to the Farm Submit dialog from the toolbar
                # (a second, Publish-ticked "Farm Submit" button opened the
                # same dialog). Publishing on the farm is also Publish's.
                id="render", label="Render", icon="clapperboard",
                tooltip="Farm Submit, with the current scene's Render ticked: "
                        "publish, playblast and render any shots or assets",
            ),
            QuickAction(
                id="entity_editor", label="Entity Editor", icon="table-2",
                tooltip="The project's assets and shots as spreadsheets: add, "
                        "edit and retire them",
            ),
            QuickAction(
                # Not in TumbleTrove's bundled icon set (a missing name draws
                # a blank button), so shipped here and named by its path.
                id="farm_manager", label="Farm Manager", icon=package_icon("server"),
                tooltip="Follow farm and local render jobs: progress, logs and errors",
            ),
            QuickAction(
                id="update", label="Update", icon="download",
                tooltip="Re-import latest published versions into the "
                        "current scene (no scene reload)",
            ),
            QuickAction(id="reload", label="Reload", icon="file-input", tooltip="Reload current scene"),
        ]

    def _timeline_availability(self) -> tuple[bool, str]:
        """Whether Cinedex can open, and why not. Cached: the bar asks on the
        GUI thread, so the lookup (a file check) is not repeated per ask; when
        it changes the bar is asked to draw again."""
        now = time.monotonic()
        cached = self._timeline_state
        if cached is not None and now - cached[0] < self._TIMELINE_CHECK_S:
            return cached[1], cached[2]
        from tumblepipe.pipe.review_playlist import find_player
        try:
            find_player()
            ok, why = True, ""
        except (OSError, ValueError) as error:
            ok, why = False, f"Cinedex cannot open: {error}"
        self._timeline_state = (now, ok, why)
        if cached is not None and (cached[1], cached[2]) != (ok, why):
            self._refresh_bar()
        return ok, why

    def execute_quick_action(self, action_id: str, done_cb=None) -> None:
        if action_id == "open_timeline":
            self._playback.open_timeline(
                self._selected_asset(), self._session_project(), done_cb,
                self._index.last_query_tags,
            )
        elif action_id == "save":
            self._scene.save_current_scene(done_cb)
        elif action_id == "publish":
            self._scene.publish_current_scene(done_cb)
        elif action_id == "render":
            self._scene.render_current_scene(done_cb)
        elif action_id == "entity_editor":
            self._open_scene_entity_editor(done_cb)
        elif action_id == "farm_manager":
            from . import farm_jobs
            farm_jobs.open_manager(self._scene, _watch_farm(self).session, done_cb)
        elif action_id == "update":
            self._scene.update_scene_imports(done_cb)
        elif action_id == "reload":
            self._scene.reload_current_scene(done_cb)

    def _open_scene_entity_editor(self, done_cb=None) -> None:
        """The Entity Editor quick action. Magnus asked for it beside Render:
        the editor was only reachable from the "+" card and card menus.

        Opens the loaded workfile's project on its entity's tab, group and
        row; an untitled or off-pipeline scene opens on Shots and asks for
        the project when several are registered.
        """
        def _open():
            try:
                import hou
                from tumblepipe.pipe.paths import get_workfile_context
                from tumblepipe.pipe.houdini.untitled import scene_is_untitled
                project_name, kind, group, name = "", "shots", "", ""
                hip_path = Path(hou.hipFile.path())
                proj = (
                    None if scene_is_untitled()
                    else self._resolver.project_for_path(hip_path)
                )
                if proj is not None:
                    project_name = proj.name
                    context = get_workfile_context(hip_path)
                    segments = (
                        context.entity_uri.segments if context is not None else []
                    )
                    if len(segments) == 3 and segments[0] in ("assets", "shots"):
                        kind, group, name = segments
                    elif (
                        len(segments) == 2
                        and context.entity_uri.purpose == "groups"
                    ):
                        # A Multi's workfile, groups:/<ctx>/<name>: its row
                        # on the Multis tab.
                        kind, (group, name) = "multis", segments
                self._ops.open_entity_editor(project_name, kind, group, name)
            finally:
                if callable(done_cb):
                    done_cb()

        run_on_main_thread(reporting("Opening the Entity Editor", _open))

    def get_quick_action_menu_items(self, action_id: str):
        """Right-click items for a quick-action toolbar button.

        Only ``save`` carries one: an *inline* emergency save. The normal
        left-click Save defers to the main-thread event loop, which is frozen
        while Houdini sits in its crash-report dialog — so the queued save
        never lands. The menu entry runs the save directly from the context-
        menu handler so it still completes during a crash. See
        :meth:`SceneManager.emergency_save_current_scene`.

        Then the Autosave interval and the unsaved-time warning
        (:mod:`.save_timer`, Remy 2026-11-11), each a submenu of ticked
        choices like the Refresh button's auto-refresh.

        ``farm_manager`` offers Open Farm Manager, then this session's ended
        batches, failures first, each running its main action (Preview in
        Cinedex, or Show in Jobs; a failure that wrote frames has Preview N
        Frames in Cinedex too): the way to a finished job's outputs now
        that a click on the button always opens the Farm Manager
        (``farm_jobs.menu_items``).
        """
        if action_id == "farm_manager":
            from . import farm_jobs
            return reporting_menu(
                farm_jobs.menu_items(self._scene, _watch_farm(self).session)
            )
        if action_id == "save":
            from . import save_timer
            autosave = self._prefs.autosave_minutes
            warning = self._prefs.unsaved_warning_minutes
            return reporting_menu([
                ("Emergency Save (off-thread)", self._emergency_save),
                ("__separator__", None),
                ("Autosave", [
                    _choice_item(
                        save_timer.autosave_label(m),
                        lambda m=m: self._set_save_pref("autosave_minutes", m),
                        m == autosave,
                    )
                    for m in save_timer.AUTOSAVE_CHOICES
                ]),
                ("Warn When Unsaved", [
                    _choice_item(
                        save_timer.warning_label(m),
                        lambda m=m: self._set_save_pref("unsaved_warning_minutes", m),
                        m == warning,
                    )
                    for m in save_timer.WARNING_CHOICES
                ]),
            ])
        return []

    def _set_save_pref(self, name: str, minutes: int) -> None:
        """Save one of the Save menu's minutes prefs, and use it now."""
        from . import save_timer
        from .prefs import update_prefs
        setattr(self._prefs, name, minutes)
        update_prefs(**{name: minutes})
        save_timer.poll(self)

    def get_quick_action_busy(self, action_id: str) -> bool:
        """Whether the quick action's button shows its busy outline.

        The Farm Manager's does while this user has farm or local jobs
        queued or rendering in this project. The browser asks every second
        on the GUI thread, so this only reads what a daemon thread counted
        (``farm.manager.activity``); the thread starts on the first ask, so
        a TumbleTrove that never asks never starts it, and it stops reading
        while nobody asks (a closed or hidden browser).
        """
        if action_id != "farm_manager":
            return False
        return _watch_farm(self).busy

    def get_quick_action_alert(self, action_id: str) -> bool:
        """Whether the quick action's button shows its warning badge.

        The Farm Manager's does while some of this user's recent jobs in
        this project (still under way, or submitted in the last day) are
        failed; retrying or removing them takes it away. Read from the same
        daemon thread as :meth:`get_quick_action_busy`, never waiting on
        it. A TumbleTrove without the hook never asks: the method is then
        simply unused.
        """
        if action_id == "save":
            # Asked every second on the GUI thread: also where the
            # autosave timer starts, since the browser is open by then.
            from . import save_timer
            return save_timer.poll(self)
        if action_id != "farm_manager":
            return False
        return _watch_farm(self).alert

    def get_quick_action_dot(self, action_id: str) -> str:
        """The dot at the Farm Manager button's foot: ``danger`` while a batch
        of this session failed and the Farm Manager has not been opened from
        the button since, ``accent`` while one finished, ``""`` once seen
        (``farm_jobs``). Read from the farm watcher's thread, never waiting
        on it; TumbleTrove 0.60.0 asks, an older one never does."""
        if action_id != "farm_manager":
            return ""
        return _watch_farm(self).session.dot()

    def get_quick_action_popover(self, action_id: str):
        """No quick action has a popover: a click on a button always runs
        its action, so the Farm Manager button always opens the Farm Manager
        (``farm_jobs``; it opened a "Finished since you looked" popover
        through 1.112, which now is part of the button's hover)."""
        from . import farm_jobs
        return farm_jobs.CLICK_POPOVER

    def quick_action_popover_opened(self, action_id: str) -> None:
        """Never asked, as no action has a popover; were one opened for the
        Farm Manager, its ended batches would be seen."""
        if action_id == "farm_manager":
            _watch_farm(self).session.mark_seen()

    def execute_quick_action_popover_button(
        self, action_id: str, row_id: str, button_id: str,
    ) -> None:
        """An ended batch's Preview in Cinedex or Show in Jobs, by its id."""
        if action_id != "farm_manager":
            return
        from . import farm_jobs
        farm_jobs.run_button(self._scene, _watch_farm(self).session, row_id, button_id)

    def cleanup(self) -> None:
        """Browser shutdown: stop counting farm jobs and the autosave
        timer (the next browser's first ask starts it again)."""
        if self._farm_activity is not None:
            self._farm_activity.stop()
        from . import save_timer
        save_timer.stop()

    def _emergency_save(self) -> None:
        # Intentionally NOT wrapped in run_catalog_op / gui_dispatch: deferring
        # would re-queue onto the same dead event loop the normal Save uses,
        # defeating the point. We run inline (the menu handler's nested loop
        # pumps during the crash dialog), accepting the brief GUI block.
        self._scene.emergency_save_current_scene()

    def get_quick_action_hover_html(self, action_id: str) -> str | None:
        """Return rich-text HTML for the hover popup above a quick action.

        - ``save``: last mtime of the currently-loaded hip file.
        - ``publish``: mtime of the latest published version for the
          current scene's (entity, dept, channel) tuple.
        - ``farm_manager``: this user's recent farm work in this project,
          as the farm watcher last counted it (shots, jobs by state, tasks,
          a rough finish time, the failed shots), then the batches of this
          session that ended since the Farm Manager was last opened from
          the button. The button's only summary. Asked on the GUI thread
          on hover, so it only reads what the watcher's thread counted.

        Returns ``None`` for actions we don't track (e.g. ``reload``) so
        the button falls back to its regular QToolTip.
        """
        from tumbletrove.asset_browser.core.hover_html import format_age_html

        if action_id == "open_timeline":
            # The button cannot be greyed from here (the SDK's quick actions
            # have no enabled state), so the hover says why a click will fail.
            # An older TumbleTrove draws the button enabled: say why a
            # click will fail.
            import html
            ok, why = self._timeline_availability()
            return None if ok else html.escape(why)

        if action_id == "save":
            import hou
            from . import save_timer
            from tumblepipe.pipe.houdini.untitled import scene_is_untitled
            if scene_is_untitled():
                text = format_age_html("Last saved", None)
            else:
                text = format_age_html(
                    "Last saved", Path(hou.hipFile.path()).stat().st_mtime,
                )
            unsaved = save_timer.unsaved_minutes()
            if unsaved is not None:
                text += f"<br>Unsaved changes for {unsaved} min"
            if self._prefs.autosave_minutes:
                text += f"<br>Autosave every {self._prefs.autosave_minutes} min"
            return text

        if action_id == "publish":
            scene_ctx = self._scene.get_loaded_scene_context()
            if scene_ctx is None:
                return None
            from tumblepipe.pipe.paths import latest_export_path

            def _published_at(uri):
                path = latest_export_path(
                    uri, "default", scene_ctx.department_name,
                )
                return None if path is None else path.stat().st_mtime

            return last_published_html(
                scene_ctx.entity_uri, _published_at, format_age_html,
            )

        if action_id == "farm_manager":
            import datetime as dt

            from tumbletrove.asset_browser.core import theme

            from . import farm_jobs
            activity = _watch_farm(self)
            # The button's one summary: the recent jobs, then what ended
            # since the Farm Manager was last opened from the button.
            return farm_jobs.hover_html(
                activity.summary, activity.session.unseen(), dt.datetime.now(),
                hours=activity.recent_hours,
                dim=theme.TEXT_SECONDARY, warn=theme.WARNING,
            )

        return None


def _choice_item(label: str, action, checked: bool):
    """One ticked-or-not choice in a quick-action submenu."""
    return MenuItem(label, action, checked=checked)


def last_published_html(entity_uri, published_at, format_age_html) -> str:
    """The Publish button's hover: when the open scene's work was last
    published.

    ``published_at(uri)`` is the mtime of ``uri``'s latest export of the
    scene's department, or None when it has none. A Multi's workfile names
    the Multi (``groups:/shots/<name>``), but its publish writes each member
    shot's export and nothing under the Multi: looking the Multi itself up
    said "never" after every publish. A Multi's time is its newest member's,
    with the members never published named after it.
    """
    from tumblepipe.config.groups import is_group_uri, members as group_members

    if not is_group_uri(entity_uri):
        return format_age_html("Last published", published_at(entity_uri))
    members = group_members(entity_uri)
    times = {str(member): published_at(member) for member in members}
    known = [t for t in times.values() if t is not None]
    text = format_age_html("Last published", max(known) if known else None)
    never = [
        "/".join(member.segments[1:]) for member in members
        if times[str(member)] is None
    ]
    if known and never:
        text += "<br>Never published: " + ", ".join(never)
    return text


def _watch_farm(quick: QuickActions):
    """The farm watcher, started on the first ask and kept reading while
    asked. Never does I/O itself: the GUI thread calls this."""
    if quick._farm_activity is None:
        from tumblepipe.farm.manager.activity import FarmActivity

        quick._farm_activity = FarmActivity(on_finished=quick._on_farm_finished)
        quick._farm_activity.start()
    quick._farm_activity.touch()
    return quick._farm_activity
