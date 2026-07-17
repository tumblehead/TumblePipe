"""Workfile lifecycle for the Pipeline catalog.

Owns the per-asset / per-shot / per-Multi workfile open and create
flows: path resolution, mtime / user attribution, version-aware open
(in-process or new-Houdini-instance), and the three new-from-* save
paths (template / current scene / group template).

The manager holds a back-reference to the :class:`PipelineCatalog`
because every method calls back into multiple catalog services
(asset resolver, project activator, scene-timeline application,
auto-save-before-swap, cache invalidation, card / detail refresh
hooks). Threading those through ten constructor arguments would
trade verbosity for no real decoupling — the catalog is the manager's
runtime context.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from _pipeline_houdini import run_on_main_thread, session_nc_type
from _pipeline_types import latest_workfile, workfile_for_version
import _pipeline_uris as uris

if TYPE_CHECKING:
    from _pipeline_catalog import PipelineCatalog

log = logging.getLogger(__name__)


class WorkfileManager:
    """Open, create, and inspect workfiles for the Pipeline catalog."""

    def __init__(self, catalog: "PipelineCatalog") -> None:
        self._catalog = catalog

    def open_dept_work_dir(self, asset_id: str, dept: str) -> None:
        """Open the dept's workfile directory in the OS file browser."""
        if not asset_id:
            return
        parsed = self._catalog._resolver.split(asset_id)
        if parsed is None:
            return
        project_name, second, third = parsed
        root = self._catalog._resolver.root_for(asset_id)
        if root is None:
            return
        try:
            cats = self._catalog._list_categories_for_project(project_name)
            kind = "assets" if second in cats else "shots"
            base = root / kind / second / third / dept
            target = base if base.exists() else base.parent
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception("Open Location failed for %s/%s", asset_id, dept)

    def open_latest_export(self, asset_id: str, dept: str) -> None:
        """Open the dept's latest export folder in the OS file browser."""
        if not asset_id:
            return
        # Activate the asset's project so latest_export_path resolves
        # against its config.
        proj = self._catalog._resolver.project_for(asset_id)
        if proj is not None:
            self._catalog._activate_project(proj)
        uri = self._catalog._resolver.uri_for(asset_id)
        if uri is None:
            return
        try:
            from tumblepipe.pipe.paths import latest_export_path
            path = latest_export_path(uri, "default", dept)
            if path is None:
                import hou
                hou.ui.setStatusMessage(
                    f"No export found for {dept}.",
                    severity=hou.severityType.Warning,
                )
                return
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(path)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception(
                "View Latest Export failed for %s/%s", asset_id, dept,
            )

    def dept_dir_for(self, asset_id: str, dept: str) -> Path | None:
        """Return an asset/dept's directory, or ``None``.

        Uses direct filesystem math via the asset's project root —
        does NOT call ``tumblehead.pipe.paths.get_hip_file_path``,
        because that function caches ``TH_PROJECT_PATH`` at module
        import time and returns paths under the wrong project for any
        non-launch project.
        """
        if not asset_id or not dept:
            return None
        parsed = self._catalog._resolver.split(asset_id)
        if parsed is None:
            return None
        project_name, second, third = parsed
        root = self._catalog._resolver.root_for(asset_id)
        if root is None:
            return None
        try:
            cats = self._catalog._list_categories_for_project(project_name)
            kind = "assets" if second in cats else "shots"
            return root / kind / second / third / dept
        except Exception:
            return None

    def workfile_path_for(
        self, asset_id: str, dept: str, version: str,
    ) -> Path | None:
        """Return the .hip workfile :class:`Path` for an asset/dept/version."""
        if not version:
            return None
        dept_dir = self.dept_dir_for(asset_id, dept)
        if dept_dir is None:
            return None
        try:
            return workfile_for_version(dept_dir, version)
        except Exception:
            return None

    @staticmethod
    def _read_version_sidecar(dept_dir: Path | None, version: str) -> dict:
        """Return ``{dept_dir}/_context/{version}.json``, or ``{}``.

        Takes a resolved *dept_dir* rather than an asset id so a caller
        wanting several fields resolves the directory once. Read
        directly rather than through ``ui.helpers``, which would
        re-resolve the path via the cached single-project
        ``tumblehead.pipe.paths`` functions and miss cross-project
        switches.
        """
        if dept_dir is None or not version:
            return {}
        try:
            ctx_file = dept_dir / "_context" / f"{version}.json"
            if not ctx_file.exists():
                return {}
            import json
            data = json.loads(ctx_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            log.debug(
                "Failed to read version sidecar %s/%s", dept_dir, version,
            )
            return {}

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

    def get_dept_row_meta(
        self, asset_id: str, dept: str, version: str,
    ) -> tuple[str, float]:
        """Return ``(user, mtime_epoch)`` for one dept row.

        One resolve, one glob, one stat, one sidecar read — the deck-item
        builder wants the user and mtime together (the list view's
        User/Edited columns) and would otherwise pay the dept-dir resolve
        and the workfile glob twice over, once via ``get_user_for_version``
        and again via ``get_mtime_for_version``. Over a share that is the
        difference that shows.

        Missing values are blank/zero rather than ``None``: these feed
        ``DeckItem``, whose fields are typed ``str``/``float``.
        """
        dept_dir = self.dept_dir_for(asset_id, dept)
        if dept_dir is None or not version:
            return "", 0.0

        sidecar = self._read_version_sidecar(dept_dir, version)
        user = sidecar.get("user")

        path = None
        try:
            path = workfile_for_version(dept_dir, version)
        except Exception:
            path = None

        mtime = 0.0
        if path is not None:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0

        return (str(user) if user else ""), mtime

    def get_latest_export_mtime(self, asset_id: str, dept: str):
        """Return the latest export folder's mtime for ``dept`` as a
        datetime, or ``None`` if no export exists."""
        if not asset_id:
            return None
        uri = self._catalog._resolver.uri_for(asset_id)
        if uri is None:
            return None
        try:
            from tumblepipe.pipe.paths import latest_export_path
            path = latest_export_path(uri, "default", dept)
        except Exception:
            return None
        if path is None or not path.exists():
            return None
        try:
            import datetime as dt
            return dt.datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None

    def get_user_for_export(self, asset_id: str, dept: str):
        """Read the user attribution for ``dept``'s latest export.

        Export flows stamp a ``context.json`` in the export folder at
        write time; ``save_export_context`` puts ``user`` at the top
        level while ``save_layer_context`` nests it in ``outputs[0]``
        — accept both shapes. Returns ``None`` for exports predating
        the stamp or on any read failure.
        """
        if not asset_id:
            return None
        uri = self._catalog._resolver.uri_for(asset_id)
        if uri is None:
            return None
        try:
            from tumblepipe.pipe.paths import latest_export_path
            path = latest_export_path(uri, "default", dept)
        except Exception:
            return None
        if path is None:
            return None
        ctx_file = path / "context.json"
        try:
            if not ctx_file.exists():
                return None
            import json
            data = json.loads(ctx_file.read_text(encoding="utf-8"))
        except Exception:
            log.debug(
                "Failed to read export user for %s/%s",
                asset_id, dept,
            )
            return None
        if not isinstance(data, dict):
            return None
        user = data.get("user")
        if not user:
            outputs = data.get("outputs") or []
            if outputs and isinstance(outputs[0], dict):
                user = outputs[0].get("user")
        return str(user) if user else None

    def get_export_meta(
        self, asset_id: str, dept: str,
    ) -> tuple[str, float, str]:
        """Return ``(version, mtime_epoch, user)`` for ``dept``'s latest
        export — the session panel's "Latest Export" block in one shot.

        The workspace analogue is :meth:`get_dept_row_meta`; this is its
        export-side twin. It resolves ``latest_export_path`` **once** —
        the standalone ``get_latest_export_mtime`` / ``get_user_for_export``
        each resolve it again, and over a share that repeat shows — and
        derives all three values from that single path:

        - ``version`` is the export folder's name (``v0049``); the folder
          layout *is* the version, validated by ``is_valid_version_name``
          on the way in, so no sidecar read is needed for it.
        - ``mtime`` from ``stat`` on the folder.
        - ``user`` from the folder's ``context.json`` (both the top-level
          and ``outputs[0]`` shapes, as ``get_user_for_export`` accepts).

        All-blank/zero (``"", 0.0, ""``) means no export exists yet — the
        caller renders the "Latest Export" fields dimmed rather than
        dropping them. Values are ``str``/``float`` for the same reason
        :meth:`get_dept_row_meta`'s are: they feed typed value fields.
        """
        if not asset_id:
            return "", 0.0, ""
        uri = self._catalog._resolver.uri_for(asset_id)
        if uri is None:
            return "", 0.0, ""
        try:
            from tumblepipe.pipe.paths import latest_export_path
            path = latest_export_path(uri, "default", dept)
        except Exception:
            return "", 0.0, ""
        if path is None:
            return "", 0.0, ""

        version = path.name
        mtime = 0.0
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        user = ""
        ctx_file = path / "context.json"
        try:
            if ctx_file.exists():
                import json
                data = json.loads(ctx_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    raw = data.get("user")
                    if not raw:
                        outputs = data.get("outputs") or []
                        if outputs and isinstance(outputs[0], dict):
                            raw = outputs[0].get("user")
                    user = str(raw) if raw else ""
        except Exception:
            log.debug(
                "Failed to read export context for %s/%s", asset_id, dept,
            )

        return version, mtime, user

    def get_open_workspace_meta(self) -> tuple[str, float]:
        """Return ``(user, mtime_epoch)`` for the OPEN ``.hip``.

        The session panel's "Current Workspace" block. Derived purely from
        ``hou.hipFile.path()`` — deliberately **not** from an asset id like
        :meth:`get_dept_row_meta` — because the open document may be a
        Multi/group workfile, whose id does not ``split`` into the
        ``PROJECT/CATEGORY/NAME`` shape that ``dept_dir_for`` needs, and
        which would therefore come back blank. A path always resolves.

        Workfiles are flat in their dept dir, so the version sidecar sits
        beside the open file at ``{parent}/_context/{version}.json`` — the
        same file :meth:`get_dept_row_meta` reads, reached by path here.

        Reading ``hou.hipFile.path()`` off the worker thread is safe (HOM
        lock); this builds no Qt. Blank/zero means no saved pipeline
        workfile is open (untitled, or a stray scene).
        """
        try:
            import hou
            hip = hou.hipFile.path()
        except Exception:
            return "", 0.0
        if not hip or hip == "untitled.hip":
            return "", 0.0

        from pathlib import Path
        p = Path(hip)

        mtime = 0.0
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0

        user = ""
        tail = p.stem.rsplit("_", 1)
        if len(tail) == 2:
            sidecar = self._read_version_sidecar(p.parent, tail[1])
            raw = sidecar.get("user")
            user = str(raw) if raw else ""

        return user, mtime

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
        weeks = days // 7
        if weeks < 4:
            return f"{weeks}w ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        years = days // 365
        return f"{years}y ago"

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
        proj = self._catalog._resolver.project_for(asset_id)
        if proj is None:
            return

        # Cross-project: launch a new Houdini instance instead of
        # loading into the current session (resolver + env mismatch).
        try:
            import hou
            scene_proj = self._catalog._project_for_hip_path(
                Path(hou.hipFile.path()),
            )
            if (
                scene_proj is not None
                and proj is not None
                and scene_proj.name != proj.name
            ):
                hou.ui.setStatusMessage(
                    f"Opening {proj.name} scene in a new Houdini "
                    f"instance...",
                    severity=hou.severityType.Message,
                )
                self.open_in_new_instance(asset_id, dept)
                return
        except Exception:
            pass

        path = self.workfile_path_for(asset_id, dept, version)
        if path is None or not path.exists():
            try:
                import hou
                hou.ui.setStatusMessage(
                    f"Workfile not found: {asset_id} / {dept} / {version}",
                    severity=hou.severityType.Warning,
                )
            except Exception:
                pass
            return

        path_str = str(path)

        def _do_load(target_proj=proj):
            try:
                import hou
                from tumblepipe.pipe.houdini import util
                decision = self._catalog._scene.prepare_scene_swap()
                if decision is None:
                    return  # user cancelled the save prompt
                self._catalog._activate_project(target_proj)
                # Manual update mode so neither the load, the
                # hou.setFrame() in apply_scene_timeline, nor the import
                # re-execute triggers a live full-graph cook. Mirrors
                # the open flow of the since-retired Project Browser.
                with util.update_mode(hou.updateMode.Manual):
                    hou.hipFile.load(path_str, suppress_save_prompt=decision)
                    log.info("Opened workfile: %s", path_str)
                    self._catalog._scene.apply_scene_timeline(asset_id)
                    if self._catalog._prefs.auto_refresh_on_open:
                        self._catalog._scene.refresh_scene_imports()
            except Exception:
                log.exception("Failed to load %s", path_str)
                return
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after open failed")
            self._catalog._request_global_detail_refresh()

        run_on_main_thread(_do_load)

    def new_from_template(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the next ``dept/vNNNN`` workfile for ``asset_id`` from
        the department template.

        Mirrors Project Browser's 'New: Template' action: activate the
        target project's env, clear the scene, save an empty hip at the
        next versioned path, then run the matching
        ``config:/templates/{context}/{dept}/template.py`` to populate
        ``/stage`` before saving again. Works for both empty depts
        (next = v0001) and populated depts (next = v(N+1)).
        """
        if not asset_id:
            return
        proj = self._catalog._resolver.project_for(asset_id)
        if proj is None:
            return
        # Activate before resolving anything so next_hip_file_path /
        # storage.resolve / template loading all see the right project.
        self._catalog._activate_project(proj)
        client = self._catalog._resolver.client_for(asset_id)
        if client is None:
            return
        entity_uri = self._catalog._resolver.uri_for(asset_id)
        if entity_uri is None:
            return

        try:
            from tumblepipe.pipe.paths import (
                reserve_next_hip_file_path, Context,
            )
        except Exception:
            log.exception("Create: tumblehead imports failed")
            return


        def _do_create(target_proj=proj):
            try:
                import hou
                from tumblepipe.pipe.paths import get_workfile_context
                from tumblepipe.pipe.context import (
                    save_context, save_entity_context,
                )

                # Re-activate inside the deferred tick so no background
                # thread can clobber the global client between activate
                # and the path resolution that depends on it.
                self._catalog._activate_project(target_proj)

                try:
                    next_path = reserve_next_hip_file_path(
                        entity_uri, dept, nc_type=session_nc_type(),
                    )
                except Exception:
                    log.exception(
                        "Create: reserving next version failed for %s/%s",
                        asset_id, dept,
                    )
                    return
                if next_path is None:
                    hou.ui.setStatusMessage(
                        f"Create: could not resolve path for {dept}.",
                        severity=hou.severityType.Warning,
                    )
                    return

                # Resolve the template module path.
                segs = entity_uri.segments
                entity_context = segs[0] if len(segs) > 0 else "assets"
                try:
                    template_uri = uris.dept_template(entity_context, dept)
                    template_path = client.storage.resolve(template_uri)
                except Exception:
                    log.exception(
                        "Create: failed to resolve template URI for %s/%s",
                        entity_context, dept,
                    )
                    template_path = None

                next_path.parent.mkdir(parents=True, exist_ok=True)

                hou.hipFile.clear(suppress_save_prompt=True)
                hou.hipFile.save(str(next_path))

                new_ctx = get_workfile_context(next_path) or Context(
                    entity_uri=entity_uri,
                    department_name=dept,
                    version_name=Path(next_path).stem.rsplit("_", 1)[-1],
                )
                save_context(
                    next_path.parent, None, new_ctx,
                    file_extension=next_path.suffix.lstrip("."),
                )
                save_entity_context(next_path.parent, new_ctx)

                # Run the department template against /stage
                template_applied = False
                if template_path is not None and Path(template_path).exists():
                    try:
                        from tumblepipe.pipe.houdini.ui.helpers import (
                            load_module,
                        )
                        stage = hou.node("/stage")
                        if stage is not None:
                            module_name = (
                                f"{'_'.join(entity_uri.segments[1:])}"
                                f"_{dept}_template"
                            )
                            template = load_module(
                                Path(template_path), module_name,
                            )
                            template.create(stage, entity_uri, dept)
                            stage.layoutChildren()
                            template_applied = True
                    except Exception:
                        log.exception(
                            "Create: template.create() failed for %s/%s",
                            asset_id, dept,
                        )
                else:
                    log.warning(
                        "Create: no template found at %s — saving empty scene",
                        template_path,
                    )

                # Stamp the config timeline into the fresh workfile so it
                # persists in the hip. force_frame_range=True: creation is
                # the one time a non-animatable entity (asset) should get
                # its starting range — subsequent opens leave it be so the
                # artist can freely move it.
                try:
                    self._catalog._scene.apply_scene_timeline(
                        asset_id, force_frame_range=True,
                    )
                except Exception:
                    log.exception(
                        "Create: applying scene timeline failed for %s/%s",
                        asset_id, dept,
                    )

                hou.hipFile.save(str(next_path))

                log.info(
                    "Created %s (template_applied=%s)",
                    next_path, template_applied,
                )
                msg = f"Created {Path(next_path).name}"
                if not template_applied:
                    msg += " (no template)"
                hou.ui.setStatusMessage(
                    msg, severity=hou.severityType.Message,
                )
            except Exception:
                log.exception("Create failed for %s/%s", asset_id, dept)
                return

            self._catalog.invalidate_cache()
            # Swap the entity's card/list row in place so the new
            # dept/version shows instantly — the refresh_cb below only
            # re-renders the currently-displayed detail, which may be a
            # different asset (mirrors new_group_from_template).
            try:
                self._catalog._request_card_refresh_for_id(asset_id)
            except Exception:
                log.exception(
                    "card refresh failed for new workfile %s", asset_id,
                )
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception("Detail refresh after create failed")

        run_on_main_thread(_do_create)

    def new_from_current(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the *currently loaded* scene as the next ``dept/vNNNN``
        workfile for ``asset_id``.

        Mirrors Project Browser's 'New: Current' action — copies the
        in-memory scene state into a fresh workfile slot for the target
        dept, regardless of which entity the scene was originally
        loaded from. The previous scene's context (if any) becomes
        ``from_version`` in the new ``_context/<vNNNN>.json`` so the
        provenance is preserved.
        """
        if not asset_id:
            return
        proj = self._catalog._resolver.project_for(asset_id)
        if proj is None:
            return
        self._catalog._activate_project(proj)
        entity_uri = self._catalog._resolver.uri_for(asset_id)
        if entity_uri is None:
            return


        def _do_save(target_proj=proj):
            try:
                import hou
                from tumblepipe.pipe.paths import (
                    reserve_next_hip_file_path, get_workfile_context, Context,
                )
                from tumblepipe.pipe.context import (
                    save_context, save_entity_context,
                )

                # Re-activate inside the deferred tick so no background
                # thread can clobber the global client.
                self._catalog._activate_project(target_proj)

                try:
                    next_path = reserve_next_hip_file_path(
                        entity_uri, dept, nc_type=session_nc_type(),
                    )
                except Exception:
                    log.exception(
                        "New from Current: reserving next version failed for %s/%s",
                        asset_id, dept,
                    )
                    return
                if next_path is None:
                    hou.ui.setStatusMessage(
                        f"New from Current: could not resolve path for {dept}.",
                        severity=hou.severityType.Warning,
                    )
                    return

                # Capture the current scene's context (if any) so we
                # can record from_version provenance metadata.
                prev_ctx = None
                try:
                    hip_str = hou.hipFile.path()
                    if hip_str:
                        prev_ctx = get_workfile_context(Path(hip_str))
                except Exception:
                    prev_ctx = None

                next_path.parent.mkdir(parents=True, exist_ok=True)
                hou.hipFile.save(str(next_path))

                new_ctx = get_workfile_context(next_path) or Context(
                    entity_uri=entity_uri,
                    department_name=dept,
                    version_name=Path(next_path).stem.rsplit("_", 1)[-1],
                )
                save_context(
                    next_path.parent, prev_ctx, new_ctx,
                    file_extension=next_path.suffix.lstrip("."),
                )
                save_entity_context(next_path.parent, new_ctx)

                log.info("New from Current: saved %s", next_path)
                hou.ui.setStatusMessage(
                    f"Saved {Path(next_path).name}",
                    severity=hou.severityType.Message,
                )
            except Exception:
                log.exception(
                    "New from Current failed for %s/%s",
                    asset_id, dept,
                )
                return

            self._catalog.invalidate_cache()
            # See new_from_template: refresh the entity's own card/list
            # row directly, not just the currently-displayed detail.
            try:
                self._catalog._request_card_refresh_for_id(asset_id)
            except Exception:
                log.exception(
                    "card refresh failed for new workfile %s", asset_id,
                )
            if callable(refresh_cb):
                try:
                    refresh_cb()
                except Exception:
                    log.exception(
                        "Detail refresh after New from Current failed",
                    )

        run_on_main_thread(_do_save)

    def open_in_new_instance(self, asset_id: str, dept: str) -> None:
        """Launch a new Houdini process with the latest workfile for a dept.

        Inherits the current process's full environment (TH_*, HOUDINI_PATH,
        etc.) so the new instance has identical pipeline context.
        """
        if not asset_id:
            return
        parsed = self._catalog._resolver.split(asset_id)
        if parsed is None:
            return
        project_name, second, third = parsed
        proj = self._catalog._registry.get(project_name)
        root = self._catalog._resolver.root_for(asset_id)
        if proj is None or root is None:
            return
        try:
            cats = self._catalog._list_categories_for_project(project_name)
            if second in cats:
                work_dir = root / "assets" / second / third / dept
            else:
                work_dir = root / "shots" / second / third / dept

            hip_path = latest_workfile(work_dir)

            if hip_path is None:
                import hou
                hou.ui.setStatusMessage(
                    f"No workfile found for {dept}.",
                    severity=hou.severityType.Warning,
                )
                return

            # Build env: current process env + target project's TH_* vars.
            # TH_PIPELINE_PATH is hpm-owned and inherited from the parent
            # process; never override it per-project.
            import subprocess
            env = dict(os.environ)
            env["TH_PROJECT_PATH"] = proj.project_path
            env["TH_CONFIG_PATH"] = proj.config_path

            # Use the same executable as the current Houdini process
            houdini_bin = Path(sys.executable)
            if not houdini_bin.exists():
                hfs = os.environ.get("HFS", "")
                houdini_bin = Path(hfs) / "bin" / "houdinifx.exe" if hfs else None

            # CREATE_NEW_PROCESS_GROUP detaches the child so it
            # survives after the calling callback returns.
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if houdini_bin and houdini_bin.exists():
                subprocess.Popen(
                    [str(houdini_bin), str(hip_path)],
                    env=env,
                    creationflags=flags,
                )
                log.info("Launched new Houdini instance: %s", hip_path)
            else:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", str(hip_path)],
                    env=env,
                    creationflags=flags,
                )
                log.info("Launched hip via shell: %s", hip_path)
        except Exception:
            log.exception("Failed to open in new instance: %s/%s", asset_id, dept)

    def open_workfile(self, asset_id: str, dept: str) -> None:
        """Open the latest workfile for a department.

        File-system inspection happens on the worker thread; the actual
        ``hou.hipFile.load`` must run on the GUI thread or Houdini crashes.

        Resolution is delegated to ``latest_hip_file_path_with_context``,
        which auto-routes to a covering group's workfile when one
        exists — so clicking a member shot's covered dept opens the
        group's hip, not a local shot hip.
        """
        if not asset_id:
            return
        parsed = self._catalog._resolver.split(asset_id)
        if parsed is None:
            return
        project_name, second, third = parsed
        proj = self._catalog._registry.get(project_name)
        root = self._catalog._resolver.root_for(asset_id)
        if proj is None or root is None:
            return
        try:
            cats = self._catalog._list_categories_for_project(project_name)
            if second in cats:
                work_dir = root / "assets" / second / third / dept
            else:
                work_dir = root / "shots" / second / third / dept

            hip_path: Path | None = None
            try:
                self._catalog._activate_project(proj)
                from tumblepipe.pipe import paths as paths_mod
                entity_uri = self._catalog._resolver.uri_for(asset_id)
                if entity_uri is not None:
                    resolved = paths_mod.latest_hip_file_path_with_context(
                        entity_uri, dept,
                    )
                    if resolved is not None and resolved.exists():
                        hip_path = resolved
            except Exception:
                log.debug(
                    "latest_hip_file_path_with_context failed for %s/%s",
                    asset_id, dept, exc_info=True,
                )

            # Last-resort fallback: legacy direct glob over the
            # member's own dept folder. Kept so that a tumblepipe
            # path-resolver hiccup doesn't break opens entirely.
            if hip_path is None:
                hip_path = latest_workfile(work_dir)

            if hip_path is not None:

                def _do_load(p=hip_path, target_proj=proj):
                    try:
                        import hou
                        from tumblepipe.pipe.houdini import util
                        decision = self._catalog._scene.prepare_scene_swap()
                        if decision is None:
                            return  # user cancelled the save prompt
                        self._catalog._activate_project(target_proj)
                        # Manual update mode so neither the load, the
                        # hou.setFrame() in apply_scene_timeline, nor the
                        # import re-execute triggers a live full-graph
                        # cook. Mirrors the open flow of the since-retired
                        # Project Browser.
                        with util.update_mode(hou.updateMode.Manual):
                            hou.hipFile.load(str(p), suppress_save_prompt=decision)
                            log.info("Opened workfile: %s", p)
                            self._catalog._scene.apply_scene_timeline(asset_id)
                            if self._catalog._prefs.auto_refresh_on_open:
                                self._catalog._scene.refresh_scene_imports()
                    except Exception:
                        log.exception("Failed to load workfile %s", p)
                        return
                    self._catalog._request_global_detail_refresh()

                run_on_main_thread(_do_load)
                return

            # Fallback: open the directory in Explorer (safe off-thread)
            target = work_dir if work_dir.exists() else root
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception("Failed to open workfile for %s/%s", asset_id, dept)

    def open_group_workfile(self, asset_id: str, dept: str) -> None:
        """Open the latest workfile for a group's department.

        Mirrors :meth:`_open_workfile` but routes through tumblepipe's
        group-aware path resolver. The ``hou.hipFile.load`` call is
        dispatched on the GUI thread; if no workfile exists, the
        group's work directory is opened in Explorer.
        """
        if not asset_id.startswith("group:"):
            return
        try:
            _, rest = asset_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return
        proj = self._catalog._registry.get(proj_name)
        if proj is None:
            return
        try:
            self._catalog._activate_project(proj)
            from tumblepipe.pipe import paths as paths_mod
            from tumblepipe import api as tp_api
            group_uri = uris.group(path)
            hip_path = paths_mod.latest_hip_file_path_with_context(
                group_uri, dept,
            )
            if hip_path is not None and hip_path.exists():

                def _do_load(p=hip_path, target_proj=proj):
                    try:
                        import hou
                        from tumblepipe.pipe.houdini import util
                        decision = self._catalog._scene.prepare_scene_swap()
                        if decision is None:
                            return  # user cancelled the save prompt
                        self._catalog._activate_project(target_proj)
                        # Manual update mode so neither the load, the
                        # hou.setFrame() in apply_scene_timeline, nor the
                        # import re-execute triggers a live full-graph
                        # cook. Mirrors the open flow of the since-retired
                        # Project Browser.
                        with util.update_mode(hou.updateMode.Manual):
                            hou.hipFile.load(str(p), suppress_save_prompt=decision)
                            log.info("Opened group workfile: %s", p)
                            self._catalog._scene.apply_scene_timeline(asset_id)
                            if self._catalog._prefs.auto_refresh_on_open:
                                self._catalog._scene.refresh_scene_imports()
                    except Exception:
                        log.exception(
                            "Failed to load group workfile %s", p,
                        )
                        return
                    self._catalog._request_global_detail_refresh()

                run_on_main_thread(_do_load)
                return

            # Fallback: open the resolved work directory in Explorer.
            # If the dept folder doesn't exist yet, walk up to the
            # group root so the user still lands somewhere sensible.
            workspace_uri = (
                uris.groups_root() / group_uri.segments / dept
            )
            target = tp_api.storage.resolve(workspace_uri)
            if not target.exists():
                group_root_uri = (
                    uris.groups_root() / group_uri.segments
                )
                target = tp_api.storage.resolve(group_root_uri)
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception(
                "Failed to open group workfile for %s/%s", asset_id, dept,
            )

    def open_group_dept_work_dir(self, asset_id: str, dept: str) -> None:
        """Open a group dept's workfile directory in Explorer.

        Mirrors :meth:`_open_dept_work_dir` but resolves the path via
        the group's ``groups:/`` workspace URI. Walks up to the group
        root if the dept folder doesn't exist yet.
        """
        if not asset_id.startswith("group:"):
            return
        try:
            _, rest = asset_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return
        proj = self._catalog._registry.get(proj_name)
        if proj is None:
            return
        try:
            self._catalog._activate_project(proj)
            from tumblepipe import api as tp_api
            group_uri = uris.group(path)
            workspace_uri = (
                uris.groups_root() / group_uri.segments / dept
            )
            target = tp_api.storage.resolve(workspace_uri)
            if not target.exists():
                target = tp_api.storage.resolve(
                    uris.groups_root() / group_uri.segments,
                )
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                creationflags=0x08000000,
            )
        except Exception:
            log.exception(
                "Failed to open group work dir for %s/%s", asset_id, dept,
            )

    def new_group_from_template(
        self, asset_id: str, dept: str, refresh_cb=None,
    ) -> None:
        """Save the next ``dept/vNNNN`` workfile for a group from the
        department template.

        Mirrors :meth:`_new_from_template` but routes through the
        group's ``groups:/ctx/name`` URI, which
        :func:`next_hip_file_path` and the template loader already
        handle. Falls back to a no-op if the project or URI can't be
        resolved.
        """
        if not asset_id.startswith("group:"):
            return
        try:
            _, rest = asset_id.split(":", 1)
            proj_name, path = rest.split(":", 1)
        except ValueError:
            return
        proj = self._catalog._registry.get(proj_name)
        if proj is None:
            return
        self._catalog._activate_project(proj)
        client, _err = self._catalog._clients.try_get(proj_name)
        if client is None:
            return

        try:
            from tumblepipe.pipe.paths import (
                reserve_next_hip_file_path, Context,
            )
        except Exception:
            log.exception("New: Template (group): tumblepipe imports failed")
            return

        group_uri = uris.group(path)
        # Context for groups is the first segment ("shots" or "assets")
        ctx = (
            self._catalog._group_context_from_tag(asset_id) or "shots"
        )


        def _do_create(target_proj=proj):
            try:
                import hou
                from tumblepipe.pipe.paths import get_workfile_context
                from tumblepipe.pipe.context import (
                    save_context, save_entity_context,
                )

                self._catalog._activate_project(target_proj)

                try:
                    next_path = reserve_next_hip_file_path(
                        group_uri, dept, nc_type=session_nc_type(),
                    )
                except Exception:
                    log.exception(
                        "New: Template (group): reserving next version "
                        "failed for %s/%s", asset_id, dept,
                    )
                    return
                if next_path is None:
                    hou.ui.setStatusMessage(
                        f"Create: could not resolve path for {dept}.",
                        severity=hou.severityType.Warning,
                    )
                    return

                # Resolve the template module path — groups share the
                # same per-context dept template as shots/assets.
                try:
                    template_uri = uris.dept_template(ctx, dept)
                    template_path = client.storage.resolve(template_uri)
                except Exception:
                    log.exception(
                        "New: Template (group): failed to resolve "
                        "template URI for %s/%s", ctx, dept,
                    )
                    template_path = None

                next_path.parent.mkdir(parents=True, exist_ok=True)

                hou.hipFile.clear(suppress_save_prompt=True)
                hou.hipFile.save(str(next_path))

                new_ctx = get_workfile_context(next_path) or Context(
                    entity_uri=group_uri,
                    department_name=dept,
                    version_name=Path(next_path).stem.rsplit("_", 1)[-1],
                )
                save_context(
                    next_path.parent, None, new_ctx,
                    file_extension=next_path.suffix.lstrip("."),
                )
                save_entity_context(next_path.parent, new_ctx)

                # Run the department template against /stage
                if template_path is not None and Path(template_path).exists():
                    try:
                        from tumblepipe.pipe.houdini.ui.helpers import (
                            load_module,
                        )
                        stage = hou.node("/stage")
                        if stage is not None:
                            module_name = (
                                f"{'_'.join(group_uri.segments[1:])}"
                                f"_{dept}_template_group"
                            )
                            template = load_module(
                                Path(template_path), module_name,
                            )
                            template.create(stage, group_uri, dept)
                            stage.layoutChildren()
                            hou.hipFile.save(str(next_path))
                    except Exception:
                        log.exception(
                            "New: Template (group): template apply "
                            "failed for %s/%s", ctx, dept,
                        )

                # Stamp the config timeline into the fresh group workfile
                # (see the asset/shot create path for the rationale) and
                # persist it. force_frame_range=True so non-animatable
                # groups still get a sensible starting range at creation.
                try:
                    self._catalog._scene.apply_scene_timeline(
                        asset_id, force_frame_range=True,
                    )
                    hou.hipFile.save(str(next_path))
                except Exception:
                    log.exception(
                        "New: Template (group): applying scene timeline "
                        "failed for %s/%s", asset_id, dept,
                    )

                log.info("Created group workfile: %s", next_path)
                # The detail-panel-driven refresh path only fires for
                # the currently-displayed card. After New: Template
                # from a deck item right-click we need the Multi card
                # itself to swap out its "missing" deck item for the
                # new "v0001"-bearing one — regardless of whether the
                # group's detail is open.
                try:
                    self._catalog._request_card_refresh_for_id(asset_id)
                except Exception:
                    log.exception(
                        "card refresh failed for new group workfile %s",
                        asset_id,
                    )
                if refresh_cb is not None:
                    try:
                        refresh_cb()
                    except Exception:
                        log.exception("refresh_cb failed after group create")
            except Exception:
                log.exception(
                    "New: Template (group): unexpected failure for %s/%s",
                    asset_id, dept,
                )

        run_on_main_thread(_do_create)

