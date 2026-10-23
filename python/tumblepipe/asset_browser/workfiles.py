"""Workfile lifecycle for the Pipeline catalog.

Owns the per-asset / per-shot / per-Multi workfile open and create
flows: path resolution, mtime / user attribution, version-aware open
(in-process or new-Houdini-instance), and the three new-from-* save
paths (template / current scene / group template).

Its dependencies are handed in (resolver, project registry, client pool,
project activator, scene manager, browser refresh, the catalog's cache
invalidation), not the catalog.

Every artist action here is its own failure boundary (:func:`_action`):
whatever goes wrong - a share that will not answer, a template that
raises, a project whose config cannot load - reaches the artist through
:func:`report_failure`. Work deferred to Houdini's main thread is a second
boundary of its own, since by then the action has returned.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from tumblepipe.pipe.paths.naming import entity_stem
from tumblepipe.util import desktop

from .houdini import session_nc_type
from tumblepipe.pipe.houdini.ui.report import report_failure, run_on_main_thread
from .types import workfile_for_version
from . import uris as uris

if TYPE_CHECKING:
    from .projects import PipelineProjectRegistry
    from .clients import ClientPool
    from .houdini import ProjectActivator
    from .refresh import BrowserRefresh
    from .resolver import AssetResolver
    from .scene import SceneManager

log = logging.getLogger(__name__)


def _show_stage_in_network_editors() -> None:
    """Point every unpinned network editor at ``/stage``.

    ``hou.hipFile.clear()`` drops network editors back to ``/obj``, but the
    department templates build in ``/stage`` — so a fresh workfile opened
    onto an empty ``/obj``. Call this after the clear and *before* the
    template runs: templates that want a deeper network (rig, blendshape
    cd into their SOP) still get the last word. Pinned editors belong to
    the artist and are left alone.
    """
    import hou
    if not hou.isUIAvailable():
        return
    stage = hou.node("/stage")
    if stage is None:
        return
    for pane_tab in hou.ui.paneTabs():
        if pane_tab.type() != hou.paneTabType.NetworkEditor:
            continue
        if pane_tab.isPin():
            continue
        pane_tab.setPwd(stage)


def _action(label: str):
    """Make an ``(asset_id, dept, ...)`` action method its own boundary.

    The actions are called from menu entries and detail-panel buttons, and
    a Qt slot that raises only reaches the console - which reads as "the
    button does nothing". ``label`` names the action in the report.
    """

    def wrap(method):
        @functools.wraps(method)
        def run(self, asset_id: str, dept: str, *args, **kwargs) -> None:
            try:
                method(self, asset_id, dept, *args, **kwargs)
            except Exception as exc:
                report_failure(f"{label} for {asset_id}/{dept}", exc)

        return run

    return wrap


class WorkfileManager:
    """Open, create, and inspect workfiles for the Pipeline catalog."""

    def __init__(
        self,
        *,
        resolver: "AssetResolver",
        registry: "PipelineProjectRegistry",
        clients: "ClientPool",
        activator: "ProjectActivator",
        scene: "SceneManager",
        refresh: "BrowserRefresh",
        invalidate_cache: Callable[[], None],
    ) -> None:
        self._resolver = resolver
        self._registry = registry
        self._clients = clients
        self._activator = activator
        self._scene = scene
        self._refresh = refresh
        self._invalidate_cache = invalidate_cache

    @_action("Opening the location")
    def open_dept_work_dir(self, asset_id: str, dept: str) -> None:
        """Open the dept's workfile directory in the OS file browser."""
        base = self.dept_dir_for(asset_id, dept)
        if base is None:
            return
        desktop.open_in_file_browser(base if base.exists() else base.parent)

    @_action("Opening the latest export")
    def open_latest_export(self, asset_id: str, dept: str) -> None:
        """Open the dept's latest export folder in the OS file browser."""
        if not asset_id:
            return
        # Activate the asset's project so latest_export_path resolves
        # against its config.
        proj = self._resolver.project_for(asset_id)
        if proj is not None:
            self._activator.activate(proj)
        uri = self._resolver.uri_for_ready(asset_id)
        if uri is None:
            return
        from tumblepipe.pipe.paths import latest_export_path
        path = latest_export_path(uri, "default", dept)
        if path is None:
            import hou
            hou.ui.setStatusMessage(
                f"No export found for {dept}.",
                severity=hou.severityType.Warning,
            )
            return
        desktop.open_in_file_browser(path)

    def dept_dir_for(self, asset_id: str, dept: str) -> Path | None:
        """The folder ``asset_id``'s ``dept`` workfiles are saved in, or
        ``None`` for a malformed id.

        ``pipe.paths.department_workspace``, so it is the folder Open and
        Save use: a department a Multi covers is the Multi's folder. The
        asset's project is activated first.
        """
        if not asset_id or not dept:
            return None
        if asset_id.startswith("group:"):
            proj = self._group_project(asset_id)
            uri = uris.group(self._split_group_id(asset_id)[1])
        else:
            proj = self._resolver.project_for(asset_id)
            uri = None
        if proj is None:
            return None
        self._activator.activate(proj)
        if uri is None:
            uri = self._resolver.uri_for_ready(asset_id)
        from tumblepipe.pipe.paths import department_workspace
        _workfile_uri, workspace = department_workspace(uri, dept)
        return workspace

    @staticmethod
    def _split_group_id(asset_id: str) -> tuple[str, str]:
        """``group:PROJECT:ctx/name`` → ``(PROJECT, ctx/name)``.

        Group ids are minted by the catalog itself, so one that does not
        split is a bug and raises.
        """
        _, proj_name, path = asset_id.split(":", 2)
        return proj_name, path

    def _group_project(self, asset_id: str):
        """The registry project of a ``group:PROJECT:ctx/name`` id, or ``None``."""
        proj_name, _path = self._split_group_id(asset_id)
        return self._registry.get(proj_name)

    def workfile_path_for(
        self, asset_id: str, dept: str, version: str,
    ) -> Path | None:
        """Return the .hip workfile :class:`Path` for an asset/dept/version."""
        if not version:
            return None
        dept_dir = self.dept_dir_for(asset_id, dept)
        if dept_dir is None:
            return None
        return workfile_for_version(dept_dir, version)

    @staticmethod
    def _read_version_sidecar(dept_dir: Path | None, version: str) -> dict:
        """Return ``{dept_dir}/_context/{version}.json``, or ``{}``.

        Takes a resolved *dept_dir* rather than an asset id so a caller
        wanting several fields resolves the directory once. Read
        directly rather than through ``ui.helpers``, which would
        re-resolve the path via the cached single-project
        ``tumblehead.pipe.paths`` functions and miss cross-project
        switches. A version saved before sidecars existed has none
        (``{}``); one that will not parse raises.
        """
        if dept_dir is None or not version:
            return {}
        ctx_file = dept_dir / "_context" / f"{version}.json"
        if not ctx_file.exists():
            return {}
        import json
        return json.loads(ctx_file.read_text(encoding="utf-8"))

    def get_user_for_version(self, asset_id: str, dept: str, version: str):
        """Read the user attribution for a specific dept/version."""
        sidecar = self._read_version_sidecar(
            self.dept_dir_for(asset_id, dept), version,
        )
        user = sidecar.get("user")
        return str(user) if user else None

    def get_mtime_for_version(self, asset_id: str, dept: str, version: str):
        """Return the .hip file's modification time as a datetime, or None.

        No ``exists()`` pre-check: the path came back from a glob of the
        directory, so it existed a moment ago, and a second probe only
        buys another round trip that SMB may answer wrongly anyway (the
        same reason ``save_context`` stamps the extension). ``stat`` on a
        file deleted in between raises, which is the real answer.
        """
        p = self.workfile_path_for(asset_id, dept, version)
        if p is None:
            return None
        try:
            import datetime as dt
            return dt.datetime.fromtimestamp(p.stat().st_mtime)
        except (OSError, ValueError, OverflowError):
            # stat: gone/unreachable. fromtimestamp: an mtime outside the
            # platform's representable range — a real thing on restored
            # or clock-skewed shares.
            return None

    def get_user_and_note_for_version(
        self, asset_id: str, dept: str, version: str,
    ) -> tuple[str, str]:
        """Return ``(user, note)`` for a version — ONE sidecar read, no stat.

        The card-build counterpart to :meth:`get_dept_row_meta`, and the
        distinction is deliberate: deck rows need the workfile's mtime and
        so must glob + stat it, but a *card* already has a newer-or-equal
        mtime from the directory scan it just did. Routing the card path
        through ``get_dept_row_meta`` for the sake of code reuse would add
        a glob and a stat per card to every grid rebuild — the shape of the
        stat-storm this module has already been through once. Both fields
        live in one file, so reading them together costs nothing extra.
        """
        sidecar = self._read_version_sidecar(
            self.dept_dir_for(asset_id, dept), version,
        )
        user = sidecar.get("user")
        note = sidecar.get("note")
        return (
            (str(user) if user else ""),
            (str(note) if note else ""),
        )

    def get_notes_for_versions(
        self, asset_id: str, dept: str, versions: list[str],
    ) -> dict[str, tuple[str, str]]:
        """Return ``{version: (user, note)}`` for the versions with a note.

        Feeds the right-click version menu, which is built on the GUI
        thread and wants every version's note at once. That is one
        sidecar read per version, and on a slow share those add up to a
        visible stall before the menu opens — so the reads run
        concurrently. Versions with no note (or no sidecar) are left out.
        """
        dept_dir = self.dept_dir_for(asset_id, dept)
        versions = list(versions)
        if dept_dir is None or not versions:
            return {}
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(8, len(versions))) as pool:
            sidecars = pool.map(
                lambda v: self._read_version_sidecar(dept_dir, v), versions,
            )
            result = {}
            for version, sidecar in zip(versions, sidecars):
                note = sidecar.get("note")
                if note:
                    user = sidecar.get("user")
                    result[version] = (str(user) if user else "", str(note))
        return result

    def get_dept_row_meta(
        self, asset_id: str, dept: str, version: str,
    ) -> tuple[str, float, str]:
        """Return ``(user, mtime_epoch, note)`` for one dept row.

        One resolve, one glob, one stat, one sidecar read — the deck-item
        builder wants the user, mtime and note together (the list view's
        User/Edited/Note columns) and would otherwise pay the dept-dir
        resolve and the workfile glob twice over, once via
        ``get_user_for_version`` and again via ``get_mtime_for_version``.
        Over a share that is the difference that shows.

        The note rides along for free: it comes out of the same sidecar
        read that already produced the user, so adding it costs no extra
        I/O at all. That is also why there is no ``get_note_for_version``
        — no caller wants a note on its own, and a lone getter would be a
        third read of a file this already opens.

        Missing values are blank/zero rather than ``None``: these feed
        ``DeckItem``, whose fields are typed ``str``/``float``/``str``.
        """
        dept_dir = self.dept_dir_for(asset_id, dept)
        if dept_dir is None or not version:
            return "", 0.0, ""

        sidecar = self._read_version_sidecar(dept_dir, version)
        user = sidecar.get("user")
        note = sidecar.get("note")

        path = workfile_for_version(dept_dir, version)

        mtime = 0.0
        if path is not None:
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                # Deleted between the glob that found it and this stat.
                mtime = 0.0

        return (
            (str(user) if user else ""),
            mtime,
            (str(note) if note else ""),
        )

    def get_latest_export_mtime(self, asset_id: str, dept: str):
        """Return the latest export folder's mtime for ``dept`` as a
        datetime, or ``None`` if no export exists."""
        if not asset_id:
            return None
        uri = self._resolver.uri_for_ready(asset_id)
        if uri is None:
            return None
        from tumblepipe.pipe.paths import latest_export_path
        path = latest_export_path(uri, "default", dept)
        if path is None:
            return None
        import datetime as dt
        return dt.datetime.fromtimestamp(path.stat().st_mtime)

    def get_user_for_export(self, asset_id: str, dept: str):
        """Read the user attribution for ``dept``'s latest export.

        Export flows stamp a ``context.json`` in the export folder at
        write time; ``save_export_context`` puts ``user`` at the top
        level while ``save_layer_context`` nests it in ``outputs[0]``
        — accept both shapes. Returns ``None`` for exports predating
        the stamp; a stamp that will not parse raises.
        """
        if not asset_id:
            return None
        uri = self._resolver.uri_for_ready(asset_id)
        if uri is None:
            return None
        from tumblepipe.pipe.paths import latest_export_path
        path = latest_export_path(uri, "default", dept)
        if path is None:
            return None
        ctx_file = path / "context.json"
        if not ctx_file.exists():
            return None
        import json
        data = json.loads(ctx_file.read_text(encoding="utf-8"))
        user = data.get("user")
        if not user:
            outputs = data.get("outputs") or []
            if outputs and isinstance(outputs[0], dict):
                user = outputs[0].get("user")
        return str(user) if user else None

    @staticmethod
    def format_relative_time(timestamp) -> str:
        """Format a datetime as 'Ns/m/h/d/w/mo/y ago'. Cloned from
        ``ui.helpers.format_relative_time``."""
        if timestamp is None:
            return ""
        import datetime as dt
        diff = dt.datetime.now() - timestamp
        if diff.total_seconds() < 0:
            return "in the future"
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 7:
            return f"{days}d ago"
        # Each cut-over sits where the next unit reaches 1: weeks until
        # day 30 (not 28) and months until day 365 (not 360), or days
        # 28-29 read "0mo ago" and days 360-364 "0y ago".
        weeks = days // 7
        if days < 30:
            return f"{weeks}w ago"
        months = days // 30
        if days < 365:
            return f"{months}mo ago"
        years = days // 365
        return f"{years}y ago"

    def _load(self, path: Path, proj, asset_id: str, refresh_cb=None) -> None:
        """Swap this session to the workfile at ``path``, on the main thread.

        ``hou.hipFile.load`` off the GUI thread crashes Houdini, so the swap
        is deferred there - and is therefore its own failure boundary: the
        action that scheduled it has already returned.
        """

        def _do_load() -> None:
            try:
                import hou
                from tumblepipe.pipe.houdini import playbar
                decision = self._scene.prepare_scene_swap()
                if decision is None:
                    return  # user cancelled the save prompt
                self._activator.activate(proj)
                # Manual update mode so neither the load, the
                # hou.setFrame() in apply_scene_timeline, nor load_hook's
                # import re-execute triggers a live full-graph cook.
                with playbar.update_mode(hou.updateMode.Manual):
                    try:
                        hou.hipFile.load(
                            str(path), suppress_save_prompt=decision,
                        )
                    except hou.LoadWarning as warning:
                        # The scene IS open, with warnings (a missing HDA,
                        # say); the open carries on and says so.
                        hou.ui.setStatusMessage(
                            f"Opened {path.name} with warnings: {warning}",
                            severity=hou.severityType.Warning,
                        )
                    log.info("Opened workfile: %s", path)
                    self._scene.apply_scene_timeline(asset_id)
                if callable(refresh_cb):
                    refresh_cb()
                self._refresh.detail()
            except Exception as exc:
                report_failure(f"Opening {path}", exc)

        run_on_main_thread(_do_load)

    @_action("Opening a workfile version")
    def open_version_now(
        self, asset_id: str, dept: str, version: str | None,
        refresh_cb=None,
    ) -> None:
        """Open the .hip workfile for a specific (asset, dept, version).

        For cross-project opens, launches a new Houdini instance with
        the target project's environment so the USD resolver and
        pipeline context are clean. Same-project opens load in-process
        via deferred ``hou.hipFile.load``.
        """
        if not asset_id or not version:
            return
        import hou
        is_group = asset_id.startswith("group:")
        if is_group:
            proj = self._group_project(asset_id)
        else:
            proj = self._resolver.project_for(asset_id)
        if proj is None:
            return

        # Cross-project: launch a new Houdini instance instead of
        # loading into the current session (resolver + env mismatch).
        # Multis load in-process, like open_group_workfile.
        if not is_group:
            scene_proj = self._resolver.project_for_path(
                Path(hou.hipFile.path()),
            )
            if scene_proj is not None and scene_proj.name != proj.name:
                hou.ui.setStatusMessage(
                    f"Opening {proj.name} scene in a new Houdini "
                    f"instance...",
                    severity=hou.severityType.Message,
                )
                self.open_in_new_instance(asset_id, dept)
                return

        path = self.workfile_path_for(asset_id, dept, version)
        if path is None:
            hou.ui.setStatusMessage(
                f"Workfile not found: {asset_id} / {dept} / {version}",
                severity=hou.severityType.Warning,
            )
            return
        self._load(path, proj, asset_id, refresh_cb)

    def _create_from_template(
        self, *, asset_id: str, entity_uri, context: str, dept: str,
        proj, client, module_name: str, refresh_cb,
    ) -> None:
        """Save the next ``dept/vNNNN`` workfile of ``entity_uri`` from the
        department template, on the main thread.

        Activate the target project's env, clear the scene, save an empty
        hip at the next versioned path, then run the matching
        ``config:/templates/{context}/{dept}/template.py`` to populate
        ``/stage`` and stamp the config timeline before saving again. Works
        for both empty depts (next = v0001) and populated depts
        (next = v(N+1)). A department with no template saves an empty
        scene and says so; a template that raises is reported, as is
        everything else that fails here.
        """

        def _do_create() -> None:
            try:
                import hou
                from tumblepipe.pipe.context import (
                    save_context, save_entity_context,
                    workfile_context,
                )
                from tumblepipe.pipe.houdini.workfile_save import (
                    save_hip_file,
                )
                from tumblepipe.pipe.paths import reserve_next_hip_file_path

                # Re-activate inside the deferred tick so no background
                # thread can clobber the global client between activate
                # and the path resolution that depends on it.
                self._activator.activate(proj)
                next_path = reserve_next_hip_file_path(
                    entity_uri, dept, nc_type=session_nc_type(),
                )
                template_path = Path(client.storage.resolve(
                    uris.dept_template(context, dept),
                ))

                next_path.parent.mkdir(parents=True, exist_ok=True)
                hou.hipFile.clear(suppress_save_prompt=True)
                _show_stage_in_network_editors()
                # Houdini may rewrite the extension (Education/Apprentice
                # save .hip as .hipnc); record the path it actually wrote.
                next_path = save_hip_file(
                    next_path, workfile_context(entity_uri, dept, next_path),
                )

                new_ctx = workfile_context(entity_uri, dept, next_path)
                save_context(
                    next_path.parent, None, new_ctx,
                    file_extension=next_path.suffix.lstrip("."),
                )
                save_entity_context(next_path.parent, new_ctx)

                # A department need not ship a template: it then starts
                # from an empty /stage.
                template_applied = template_path.exists()
                if template_applied:
                    from tumblepipe.pipe.houdini import playbar
                    from tumblepipe.pipe.houdini.scene_imports import (
                        refresh_scene_imports,
                    )
                    from tumblepipe.pipe.houdini.ui.helpers import load_module
                    template = load_module(template_path, module_name)
                    template.create(hou.node("/stage"), entity_uri, dept)
                    # A template creates its import nodes but need not run
                    # them (only animation's does), so a new layout or light
                    # workfile showed no geometry until the artist pressed
                    # Import. Bring them in as opening a scene does.
                    with playbar.update_mode(hou.updateMode.Manual):
                        refresh_scene_imports()

                # Stamp the config timeline into the fresh workfile so it
                # persists in the hip. force_frame_range=True: creation is
                # the one time a non-animatable entity (asset) should get
                # its starting range — subsequent opens leave it be so the
                # artist can freely move it.
                self._scene.apply_scene_timeline(
                    asset_id, force_frame_range=True,
                )
                hou.hipFile.save(str(next_path))

                log.info(
                    "Created %s (template_applied=%s)",
                    next_path, template_applied,
                )
                msg = f"Created {next_path.name}"
                if not template_applied:
                    msg += " (no template)"
                hou.ui.setStatusMessage(
                    msg, severity=hou.severityType.Message,
                )

                self._invalidate_cache()
                # Swap the entity's card/list row in place so the new
                # dept/version shows instantly — the refresh_cb below only
                # re-renders the currently-displayed detail, which may be a
                # different asset.
                self._refresh.card(asset_id)
                if callable(refresh_cb):
                    refresh_cb()
            except Exception as exc:
                report_failure(
                    f"Creating a workfile for {asset_id}/{dept}", exc,
                )

        run_on_main_thread(_do_create)

    @_action("Creating a workfile")
    def new_from_template(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the next ``dept/vNNNN`` workfile for ``asset_id`` from
        the department template (see :meth:`_create_from_template`)."""
        if not asset_id:
            return
        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return
        # Activate before resolving anything so next_hip_file_path /
        # storage.resolve / template loading all see the right project.
        self._activator.activate(proj)
        client = self._resolver.client_for(asset_id)
        # A write path: never create a workfile for an entity the config
        # does not hold (see EntityNotRegistered).
        entity_uri = self._resolver.registered_uri_for(asset_id)
        if entity_uri is None:
            return
        # A department a Multi covers belongs to the Multi: its workfile is
        # the Multi's (department_workspace routes the save there), so the
        # template must build the Multi's scene, not the member's.
        from tumblepipe.pipe.paths import department_workspace
        workfile_uri, _ = department_workspace(entity_uri, dept)
        is_group = workfile_uri.purpose == 'groups'
        self._create_from_template(
            asset_id=asset_id, entity_uri=workfile_uri,
            context=workfile_uri.segments[0], dept=dept, proj=proj,
            client=client,
            module_name=(
                entity_stem(workfile_uri, dept, "template", "group")
                if is_group else entity_stem(workfile_uri, dept, "template")
            ),
            refresh_cb=refresh_cb,
        )

    @_action("New from Current")
    def new_from_current(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the *currently loaded* scene as the next ``dept/vNNNN``
        workfile for ``asset_id``.

        Copies the in-memory scene state into a fresh workfile slot for
        the target dept, regardless of which entity the scene was
        originally loaded from. The previous scene's context (if any)
        becomes ``from_version`` in the new ``_context/<vNNNN>.json`` so
        the provenance is preserved.
        """
        if not asset_id:
            return
        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return
        self._activator.activate(proj)
        # A write path: never save the open scene as a workfile of an entity
        # the config does not hold (see EntityNotRegistered).
        entity_uri = self._resolver.registered_uri_for(asset_id)
        if entity_uri is None:
            return

        def _do_save() -> None:
            try:
                import hou
                from tumblepipe.pipe.paths import (
                    reserve_next_hip_file_path, get_workfile_context,
                )
                from tumblepipe.pipe.context import (
                    save_context, save_entity_context,
                    workfile_context,
                )
                from tumblepipe.pipe.houdini.workfile_save import (
                    save_hip_file,
                )

                # Re-activate inside the deferred tick so no background
                # thread can clobber the global client.
                self._activator.activate(proj)
                next_path = reserve_next_hip_file_path(
                    entity_uri, dept, nc_type=session_nc_type(),
                )

                # The current scene's context (None for a scene that is
                # not a pipeline workfile) records from_version provenance.
                hip_str = hou.hipFile.path()
                prev_ctx = (
                    get_workfile_context(Path(hip_str)) if hip_str else None
                )

                next_path.parent.mkdir(parents=True, exist_ok=True)
                # Houdini may rewrite the extension (Education/Apprentice
                # save .hip as .hipnc); record the path it actually wrote.
                # The copied scene's context options name its old entity;
                # save_hip_file replaces them with this workfile's.
                next_path = save_hip_file(
                    next_path, workfile_context(entity_uri, dept, next_path),
                )

                new_ctx = workfile_context(entity_uri, dept, next_path)
                save_context(
                    next_path.parent, prev_ctx, new_ctx,
                    file_extension=next_path.suffix.lstrip("."),
                )
                save_entity_context(next_path.parent, new_ctx)

                # Nodes still pinned to the scene's old entity/department
                # would publish over it: point them at the new workfile.
                retargeted = []
                if prev_ctx is not None:
                    from tumblepipe.pipe.houdini import playbar
                    from tumblepipe.pipe.houdini.scene_retarget import (
                        retarget_scene,
                    )
                    with playbar.update_mode(hou.updateMode.Manual):
                        retargeted = retarget_scene(prev_ctx, new_ctx)
                    hou.hipFile.save(str(next_path))

                log.info(
                    "New from Current: saved %s (retargeted %d node(s))",
                    next_path, len(retargeted),
                )
                msg = f"Saved {next_path.name}"
                if retargeted:
                    msg += (
                        f" ({len(retargeted)} node(s) now follow the "
                        "new context)"
                    )
                hou.ui.setStatusMessage(
                    msg, severity=hou.severityType.Message,
                )

                self._invalidate_cache()
                # See _create_from_template: refresh the entity's own
                # card/list row directly, not just the displayed detail.
                self._refresh.card(asset_id)
                if callable(refresh_cb):
                    refresh_cb()
            except Exception as exc:
                report_failure(
                    f"New from Current for {asset_id}/{dept}", exc,
                )

        run_on_main_thread(_do_save)

    @_action("Opening in a new Houdini instance")
    def open_in_new_instance(self, asset_id: str, dept: str) -> None:
        """Launch a new Houdini process with the latest workfile for a dept.

        Inherits the current process's full environment (TH_*, HOUDINI_PATH,
        etc.) so the new instance has identical pipeline context, with the
        target project's ``TH_PROJECT_PATH`` / ``TH_CONFIG_PATH``.
        """
        if not asset_id:
            return
        proj = self._resolver.project_for(asset_id)
        if proj is None:
            return
        self._activator.activate(proj)
        entity_uri = self._resolver.uri_for_ready(asset_id)
        from tumblepipe.pipe.houdini.workfiles import (
            open_workfile_in_new_instance,
        )
        # The same workfile Open lands on (a Multi's, where one covers the
        # department), in a Houdini bound to the asset's project.
        open_workfile_in_new_instance(
            entity_uri, dept,
            env_overrides={
                "TH_PROJECT_PATH": proj.project_path,
                "TH_CONFIG_PATH": proj.config_path,
            },
        )

    @_action("Opening the workfile")
    def open_workfile(self, asset_id: str, dept: str) -> None:
        """Open the latest workfile for a department.

        Resolution is delegated to ``latest_hip_file_path_with_context``,
        which auto-routes to a covering group's workfile when one
        exists — so clicking a member shot's covered dept opens the
        group's hip, not a local shot hip. With no workfile at all, the
        department's folder opens instead.
        """
        if not asset_id:
            return
        parsed = self._resolver.split(asset_id)
        if parsed is None:
            return
        proj = self._registry.get(parsed[0])
        if proj is None:
            return
        self._activator.activate(proj)
        entity_uri = self._resolver.uri_for_ready(asset_id)
        from tumblepipe.pipe import paths as paths_mod
        hip_path = paths_mod.latest_hip_file_path_with_context(
            entity_uri, dept,
        )
        if hip_path is not None and hip_path.exists():
            self._load(hip_path, proj, asset_id)
            return
        work_dir = self.dept_dir_for(asset_id, dept)
        desktop.open_in_file_browser(
            work_dir if work_dir.exists() else work_dir.parent,
        )

    @_action("Opening the group workfile")
    def open_group_workfile(self, asset_id: str, dept: str) -> None:
        """Open the latest workfile for a group's department.

        Mirrors :meth:`open_workfile` but routes through tumblepipe's
        group-aware path resolver. If no workfile exists, the group's
        work directory is opened in Explorer.
        """
        if not asset_id.startswith("group:"):
            return
        proj = self._group_project(asset_id)
        if proj is None:
            return
        _proj_name, path = self._split_group_id(asset_id)
        self._activator.activate(proj)
        from tumblepipe.pipe import paths as paths_mod
        hip_path = paths_mod.latest_hip_file_path_with_context(
            uris.group(path), dept,
        )
        if hip_path is not None and hip_path.exists():
            self._load(hip_path, proj, asset_id)
            return
        self._open_group_folder(path, dept)

    @_action("Opening the group work directory")
    def open_group_dept_work_dir(self, asset_id: str, dept: str) -> None:
        """Open a group dept's workfile directory in Explorer."""
        if not asset_id.startswith("group:"):
            return
        proj = self._group_project(asset_id)
        if proj is None:
            return
        _proj_name, path = self._split_group_id(asset_id)
        self._activator.activate(proj)
        self._open_group_folder(path, dept)

    @staticmethod
    def _open_group_folder(path: str, dept: str) -> None:
        """Show a group dept's work directory, or the group's own root
        when the dept folder does not exist yet. The group's project must
        be active."""
        from tumblepipe.api import api as tp_api
        group_segments = uris.group(path).segments
        target = tp_api.storage.resolve(
            uris.groups_root() / group_segments / dept,
        )
        if not target.exists():
            target = tp_api.storage.resolve(
                uris.groups_root() / group_segments,
            )
        desktop.open_in_file_browser(target)

    @_action("Creating a group workfile")
    def new_group_from_template(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the next ``dept/vNNNN`` workfile for a group from the
        department template, via the group's ``groups:/ctx/name`` URI (see
        :meth:`_create_from_template`)."""
        if not asset_id.startswith("group:"):
            return
        proj = self._group_project(asset_id)
        if proj is None:
            return
        proj_name, path = self._split_group_id(asset_id)
        self._activator.activate(proj)
        client = self._clients.get(proj_name)
        group_uri = uris.group(path)
        self._create_from_template(
            asset_id=asset_id, entity_uri=group_uri,
            # A group's context is its path's first segment; groups share
            # the per-context dept template with shots/assets.
            context=group_uri.segments[0], dept=dept, proj=proj,
            client=client,
            module_name=entity_stem(group_uri, dept, "template", "group"),
            refresh_cb=refresh_cb,
        )
